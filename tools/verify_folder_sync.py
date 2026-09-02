"""End-to-end live check of whole-folder object sync against local OpenSim.

Drives the real code paths -- `_create_task_script_rows` and the task
inventory upload capability -- rather than reimplementing them, so a pass here
says something about the shipped code.
"""

import asyncio
import os
import platform
import tempfile
from pathlib import Path

from vibestorm.bus.events import ObjectInventorySnapshotReady
from vibestorm.caps.client import CapabilityClient
from vibestorm.caps.task_inventory_upload_client import TaskInventoryUploadClient
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import DEFAULT_SCRIPT_ASSET_ID
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.app import (
    SCRIPT_TASK_CAP_NAMES,
    _await_object_inventory,
    _create_task_script_rows,
    _first_resolved,
)

SCRIPT_TEXT = """default
{
    state_entry()
    {
        llSay(0, "vibestorm folder sync end to end");
    }
}
"""


class _Scene:
    def apply_chat_alert(self, alert):
        print(f"  [scene] {getattr(alert, 'message', alert)}")


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
    print(f"login ok sim={bootstrap.sim_ip}:{bootstrap.sim_port}")

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
        # Wait for a prim we own, with its ownership known.
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
            print("FAIL: no prim owned by this avatar came into view")
            return 1
        task_id, local_id = target
        print(f"target prim {task_id} local_id={local_id}")

        session = client.current
        caps = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
            session.bootstrap.seed_capability,
            SCRIPT_TASK_CAP_NAMES,
            udp_listen_port=session.caps_udp_listen_port,
            user_agent="Vibestorm",
        )
        script_cap = _first_resolved(caps, SCRIPT_TASK_CAP_NAMES)
        print(f"script cap resolved: {bool(script_cap)}")
        if not script_cap:
            print("FAIL: no script task cap")
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            name = f"e2e-sync-{os.getpid()}"
            path = Path(tmp) / f"{name}.lsl"
            path.write_text(SCRIPT_TEXT)

            print("--- creating row ---")
            created, skipped = await _create_task_script_rows(
                client, session, _Scene(),
                handle=client.current_handle or 0,
                task_id=task_id, local_id=local_id, files=[path],
            )
            if not created:
                print(f"FAIL: no row created; skipped={skipped}")
                return 1
            _f, selection = created[0]
            print(f"created row item_id={selection.item_id} name={selection.item_name!r}")

            print("--- uploading contents onto it ---")
            result = await TaskInventoryUploadClient(timeout_seconds=25.0).upload_task_script(
                script_cap,
                item_id=selection.item_id,
                task_id=task_id,
                script_bytes=path.read_bytes(),
                udp_listen_port=session.caps_udp_listen_port,
            )
            print(f"upload state={result.state} compiled={result.compiled} errors={result.errors[:2]}")
            if not result.compiled:
                print("FAIL: uploaded script did not compile")
                return 1

            print("--- re-reading to confirm the asset changed ---")
            after = await _await_object_inventory(client, local_id, timeout=20.0)
            if after is None:
                print("FAIL: no inventory re-read")
                return 1
            row = next((i for i in after.items if i.item_id == selection.item_id), None)
            if row is None:
                print("FAIL: created row vanished")
                return 1
            print(f"final row name={row.name!r} asset_id={row.asset_id} type={row.asset_type}")
            if row.asset_id == DEFAULT_SCRIPT_ASSET_ID:
                print("FAIL: row still points at the default script; upload did not land")
                return 1
            print("PASS: row created, contents uploaded, compiled, and asset id moved off the default")
            return 0
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=20)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
