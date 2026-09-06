"""Live check: what does the simulator actually say about the sun?

Written to answer one question -- how to index a region's day cycle -- and it
answered three, none of them the expected one.

1. **`SunDirection` is `(0, 0, 0)`.** Every message, from this OpenSim. Not a
   missing field a client can test for with ``None``: a well-formed vector of
   length nothing, which a normalise steps over into whatever fallback sits
   behind it. The sun in this viewer had never moved in any session.
2. **`UsecSinceStart` is a Unix timestamp in microseconds**, not an uptime.
   The run below prints it against the machine's own clock.
3. **`SunPhase` does not advance at a constant rate.** Two runs a little over
   twenty minutes apart, each averaging over about a hundred seconds, measured
   2.1816e-4 and 4.3634e-4 radians a second -- a factor of exactly two. Over
   the first window that works out to one turn per 28800 s and over the second
   to one per 14400 s, on a region reporting `SecPerDay = 14400` in the same
   message both times. So a client cannot read the field as a clock: whatever
   rate a single sample window measures is the rate for that part of the day
   only.

Nothing in the client depends on any of it. The region's own day cycle, from
the `ExtEnvironment` capability, carries a `sun_rotation` per sky keyframe, and
that is what places the sun -- see `viewer3d/atmosphere.py`.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/probe_sun.py
"""

import asyncio
import math
import os
import platform
import time
from pathlib import Path

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

#: How many distinct time messages to collect before working out the rate.
#: They arrive every few seconds, so this is a minute and a half or so.
SAMPLES = 40



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

    samples: list[tuple[int, int, float, tuple[float, float, float] | None]] = []
    try:
        for _ in range(280):
            await asyncio.sleep(1.0)
            session = client.current
            if session is None:
                continue
            snapshot = session.world_view.latest_time
            if snapshot is None:
                continue
            row = (
                snapshot.usec_since_start,
                snapshot.sec_per_day,
                snapshot.sun_phase,
                snapshot.sun_direction,
            )
            if samples and samples[-1][2] == row[2]:
                continue
            samples.append(row)
            direction = row[3] or (0.0, 0.0, 0.0)
            magnitude = math.sqrt(sum(c * c for c in direction))
            print(
                f"usec={row[0]} sec_per_day={row[1]} phase={row[2]:.6f} "
                f"dir=({direction[0]:+.4f},{direction[1]:+.4f},{direction[2]:+.4f}) "
                f"|dir|={magnitude:.4f}"
            )
            if len(samples) >= SAMPLES:
                break
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    if len(samples) < 2:
        print("FAIL: not enough time messages to measure anything")
        return 1

    print()
    zero_directions = sum(
        1 for row in samples if not any(row[3] or (0.0, 0.0, 0.0))
    )
    print(f"--- SunDirection: {zero_directions} of {len(samples)} were all zeros ---")

    first, last = samples[0], samples[-1]
    seconds = (last[0] - first[0]) / 1_000_000.0
    # The *last* sample against the clock now: the first is a whole sampling
    # window old, and comparing that one makes a perfectly synchronised clock
    # look a hundred seconds out.
    print(f"--- UsecSinceStart: {last[0] / 1_000_000.0:.0f} vs this machine's "
          f"{time.time():.0f} ---")
    print(f"    difference {abs(last[0] / 1_000_000.0 - time.time()):.1f} s -- "
          "a Unix clock, not an uptime")

    turned = last[2] - first[2]
    if turned <= 0.0 or seconds <= 0.0:
        print("--- SunPhase did not advance; nothing to measure ---")
        return 0
    rate = turned / seconds
    period = 2.0 * math.pi / rate
    print(f"--- SunPhase: {rate:.6e} rad/s over {seconds:.1f} s, starting at "
          f"{first[2]:.4f} rad ---")
    print(f"    that is one turn per {period:.0f} s; SecPerDay says {first[1]} s, "
          f"a ratio of {period / first[1]:.2f}")
    print("    Run this again an hour later. The rate is not the same all day, "
          "so the ratio is a reading and not a constant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
