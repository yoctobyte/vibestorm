"""What a frame of ``Scene.refresh_from_world_view`` costs, by region size.

The refresh runs once per frame and walks every object in view, so its cost is
the floor under the viewer's frame rate before a single triangle is drawn. The
local test region holds a handful of prims and hides that entirely; a Second
Life mainland region holds thousands, mostly in linksets, and that is where the
client has to work.

So this builds regions of a given size and measures the refresh against them.
It is a benchmark, not a test: it prints numbers and asserts nothing about how
fast the machine it runs on is.

The ``% moving`` column is what makes it honest. Objects that did not change
are handed back the entity they already had, so a benchmark that re-measures a
frozen world flatters the cache. A busy region has a few percent of its objects
in motion at any moment; each of those gets a fresh ``WorldObject`` from the
updater, exactly as a real ``ObjectUpdate`` would.

    .venv/bin/python tools/bench_scene_refresh.py
"""

from __future__ import annotations

import argparse
import copy
import os
import random
import sys
import time
from types import SimpleNamespace

from vibestorm.viewer3d.linkset import IDENTITY
from vibestorm.viewer3d.scene import Scene

#: Region shapes worth knowing the cost of: a sparse region of unlinked prims,
#: and two sizes of the linkset-heavy world a mainland region actually is.
SHAPES: dict[str, tuple[int, int]] = {
    "1000 single prims": (1000, 0),
    "1000 linksets of 5": (1000, 4),
    "3000 linksets of 5": (3000, 4),
}

#: What fraction of the region is in motion. 0 is a still world, 5 is busy.
MOVING_PERCENTS = (0, 1, 5)


def _prim(local_id: int, parent_id: int, position: tuple[float, float, float]):
    """Enough of a ``WorldObject`` for the refresh to read.

    A stand-in rather than the real dataclass so the benchmark stays readable;
    the refresh reaches for these fields and no others.
    """
    return SimpleNamespace(
        local_id=local_id,
        pcode=9,
        parent_id=parent_id,
        position=position,
        scale=(0.5, 0.5, 0.5),
        rotation=IDENTITY,
        properties_family=None,
        name_values=None,
        shape=SimpleNamespace(path_curve=16, profile_curve=1),
        extra_params_entries=(),
        default_texture_id=None,
        texture_entry=None,
        hover_text=None,
        hover_text_color=None,
    )


def build_region(roots: int, children_per_root: int, *, seed: int = 7):
    rng = random.Random(seed)
    objects: dict[int, SimpleNamespace] = {}
    local_id = 1
    root_ids = []
    for _ in range(roots):
        objects[local_id] = _prim(
            local_id, 0, (rng.uniform(0.0, 256.0), rng.uniform(0.0, 256.0), 25.0)
        )
        root_ids.append(local_id)
        local_id += 1
    for root in root_ids:
        for _ in range(children_per_root):
            # A child reports its offset from its root, not a region position.
            objects[local_id] = _prim(local_id, root, (rng.uniform(-4.0, 4.0), 0.0, 0.0))
            local_id += 1
    return SimpleNamespace(
        objects=objects,
        terse_objects={},
        region=None,
        latest_time=None,
        latest_sim_stats=None,
    )


def move(world, local_ids, rng: random.Random) -> None:
    """Nudge some objects the way an update does: a *new* instance each time.

    ``WorldView`` never edits an object in place, and the refresh relies on
    that, so a benchmark that mutated one would measure something the client
    never sees.
    """
    for local_id in local_ids:
        was = world.objects[local_id]
        now = copy.copy(was)
        now.position = (was.position[0] + rng.uniform(-0.1, 0.1), was.position[1], was.position[2])
        world.objects[local_id] = now


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=8, help="frames to time per row")
    parser.add_argument(
        "--rounds",
        type=int,
        default=4,
        help="times to walk the whole table, keeping each row's best.",
    )
    args = parser.parse_args(argv)

    # The load, before and after, because this is a developer's own desktop and
    # a number measured on a busy one says as much about the compiler running
    # beside it as about the code. On 2026-09-07 the same row came back at
    # 2.8 ms and at 6.2 twenty minutes apart, with nothing between the two but
    # two of the owner's builds -- and CPU time rather than wall clock narrows
    # that without closing it, because a core that is stalled on somebody
    # else's cache misses is still a core spending our time.
    load_before = os.getloadavg()[0]
    best: dict[tuple[str, int], float] = {}
    drew: dict[tuple[str, int], tuple[int, int]] = {}
    # Round by round rather than row by row: a round takes seconds, so samples
    # for one row land at several different moments of the machine's day
    # instead of all inside one busy minute.
    for _ in range(max(args.rounds, 1)):
        for label, (roots, children) in SHAPES.items():
            world = build_region(roots, children)
            every_id = list(world.objects)
            rng = random.Random(11)
            scene = Scene()
            scene.refresh_from_world_view(world)  # warm the caches
            for percent in MOVING_PERCENTS:
                moving = len(every_id) * percent // 100
                for _frame in range(args.frames):
                    move(world, rng.sample(every_id, moving), rng)
                    start = time.process_time()
                    scene.refresh_from_world_view(world)
                    taken = time.process_time() - start
                    key = (label, percent)
                    if key not in best or taken < best[key]:
                        best[key] = taken
                drawn = len(scene.object_entities) + len(scene.avatar_entities)
                drew[(label, percent)] = (drawn, len(world.objects))

    for label, (roots, children) in SHAPES.items():
        for percent in MOVING_PERCENTS:
            key = (label, percent)
            drawn, total = drew[key]
            if drawn != total:
                print(f"  !! drew {drawn} of {total} -- the region is not intact")
            ms = best[key] * 1000.0
            print(
                f"{label:22s} {total:6d} objects  {percent:2d}% moving  "
                f"{ms:7.2f} ms/frame  ({1000.0 / ms:6.0f} fps ceiling)"
            )
        print()
    print(f"machine load {load_before:.1f} at the start, {os.getloadavg()[0]:.1f} at the end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
