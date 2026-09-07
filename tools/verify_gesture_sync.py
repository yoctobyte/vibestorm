"""Live check: the gesture round trip, and the one number that is not pinned.

Two things here cannot be settled by a unit test, and one of them is why this
tool exists at all.

**The inventory type.** A gesture is asset type 21 and inventory type 20, and
the second number comes from no source this project has: `caps/inventory_types`
says libomv's ``InventoryType`` table is not in the committed OpenSim source
and is deliberately left unguessed. So `INV_TYPE_GESTURE` is checked here
against the account's *own* gestures -- items a real viewer created -- rather
than against the constant it would otherwise be compared to. Step 2 fails the
run if they disagree, and prints what the grid actually says.

**The update.** Nothing on the far side looks at a gesture's contents:
`UpdateGestureItemAsset` stores what it is sent. A push that reports success
and a gesture that is actually there and readable are therefore two claims, not
one, and only fetching the asset back separates them.

What it does, in order:

1. Finds the object and resolves the gesture pair of capabilities.
2. Walks agent inventory and reads the inventory type off existing gestures.
3. Pushes a folder holding one `.gesture` file, which creates the row.
4. Reads the object's inventory back and checks the row is typed `gesture`.
5. Pulls the object into a fresh folder and checks the file came back with
   the suffix push will recognise and the bytes that went in.
6. Edits it, pushes again, and fetches the asset back to confirm the *new*
   bytes are what the object now holds -- an update, not a second row.
7. Writes a malformed gesture and checks the push refuses it and leaves the
   object's asset alone.
8. Removes the row it made.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_gesture_sync.py
"""

import asyncio
import os
import platform
import sys
import tempfile
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from vibestorm.assets.gesture import decode_gesture  # noqa: E402
from vibestorm.caps.client import CapabilityClient  # noqa: E402
from vibestorm.caps.inventory_client import InventoryCapabilityClient  # noqa: E402
from vibestorm.caps.inventory_walk import walk_inventory  # noqa: E402
from vibestorm.login.client import LoginClient  # noqa: E402
from vibestorm.login.models import LoginCredentials, LoginRequest  # noqa: E402
from vibestorm.sync.engine import (  # noqa: E402
    GESTURE_ASSET_TYPE,
    pull_object_to_folder,
    push_folder_to_object,
    resolve_sync_caps,
)
from vibestorm.sync.gestures import ASSET_TYPE_GESTURE, INV_TYPE_GESTURE  # noqa: E402
from vibestorm.sync.task_inventory import await_object_inventory, fetch_task_asset  # noqa: E402
from vibestorm.udp.dispatch import MessageDispatcher  # noqa: E402
from vibestorm.udp.session import SessionConfig, run_live_session  # noqa: E402
from vibestorm.udp.world_client import WorldClient  # noqa: E402

TASK_ID = UUID(os.environ.get("VIBESTORM_SYNC_OBJECT", "d7f47f7e-4328-4d17-a665-19feaec7b1e9"))

#: A real animation uuid is not needed: a gesture's steps are stored, not
#: resolved, and this client's decoder is what has to read it back.
ANIM = "b906c4ba-703b-1940-32a3-0c7f7d791510"


def _gesture(trigger: str, flag: int) -> bytes:
    """A two-line-header gesture with one animation step.

    Written out here rather than imported from the test helper, because the
    point of a live check is to exercise the shipped decoder against bytes
    assembled the way an owner's file would be.
    """
    body = f"0\nanim\n{ANIM}\n{flag}"
    text = (
        "2\n"  # version
        "255\n"  # key
        "0\n"  # mask
        f"{trigger}\n"
        "\n"  # replace-with, deliberately blank
        "1\n"  # step count
        f"{body}\n"
    )
    return text.encode("utf-8")


async def _wait_for_object(client, task_id: UUID, *, timeout: float = 60.0) -> int | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        session = client.current
        if session is not None and session.movement_completed:
            obj = session.world_view.objects.get(task_id)
            if obj is not None:
                return obj.local_id
        await asyncio.sleep(0.5)
    return None


async def _inv_type_of_existing_gestures(bootstrap) -> list[tuple[str, int | None]]:
    """What the grid says a gesture's inventory type is.

    Reads it off items this client did not create, which is the whole value:
    asking the grid to echo back a number we just sent it proves nothing.
    """
    resolved = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
        bootstrap.seed_capability, ["FetchInventoryDescendents2"], user_agent="Vibestorm"
    )
    url = resolved.get("FetchInventoryDescendents2")
    if not url or bootstrap.inventory_root_folder_id is None:
        return []
    snapshot, _ = await walk_inventory(
        InventoryCapabilityClient(timeout_seconds=20.0),
        url,
        root_folder_id=bootstrap.inventory_root_folder_id,
        owner_id=bootstrap.agent_id,
    )
    return [
        (item.name, item.inv_type)
        for folder in snapshot.folders
        for item in folder.items
        if item.type == ASSET_TYPE_GESTURE and not item.is_link
    ]


async def main() -> int:  # noqa: PLR0911, PLR0912, PLR0915 - a linear script
    request = LoginRequest(
        login_uri=os.environ["VIBESTORM_LOGIN_URI"],
        credentials=LoginCredentials(
            first=os.environ["VIBESTORM_FIRST_NAME"],
            last=os.environ["VIBESTORM_LAST_NAME"],
            password=os.environ["VIBESTORM_PASSWORD"],
        ),
        start="uri:Vibestorm Test&128&128&25",
        platform=platform.system(),
    )
    bootstrap = await LoginClient().login(request)
    print(f"login ok agent={bootstrap.agent_id}")
    if bootstrap.inventory_root_folder_id is None:
        print("FAIL: login did not name an inventory root folder")
        return 1

    client = WorldClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(duration_seconds=420.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    created_ids: list[UUID] = []
    local_id: int | None = None
    try:
        local_id = await _wait_for_object(client, TASK_ID)
        if local_id is None:
            print(f"FAIL: object {TASK_ID} never came into view")
            return 1
        print(f"object in view local_id={local_id}")

        caps = await resolve_sync_caps(client.current)
        print(f"caps task={bool(caps.gesture)} agent={bool(caps.gesture_agent)}")
        if not caps.gesture or not caps.gesture_agent:
            print("FAIL: the simulator does not offer both gesture capabilities")
            return 1

        print("--- 2. what the grid says a gesture's inventory type is ---")
        existing = await _inv_type_of_existing_gestures(bootstrap)
        observed = sorted({inv for _name, inv in existing if inv is not None})
        print(f"{len(existing)} gestures in agent inventory, inv_type values {observed}")
        if not observed:
            print(
                f"  NOTE: no existing gestures to read; INV_TYPE_GESTURE="
                f"{INV_TYPE_GESTURE} stays unverified by this run"
            )
        elif observed != [INV_TYPE_GESTURE]:
            failures.append(
                f"INV_TYPE_GESTURE is {INV_TYPE_GESTURE}, the grid's own gestures say {observed}"
            )
            print(f"  FAIL: {failures[-1]}")
        else:
            print(f"  ok: matches INV_TYPE_GESTURE={INV_TYPE_GESTURE}")

        name = f"vibestorm-gesture-{os.getpid()}"
        first = _gesture(trigger="/vibestorm", flag=0)
        second = _gesture(trigger="/vibestorm", flag=1)
        if first == second:
            print("FAIL: the two fixtures are identical; the update step would prove nothing")
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "push"
            folder.mkdir()
            (folder / f"{name}.gesture").write_bytes(first)

            print("--- 3. pushing a folder with a new gesture in it ---")
            outcome = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=caps.script,
                notecard_cap=caps.notecard,
                notecard_agent_cap=caps.notecard_agent,
                gesture_cap=caps.gesture,
                gesture_agent_cap=caps.gesture_agent,
                new_file_cap=caps.new_file,
                agent_folder_id=bootstrap.inventory_root_folder_id,
                on_progress=lambda line: print(f"  {line}"),
            )
            print(
                f"  created={outcome.created} uploaded={outcome.uploaded} "
                f"skipped={outcome.skipped} failed={outcome.failed}"
            )
            if not outcome.created:
                print("FAIL: the gesture row was not created")
                return 1

            print("--- 4. the row the object now holds ---")
            snapshot = await await_object_inventory(client, local_id)
            if snapshot is None:
                print("FAIL: the object's inventory did not come back")
                return 1
            row = next((i for i in snapshot.items if i.name.startswith(name)), None)
            if row is None:
                print(f"FAIL: no row named {name!r} in the object")
                return 1
            created_ids.append(row.item_id)
            print(
                f"  name={row.name!r} asset_type={row.asset_type!r} "
                f"inv_type={row.inventory_type!r} asset_id={row.asset_id}"
            )
            if row.asset_type != "gesture":
                failures.append(f"row typed {row.asset_type!r}, not 'gesture'")
                print(f"  FAIL: {failures[-1]}")
            first_asset_id = row.asset_id

            print("--- 5. pulling it back out ---")
            pulled = Path(tmp) / "pull"
            pulled.mkdir()
            await pull_object_to_folder(
                client, task_id=TASK_ID, local_id=local_id, folder=pulled
            )
            written = pulled / f"{row.name}.gesture"
            if not written.is_file():
                names = sorted(p.name for p in pulled.iterdir())
                failures.append(f"pull wrote no {row.name}.gesture (got {names})")
                print(f"  FAIL: {failures[-1]}")
            elif written.read_bytes() != first:
                failures.append("the pulled gesture is not the bytes that were pushed")
                print(f"  FAIL: {failures[-1]}")
            else:
                print(f"  ok: {written.name} came back byte for byte")

            print("--- 6. editing it and pushing again ---")
            (folder / f"{name}.gesture").write_bytes(second)
            # The pushed folder tracks the row under the name the object gave
            # it, which may be "<name> 1" if something collided; rename the
            # local file to match so this is an update rather than a create.
            if row.name != name:
                (folder / f"{name}.gesture").rename(folder / f"{row.name}.gesture")
                print(f"  (the object renamed it to {row.name!r})")
            outcome = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=caps.script,
                notecard_cap=caps.notecard,
                notecard_agent_cap=caps.notecard_agent,
                gesture_cap=caps.gesture,
                gesture_agent_cap=caps.gesture_agent,
                new_file_cap=caps.new_file,
                agent_folder_id=bootstrap.inventory_root_folder_id,
                on_progress=lambda line: print(f"  {line}"),
            )
            print(
                f"  created={outcome.created} uploaded={outcome.uploaded} "
                f"skipped={outcome.skipped} failed={outcome.failed}"
            )
            if outcome.created:
                failures.append("the second push created a row instead of updating one")
                print(f"  FAIL: {failures[-1]}")

            after = await await_object_inventory(client, local_id)
            row_after = next(
                (i for i in (after.items if after else ()) if i.item_id == row.item_id), None
            )
            if row_after is None:
                print("FAIL: the row vanished after the update")
                return 1
            print(f"  asset_id {first_asset_id} -> {row_after.asset_id}")
            if row_after.asset_id == first_asset_id:
                failures.append("the asset id did not move; the update did not land")
                print(f"  FAIL: {failures[-1]}")

            print("--- 6b. fetching the asset back ---")
            data = await fetch_task_asset(
                client,
                asset_id=row_after.asset_id,
                asset_type=GESTURE_ASSET_TYPE,
                task_id=TASK_ID,
                item_id=row_after.item_id,
            )
            if data is None:
                failures.append("the gesture asset did not come back")
                print(f"  FAIL: {failures[-1]}")
            elif data != second:
                failures.append(f"the object holds {len(data)} bytes, not the edited gesture")
                print(f"  FAIL: {failures[-1]}")
                print(f"    got:      {data[:80]!r}")
                print(f"    expected: {second[:80]!r}")
            else:
                decode_gesture(data)
                print("  ok: the object holds the edited gesture, and it decodes")

            print("--- 7. a malformed gesture must be refused ---")
            (folder / f"{row.name}.gesture").write_bytes(b"this is not a gesture")
            outcome = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=caps.script,
                notecard_cap=caps.notecard,
                notecard_agent_cap=caps.notecard_agent,
                gesture_cap=caps.gesture,
                gesture_agent_cap=caps.gesture_agent,
                new_file_cap=caps.new_file,
                agent_folder_id=bootstrap.inventory_root_folder_id,
            )
            print(f"  failed={outcome.failed} uploaded={outcome.uploaded}")
            if not any("not a readable gesture" in reason for _f, reason in outcome.failed):
                failures.append("a malformed gesture was not refused")
                print(f"  FAIL: {failures[-1]}")
            final = await await_object_inventory(client, local_id)
            row_final = next(
                (i for i in (final.items if final else ()) if i.item_id == row.item_id), None
            )
            if row_final is not None and row_final.asset_id != row_after.asset_id:
                failures.append("the refused gesture still changed the object's asset")
                print(f"  FAIL: {failures[-1]}")
            else:
                print("  ok: the object's asset is untouched")

        if failures:
            print(f"\nFAIL: {len(failures)} check(s) failed")
            for line in failures:
                print(f"  - {line}")
            return 1
        print("\nPASS: gesture created, pulled, updated, fetched back, and malformed refused")
        return 0
    finally:
        for item_id in created_ids:
            await _cleanup(client, local_id, item_id)
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=20)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


async def _cleanup(client, local_id: int | None, item_id: UUID | None) -> None:
    """Leave the prim as it was found."""
    session = client.current
    if session is None or local_id is None or item_id is None:
        return
    try:
        client.queue_outbound_packet(
            client.current_handle or 0,
            session.build_remove_task_inventory_packet(local_id=local_id, item_id=item_id),
        )
        print(f"cleaned up: removed {item_id} from {local_id}")
        await asyncio.sleep(1.0)
    except Exception as exc:  # noqa: BLE001 - cleanup must not mask the result
        print(f"cleanup failed (harmless, remove {item_id} by hand): {exc}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
