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
        out: dict[str, Any] = {"elapsed_s": round(elapsed_s, 3), "frame": frame}
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


def _verdict(points: Sequence[tuple[float, float]], kind: str) -> str:
    values = [v for _, v in points]
    if len(points) < MIN_SAMPLES_FOR_VERDICT:
        return "too-short"
    if max(values) == min(values):
        return "flat"
    mid = len(points) // 2
    early = values[mid] - values[0]
    late = values[-1] - values[mid]
    if kind == "counter":
        # A counter is supposed to climb. The failure is the opposite one: a
        # session that stopped receiving, or a loop that stopped drawing,
        # neither of which raises anything.
        return "stalled" if late <= 0.0 else "rising"
    if late <= 0.0:
        return "settled"
    if early > 0.0 and late < early / 2.0:
        return "settling"
    return "growing"


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
    "HealthProbe",
    "MIN_SAMPLES_FOR_VERDICT",
    "PROCESS_GAUGES",
    "SoakLog",
    "format_growth_report",
    "growth_report",
    "process_rss_bytes",
    "read_soak_log",
]
