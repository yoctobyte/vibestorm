"""Live check: does the viewer's own account of the world match the world?

Written after a screenshot caught the position readout being wrong by twenty
metres while the picture beside it was right. The bug lived because every test
that touched a readout set `scene.avatar_position` directly, so the one line
that computes it from a message had no test at all -- and a viewer that draws
the world correctly and *describes* it wrongly is still a viewer nobody can
trust, because the description is what a person reads when the picture looks
odd.

So each check here computes its own answer from the `WorldView` -- from the
messages, by a different route than the scene took -- and compares. Where the
simulator itself states a number (`SimStats`), that is used as a third opinion.

It reads and does not rez, so it leaves nothing behind and can be run against
any region the agent can reach.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_hud_readouts.py
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# No window: this must be runnable while somebody is using the desktop.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from probe_support import wait_until_quiet  # noqa: E402

from vibestorm.login.client import LoginClient  # noqa: E402
from vibestorm.login.models import LoginCredentials, LoginRequest  # noqa: E402
from vibestorm.udp.dispatch import MessageDispatcher  # noqa: E402
from vibestorm.udp.session import SessionConfig, run_live_session  # noqa: E402
from vibestorm.udp.world_client import WorldClient  # noqa: E402
from vibestorm.viewer3d.scene import Scene  # noqa: E402

#: `SimStats` id 11, the region's own count of the prims it holds.
STAT_TOTAL_PRIMS = 11

#: How far the scene's avatar may be from the world view's before it is a
#: disagreement rather than a rounding difference. They should be the same
#: tuple; this is here so a float that went through a composition still passes.
POSITION_TOLERANCE_M = 0.01


class Checks:
    """A tally that prints as it goes, because a live run is watched."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checked = 0

    def that(self, claim: str, ours: object, theirs: object, *, note: str = "") -> None:
        self.checked += 1
        tail = f"  ({note})" if note else ""
        if ours == theirs:
            print(f"    ok    {claim}: {ours}{tail}")
            return
        print(f"    FAIL  {claim}: viewer says {ours!r}, world says {theirs!r}{tail}")
        self.failures.append(claim)

    def note(self, text: str) -> None:
        print(f"    --    {text}")


def _self_object(world_view, agent_id):
    """Our own avatar as the world view holds it, or None."""
    return world_view.objects.get(agent_id)


def _drawn_prims(scene) -> int:
    return len(scene.object_entities)


def _total_prims(stats: object) -> float | None:
    """The region's own prim count out of a `SimStats`, or None if absent.

    A function, and not the inline `next(...)` it used to be, because inline
    it sat behind a live simulator and could not be reached by a test. It
    read `s.stat_value` -- the field name on the raw `SimStatEntry` off the
    wire -- from a `NamedSimStat`, which calls it `value` because
    `name_sim_stats` renames it as it attaches the name. Every local run
    ended in an AttributeError here and nobody saw it, because the four
    checks above print `ok` first.
    """
    for stat in getattr(stats, "stats", ()):
        if stat.stat_id == STAT_TOTAL_PRIMS:
            return float(stat.value)
    return None


def _placeable_prims(world_view) -> int:
    """Prims the client received and could place, counted without the scene.

    Deliberately not the scene's own arithmetic: roots are counted directly,
    and a child is counted only if every parent above it arrived, which is the
    same rule `resolve_world_transforms` applies by a different route.

    The two maps are the point, and the first version of this had one. An
    avatar is not a prim and must not be *counted* -- it has its own row -- but
    it is very much a thing that can be a *parent*, because an attachment is a
    prim whose `parent_id` is the avatar wearing it. The scene's own transform
    pass has no pcode filter at all: every object with a position goes in, so
    attachments resolve and get drawn. Excluding avatars from the parent
    lookup as well as from the count would have made every attachment in the
    region read as unplaceable, and this tool would have reported the viewer
    drawing prims that "could not be placed" -- a failure in the checker
    printed as a failure in the thing being checked, which is the one bug a
    verifier must not have.
    """
    parents: dict[int, object] = {}
    countable: set[int] = set()
    for obj in world_view.objects.values():
        if obj.position is None:
            continue
        parents[obj.local_id] = obj
        if obj.pcode != 47:
            countable.add(obj.local_id)

    def placed(local_id: int, depth: int = 0) -> bool:
        if depth > 64:
            return False
        obj = parents.get(local_id)
        if obj is None:
            return False
        parent = getattr(obj, "parent_id", 0)
        return True if not parent else placed(parent, depth + 1)

    return sum(1 for local_id in countable if placed(local_id))


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
            MessageDispatcher.from_repo_root(Path(__file__).resolve().parents[1]),
            config=SessionConfig(duration_seconds=300.0),
            world_client=client,
            stop_event=stop,
        )
    )

    checks = Checks()
    try:
        for _ in range(60):
            if client.current is not None and client.current.movement_completed:
                break
            await asyncio.sleep(0.5)
        session = client.current
        if session is None or not session.movement_completed:
            print("FAIL: never finished arriving")
            return 1
        settled = await wait_until_quiet(client, quiet_for=5.0, limit=90.0)
        print(f"region settled at {settled} objects")

        view = client.current.world_view
        scene = Scene()
        scene.refresh_from_world_view(view)

        print("--- 1. where we are ---")
        me = _self_object(view, bootstrap.agent_id)
        if me is None or me.position is None:
            checks.note("no ObjectUpdate for our own avatar yet; skipping position")
        elif getattr(me, "parent_id", 0):
            checks.note(f"we are sitting on {me.parent_id}; the coarse blip is the source")
        else:
            here = scene.avatar_position
            same = here is not None and all(
                abs(a - b) <= POSITION_TOLERANCE_M for a, b in zip(here, me.position)
            )
            checks.that(
                "avatar position",
                tuple(round(c, 3) for c in here) if here else None,
                tuple(round(c, 3) for c in me.position) if same else "a different place",
                note="the readout that was twenty metres out",
            )

        print("--- 2. the region and its sea ---")
        region = view.region
        checks.that("region name", scene.region_name, getattr(region, "name", None))
        water = getattr(region, "water_height", None)
        if water is None:
            checks.note("the handshake carried no water height; the client's default stands")
        else:
            checks.that("water height", scene.water_height, float(water))
            if scene.avatar_position is not None:
                z = scene.avatar_position[2]
                checks.that(
                    "under water",
                    z < scene.water_height,
                    z < float(water),
                    note=f"avatar z {z:.1f} against {float(water):.1f}",
                )

        print("--- 3. what got drawn ---")
        placeable = _placeable_prims(view)
        checks.that(
            "prims drawn",
            _drawn_prims(scene),
            placeable,
            note="every prim received whose parents all arrived",
        )
        stats = view.latest_sim_stats
        if stats is None:
            checks.note("no SimStats yet; the simulator's own prim count is unavailable")
        else:
            total = _total_prims(stats)
            if total is None:
                checks.note("SimStats carried no total prim count")
            else:
                # Not a failure on its own: a simulator sends what is near
                # enough to matter, and a big region sends a fraction of
                # itself. It is a failure only if we drew *more* than exist.
                received = sum(1 for o in view.objects.values() if o.pcode != 47)
                checks.note(
                    f"region holds {int(total)} prims, we received {received}, "
                    f"drew {_drawn_prims(scene)}"
                )
                if _drawn_prims(scene) > int(total):
                    checks.failures.append("drew more prims than the region holds")
                    print("    FAIL  drew more prims than the region holds")

        print("--- 4. avatars ---")
        avatars_in_view = sum(1 for o in view.objects.values() if o.pcode == 47)
        checks.that(
            "avatars drawn",
            len(scene.avatar_entities),
            avatars_in_view,
            note="pcode 47 in the world view",
        )
        if view.coarse_agents:
            checks.note(
                f"{len(view.coarse_agents)} coarse blips, "
                f"{sum(1 for c in view.coarse_agents if c.is_you)} of them us"
            )
    finally:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=20.0)
        except TimeoutError:
            print("note: the session did not stop within 20 s")

    print()
    if checks.failures:
        print(f"FAIL: {len(checks.failures)} of {checks.checked} checks disagree")
        for name in checks.failures:
            print(f"  - {name}")
        return 1
    print(f"PASS: {checks.checked} checks agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
