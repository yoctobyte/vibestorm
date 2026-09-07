#!/usr/bin/env python3
"""Say what a soak log's numbers did, worst first.

Usage:

    .venv/bin/python tools/soak_report.py local/soak/run.jsonl

The ranking is by the rate over the *second half* of the run, because the
first half of any viewer run is caches filling and that is the viewer
working. What matters is what was still climbing at the end.

`--counters` names the series that only ever rise by design. It defaults to
the ones `viewer3d.app` declares, so the report knows a packet count from a
cache, and passing it explicitly is only for a log written by something else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vibestorm.viewer3d.health import (  # noqa: E402
    format_growth_report,
    growth_report,
    pace_report,
    read_soak_log,
)

DEFAULT_COUNTERS = (
    "udp.total_received",
    "udp.agent_updates",
    "udp.packet_acks",
    "udp.appended_acks",
    "udp.pings_answered",
    "udp.reliable_resends",
    "udp.reliable_abandoned",
    "eq.attempts",
    "eq.batches",
    "eq.events",
    "world.object_updates",
    "scene.repeat_frames",
    "scene.rebuilt_frames",
    "gc.young_collections",
    "gc.auto_collections",
    "gc.full_collections",
    # The names these went out under before the index mapping was measured.
    # Soak run 4 is written in them; a report of it must still read them as
    # counters rather than as three permanent `growing` rows.
    "gc.gen0",
    "gc.gen1",
    "gc.gen2",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--counters",
        nargs="*",
        default=list(DEFAULT_COUNTERS),
        help="Series that only ever rise by design.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Show only the first N rows.")
    parser.add_argument(
        "--only",
        choices=("all", "growing", "stalled"),
        default="all",
        help="Filter to the rows worth acting on.",
    )
    args = parser.parse_args(argv)

    samples = read_soak_log(args.log)
    if not samples:
        print(f"no samples in {args.log}", file=sys.stderr)
        return 1

    pace = pace_report(samples)
    if pace is None:
        print(f"{len(samples)} samples -- too few to say anything about the pace")
    else:
        print(
            f"{len(samples)} samples over {pace.span_s / 60.0:.1f} min, "
            f"{pace.frames:,d} frames"
        )
        if pace.cut_short and pace.run_seconds is not None:
            # First line, and loud, because everything below it is computed
            # over a run that did not happen: a soak cut short reads exactly
            # like a short soak, and every verdict inherits that quietly.
            print(
                f"  ** CUT SHORT: asked for {pace.run_seconds / 60.0:.0f} min, "
                f"got {pace.span_s / 60.0:.1f}. Treat everything below as a "
                f"partial run. **"
            )
        if pace.starved:
            longest = pace.longest_gap_s
            print(
                f"  ** STARVED: one gap of {longest / 60.0:.0f} min between samples "
                f"that were asked for every {pace.interval_s:.0f} s. The loop was "
                f"not being scheduled, so the verdicts below describe a process "
                f"that was barely running. Run it again on a quiet machine. **"
            )
        print(
            f"  frame rate   {pace.fps_first_half:.1f} fps in the first half, "
            f"{pace.fps_second_half:.1f} in the second "
            f"({pace.slowed_by * 100:+.0f}% slower)"
        )
        asked = (
            f" (asked for every {pace.interval_s:.0f} s)"
            if pace.interval_s is not None
            else ""
        )
        print(
            f"  gaps         {pace.shortest_gap_s:.1f} s shortest, "
            f"{pace.longest_gap_s:.1f} s longest{asked}"
        )
        # Conditions, not results -- and printed before the results because
        # they decide whether to believe them. A run whose second half was
        # measured under somebody else's build says as much about the build as
        # about this client, and nothing else on the page can tell the reader
        # that. Absent from a log written before the probe recorded them, in
        # which case the report says nothing rather than implying a quiet one.
        if pace.load_first_half is not None and pace.load_second_half is not None:
            print(
                f"  machine      load {pace.load_first_half:.1f} in the first "
                f"half, {pace.load_second_half:.1f} in the second"
            )
        if pace.cores_first_half is not None and pace.cores_second_half is not None:
            print(
                f"  this client  {pace.cores_first_half:.2f} cores in the first "
                f"half, {pace.cores_second_half:.2f} in the second"
            )
    print()

    report = growth_report(samples, counters=args.counters)
    if args.only == "growing":
        report = [g for g in report if g.verdict == "growing"]
    elif args.only == "stalled":
        report = [g for g in report if g.verdict == "stalled"]
    if not report:
        print(f"nothing {args.only}")
        return 0
    print(format_growth_report(report, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
