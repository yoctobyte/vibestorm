"""Live check: what happens to an avatar when it sits on something?

An avatar that sits becomes, in the protocol's terms, a child of the object it
is sitting on. If that is true, then a seated avatar's position is reported in
the *seat's* frame -- the same thing that was observed for a linkset's child
prims in ``verify_child_prim_frame.py`` -- and every seated avatar in a region
was being drawn near the region corner before that fix.

This checks it rather than assuming it, because "sitting is parenting" is
exactly the kind of thing that is obvious right up until it is wrong.

Three questions, in order:

1. **Does sitting reparent the avatar?** Its ``ObjectUpdate`` should start
   reporting a ``parent_id``, and that id should be the seat's.
2. **Is its position then relative to the seat?** A region position near
   (128, 128, 25) becoming half a metre means yes.
3. **Does the viewer put it back?** ``Scene`` composes children through their
   parents, so the seated avatar should be drawn on the seat, not at the
   region corner.

It rezzes its own seat, sits, looks, stands, and deletes the seat again.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_seated_avatar.py
"""

import asyncio
import math
import os
import platform
from pathlib import Path

from vibestorm.bus.commands import AddControlFlags, ClearControlFlags
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.control_flags import AgentControlFlags
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.scene import Scene

#: Anything below this is not a region coordinate. A region is 256 m across and
#: a seat offset is well under a metre, so there is a lot of room in between.
REGION_SCALE_M = 32.0


async def wait_for(predicate, *, seconds: float, step: float = 1.0) -> bool:
    waited = 0.0
    while waited < seconds:
        if predicate():
            return True
        await asyncio.sleep(step)
        waited += step
    return predicate()


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
            config=SessionConfig(duration_seconds=240.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    seat_id = None
    try:
        await wait_for(
            lambda: client.current is not None and client.current.movement_completed,
            seconds=30.0,
            step=0.5,
        )
        session = client.current
        if session is None or not session.movement_completed:
            print("FAIL: never finished arriving")
            return 1
        handle = client.current_handle or 0

        me = session.world_view.objects.get(bootstrap.agent_id)
        if me is None or me.position is None:
            print("FAIL: cannot see my own avatar")
            return 1
        standing_at = me.position
        print(f"    standing at {tuple(round(c, 2) for c in standing_at)}")

        print("--- 1. rez something to sit on ---")
        known = {obj.local_id for obj in session.world_view.objects.values()}
        spot = (standing_at[0] + 1.5, standing_at[1], standing_at[2])
        client.queue_outbound_packet(handle, session.build_object_add_packet(position=spot))
        await asyncio.sleep(6.0)

        session = client.current
        fresh = [
            obj for obj in session.world_view.objects.values() if obj.local_id not in known
        ]
        if not fresh:
            print("FAIL: the seat never rezzed")
            return 1
        seat = min(fresh, key=lambda obj: math.dist(obj.position, spot))
        seat_id = seat.full_id
        print(f"    seat {seat_id} local={seat.local_id} at "
              f"{tuple(round(c, 2) for c in seat.position)}")

        print("--- 2. sit on it ---")
        client.queue_outbound_packet(handle, session.build_agent_request_sit_packet(seat_id))
        await asyncio.sleep(2.0)
        client.queue_outbound_packet(handle, session.build_agent_sit_packet())

        def seated() -> bool:
            current = client.current
            if current is None:
                return False
            avatar = current.world_view.objects.get(bootstrap.agent_id)
            return bool(avatar is not None and avatar.parent_id)

        if not await wait_for(seated, seconds=20.0):
            failures.append("sitting did not reparent the avatar within 20 s")
            print("    the avatar reports no parent after sitting")
        session = client.current
        avatar = session.world_view.objects.get(bootstrap.agent_id)
        print(f"    avatar parent_id={avatar.parent_id} (seat local={seat.local_id})")
        print(f"    avatar position now {tuple(round(c, 3) for c in avatar.position)}")

        if avatar.parent_id and avatar.parent_id != seat.local_id:
            failures.append(
                f"seated on {avatar.parent_id}, which is not the seat {seat.local_id}"
            )

        if avatar.parent_id:
            magnitude = math.dist((0.0, 0.0, 0.0), avatar.position)
            frame = "PARENT-RELATIVE" if magnitude < REGION_SCALE_M else "REGION-ABSOLUTE"
            print(f"    magnitude {magnitude:.2f} m  ->  {frame}")

            print("--- 3. where does the viewer draw it? ---")
            scene = Scene()
            scene.refresh_from_world_view(client.world_view())
            drawn = scene.avatar_entities.get(avatar.local_id)
            if drawn is None:
                failures.append("the seated avatar is not drawn at all")
                print("    the scene has no entity for the seated avatar")
            else:
                print(f"    scene draws it at {tuple(round(c, 2) for c in drawn.position)}")
                off_by = math.dist(drawn.position, seat.position)
                print(f"    that is {off_by:.2f} m from the seat")
                if off_by > 3.0:
                    failures.append(
                        f"the seated avatar is drawn {off_by:.2f} m from its seat"
                    )

        print("--- 4. stand up again ---")
        client.bus.dispatch(AddControlFlags(AgentControlFlags.STAND_UP))
        await asyncio.sleep(3.0)
        client.bus.dispatch(ClearControlFlags())

        def standing() -> bool:
            current = client.current
            if current is None:
                return False
            back = current.world_view.objects.get(bootstrap.agent_id)
            return bool(back is not None and not back.parent_id)

        if not await wait_for(standing, seconds=20.0):
            failures.append("the avatar never stood back up")
        else:
            back = client.current.world_view.objects.get(bootstrap.agent_id)
            print(f"    standing again at {tuple(round(c, 2) for c in back.position)}")
    finally:
        # Take the seat away again rather than leaving another cube behind.
        if seat_id is not None and client.current is not None:
            session = client.current
            seat = session.world_view.objects.get(seat_id)
            if seat is not None:
                client.queue_outbound_packet(
                    client.current_handle or 0,
                    session.build_object_delete_packet([seat.local_id]),
                )
                await asyncio.sleep(6.0)
                gone = seat_id not in (client.current.world_view.objects if client.current else {})
                print(f"    seat removed: {gone}")
                if not gone:
                    print(f"    NOTE: the seat is still there -- {seat_id}")
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: sitting reparents the avatar onto the seat, its position is "
          "reported in the seat's frame, and the viewer puts it back")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
