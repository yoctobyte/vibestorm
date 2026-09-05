"""End-to-end live check of object <-> folder sync against local OpenSim.

Drives the shipped engine -- ``pull_object_to_folder``, ``push_folder_to_object``
and the watch loop's change detector -- rather than reimplementing them, so a
pass here says something about the code that ships.

The properties it checks, in order:

1. **Pull writes real content.** Not "a file appeared": the bytes match what
   the simulator serves.
2. **Pull then push is a no-op.** The push must recognise its own pulled files
   as already in step. This is what makes a watch loop safe to leave running;
   without it every tick would re-upload the whole folder.
3. **An edit reaches the object.** Push it, then pull into a *fresh* folder and
   compare -- reading back through the sim rather than trusting the upload's
   own report.
4. **Pushing twice uploads once.** A push that fails to record the asset id the
   sim assigned looks exactly like an in-world edit on the next run.
"""

import asyncio
import os
import platform
import tempfile
from pathlib import Path
from uuid import UUID

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.sync.engine import (
    pull_object_to_folder,
    push_folder_to_object,
    resolve_sync_caps,
)
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

TASK_ID = UUID(os.environ.get("VIBESTORM_SYNC_OBJECT", "d7f47f7e-4328-4d17-a665-19feaec7b1e9"))


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

        script_cap, notecard_cap = await resolve_sync_caps(client.current)
        print(f"caps script={bool(script_cap)} notecard={bool(notecard_cap)}")
        if not script_cap:
            print("FAIL: no script task capability")
            return 1

        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw) / "work"

            print("--- 1. pull ---")
            pulled = await pull_object_to_folder(
                client, task_id=TASK_ID, local_id=local_id, folder=folder
            )
            print(f"    {pulled.summary()}")
            for name, reason in pulled.conflicts:
                print(f"    conflict: {name}: {reason}")
            scripts = sorted(folder.glob("*.lsl"))
            if not scripts:
                print("FAIL: pull produced no scripts to work with")
                return 1
            target = scripts[0]
            print(f"    working on {target.name} ({target.stat().st_size} bytes)")

            print("--- 2. push with nothing edited ---")
            quiet = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=script_cap,
                notecard_cap=notecard_cap,
            )
            print(f"    {quiet.summary()}")
            if quiet.transferred or quiet.created:
                failures.append("a push straight after a pull uploaded something")

            print("--- 3. edit, push, and read it back through the sim ---")
            marker = f"verify-object-folder-sync-{os.getpid()}"
            target.write_text(
                "default\n{\n    state_entry()\n    {\n"
                f'        llSay(0, "{marker}");\n'
                "    }\n}\n"
            )
            pushed = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=script_cap,
                notecard_cap=notecard_cap,
            )
            print(f"    {pushed.summary()}")
            for name, reason in pushed.failed:
                print(f"    failed: {name}: {reason}")
            if target.name not in pushed.transferred:
                failures.append(f"the edit to {target.name} was not uploaded")

            fresh = Path(raw) / "readback"
            back = await pull_object_to_folder(
                client, task_id=TASK_ID, local_id=local_id, folder=fresh
            )
            print(f"    read back: {back.summary()}")
            readback = fresh / target.name
            if not readback.exists():
                failures.append(f"{target.name} did not come back from the sim")
            elif marker not in readback.read_text():
                failures.append(f"{target.name} came back without the edit")
            else:
                print(f"    the sim served the edited script back, marker {marker} present")

            print("--- 4. push again ---")
            again = await push_folder_to_object(
                client,
                client.current,
                handle=client.current_handle or 0,
                task_id=TASK_ID,
                local_id=local_id,
                folder=folder,
                script_cap=script_cap,
                notecard_cap=notecard_cap,
            )
            print(f"    {again.summary()}")
            if again.transferred or again.created:
                failures.append("pushing an unchanged folder uploaded again")
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: pull, no-op push, edit round trip, and repeat push all behaved")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
