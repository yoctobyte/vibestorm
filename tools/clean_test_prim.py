"""Remove the items this project's live probes left in a test prim.

Every live verification drops rows into the local test prim and nothing takes
them out, so the prim accumulates: at the time of writing it holds ten items,
two of which collide on one file name and are reported as a conflict on every
single run. That is a verification tool authoring the failures it reports.

This deletes, and there is no undo, so:

- It **lists and exits** unless you pass ``--yes``.
- It only ever offers rows whose names match ``--prefix``, which defaults to the
  prefixes this project's own probes generate. Anything a person put in the prim
  by hand is never named, never counted and never removed.
- It prints exactly what it will do first, and re-reads the inventory afterwards
  so the report is what the simulator says, not what we asked for.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/clean_test_prim.py            # list only
    .venv/bin/python tools/clean_test_prim.py --yes      # actually remove
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
from pathlib import Path
from uuid import UUID

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.sync.task_inventory import await_object_inventory
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

#: Names this project's probes generate. Deliberately specific: a broader match
#: would eventually eat something a person made.
DEFAULT_PREFIXES = (
    "vibestorm-sync-",
    "e2e-sync-",
    "verify-sync-",
    "verify-note-",
    "sync-made-",
    "note-",
)

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


def _matches(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name.startswith(prefix) for prefix in prefixes)


async def run(args: argparse.Namespace) -> int:
    prefixes = tuple(args.prefix) if args.prefix else DEFAULT_PREFIXES
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
    try:
        local_id = await _wait_for_object(client, TASK_ID)
        if local_id is None:
            print(f"object {TASK_ID} never came into view")
            return 1

        snapshot = await await_object_inventory(client, local_id)
        if snapshot is None:
            print("the object's inventory did not come back")
            return 1

        keep, remove = [], []
        for item in snapshot.items:
            name = item.name or ""
            (remove if _matches(name, prefixes) else keep).append(item)

        print(f"\nprim {TASK_ID} holds {len(snapshot.items)} item(s)")
        for item in keep:
            print(f"  keep    {item.name!r}")
        for item in remove:
            print(f"  REMOVE  {item.name!r}  item={item.item_id}")

        if not remove:
            print("\nnothing matches; nothing to do")
            return 0
        if not args.yes:
            print(f"\n{len(remove)} item(s) would be removed. Re-run with --yes to do it.")
            return 0

        session = client.current
        for item in remove:
            if item.item_id is None:
                print(f"  skipped {item.name!r}: the listing carried no item id")
                continue
            client.queue_outbound_packet(
                client.current_handle or 0,
                session.build_remove_task_inventory_packet(
                    local_id=local_id, item_id=item.item_id
                ),
            )
            print(f"  sent removal for {item.name!r}")

        # Re-read rather than trust the sends: RemoveTaskInventory has no reply,
        # so the inventory listing is the only thing that can say it worked.
        await asyncio.sleep(2.0)
        after = await await_object_inventory(client, local_id)
        if after is None:
            print("\nthe inventory did not come back; cannot confirm")
            return 1
        left = [i.name for i in after.items if _matches(i.name or "", prefixes)]
        print(f"\nprim now holds {len(after.items)} item(s)")
        if left:
            print(f"still present: {left}")
            return 1
        print("every matching item is gone")
        return 0
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=15.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually remove the items. Without it this only lists them.",
    )
    parser.add_argument(
        "--prefix",
        action="append",
        help=(
            "A name prefix to remove. Repeatable. Defaults to the prefixes this "
            "project's probes generate."
        ),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
