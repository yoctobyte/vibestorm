"""Why a viewer that leaks nothing still grows, and what stops it.

Soak run 3 ended at RSS 1.04 GB from 481 MB, with `obj.list` at 41,976 from
5,541 and `TextBoxLayout` at 3,333 from 132, while the packet and frame
counters ran dead straight the whole two hours. Nothing held a reference to any
of it. pygame_gui builds a small graph of objects for every line of text it
lays out, the parts of that graph refer to each other, and so only the *cyclic*
collector can free them -- which makes the question "why does the collection
stop arriving", not "who is holding this".

CPython runs its oldest generation when ``long_lived_pending`` passes
``long_lived_total / 4``. A viewer's static heap is most of that total and is
never garbage; all it does is push the threshold up, until a quarter of it is
more text objects than an hour of HUD updates produces.

This drives a real HUD at a fixed frame budget with no window and samples the
heap by type as it goes, in three modes:

    none      what the viewer used to do
    freeze    ``gc.freeze()`` after startup, which is what it does now
    collect   an explicit ``gc.collect()`` every N frames, the deferred option

**Do not add a ``gc.collect()`` to the sampling loop.** The first version of
this harness collected before each census and reported a clean heap for as
long as it was asked to: a census that collects first cannot see a collection
failing to happen. That is the bug this file exists to make visible.

    .venv/bin/python tools/gc_pressure.py --mode none --frames 20000
    .venv/bin/python tools/gc_pressure.py --mode freeze --frames 20000
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# No window: this runs on a shared desktop and under Xvfb-less CI alike, and
# the leak is in the text layout rather than in anything a GPU touches.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from vibestorm.viewer3d.health import TypeCensus  # noqa: E402
from vibestorm.viewer3d.hud import HUD  # noqa: E402
from vibestorm.viewer3d.scene import Scene  # noqa: E402

#: The types the leak showed up in, in the order they were noticed.
WATCH = (
    ("list", "list"),
    ("collections.deque", "deque"),
    ("pygame_gui.core.text.text_box_layout.TextBoxLayout", "TextBox"),
)

WINDOW = (1280, 800)


def build_hud() -> tuple[HUD, Scene]:
    """A HUD with its diagnostics panel open, which is the worst case.

    The panel relays a dozen lines of text a frame, and every one of those is
    a fresh layout. A soak runs with `--diagnostics` for exactly that reason.
    """
    pygame.init()
    pygame.display.set_mode(WINDOW)
    hud = HUD(WINDOW, on_chat_submit=lambda _text: None)
    scene = Scene()
    scene.region_name = "Vibestorm Test"
    scene.parcel_name = "Sandbox"
    scene.avatar_position = (128.0, 128.0, 25.0)
    hud.diagnostics_window.show()
    # Let the startup allocations happen before anything is measured or
    # frozen: a HUD's first frames build fonts, surfaces and theme data that
    # are not garbage and never will be.
    for _ in range(60):
        hud.update(1.0 / 30.0, scene)
    return hud, scene


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("none", "freeze", "collect"), default="none")
    parser.add_argument("--frames", type=int, default=20000)
    parser.add_argument("--sample-every", type=int, default=2000)
    parser.add_argument(
        "--collect-every",
        type=int,
        default=300,
        help="frames between explicit collections, with --mode collect",
    )
    args = parser.parse_args(argv)

    hud, scene = build_hud()

    frozen = 0
    if args.mode == "freeze":
        gc.collect()
        gc.freeze()
        frozen = gc.get_freeze_count()

    census = TypeCensus(top=80, limit=800)
    rows: list[tuple[int, ...]] = []
    collect_ms: list[float] = []
    for frame in range(args.frames):
        # Move the avatar so the HUD has something new to say every frame;
        # a HUD relaying an unchanging number lays out no new text.
        scene.avatar_position = (128.0 + (frame % 97) * 0.37, 128.0, 25.0 + (frame % 13))
        hud.update(1.0 / 30.0, scene)
        if args.mode == "collect" and frame and frame % args.collect_every == 0:
            start = time.perf_counter()
            gc.collect()
            collect_ms.append((time.perf_counter() - start) * 1000.0)
        if frame % args.sample_every == 0:
            counts = census.counts()
            rows.append(
                (frame, *(counts.get(name, 0) for name, _ in WATCH), sum(counts.values()))
            )

    print(f"mode={args.mode}  frames={args.frames}  frozen={frozen}")
    header = f"{'frame':>7}" + "".join(f" {short:>8}" for _, short in WATCH) + f" {'total':>9}"
    print(header)
    for row in rows:
        print(f"{row[0]:7d}" + "".join(f" {value:8d}" for value in row[1:-1]) + f" {row[-1]:9d}")
    if collect_ms:
        median = sorted(collect_ms)[len(collect_ms) // 2]
        print(
            f"gc.collect(): {len(collect_ms)} calls, min {min(collect_ms):.1f} ms, "
            f"median {median:.1f}, max {max(collect_ms):.1f}"
        )
    start = time.perf_counter()
    gc.collect()
    print(f"one full gc.collect() at the end: {(time.perf_counter() - start) * 1000.0:.1f} ms")
    print(f"machine load {os.getloadavg()[0]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
