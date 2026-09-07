#!/usr/bin/env python3
"""Why building a HUD costs 120 MB that never comes back.

The test suite peaks at **10.3 GB** of resident memory. It passes, and on a
60 GB machine it has never been the thing that failed -- but the owner runs
their own builds on this machine, and a suite that wants ten gigabytes is one
unlucky overlap away from an OOM kill landing on somebody's compile.

Where it goes, traced per test with a `pytest_runtest_logreport` hook:

    3.3 GB    28 tests  test/test_viewer3d_hud_render_mode.py
    1.5 GB     7 tests  test/test_viewer3d_hud_scale.py
    1.4 GB    11 tests  test/test_viewer3d_hud_dirty.py
    1.3 GB     9 tests  test/test_viewer3d_object_inspector.py
    0.8 GB     7 tests  test/test_viewer3d_hud_refresh.py
    0.8 GB     7 tests  test/test_viewer3d_health.py
    0.3 GB     2 tests  test/test_viewer3d_hud_events.py
    -------
    9.3 GB    62 tests, every one of which builds a HUD

`--mode hud` is where that number comes from: build a HUD, drop it, collect,
census. Twelve rounds, and the answer is a straight line -- **7,162 objects
and 120.8 MB per HUD**, no deviation in any round. A straight line through a
census taken after `gc.collect()` is a retention, not allocator arenas: the
objects are still there to be counted.

`--mode elements` is the bisection, and it lands on one construct. Every
pygame_gui element type is flat at zero per round except one:

    30 buttons              +0/round
    a text entry            +0/round
    a text box              +0/round
    a selection list        +0/round
    a drop down             +0/round
    a window              +601/round

The HUD builds nine `UIWindow`s, which at 601 apiece is 5,409 of the 7,162;
the rest is the widgets those windows carry, since the one in this bisection
is empty.

**It is upstream, and it is not worked around.** pygame_gui 0.6.14, in a
process with no vibestorm code in it at all, leaks the same 601 objects per
window. Neither `window.kill()` nor `manager.clear_and_reset()` gives any of
it back -- both were measured, and both still leak 601 -- and walking the
referrers finds no module global holding it: the window sits in a closed
cycle that `gc` will not free, which is what a C-level reference the collector
cannot traverse looks like from Python.

So there is nothing for the HUD to release, and no teardown to write. The only
lever is building fewer windows, which in practice means building fewer HUDs
in the tests -- and that is deliberately **not** done here. Those 62 tests
toggle render modes, resize, hide and show windows and change scale; sharing
one HUD between them trades 9 GB for order-dependence, and a suite that fails
depending on what ran before it is worse than a suite that wants a lot of
memory on a machine that has it. Recorded rather than fixed, with the
measurement kept so the trade can be re-made if the memory ever starts
costing something real.

Run it when a pygame_gui upgrade lands: if `a window` joins the other rows at
+0, the 9.3 GB goes away on its own.
"""

from __future__ import annotations

import argparse
import gc
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

# Before pygame is imported by anything: no window opens on anyone's desktop.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from vibestorm.viewer3d.health import TypeCensus, process_rss_bytes  # noqa: E402


def _screen():
    import pygame

    pygame.init()
    pygame.display.set_mode((800, 600))
    return pygame


def measure_hud(rounds: int) -> None:
    """Build a HUD, drop it, and count what stayed."""
    pygame = _screen()
    from vibestorm.viewer3d.hud import HUD

    census = TypeCensus(top=8)
    print(f"{'round':>5} {'rss MB':>9} {'objects':>10}")
    first = last = 0.0
    for i in range(rounds + 1):
        hud = HUD((800, 600), on_chat_submit=lambda _text: None)
        del hud
        gc.collect()
        total = census().get("obj._total", 0.0)
        if i == 1:
            first = total
        last = total
        print(f"{i:5d} {process_rss_bytes() / 1e6:9.1f} {total:10,.0f}", flush=True)
    if rounds > 1:
        print(f"\n{(last - first) / (rounds - 1):+,.0f} objects per HUD")
    print("\nwhat is holding it:")
    for name, value in sorted(census().items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {value:12,.0f}  {name}")
    pygame.quit()


def measure_elements(rounds: int) -> None:
    """One element type at a time, until only one of them is still rising."""
    pygame = _screen()
    import pygame_gui

    rect = pygame.Rect
    census = TypeCensus(top=3)

    def buttons() -> None:
        manager = pygame_gui.UIManager((800, 600))
        for i in range(30):
            pygame_gui.elements.UIButton(
                relative_rect=rect(0, i, 80, 20), text="b", manager=manager
            )

    def text_entry() -> None:
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UITextEntryLine(relative_rect=rect(0, 0, 200, 30), manager=manager)

    def text_box() -> None:
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UITextBox(
            html_text="hello", relative_rect=rect(0, 0, 200, 80), manager=manager
        )

    def selection_list() -> None:
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UISelectionList(
            relative_rect=rect(0, 0, 200, 100), item_list=["a", "b", "c"], manager=manager
        )

    def drop_down() -> None:
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UIDropDownMenu(
            options_list=["a", "b"],
            starting_option="a",
            relative_rect=rect(0, 0, 150, 30),
            manager=manager,
        )

    def window() -> None:
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UIWindow(
            rect=rect(0, 0, 300, 200), manager=manager, window_display_title="w"
        )

    def window_killed() -> None:
        """The obvious fix, measured rather than assumed. It does not work."""
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UIWindow(
            rect=rect(0, 0, 300, 200), manager=manager, window_display_title="w"
        ).kill()

    def window_reset() -> None:
        """Nor does this one."""
        manager = pygame_gui.UIManager((800, 600))
        pygame_gui.elements.UIWindow(
            rect=rect(0, 0, 300, 200), manager=manager, window_display_title="w"
        )
        manager.clear_and_reset()

    cases = (
        ("30 buttons", buttons),
        ("a text entry", text_entry),
        ("a text box", text_box),
        ("a selection list", selection_list),
        ("a drop down", drop_down),
        ("a window", window),
        ("a window, kill()ed", window_killed),
        ("a window, manager reset", window_reset),
    )
    for name, build in cases:
        counts = []
        for _ in range(rounds + 1):
            build()
            gc.collect()
            counts.append(census().get("obj._total", 0.0))
        # From the second round on: the first builds caches that are not the
        # question, and counting them would make every row look like a leak.
        per = (counts[-1] - counts[1]) / (rounds - 1) if rounds > 1 else 0.0
        print(f"  {name:26s} {per:+8,.0f} per round", flush=True)
    pygame.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=("hud", "elements"), default="hud")
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args()
    if args.mode == "hud":
        measure_hud(args.rounds)
    else:
        measure_elements(args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
