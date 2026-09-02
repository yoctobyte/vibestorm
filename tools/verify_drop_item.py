"""Live check: drop an existing agent inventory item into a prim.

`UpdateTaskInventory` is the only route for item types that have no create-
from-nothing message -- notecards above all -- so this proves the second half
of whole-folder sync before the notecard chain is built on top of it.
"""

import asyncio
import os
import platform
from pathlib import Path

from vibestorm.caps.client import CapabilityClient
from vibestorm.caps.inventory_client import InventoryCapabilityClient
from vibestorm.caps.inventory_walk import walk_inventory
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.app import _await_object_inventory

INVENTORY_NOTECARD = 7


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

    client = WorldClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(duration_seconds=180.0),
            world_client=client,
            stop_event=stop,
        )
    )
    try:
        target = None
        for _ in range(60):
            await asyncio.sleep(1.0)
            session = client.current
            if session is None or not session.movement_completed:
                continue
            for full_id, obj in session.world_view.objects.items():
                fam = obj.properties_family
                if fam is not None and obj.parent_id == 0 and fam.owner_id == bootstrap.agent_id:
                    target = (full_id, obj.local_id)
                    break
            if target:
                break
        if not target:
            print("FAIL: no owned prim in view")
            return 1
        task_id, local_id = target
        print(f"target prim {task_id} local_id={local_id}")

        session = client.current
        caps = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
            session.bootstrap.seed_capability,
            ["FetchInventoryDescendents2"],
            udp_listen_port=session.caps_udp_listen_port,
            user_agent="Vibestorm",
        )
        snapshot, _state = await walk_inventory(
            InventoryCapabilityClient(timeout_seconds=15.0),
            caps["FetchInventoryDescendents2"],
            root_folder_id=bootstrap.inventory_root_folder_id,
            owner_id=bootstrap.agent_id,
            max_depth=3,
            udp_listen_port=session.caps_udp_listen_port,
        )
        notecards = [
            i
            for folder in snapshot.folders
            for i in folder.items
            if i.type == INVENTORY_NOTECARD
        ]
        if not notecards:
            print("FAIL: no notecard in agent inventory to drop")
            return 1
        item = notecards[0]
        print(f"dropping notecard {item.name!r} item_id={item.item_id}")

        before = await _await_object_inventory(client, local_id, timeout=20.0)
        if before is None:
            print("FAIL: no baseline inventory read")
            return 1
        baseline = {i.item_id for i in before.items if i.item_id is not None}
        print(f"prim has {len(before.items)} items before")

        # Must go through the session builder: queue_outbound_packet expects a
        # framed UDP packet (header, sequence, flags), not a bare message body.
        packet = session.build_update_task_inventory_packet(
            local_id=local_id,
            item_id=item.item_id,
            name=item.name,
            asset_type=INVENTORY_NOTECARD,
            inv_type=INVENTORY_NOTECARD,
        )
        client.queue_outbound_packet(client.current_handle or 0, packet)
        await asyncio.sleep(3.0)

        after = await _await_object_inventory(client, local_id, timeout=20.0)
        if after is None:
            print("FAIL: no inventory re-read")
            return 1
        added = [i for i in after.items if i.item_id is not None and i.item_id not in baseline]
        print(f"prim has {len(after.items)} items after; added={len(added)}")
        for i in added:
            print(f"  added name={i.name!r} type={i.asset_type} asset={i.asset_id}")
        if not added:
            print("FAIL: UpdateTaskInventory did not add a row")
            return 1
        if not any(i.name == item.name for i in added):
            print("FAIL: added row does not carry the expected name")
            return 1
        print("PASS: agent inventory item copied into the prim")
        return 0
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=20)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
