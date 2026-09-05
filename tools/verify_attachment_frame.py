"""Live check: is an attached prim a child of the avatar?

``viewer3d/linkset.py`` says so -- "Attachments are the same shape of thing:
an attached prim is a child of the avatar" -- and until now nothing had
watched one. Two neighbouring facts *were* observed: a linkset's child reports
its position in the root's frame (`verify_child_prim_frame.py`), and sitting
reparents an avatar onto its seat (`verify_seated_avatar.py`). Attachments
being the third of the same shape is plausible, which is exactly the kind of
claim that is worth going and looking at rather than believing.

If it holds, the linkset composition already added to the viewer draws
attachments correctly, and an attached prim drawn near the region corner is a
bug that cannot happen. If it does not, something else is going on and the
docstring is wrong.

Four questions:

1. **Does attaching reparent the prim?** Its ``ObjectUpdate`` should start
   reporting a ``parent_id``, and that id should be the avatar's.
2. **Is its position then relative to the avatar?** A prim rezzed near
   (128, 128, 26) reporting under a metre says yes.
3. **Does the viewer put it back?** ``Scene`` composes children through their
   parents, so the attached prim should be drawn on the avatar.
4. **Does it come off again?** ``ObjectDetach`` is the way back, and the way
   this does not leave a prim worn: the local OpenSim build has no handler for
   ``ObjectDelete``, so anything it cannot detach it cannot get rid of.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_attachment_frame.py [--point N]

``--point`` is the simulator's own attachment-point numbering, which nothing
in this project claims to know: 0 asks for the object's default. Pass a number
and watch where the prim lands if you want to find out what it means.
"""

import argparse
import asyncio
import math
import os
import platform
import sys
from pathlib import Path

from probe_support import wait_until_quiet

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.scene import Scene

#: Anything below this is not a region coordinate. A region is 256 m across
#: and an attachment offset is well under a metre.
REGION_SCALE_M = 32.0

#: How far the drawn prim may be from the avatar before something is wrong.
#: Generous: an attachment point can be an arm's length from the centre.
NEAR_THE_AVATAR_M = 3.0


async def wait_for(predicate, *, seconds: float, step: float = 1.0) -> bool:
    waited = 0.0
    while waited < seconds:
        if predicate():
            return True
        await asyncio.sleep(step)
        waited += step
    return predicate()


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--point",
        type=int,
        default=0,
        help="attachment point, in the simulator's numbering. 0 is the object's default.",
    )
    args = parser.parse_args(argv)

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
    worn_local_id: int | None = None
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
        print(f"    avatar local={me.local_id} at {tuple(round(c, 2) for c in me.position)}")

        print("--- 1. rez something to wear ---")
        settled = await wait_until_quiet(client)
        print(f"    region settled at {settled} objects")
        session = client.current
        known = {obj.local_id for obj in session.world_view.objects.values()}
        spot = (me.position[0] + 1.5, me.position[1], me.position[2] + 1.0)
        client.queue_outbound_packet(handle, session.build_object_add_packet(position=spot))
        await asyncio.sleep(6.0)

        session = client.current
        fresh = [obj for obj in session.world_view.objects.values() if obj.local_id not in known]
        if not fresh:
            print("FAIL: nothing rezzed")
            return 1
        prim = min(fresh, key=lambda obj: math.dist(obj.position, spot))
        print(f"    rezzed {prim.full_id} local={prim.local_id} at "
              f"{tuple(round(c, 2) for c in prim.position)}")

        print("--- 2. wear it ---")
        # Selected first, because that is what a viewer does before it acts on
        # an object, and an attach is exactly the kind of thing a permission
        # check would want a selection for.
        client.queue_outbound_packet(
            handle, session.build_object_select_packet([prim.local_id])
        )
        await asyncio.sleep(2.0)
        client.queue_outbound_packet(
            handle,
            session.build_object_attach_packet([prim.local_id], attachment_point=args.point),
        )

        def worn() -> bool:
            current = client.current
            if current is None:
                return False
            now = current.world_view.objects.get(prim.full_id)
            return bool(now is not None and now.parent_id)

        if not await wait_for(worn, seconds=20.0):
            failures.append("attaching did not reparent the prim within 20 s")
            print("    the prim reports no parent after ObjectAttach")

        session = client.current
        attached = session.world_view.objects.get(prim.full_id)
        if attached is None:
            print("FAIL: the prim vanished from the region entirely")
            return 1
        worn_local_id = attached.local_id
        print(f"    prim parent_id={attached.parent_id} (avatar local={me.local_id})")
        print(f"    prim position now {tuple(round(c, 3) for c in attached.position)}")

        if attached.parent_id and attached.parent_id != me.local_id:
            failures.append(
                f"attached to {attached.parent_id}, which is not the avatar {me.local_id}"
            )

        if attached.parent_id:
            magnitude = math.dist((0.0, 0.0, 0.0), attached.position)
            frame = "PARENT-RELATIVE" if magnitude < REGION_SCALE_M else "REGION-ABSOLUTE"
            print(f"    magnitude {magnitude:.2f} m  ->  {frame}")
            if magnitude >= REGION_SCALE_M:
                failures.append(
                    "the attached prim still reports a region coordinate, so the "
                    "linkset composition does not apply to attachments after all"
                )

            print("--- 3. where does the viewer draw it? ---")
            scene = Scene()
            scene.refresh_from_world_view(client.world_view())
            drawn = scene.object_entities.get(attached.local_id)
            avatar_drawn = scene.avatar_entities.get(me.local_id)
            if drawn is None:
                failures.append("the attached prim is not drawn at all")
                print("    the scene has no entity for it")
            elif avatar_drawn is None:
                failures.append("the avatar wearing it is not drawn")
            else:
                off_by = math.dist(drawn.position, avatar_drawn.position)
                print(f"    scene draws it at {tuple(round(c, 2) for c in drawn.position)}")
                print(f"    the avatar is at {tuple(round(c, 2) for c in avatar_drawn.position)}")
                print(f"    that is {off_by:.2f} m apart")
                if off_by > NEAR_THE_AVATAR_M:
                    failures.append(
                        f"the attached prim is drawn {off_by:.2f} m from the avatar wearing it"
                    )

        print("--- 4. take it off again ---")
        client.queue_outbound_packet(
            handle, session.build_object_detach_packet([attached.local_id])
        )

        def bare() -> bool:
            current = client.current
            if current is None:
                return False
            now = current.world_view.objects.get(prim.full_id)
            return now is None or not now.parent_id

        if await wait_for(bare, seconds=20.0):
            still = client.current.world_view.objects.get(prim.full_id)
            if still is None:
                # ObjectDetach takes an attachment *into inventory*, so the
                # prim leaves the region rather than being dropped where the
                # avatar stands. Rez, attach, detach is therefore a way to take
                # a prim out of a region that has no ObjectDelete handler --
                # see tools/delete_prims.py.
                print("    detached, and gone from the region (into inventory)")
            else:
                print(f"    detached, still in the region at "
                      f"{tuple(round(c, 2) for c in still.position)}")
            worn_local_id = None
        else:
            failures.append("ObjectDetach did not take the prim off")
    finally:
        if worn_local_id is not None:
            print(f"    LEFT WORN: prim {worn_local_id} is still attached to the test avatar")
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
    print("\nPASS: attaching reparents the prim onto the avatar, its position is "
          "reported in the avatar's frame, and the viewer draws it on the avatar")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
