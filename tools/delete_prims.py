"""Remove in-world prims this project's live probes rezzed.

Its sibling ``clean_test_prim.py`` empties a prim's *inventory*; this removes
whole prims. Both exist because live verification leaves things behind and
nothing took them out: ``verify_child_prim_frame.py`` has to rez two prims and
link them to observe what frame a child's position is in, and every run of it
adds two more.

**It does not work against the local OpenSim build.** The simulator's own log
answers every attempt with

    WARN [CLIENT]: ignoring unhandled packet ObjectDelete

-- there is no handler for the message at all. The encoder follows the message
template and nothing here suggests it is wrong; the server simply does not
implement it. Prims that vanished around the first runs of this tool vanished
on their own, not because of it, and reading that coincidence as success is
what this paragraph exists to stop happening twice.

It is kept because Second Life does implement `ObjectDelete`, and because the
readback below reports honestly either way: what it prints is what the region
says afterwards.

This deletes objects out of a region, so:

- It **lists and exits** unless you pass ``--yes``.
- It only ever touches prims named on the command line, by full UUID. There is
  no pattern, no "everything I own", and no default set. A UUID is stable
  across sessions in a way the local id in an ``ObjectUpdate`` is not, so what
  you looked at is what gets deleted.
- It prints what it will do, then re-reads the region afterwards and reports
  what actually went, rather than what was asked for. That readback is the
  only check on ownership there is: nothing in this tree decodes who owns an
  object, so the refusal comes from the simulator, and the way you see it is a
  prim that is still there afterwards.
- ``Force`` is never set. That flag is for god accounts overriding the
  permission check, and this is not one.

A prim that is part of a linkset is unlinked first: deleting a root takes its
children with it, which is more than was asked for.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/delete_prims.py <uuid> [<uuid> ...]         # list only
    .venv/bin/python tools/delete_prims.py <uuid> [<uuid> ...] --yes   # remove
"""

import argparse
import asyncio
import os
import platform
import sys
from pathlib import Path
from uuid import UUID

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("object_ids", nargs="+", help="full UUIDs of the prims to remove")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually delete. Without it this lists what it would do and stops.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=8.0,
        help="how long to wait for the region to catch up before re-reading it",
    )
    return parser


def describe(obj) -> str:
    properties = getattr(obj, "properties_family", None)
    name = getattr(properties, "name", None) or "(unnamed)"
    return (
        f"{obj.full_id} local={obj.local_id} parent={obj.parent_id} "
        f"pos={tuple(round(c, 2) for c in obj.position)} name={name}"
    )


async def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        wanted = [UUID(value) for value in args.object_ids]
    except ValueError as exc:
        print(f"not a UUID: {exc}")
        return 2

    request = LoginRequest(
        login_uri=os.environ["VIBESTORM_LOGIN_URI"],
        credentials=LoginCredentials(
            first=os.environ["VIBESTORM_FIRST_NAME"],
            last=os.environ["VIBESTORM_LAST_NAME"],
            password=os.environ["VIBESTORM_PASSWORD"],
        ),
        start=os.environ.get("VIBESTORM_START_LOCATION", "uri:Vibestorm Test&128&128&25"),
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
        for _ in range(60):
            await asyncio.sleep(0.5)
            session = client.current
            if session is not None and session.movement_completed:
                break
        session = client.current
        if session is None or not session.movement_completed:
            print("FAIL: never finished arriving")
            return 1

        # Arriving is not the same as having seen the region. Objects stream in
        # over the seconds after that, so a tool that reads the world view the
        # moment it lands reports every prim as "not in view".
        deadline = args.settle_seconds * 6.0
        waited = 0.0
        while waited < deadline:
            session = client.current
            if session is not None and all(
                object_id in session.world_view.objects for object_id in wanted
            ):
                break
            await asyncio.sleep(1.0)
            waited += 1.0
        print(f"  region settled after {waited:.0f}s")

        session = client.current
        found = []
        for object_id in wanted:
            obj = session.world_view.objects.get(object_id)
            if obj is None:
                print(f"  not in view, skipping: {object_id}")
                continue
            found.append(obj)
            print(f"  {describe(obj)}")

        if not found:
            print("nothing to do: none of those objects are in view")
            return 1

        # A root takes its children with it, which is more than was asked for.
        roots = {obj.local_id for obj in found if not obj.parent_id}
        family = [
            obj
            for obj in session.world_view.objects.values()
            if obj.parent_id and obj.parent_id in roots
        ]
        for obj in family:
            print(f"  will be unlinked first (child of a named root): {describe(obj)}")

        if not args.yes:
            print(f"\nwould delete {len(found)} prim(s). Re-run with --yes to do it.")
            return 0

        handle = client.current_handle or 0
        # Deduplicated by local id. A child that was both named on the command
        # line and found as a named root's child appeared twice, and OpenSim
        # answered the duplicate with a NullReferenceException inside
        # SceneGraph.DelinkObjects -- a crash in the simulator, caused here.
        linked = list(
            {obj.local_id: obj for obj in [*(o for o in found if o.parent_id), *family]}.values()
        )
        if linked:
            print(f"--- unlinking {len(linked)} prim(s) ---")
            client.queue_outbound_packet(
                handle,
                session.build_object_delink_packet([obj.local_id for obj in linked]),
            )
            await asyncio.sleep(args.settle_seconds / 2.0)

        # A viewer selects before it acts and the simulator tracks that
        # selection, so this is what a permission check has to hand. It is not
        # known to be *required*: three of four prims deleted fine without it,
        # and the fourth -- a linkset root whose child had just been deleted --
        # refuses with or without, silently, run after run. Sent because it is
        # what a viewer does, not because it fixed anything.
        client.queue_outbound_packet(
            handle,
            session.build_object_select_packet([obj.local_id for obj in found]),
        )
        await asyncio.sleep(2.0)

        print(f"--- deleting {len(found)} prim(s) ---")
        client.queue_outbound_packet(
            handle,
            session.build_object_delete_packet([obj.local_id for obj in found]),
        )
        # What the region says, not what we asked for -- and give it time to
        # say it. The KillObject for a delete does not come back inside a
        # second, and reporting "still there" before it arrives turns a
        # successful delete into a failure that reruns and deletes nothing.
        waited = 0.0
        while waited < args.settle_seconds * 4.0:
            session = client.current
            if session is not None and not any(
                object_id in session.world_view.objects for object_id in wanted
            ):
                break
            await asyncio.sleep(1.0)
            waited += 1.0

        session = client.current
        still_there = [
            object_id for object_id in wanted if object_id in session.world_view.objects
        ]
        gone = len(wanted) - len(still_there)
        print(f"\n{gone}/{len(wanted)} gone from the region's view")
        for object_id in still_there:
            print(f"  still there: {object_id}")
        return 0 if not still_there else 1
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
