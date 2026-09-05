"""Live check: what frame is a child prim's position expressed in?

`ObjectUpdate` carries a `parent_id`, and the 3D viewer ignores it -- every
prim is drawn at the position the update reports, as if that were a region
position. If a child's position is instead relative to its parent, then every
linkset in every region is drawn wrong, with the children scattered near the
region corner instead of around their root.

Nothing in this tree records which it is, the local test region contains no
linksets to look at, and no viewer implementation is consulted here. So this
makes one: it rezzes two prims at known, well-separated positions, links them,
and reads back what the simulator then says about the child.

The two answers are unmistakable. A prim rezzed near the middle of a region
sits at roughly (128, 128, 25). If the child comes back still reporting numbers
like that, positions are absolute and the viewer is already correct. If it
comes back reporting a couple of metres -- the offset from its root -- they are
parent-relative and the viewer has a real bug.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_child_prim_frame.py

It takes the linkset away again afterwards, by wearing each prim and taking it
off -- ``ObjectDelete`` goes unhandled on this simulator. See
``tools/prim_cleanup.py``. Anything it cannot remove it names.
"""

import asyncio
import math
import os
import platform
from pathlib import Path

from probe_support import take_prim_away, wait_until_quiet

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

#: How far apart to rez the two prims. Comfortably larger than any plausible
#: rez jitter, and comfortably smaller than the region, so "a few metres" and
#: "a region coordinate" cannot be mistaken for each other.
SEPARATION_M = 4.0


def new_objects(session, known: set[int]) -> list:
    """Objects whose *local id* was not present before. Keyed by local id, not
    by full id: the two are different numbers and comparing across them silently
    matches nothing, which is how an earlier run linked two prims it had not
    rezzed."""
    return [obj for obj in session.world_view.objects.values() if obj.local_id not in known]


async def settle(client, seconds: float) -> None:
    await asyncio.sleep(seconds)


async def main() -> int:
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
            config=SessionConfig(duration_seconds=420.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    #: Everything this run rezzed, so the cleanup can take it away again.
    rezzed_ids: list[object] = []
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

        me = session.world_view.objects.get(bootstrap.agent_id)
        if me is None or me.position is None:
            print("FAIL: cannot see my own avatar, so there is nowhere to rez")
            return 1
        base_x, base_y, base_z = me.position
        # Not the moment we land: objects stream in for tens of seconds after
        # that, and a snapshot taken early calls every late arrival a prim
        # this run rezzed. One run linked two prims from an earlier run that
        # way and reported on those.
        settled = await wait_until_quiet(client)
        print(f"    region settled at {settled} objects")
        session = client.current
        known = {obj.local_id for obj in session.world_view.objects.values()}

        print("--- 1. rez two prims a known distance apart ---")
        spots = (
            (base_x + 2.0, base_y, base_z + 1.0),
            (base_x + 2.0 + SEPARATION_M, base_y, base_z + 1.0),
        )
        handle = client.current_handle or 0
        for spot in spots:
            client.queue_outbound_packet(handle, session.build_object_add_packet(position=spot))
            await settle(client, 2.0)

        await settle(client, 4.0)
        session = client.current
        rezzed = new_objects(session, known)
        for obj in rezzed:
            print(f"    rezzed local={obj.local_id} pos={obj.position} parent={obj.parent_id}")
        if len(rezzed) < 2:
            print(f"FAIL: expected two new prims, saw {len(rezzed)}")
            return 1
        # Identify them by where they were asked to go, not by arrival order:
        # the sim may send other updates in between.
        rezzed = [
            min(rezzed, key=lambda obj, spot=spot: math.dist(obj.position, spot))
            for spot in spots
        ]
        if rezzed[0].local_id == rezzed[1].local_id:
            print("FAIL: both spots matched the same prim")
            return 1
        before = {obj.local_id: obj.position for obj in rezzed}
        rezzed_ids = [obj.full_id for obj in rezzed]
        apart = math.dist(rezzed[0].position[:2], rezzed[1].position[:2])
        print(f"    linking {[obj.local_id for obj in rezzed]}, {apart:.2f} m apart")
        if apart < SEPARATION_M / 2.0:
            failures.append("the two prims did not rez apart, so the test cannot tell anything")

        print("--- 2. link them ---")
        client.queue_outbound_packet(
            handle,
            session.build_object_link_packet([obj.local_id for obj in rezzed]),
        )
        await settle(client, 6.0)

        print("--- 3. what does the simulator say about the child? ---")
        session = client.current
        children = [
            obj for obj in session.world_view.objects.values() if obj.local_id in before
        ]
        for event in list(getattr(session, "events", ()))[-25:]:
            detail = getattr(event, "detail", "")
            kind = getattr(event, "kind", "")
            if any(word in f"{kind}{detail}".lower() for word in ("link", "object.add", "alert")):
                print(f"    event {kind}: {detail}")
        parented = [obj for obj in children if obj.parent_id]
        if not parented:
            print("    no prim reports a parent -- the link did not take")
            for obj in children:
                print(f"    local={obj.local_id} pos={obj.position} parent={obj.parent_id}")
            failures.append("ObjectLink produced no parented prim, so nothing was observed")
        for obj in parented:
            was = before.get(obj.local_id)
            now = obj.position
            magnitude = math.dist((0.0, 0.0, 0.0), now)
            relative = "PARENT-RELATIVE" if magnitude < 32.0 else "REGION-ABSOLUTE"
            print(
                f"    local={obj.local_id} parent={obj.parent_id}\n"
                f"      before link: {was}\n"
                f"      after  link: {now}\n"
                f"      magnitude {magnitude:.2f} m  ->  {relative}"
            )
            print(
                "\n    A child reporting a couple of metres is an offset from its root, "
                "and the viewer -- which draws every prim at the position the update "
                "reports -- puts it near the region corner instead."
                if magnitude < 32.0
                else "\n    A child still reporting a region coordinate means the viewer's "
                "flat treatment of every prim is already right."
            )
    finally:
        # Take both prims away rather than leaving another linkset behind.
        # Roots first: a linkset is worn whole, so taking the root away takes
        # its child with it, and a child cannot be worn on its own. A prim
        # already gone reports gone rather than failing.
        def parent_of(object_id) -> int:
            current = client.current
            obj = current.world_view.objects.get(object_id) if current is not None else None
            return obj.parent_id if obj is not None else 0

        for object_id in sorted(rezzed_ids, key=parent_of):
            if not await take_prim_away(client, client.current_handle or 0, object_id):
                print(f"    left behind -- {object_id}")
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    if failures:
        print("\nINCONCLUSIVE:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nOBSERVED: see the frame reported above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
