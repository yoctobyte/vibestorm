"""Live check of what actually turns and moves the avatar.

The viewer maps the cursor keys to AGENT_CONTROL_TURN_LEFT/TURN_RIGHT, but the
avatar never turned. This probe separates the two candidate mechanisms against
the running simulator:

  A. control flags alone -- hold TURN_LEFT, send identity BodyRotation
  B. BodyRotation alone  -- no flags, send a yawed BodyRotation
  C. BodyRotation + AT_POS -- does walking follow the body's facing?

Phase C walks from wherever the avatar stands, so a short leg means an
obstacle rather than a protocol fault; compare the legs against each other.

Each phase reports our own avatar's rotation and position as the simulator
reports them back, so the answer comes from the sim rather than from a reading
of our own send path.
"""

import asyncio
import math
import os
import platform
from pathlib import Path

from vibestorm.bus.commands import (
    AddControlFlags,
    ClearControlFlags,
    SetBodyRotation,
    SetHeadRotation,
)
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.control_flags import AgentControlFlags
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

PHASE_SECONDS = 8.0


def yaw_of(rotation) -> float:
    """Degrees about Z from a full (x, y, z, w) quaternion."""
    if rotation is None:
        return float("nan")
    x, y, z, w = rotation
    return math.degrees(math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z)))


def packed_yaw(degrees: float) -> tuple[float, float, float]:
    """The wire form of a yaw: a quaternion's x, y, z with w implied."""
    half = math.radians(degrees) / 2.0
    return (0.0, 0.0, math.sin(half))


def find_self(session, agent_id):
    """Our own avatar, by full id, with whatever rotation the sim last sent."""
    for full_id, obj in session.world_view.objects.items():
        if full_id == agent_id:
            return obj
    return None


async def sample(client, agent_id, label: str) -> None:
    session = client.current
    obj = find_self(session, agent_id) if session else None
    if obj is None:
        print(f"  {label:<22} (self not in view yet)")
        return
    pos = tuple(round(v, 2) for v in obj.position) if obj.position else None
    print(f"  {label:<22} yaw={yaw_of(obj.rotation):8.2f}deg pos={pos}")


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
    agent_id = bootstrap.agent_id
    print(f"login ok agent={agent_id}")

    client = WorldClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(duration_seconds=120.0, agent_update_interval_seconds=0.1),
            world_client=client,
            stop_event=stop,
        )
    )

    try:
        for _ in range(60):
            await asyncio.sleep(0.5)
            if client.current is not None and client.current.movement_completed:
                break
        else:
            print("FAIL: movement never completed")
            return 1
        await asyncio.sleep(2.0)
        await sample(client, agent_id, "baseline")

        print(f"--- A: TURN_LEFT flag, client-side turn integration ({PHASE_SECONDS}s) ---")
        print("     expect the yaw to climb steadily at the configured turn rate")
        client.bus.dispatch(SetBodyRotation((0.0, 0.0, 0.0)))
        client.bus.dispatch(SetHeadRotation((0.0, 0.0, 0.0)))
        client.bus.dispatch(AddControlFlags(int(AgentControlFlags.TURN_LEFT)))
        for i in range(int(PHASE_SECONDS)):
            await asyncio.sleep(1.0)
            await sample(client, agent_id, f"A t+{i + 1}s")
        client.bus.dispatch(ClearControlFlags())

        print(f"--- B: BodyRotation yaw=90, no flags ({PHASE_SECONDS}s) ---")
        client.bus.dispatch(SetBodyRotation(packed_yaw(90.0)))
        client.bus.dispatch(SetHeadRotation(packed_yaw(90.0)))
        for i in range(int(PHASE_SECONDS)):
            await asyncio.sleep(1.0)
            await sample(client, agent_id, f"B t+{i + 1}s")

        print("--- C: walk each compass facing, 6s apiece ---")
        print("     expect roughly 12 m per leg; walking follows the body's facing")
        for name, degrees in (
            ("east (+X)", 0.0),
            ("north (+Y)", 90.0),
            ("west (-X)", 180.0),
            ("south (-Y)", -90.0),
        ):
            client.bus.dispatch(ClearControlFlags())
            client.bus.dispatch(SetBodyRotation(packed_yaw(degrees)))
            await asyncio.sleep(1.0)
            before = find_self(client.current, agent_id)
            client.bus.dispatch(AddControlFlags(int(AgentControlFlags.AT_POS)))
            await asyncio.sleep(6.0)
            client.bus.dispatch(ClearControlFlags())
            await asyncio.sleep(1.0)
            after = find_self(client.current, agent_id)
            if before is None or after is None:
                print(f"  {name:<12} (self not in view)")
                continue
            moved = math.dist(before.position[:2], after.position[:2])
            print(f"  {name:<12} moved {moved:6.2f} m")
        client.bus.dispatch(ClearControlFlags())
        await sample(client, agent_id, "final")
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
