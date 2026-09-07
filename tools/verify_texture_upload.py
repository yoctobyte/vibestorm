"""Live check: does a texture the owner made actually get into an object?

This is D's remaining half, and the only evidence that matters for it is a
round trip through a simulator. The unit tests say the right bytes are handed
to the right capability with the right type strings; they cannot say that
OpenSim agrees, because the thing they mock is exactly the thing in question
-- `NewFileAgentInventory` types a texture correctly only by *taking none of
its branches*, and a claim shaped like that is worth checking against a real
one.

What it does, in order:

1. Builds a PNG in memory with a colour nothing else would produce.
2. Uploads it through both hops -- agent inventory, then a copy into the
   prim -- with the shipped `create_task_texture`.
3. Reads the object's inventory back and finds the row, checking the
   simulator typed it as a texture (asset type 0) rather than leaving it at
   whatever the fall-through gave it.
4. Pulls the asset back out through the *same* path the viewer uses to draw
   one, decodes it, and compares the colour and the size. This is the step
   that distinguishes "the upload reported success" from "the texture is
   there", and they are not the same thing: an item can exist, correctly
   typed, holding bytes nothing can read.
5. Does it again through `push_folder_to_object` -- a folder with a PNG in
   it, which is D as the owner would actually use it -- and checks that a
   second push reports the texture *skipped* rather than uploading a second
   copy beside it as `sunset 1`.
6. Removes the rows it made, so the prim is left as it was found.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_texture_upload.py
"""

import asyncio
import io
import os
import platform
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from vibestorm.assets.j2k import decode_j2k  # noqa: E402
from vibestorm.caps.get_texture_client import GetTextureClient  # noqa: E402
from vibestorm.login.client import LoginClient  # noqa: E402
from vibestorm.login.models import LoginCredentials, LoginRequest  # noqa: E402
from vibestorm.sync.engine import push_folder_to_object, resolve_sync_caps  # noqa: E402
from vibestorm.sync.task_inventory import await_object_inventory  # noqa: E402
from vibestorm.sync.textures import create_task_texture  # noqa: E402
from vibestorm.udp.dispatch import MessageDispatcher  # noqa: E402
from vibestorm.udp.session import SessionConfig, run_live_session  # noqa: E402
from vibestorm.udp.world_client import WorldClient  # noqa: E402

TASK_ID = UUID(os.environ.get("VIBESTORM_SYNC_OBJECT", "d7f47f7e-4328-4d17-a665-19feaec7b1e9"))

#: A colour no default texture is, so a wrong asset coming back is obvious
#: rather than plausible.
MARKER_COLOUR = (203, 61, 137)

#: Not a power of two, on purpose: the encoder rounds down, and a run that
#: came back 96x96 would mean the fitting step never ran.
SOURCE_SIZE = (96, 96)
EXPECTED_SIZE = (64, 64)


def _marker_png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", SOURCE_SIZE, MARKER_COLOUR).save(buffer, format="PNG")
    return buffer.getvalue()


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


async def main() -> int:
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
            config=SessionConfig(duration_seconds=300.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    made: tuple[UUID, str] | None = None
    #: Every row this run put in the prim, so the cleanup can take them all
    #: out again whatever went wrong in between.
    created_ids: list[UUID] = []
    local_id: int | None = None
    try:
        local_id = await _wait_for_object(client, TASK_ID)
        if local_id is None:
            print(f"FAIL: object {TASK_ID} never came into view")
            return 1
        print(f"object in view local_id={local_id}")

        caps = await resolve_sync_caps(client.current)
        print(f"caps new-file={bool(caps.new_file)}")
        if not caps.new_file:
            print("FAIL: the simulator offers no NewFileAgentInventory capability")
            return 1

        name = f"vibestorm-texture-{os.getpid()}"
        print("--- 1. upload and copy ---")
        made = await create_task_texture(
            client,
            client.current,
            handle=client.current_handle or 0,
            local_id=local_id,
            folder_id=bootstrap.inventory_root_folder_id,
            upload_url=caps.new_file,
            path=Path(f"{name}.png"),
            data=_marker_png(),
            on_progress=lambda message: print(f"    {message}"),
        )
        if made is None:
            print("FAIL: the texture never appeared in the object")
            return 1
        item_id, assigned = made
        created_ids.append(item_id)
        print(f"    row {assigned!r} item={item_id}")

        print("--- 2. the row the simulator wrote ---")
        snapshot = await await_object_inventory(client, local_id)
        row = next(
            (item for item in (snapshot.items if snapshot else ()) if item.item_id == item_id),
            None,
        )
        if row is None:
            failures.append("the row vanished between the copy and the read-back")
        else:
            print(f"    name={row.name!r} type={row.asset_type!r} asset={row.asset_id}")
            # Task inventory names types as strings; a texture is "texture",
            # which is what asset type 0 resolves to.
            if str(row.asset_type) not in ("0", "texture"):
                failures.append(f"the row is typed {row.asset_type!r}, not a texture")

        print("--- 3. the bytes, fetched back the way the viewer fetches one ---")
        asset_id = getattr(row, "asset_id", None) if row is not None else None
        if asset_id is None:
            failures.append("the row carries no asset id, so the texture cannot be fetched")
        else:
            texture_cap = await _texture_cap(client)
            if texture_cap is None:
                print("    skipped: no GetTexture capability on this simulator")
            else:
                fetched = await GetTextureClient().fetch(texture_cap, asset_id)
                decoded = decode_j2k(fetched.data)
                print(
                    f"    {len(fetched.data):,} bytes, {decoded.width}x{decoded.height} "
                    f"{decoded.mode}"
                )
                if (decoded.width, decoded.height) != EXPECTED_SIZE:
                    failures.append(
                        f"came back {decoded.width}x{decoded.height}, expected {EXPECTED_SIZE}"
                    )
                red, green, blue = decoded.pixels[0], decoded.pixels[1], decoded.pixels[2]
                for got, want, channel in zip(
                    (red, green, blue), MARKER_COLOUR, ("red", "green", "blue")
                ):
                    if abs(got - want) > 6:
                        failures.append(f"{channel} came back {got}, expected about {want}")
        print("--- 4. the same thing through a folder push ---")
        failures.extend(
            await _check_folder_push(client, bootstrap, caps, local_id, created_ids)
        )
    finally:
        if local_id is not None:
            for made_id in created_ids:
                await _remove_row(client, local_id, made_id)
        stop.set()
        await asyncio.wait_for(task, timeout=30.0)

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: the texture went up, was typed as one, and came back readable")
    return 0


async def _check_folder_push(client, bootstrap, caps, local_id: int, created_ids) -> list[str]:
    """D as the owner would use it: a folder with a picture in it.

    The direct call above proves the two hops work. This proves the *planner*
    routes an image to them -- and, on the second run, that it does not route
    it there again. A push that re-uploaded every texture on every tick would
    make the watch loop unusable and would fill the object with `sunset 1`,
    `sunset 2`, and so on, one per save.
    """
    import tempfile

    from vibestorm.sync.task_inventory import await_object_inventory

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        name = f"folder-texture-{os.getpid()}"
        (folder / f"{name}.png").write_bytes(_marker_png())

        first = await push_folder_to_object(
            client,
            client.current,
            handle=client.current_handle or 0,
            task_id=TASK_ID,
            local_id=local_id,
            folder=folder,
            script_cap=caps.script,
            notecard_cap=caps.notecard,
            notecard_agent_cap=caps.notecard_agent,
            new_file_cap=caps.new_file,
            agent_folder_id=bootstrap.inventory_root_folder_id,
            on_progress=lambda message: print(f"    {message}"),
        )
        print(f"    first push: {first.summary()}")
        for name_, reason in [*first.skipped, *first.failed]:
            print(f"      {name_}: {reason}")
        if f"{name}.png" not in first.created:
            failures.append(f"the folder push did not create {name}.png")

        snapshot = await await_object_inventory(client, local_id)
        rows = [item for item in (snapshot.items if snapshot else ()) if item.name == name]
        for row in rows:
            if row.item_id is not None:
                created_ids.append(row.item_id)
        if len(rows) != 1:
            failures.append(f"expected one row called {name!r}, found {len(rows)}")

        second = await push_folder_to_object(
            client,
            client.current,
            handle=client.current_handle or 0,
            task_id=TASK_ID,
            local_id=local_id,
            folder=folder,
            script_cap=caps.script,
            notecard_cap=caps.notecard,
            notecard_agent_cap=caps.notecard_agent,
            new_file_cap=caps.new_file,
            agent_folder_id=bootstrap.inventory_root_folder_id,
        )
        print(f"    second push: {second.summary()}")
        if second.created:
            failures.append(f"the second push created {second.created} all over again")
        if not any(file_name == f"{name}.png" for file_name, _ in second.skipped):
            failures.append("the second push did not report the texture as skipped")

        failures.extend(
            await _check_collision(
                client, bootstrap, caps, local_id=local_id, created_ids=created_ids
            )
        )
    return failures


async def _check_collision(client, bootstrap, caps, *, local_id, created_ids):
    """The same idempotency, for a name the object has to change.

    Everything above exercises the case where nothing collides, which is the
    case where matching by name happens to work. When the object already holds
    a row by that name the copy arrives as ``<name> 1``, and a planner matching
    on the name then reports "not there" and uploads it again -- ``<name> 2``
    on the next push, ``<name> 3`` after that, without limit. That is a real
    bug this tool did not catch, found later by a mutation battery, and this
    is the step that would have.
    """
    import tempfile

    from vibestorm.sync.task_inventory import await_object_inventory

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        name = f"collide-texture-{os.getpid()}"

        # A *notecard* of that name, so the texture planner's own
        # already-there check cannot see it and the create really runs.
        note_folder = Path(raw) / "note"
        note_folder.mkdir()
        (note_folder / f"{name}.txt").write_bytes(b"placeholder, to collide with")
        await _push(client, bootstrap, caps, local_id=local_id, folder=note_folder)

        (folder / f"{name}.png").write_bytes(_marker_png())
        for attempt in (1, 2, 3):
            outcome = await _push(client, bootstrap, caps, local_id=local_id, folder=folder)
            print(f"    collision push {attempt}: {outcome.summary()}")
            if attempt > 1 and outcome.created:
                failures.append(
                    f"push {attempt} created {outcome.created} again after a rename"
                )

        snapshot = await await_object_inventory(client, local_id)
        rows = [
            item
            for item in (snapshot.items if snapshot else ())
            if item.name.startswith(name)
        ]
        for row in rows:
            if row.item_id is not None:
                created_ids.append(row.item_id)
        print(f"    rows named {name!r}*: {[row.name for row in rows]}")
        # One notecard and one texture. A third row means a copy per push.
        if len(rows) != 2:
            failures.append(f"expected 2 rows starting {name!r}, found {len(rows)}")
    return failures


async def _push(client, bootstrap, caps, *, local_id, folder):
    return await push_folder_to_object(
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


async def _texture_cap(client) -> str | None:
    from vibestorm.caps.client import CapabilityClient

    session = client.current
    caps = await CapabilityClient().resolve_seed_caps(
        session.bootstrap.seed_capability,
        ["GetTexture"],
        udp_listen_port=session.caps_udp_listen_port,
        user_agent="Vibestorm",
    )
    return caps.get("GetTexture") or None


async def _remove_row(client, local_id: int, item_id: UUID) -> None:
    """Leave the prim as it was found.

    A verifier that accumulates rows makes its own next run slower and the
    owner's object messier, and the row it left is indistinguishable from one
    somebody put there on purpose.
    """
    session = client.current
    if session is None:
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
