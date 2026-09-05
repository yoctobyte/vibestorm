"""Live check that the derived gait tracks what the simulator reports.

The walk is not played back from an animation asset -- see
``viewer3d/avatar_pose`` for why it cannot be -- it is derived from where the
avatar has been. So the thing worth checking against a live simulator is the
derivation, not the drawing: does the pose follow real ``ObjectUpdate``
positions, and does it stop when the avatar does?

Unit tests feed it synthetic straight lines at a fixed tick. This feeds it the
sim's own position stream, at whatever rate the sim chooses to send it, with
the jitter and the occasional physics correction that come with it. What the
tests cannot cover is exactly that: a real update cadence.

Three phases:

1. **Stand still.** The pose must stay at rest. Position updates jitter by
   centimetres even when nobody moves, and a gait that reacts to that leaves
   every idle avatar in the region twitching.
2. **Walk forward.** The gait must start, the stride must advance, and the
   phase must advance with distance rather than with the clock.
3. **Stop.** The pose must return to rest rather than freeze mid-stride.
"""

import asyncio
import math
import os
import platform
from pathlib import Path

from vibestorm.bus.commands import AddControlFlags, RemoveControlFlags
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.control_flags import AgentControlFlags
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.avatar_pose import STILL_SPEED_MPS, advance_motion, pose_for_motion

#: The viewer samples once a frame; 20 Hz is close enough and keeps the log
#: readable.
SAMPLE_INTERVAL_S = 0.05


def find_self(session, agent_id):
    for full_id, obj in session.world_view.objects.items():
        if full_id == agent_id:
            return obj
    return None


async def sample(client, agent_id, seconds: float, label: str):
    """Drive the motion state off live positions and report what it did.

    Returns ``(max_speed, phase_advance, distance, samples, last_pose)``.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    motion = None
    previous_clock = None
    max_speed = 0.0
    phase_advance = 0.0
    distance = 0.0
    samples = 0
    start = None
    last_pose: dict[str, float] = {}

    while loop.time() < deadline:
        await asyncio.sleep(SAMPLE_INTERVAL_S)
        session = client.current
        if session is None:
            continue
        obj = find_self(session, agent_id)
        if obj is None or obj.position is None:
            continue
        now = loop.time()
        dt = 0.0 if previous_clock is None else now - previous_clock
        previous_clock = now
        before = motion.gait_phase if motion is not None else 0.0
        motion = advance_motion(motion, tuple(obj.position), dt)
        if start is None:
            start = motion.position
        samples += 1
        max_speed = max(max_speed, motion.speed_mps)
        phase_advance += (motion.gait_phase - before) % (2.0 * math.pi)
        last_pose = pose_for_motion(motion)

    if motion is not None and start is not None:
        distance = math.dist(start[:2], motion.position[:2])
    walking = "walking" if last_pose.get("leg_l") else "at rest"
    print(
        f"  {label}: {samples} samples, moved {distance:.2f} m, "
        f"peak speed {max_speed:.2f} m/s, stride advanced "
        f"{phase_advance / (2.0 * math.pi):.2f} cycles, ends {walking}"
    )
    return max_speed, phase_advance, distance, samples, last_pose


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
            config=SessionConfig(duration_seconds=180.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            session = client.current
            if session is not None and session.movement_completed:
                break
        if client.current is None or not client.current.movement_completed:
            print("FAIL: never finished arriving")
            return 1

        print("--- 1. standing still ---")
        speed, phase, _distance, samples, pose = await sample(
            client, bootstrap.agent_id, 6.0, "still"
        )
        if samples < 20:
            failures.append("too few position samples to say anything")
        if speed >= STILL_SPEED_MPS:
            failures.append(f"standing still read as {speed:.2f} m/s of movement")
        if phase > 0.01:
            failures.append("the stride advanced while the avatar stood still")
        if pose.get("leg_l"):
            failures.append("a standing avatar was posed mid-stride")

        print("--- 2. walking forward ---")
        client.bus.dispatch(AddControlFlags(AgentControlFlags.AT_POS))
        try:
            speed, phase, distance, _samples, pose = await sample(
                client, bootstrap.agent_id, 8.0, "walking"
            )
        finally:
            client.bus.dispatch(RemoveControlFlags(AgentControlFlags.AT_POS))

        if distance < 2.0:
            print(
                f"    NOTE: only moved {distance:.2f} m -- an obstacle, not "
                "necessarily a fault"
            )
        if speed < STILL_SPEED_MPS:
            failures.append(f"walking never rose above the standing floor ({speed:.2f} m/s)")
        if phase <= 0.0 and distance > 1.0:
            failures.append("the avatar covered ground without the stride advancing")
        # The phase is tied to distance, not to time, so the two must agree.
        if distance > 1.0:
            from vibestorm.viewer3d.avatar_pose import STRIDE_LENGTH_M

            expected = distance / STRIDE_LENGTH_M
            actual = phase / (2.0 * math.pi)
            print(f"    {actual:.2f} cycles for {distance:.2f} m; {expected:.2f} expected")
            if abs(actual - expected) > 0.35 * max(1.0, expected):
                failures.append(
                    f"the stride does not track distance: {actual:.2f} cycles "
                    f"for {expected:.2f} metres' worth"
                )

        print("--- 3. stopped again ---")
        speed, _phase, _distance, _samples, pose = await sample(
            client, bootstrap.agent_id, 5.0, "stopped"
        )
        if pose.get("leg_l"):
            failures.append("the avatar stayed posed mid-stride after stopping")
    finally:
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
    print("\nPASS: the gait starts when the sim says the avatar moves, tracks the "
          "distance it covers, and stops when it does")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
