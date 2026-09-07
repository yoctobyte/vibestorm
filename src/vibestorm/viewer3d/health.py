"""What a viewer left running for hours is made of, counted on a cadence.

A leak in a viewer does not announce itself. It shows up as a session that
was fine for an hour and is swapping at four, and by the time it does, the
frame it dies on says nothing at all about which container grew. Every
measurement in this project so far has been of one frame or one tick; this
is the one that can only be taken over time.

So the containers that *can* grow are counted on a fixed cadence and written
down, and "which of these grew" is answered afterwards from the record
rather than from a guess. Two rules make the record worth reading:

**A gauge may not kill the viewer.** A health probe that crashes the thing it
is watching is worse than no probe at all, so every gauge is called inside a
guard and an unreadable one is recorded as ``None`` rather than raised. A run
that ends early tells you nothing about hour four.

**A counter is not a leak.** Frames drawn and packets received rise for the
whole run by design, and mixing them in with the caches buries the finding.
They are declared separately -- and they earn their place, because a counter
that *stops* is its own kind of failure: a session that went deaf an hour in
is not a crash, and nothing else in this client would notice it.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Gauge = Callable[[], float]

#: How many samples a growth verdict needs before it means anything. Two
#: points are a line through anything; the halves the verdict rests on need
#: two apiece.
MIN_SAMPLES_FOR_VERDICT = 4

#: Below this, `settling` is not offered at all -- see `_rate_is_still_falling`.
#: Six is the fewest that gives two comparable stretches after the first third
#: is discarded, which at the default cadence is three minutes of a soak.
MIN_SAMPLES_FOR_TREND = 6


def process_rss_bytes() -> float:
    """Resident set size of this process, in bytes.

    ``/proc/self/statm`` rather than ``resource.getrusage``: that reports the
    *peak*, which never comes down, so a cache that fills and is evicted reads
    there as a leak for the rest of the run. Returns 0.0 where there is no
    procfs, which is not Linux and not where this runs.
    """
    try:
        with open("/proc/self/statm", "rb") as handle:
            fields = handle.read().split()
        return float(int(fields[1]) * 4096)
    except (OSError, IndexError, ValueError):
        return 0.0


def _allocated_blocks() -> float:
    # The best single number CPython offers for "is Python itself holding on
    # to more than it was": live allocator blocks, no traversal, no GC pass.
    return float(sys.getallocatedblocks())


def _thread_count() -> float:
    return float(threading.active_count())


#: Taken on every sample whatever the caller declares. All three are cheap
#: enough to read sixty times a second, which is not how often they are read.
#:
#: ``gc.get_count()`` is deliberately not among them. It counts allocations
#: since the last collection, so it swings on every frame and reports
#: "growing" about as often as not -- and a report that cries wolf on one row
#: of every run is a report nobody reads to the bottom of.
#: ``sys.getallocatedblocks()`` answers the same question without the noise.
PROCESS_GAUGES: dict[str, Gauge] = {
    "proc.rss_bytes": process_rss_bytes,
    "proc.py_blocks": _allocated_blocks,
    "proc.threads": _thread_count,
}


@dataclass
class HealthProbe:
    """Reads a named set of numbers, on a cadence, without ever raising.

    ``gauges`` are things that may go up and down -- the size of a cache, the
    bytes of texture resident, the number of entities drawn. ``counters`` only
    ever rise: frames, packets, errors. The two are kept apart because the
    report reads them in opposite directions, a gauge that keeps climbing
    being the finding and a counter that stops climbing being one.
    """

    gauges: Mapping[str, Gauge] = field(default_factory=dict)
    counters: Mapping[str, Gauge] = field(default_factory=dict)
    interval_s: float = 30.0
    #: How long the run was asked to last, if it was asked for anything. Goes
    #: into every sample so the report can say whether the run it is reading
    #: is the run somebody meant to take -- a soak that is cut short reads
    #: exactly like a short soak otherwise, and a two-hour question answered
    #: by thirteen minutes of data is worse than no answer.
    run_seconds: float = 0.0
    #: Wall clock of the last sample, in the caller's own units, so the probe
    #: never reads a clock the tests then have to freeze.
    _last_at: float | None = field(default=None, repr=False)

    def due(self, elapsed_s: float) -> bool:
        """Has ``interval_s`` passed since the last sample?

        The first call is always due: a run wants a reading from before
        anything has had a chance to grow, or there is no baseline to
        measure the growth against.
        """
        if self._last_at is None:
            return True
        return elapsed_s - self._last_at >= self.interval_s

    def sample(self, *, elapsed_s: float, frame: int) -> dict[str, Any]:
        self._last_at = elapsed_s
        # The cadence goes in the sample rather than being inferred from it.
        # A report that reads the shortest observed gap and calls it "asked
        # for" is reading the tightest hitch in the run: the final sample is
        # written from the shutdown path moments after a scheduled one, so the
        # shortest gap is an artefact, and it made a 30-second soak print
        # "asked for every 5 s".
        out: dict[str, Any] = {
            "elapsed_s": round(elapsed_s, 3),
            "frame": frame,
            "soak_interval_s": self.interval_s,
        }
        if self.run_seconds > 0.0:
            out["soak_run_seconds"] = self.run_seconds
        for name, read in PROCESS_GAUGES.items():
            out[name] = _read(read)
        for name, read in self.gauges.items():
            out[name] = _read(read)
        for name, read in self.counters.items():
            out[name] = _read(read)
        return out

    @property
    def counter_names(self) -> frozenset[str]:
        return frozenset(self.counters)


def _read(read: Gauge) -> float | None:
    try:
        value = float(read())
    except Exception:
        # Deliberately bare. A gauge is a lambda over somebody else's cache
        # and the list of ways one can fail is not knowable from here; what
        # is knowable is that none of them should end the run.
        return None
    if not math.isfinite(value):
        return None
    return value


class SoakLog:
    """One JSON object per line, flushed as it goes.

    Flushed because the interesting run is the one that dies: a report that
    is only readable after a clean exit cannot say anything about the hour
    that ended in a traceback.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, sample: Mapping[str, Any]) -> None:
        self._handle.write(json.dumps(sample, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass

    def __enter__(self) -> SoakLog:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def read_soak_log(path: Path) -> list[dict[str, Any]]:
    """Read a soak log, skipping any line that is not a whole JSON object.

    A run killed mid-write leaves a partial last line, and that is the run
    whose report matters most.
    """
    samples: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                samples.append(obj)
    return samples


@dataclass(frozen=True)
class Pace:
    """How the run's own frame rate held up, and whether it ever stopped.

    A viewer that is fine for an hour and drawing at five frames a second by
    the fourth has failed in exactly the way this file exists to catch, and
    no gauge in the report says so: every container can be perfectly stable
    while the loop grinds. The frame counter and the clock are in every
    sample already, so this costs nothing to work out and is the first thing
    worth reading.

    ``longest_gap_s`` is the other half. Samples are taken from inside the
    frame loop, so a gap far longer than the interval is the loop having
    stopped -- a hitch, a blocking call, a garbage collection nobody
    budgeted for. A mean frame rate hides those completely.
    """

    frames: int
    span_s: float
    fps_first_half: float
    fps_second_half: float
    longest_gap_s: float
    shortest_gap_s: float
    #: What the run was *asked* for, straight off the samples -- `None` for a
    #: log written before the probe recorded it, in which case the report says
    #: nothing rather than guessing.
    interval_s: float | None
    #: How long the run was meant to last, same rules. `cut_short` is the
    #: question it exists to answer.
    run_seconds: float | None

    @property
    def cut_short(self) -> bool:
        """Did the run end well before it was asked to?

        A soak that dies early reads exactly like a short soak: the report is
        happy, every verdict is computed over whatever arrived, and nobody is
        told that the two-hour question was answered with thirteen minutes.
        Measured on 2026-09-07, when logging a probe in as the same avatar
        closed a running soak's circuit and the report said nothing at all.

        The tolerance is one sampling interval, because the last sample lands
        wherever the loop happened to be.
        """
        if self.run_seconds is None:
            return False
        slack = self.interval_s if self.interval_s is not None else 0.0
        return self.span_s < self.run_seconds - slack

    @property
    def slowed_by(self) -> float:
        """Fraction of the early frame rate that has been lost by the end."""
        if self.fps_first_half <= 0.0:
            return 0.0
        return 1.0 - (self.fps_second_half / self.fps_first_half)


def pace_report(samples: Sequence[Mapping[str, Any]]) -> Pace | None:
    """What the loop did, as opposed to what it was holding on to."""
    points = [
        (float(s["elapsed_s"]), int(s["frame"]))
        for s in samples
        if isinstance(s.get("elapsed_s"), (int, float)) and isinstance(s.get("frame"), int)
    ]
    if len(points) < 3:
        return None
    mid = len(points) // 2
    span = points[-1][0] - points[0][0]
    gaps = [b[0] - a[0] for a, b in zip(points, points[1:], strict=False)]
    return Pace(
        frames=points[-1][1] - points[0][1],
        span_s=span,
        fps_first_half=_fps(points[0], points[mid]),
        fps_second_half=_fps(points[mid], points[-1]),
        longest_gap_s=max(gaps) if gaps else 0.0,
        shortest_gap_s=min(gaps) if gaps else 0.0,
        interval_s=_asked_for(samples, "soak_interval_s"),
        run_seconds=_asked_for(samples, "soak_run_seconds"),
    )


def _asked_for(samples: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """What the run was asked for under `key`, if the samples say."""
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _fps(start: tuple[float, int], end: tuple[float, int]) -> float:
    seconds = end[0] - start[0]
    if seconds <= 0.0:
        return 0.0
    return (end[1] - start[1]) / seconds


@dataclass(frozen=True)
class Growth:
    """What one name did over a run."""

    name: str
    kind: str  # "gauge" or "counter"
    samples: int
    first: float
    last: float
    low: float
    peak: float
    #: Rate over the *second half* of the run, per hour. The first half of any
    #: viewer run is caches filling, which is not a leak; what separates the
    #: two is whether it is still going at the end.
    late_rate_per_hour: float
    verdict: str

    @property
    def delta(self) -> float:
        return self.last - self.first


def growth_report(
    samples: Sequence[Mapping[str, Any]],
    *,
    counters: Iterable[str] = (),
) -> list[Growth]:
    """Say what each name did, worst first.

    "Worst" is the late rate, not the total change: a cache that filled in
    the first minute and sat still for an hour is the viewer working, and a
    cache that gained a little in every one of those sixty minutes is the
    bug. Sorting on the total change puts them the wrong way round.
    """
    counter_names = frozenset(counters)
    names: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        for name in sample:
            if name in ("elapsed_s", "frame") or name in seen:
                continue
            seen.add(name)
            names.append(name)

    report: list[Growth] = []
    for name in names:
        points = [
            (float(s.get("elapsed_s", 0.0)), float(s[name]))
            for s in samples
            if isinstance(s.get(name), (int, float)) and not isinstance(s.get(name), bool)
        ]
        if not points:
            continue
        values = [v for _, v in points]
        kind = "counter" if name in counter_names else "gauge"
        report.append(
            Growth(
                name=name,
                kind=kind,
                samples=len(points),
                first=values[0],
                last=values[-1],
                low=min(values),
                peak=max(values),
                late_rate_per_hour=_late_rate_per_hour(points),
                verdict=_verdict(points, kind),
            )
        )
    report.sort(key=lambda g: (g.kind != "gauge", -abs(g.late_rate_per_hour), g.name))
    return report


def _late_rate_per_hour(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    mid = len(points) // 2
    start_t, start_v = points[mid]
    end_t, end_v = points[-1]
    span = end_t - start_t
    if span <= 0.0:
        return 0.0
    return (end_v - start_v) * 3600.0 / span


def _rate(first: tuple[float, float], last: tuple[float, float]) -> float:
    span = last[0] - first[0]
    if span <= 0.0:
        return 0.0
    return (last[1] - first[1]) / span


def _rate_is_converging(points: Sequence[tuple[float, float]]) -> bool:
    """Has the rate at least halved between the middle stretch and the last?

    Two decisions, and both were wrong once.

    *The first third is thrown away.* Every run begins with caches filling, so
    any comparison against the start is dominated by it: a process that burns
    a hundred megabytes in its first twenty minutes and then leaks forty an
    hour for ever has a second half far smaller than its first, and reads as
    settling on any early-versus-late test. That is measured, not imagined --
    a two-hour run went 182 MB an hour, then 16, then 39, and was reported as
    settling.

    *And the fall has to be a halving, not merely a fall.* A container filling
    at a flat rate is the canonical leak, and a flat rate wobbles: a couple of
    per cent either way is noise, so "lower than before" calls half of all
    leaks settled. Halving is the same convergence test the early-versus-late
    rule was reaching for -- it was only ever applied in the wrong place.
    """
    count = len(points)
    first, second = count // 3, 2 * count // 3
    middle = _rate(points[first], points[second])
    last = _rate(points[second], points[-1])
    return last < middle / 2.0


def _verdict(points: Sequence[tuple[float, float]], kind: str) -> str:
    values = [v for _, v in points]
    if len(points) < MIN_SAMPLES_FOR_VERDICT:
        return "too-short"
    if max(values) == min(values):
        return "flat"
    mid = len(points) // 2
    late = values[-1] - values[mid]
    if kind == "counter":
        # A counter is supposed to climb. The failure is the opposite one: a
        # session that stopped receiving, or a loop that stopped drawing,
        # neither of which raises anything.
        return "stalled" if late <= 0.0 else "rising"
    if late <= 0.0:
        return "settled"
    if len(points) < MIN_SAMPLES_FOR_TREND:
        # Not enough to see a trend in, so do not claim one. Of the two words
        # available the alarming one is the safe default: a short run that
        # says "growing" costs a second look, and one that says "settling"
        # costs the finding.
        return "growing"
    return "settling" if _rate_is_converging(points) else "growing"


def format_growth_report(report: Sequence[Growth], *, limit: int = 0) -> str:
    rows = list(report)
    if limit:
        rows = rows[:limit]
    width = max((len(g.name) for g in rows), default=4)
    lines = [
        f"{'name':{width}s} {'kind':8s} {'first':>14s} {'last':>14s} "
        f"{'peak':>14s} {'per hour':>14s}  verdict"
    ]
    for g in rows:
        lines.append(
            f"{g.name:{width}s} {g.kind:8s} {_num(g.first):>14s} {_num(g.last):>14s} "
            f"{_num(g.peak):>14s} {_num(g.late_rate_per_hour):>14s}  {g.verdict}"
        )
    return "\n".join(lines)


def _num(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,d}"
    return f"{value:,.2f}"


__all__ = [
    "Gauge",
    "Growth",
    "Pace",
    "HealthProbe",
    "MIN_SAMPLES_FOR_TREND",
    "MIN_SAMPLES_FOR_VERDICT",
    "PROCESS_GAUGES",
    "SoakLog",
    "format_growth_report",
    "growth_report",
    "pace_report",
    "process_rss_bytes",
    "read_soak_log",
]
