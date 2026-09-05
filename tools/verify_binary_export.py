"""Live check: a non-text asset comes out of an object intact, and stays out.

Run against local OpenSim with the credentials in ``local/vibestorm-login.env``:

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_binary_export.py


C is "extract all internals". Scripts and notecards were the easy half; this
is the other one. It drops a real texture from agent inventory into the test
prim, pulls with ``include_binary``, and compares the file on disk against the
bytes the simulator serves for that asset id -- so a pass says the export is
the asset, not merely that a file appeared.
"""

import asyncio
import os
import platform
import tempfile
from pathlib import Path
from uuid import UUID

from vibestorm.caps.client import CapabilityClient
from vibestorm.caps.inventory_client import InventoryCapabilityClient
from vibestorm.caps.inventory_walk import walk_inventory
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.sync.engine import pull_object_to_folder, push_folder_to_object, resolve_sync_caps
from vibestorm.sync.notecards import copy_item_into_object
from vibestorm.sync.task_inventory import await_object_inventory, fetch_task_asset
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

TASK_ID = UUID(os.environ.get("VIBESTORM_SYNC_OBJECT", "d7f47f7e-4328-4d17-a665-19feaec7b1e9"))
TEXTURE = 0
BODYPART = 13

#: (asset type, exported suffix, a signature the bytes must carry). The
#: signature is what separates "a file appeared" from "the asset came out
#: intact" -- a truncated or re-encoded wearable still writes a file.
WANTED = [
    (BODYPART, ".wearable", b"LLWearable"),
    (TEXTURE, ".j2k", None),
]


async def _wait_for_object(client, task_id, *, timeout=60.0):
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

    # Find a texture in the agent's own inventory to drop into the prim.
    resolved = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
        bootstrap.seed_capability, ["FetchInventoryDescendents2"], user_agent="Vibestorm"
    )
    snapshot, _ = await walk_inventory(
        InventoryCapabilityClient(timeout_seconds=20.0),
        resolved["FetchInventoryDescendents2"],
        root_folder_id=bootstrap.inventory_root_folder_id,
        owner_id=bootstrap.agent_id,
    )
    all_items = [item for folder in snapshot.folders for item in folder.items]
    sources = []
    for asset_type, suffix, signature in WANTED:
        found = [it for it in all_items if getattr(it, "type", None) == asset_type]
        if not found:
            print(f"  note: the account holds no asset of type {asset_type}; skipping it")
            continue
        sources.append((max(found, key=lambda it: it.name or ""), asset_type, suffix, signature))
    if not sources:
        print("FAIL: the account has nothing non-text to drop into the prim")
        return 1
    for item, asset_type, suffix, _ in sources:
        print(f"source type={asset_type} item={item.item_id} name={item.name!r} -> *{suffix}")

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
    try:
        local_id = await _wait_for_object(client, TASK_ID)
        if local_id is None:
            print(f"FAIL: object {TASK_ID} never came into view")
            return 1
        print(f"object in view local_id={local_id}")

        print("--- 1. drop them into the prim ---")
        # Reuse a row this tool left behind on an earlier run rather than
        # dropping a second copy. The simulator renames a colliding copy to
        # "<name> 1", so a tool that always drops would silently fill the prim
        # with numbered duplicates and make its own conflict reports.
        existing = await await_object_inventory(client, local_id)
        present = {
            (it.name or ""): it
            for it in (existing.items if existing is not None else ())
        }
        dropped = []
        for item, asset_type, suffix, signature in sources:
            already = present.get(item.name or "")
            if already is not None:
                print(f"    {item.name!r} is already in the prim; reusing it")
                dropped.append(
                    (item, already.name, already.item_id, asset_type, suffix, signature)
                )
                continue
            copied = await copy_item_into_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                local_id=local_id,
                item_id=item.item_id,
                name=item.name,
                asset_type=asset_type,
                inv_type=asset_type,
            )
            if copied is None:
                failures.append(f"{item.name!r} did not land in the object")
                continue
            task_item_id, assigned = copied
            print(f"    {item.name!r} landed as {assigned!r}")
            dropped.append((item, assigned, task_item_id, asset_type, suffix, signature))
        if not dropped:
            print("FAIL: nothing landed in the object")
            return 1

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "work"

            print("--- 2. pull without --all-assets: they are skipped ---")
            plain = await pull_object_to_folder(
                client, task_id=TASK_ID, local_id=local_id, folder=folder
            )
            skipped = {n for n, _ in plain.skipped}
            for _item, assigned, _tid, _at, _sfx, _sig in dropped:
                if assigned not in skipped:
                    failures.append(f"a text-only pull did not skip {assigned!r}")
            if not failures:
                print(f"    skipped {sorted(skipped)}, as it should be")

            print("--- 3. pull with --all-assets ---")
            full = await pull_object_to_folder(
                client,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                include_binary=True,
            )
            print(f"    {full.summary()}")

            exported: list[str] = []
            for item, assigned, task_item_id, asset_type, suffix, signature in dropped:
                written = [n for n in full.transferred if n.endswith(suffix)]
                if not written:
                    failures.append(f"nothing ending in {suffix} was written")
                    continue
                name = written[0]
                exported.append(name)
                on_disk = (folder / name).read_bytes()
                print(f"    wrote {name} ({len(on_disk)} bytes) head {on_disk[:10].hex(' ')}")

                if signature is not None and not on_disk.startswith(signature):
                    failures.append(f"{name} does not start with {signature!r}")

                # Compare against what the simulator serves, not against itself.
                served = await fetch_task_asset(
                    client,
                    asset_id=UUID(str(item.asset_id)),
                    asset_type=asset_type,
                    task_id=TASK_ID,
                    item_id=task_item_id,
                )
                if served is None:
                    failures.append(f"could not re-fetch {name} to compare against")
                elif served != on_disk:
                    failures.append(
                        f"{name} differs from what the sim serves "
                        f"({len(on_disk)} vs {len(served)} bytes)"
                    )
                else:
                    print(f"    {name} matches the asset the sim serves, byte for byte")

            print("--- 4. push must never send them back ---")
            caps = await resolve_sync_caps(client.current)
            pushed = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=caps.script,
                notecard_cap=caps.notecard,
                notecard_agent_cap=caps.notecard_agent,
                agent_folder_id=bootstrap.inventory_root_folder_id,
            )
            print(f"    {pushed.summary()}")
            reasons = {n: r for n, r in pushed.skipped}
            for name in exported:
                if name in pushed.transferred or name in pushed.created:
                    failures.append(f"push sent {name} back")
                print(f"    {name}: {reasons.get(name, '(NOT SKIPPED)')}")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=15.0)

    if failures:
        print("\nFAIL:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nPASS: non-text assets export intact and are never pushed back")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
