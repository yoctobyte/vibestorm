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

import gc
import json
import math
import os
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Gauge = Callable[[], float]
Census = Callable[[], Mapping[str, float]]

#: How many samples a growth verdict needs before it means anything. Two
#: points are a line through anything; the halves the verdict rests on need
#: two apiece.
MIN_SAMPLES_FOR_VERDICT = 4

#: Below this, `settling` is not offered at all -- see `_rate_is_converging`.
#: Six is the fewest that gives two comparable stretches after the first third
#: is discarded, which at the default cadence is three minutes of a soak.
MIN_SAMPLES_FOR_TREND = 6

#: How concentrated a rise may be before the report calls it a step rather
#: than a climb: the share of a run's second-half samples that has to carry
#: half of everything the gauge gained.
#:
#: A leak arrives a little at a time, so half of it takes about half the
#: samples. A step arrives at once. Measured across every soak on record, and
#: the two do not overlap: run 4's `proc.rss_bytes` put half its rise into
#: **2 of 120 samples**, while every row of the two runs that really were
#: leaking -- run 1 and run 3, `rss`, `py_blocks`, `obj._total`, `obj.list` --
#: needed **19 to 26 per cent** of them. The cut is at five, which is nearly
#: four times clear of the nearest leak.
STEP_CONCENTRATION = 0.05

#: How many standard errors a fitted slope must clear before the report calls
#: it a climb rather than the shape of the noise. Not a knob: the two soak
#: runs this was measured against sit twenty-fold either side of it. A heap
#: that was flat to the byte for twenty minutes fits at 0.5 and 0.9 sigma; the
#: leak that prompted all of this fits at 37 to 41 on every row. Anything
#: between 1.5 and 10 separates them identically, and 2 is the conventional
#: place to put it.
TREND_SIGMA = 2.0

#: How much of a gauge one automatic collection has to take back before its
#: climb is better described as garbage than as growth. A half deliberately:
#: below that the row is holding on to more than the collector reclaims, and
#: the reader still has something to look at. Above it, what the row was
#: measuring was the interval between collections.
CYCLIC_RECLAIM_FRACTION = 0.5


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


def freeze_static_heap() -> int:
    """Move everything alive now out of the cyclic collector's reach.

    Not an optimisation. It is the fix for a leak that is not a leak: the
    objects pygame_gui makes for a line of text refer to each other, so only
    the *cyclic* collector frees them, and this interpreter collects the old
    generation **incrementally**. A young collection runs every few thousand
    net allocations and scans only a slice of the old generation with it, so
    one complete pass over the old generation takes as many slices as that
    generation is large. A viewer's static heap -- the UI, the GL objects, the
    modules -- is most of the old generation and none of it is ever garbage:
    all it does is lengthen every pass, until the pass that would free a text
    object promoted an hour ago has still not come round.

    Freezing moves that static heap into the permanent generation, which is
    not scanned at all, so a pass over what is left completes in a fraction of
    the slices.

    Measured directly, by `tools/gc_pressure.py --mode threshold`: with
    624,000 objects held in the old generation, one automatic collection is
    worth **415,586** allocated cyclic objects; freeze that heap and the same
    collection arrives every **3,998**. A hundredfold. Driving the real HUD
    for 20,000 frames says the same thing in the shape a soak sees it: live
    objects sawtooth between 36,000 and 64,000 without this and between 700
    and 4,000 with it, and a full collection costs 20.6 ms against 2.0 ms.
    Soak run 3 is the same again at two hours -- flat for fifty-five minutes
    and then a straight climb to a gigabyte, as each pass took longer than the
    one before.

    Call this once, after the viewer is built and **before** the world
    arrives. Frozen objects are never collected again, so a prim frozen here
    would be a real leak the moment its region went away.

    Returns how many objects were frozen, which is only worth anything as a
    number to put in a log.
    """
    gc.collect()
    gc.freeze()
    return int(gc.get_freeze_count())


def _gc_collections(generation: int) -> Gauge:
    """How many times that generation has been collected, ever."""

    def read() -> float:
        try:
            return float(gc.get_stats()[generation]["collections"])
        except (IndexError, KeyError, TypeError):
            return 0.0

    return read


#: Counters, not gauges: they only rise. Declared here rather than in the
#: viewer because the *rate* of the automatic one explains a heap growing
#: while every container in the report is settled -- collections arriving
#: further and further apart as the generation they scan gets longer. A run
#: where `gc.auto_collections` slows while `obj._total` climbs is that story
#: and no other.
#:
#: The names say what each index was **measured** to count on this
#: interpreter, not what `gc.get_stats()` calls them. Python 3.13 replaced the
#: three-generation collector with an incremental one, and the indices no
#: longer mean generations: index 1 is where the automatic collector counts,
#: and 0 and 2 move only when something calls `gc.collect(0)` or `gc.collect()`
#: by hand. `test_viewer3d_health.py` pins that mapping, so it cannot drift
#: under a future interpreter without a red test.
GC_COUNTERS: dict[str, Gauge] = {
    "gc.young_collections": _gc_collections(0),
    "gc.auto_collections": _gc_collections(1),
    "gc.full_collections": _gc_collections(2),
}


def machine_load_1m() -> float:
    """The machine's one-minute load average -- the *machine's*, not ours.

    Not a leak gauge. A condition. Every figure in a soak report is measured
    against whatever else the machine was doing, and on a developer's own
    desktop that is a compiler, a browser and the test suite of the thing
    being soaked. Without this the report cannot tell "the client started
    growing at minute fifty-five" from "something else started at minute
    fifty-five", and the two look identical -- which is how a soak comes to
    say a leak is real when what it measured was a build.

    Returns 0.0 where the platform has no load average, which is not Linux.
    """
    try:
        return float(os.getloadavg()[0])
    except (AttributeError, OSError, IndexError, ValueError):
        return 0.0


def process_cpu_seconds() -> float:
    """CPU seconds this process has used, user plus system.

    The other half of `machine_load_1m`: load says the machine was busy, this
    says whether *we* were. A viewer holding 30 fps on a quarter of a core
    while the load average is six is a viewer that was not starved, and the
    report should be able to say so rather than leaving it to be remembered.
    """
    try:
        with open("/proc/self/stat", "rb") as handle:
            fields = handle.read().rsplit(b") ", 1)[-1].split()
        ticks = float(int(fields[11]) + int(fields[12]))
        return ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, IndexError, ValueError, ZeroDivisionError):
        return 0.0


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
    "proc.load_1m": machine_load_1m,
    "proc.cpu_seconds": process_cpu_seconds,
}

#: Recorded on every sample and kept *out* of the growth report. These say
#: what the run was measured under, not what the client was holding on to,
#: and both would read as leaks: a load average that rises because a build
#: started is "growing" by every test in this file, and CPU seconds only ever
#: rise by definition. They belong in the header, beside the frame rate, where
#: the reader is deciding whether to believe the rest of the page.
CONDITION_NAMES = frozenset({"proc.load_1m", "proc.cpu_seconds"})


#: Every name a census emits starts with this, so a census can never be
#: mistaken for -- or quietly overwrite -- a gauge somebody declared.
OBJECT_CENSUS_PREFIX = "obj."

#: How many types are picked up from each reading. Forty because the report is
#: read by eye and the tail of a type histogram is thousands of names with two
#: objects apiece; the leak is not down there.
OBJECT_CENSUS_TOP = 40

#: A ceiling on the names the census will follow, because an instrument that
#: grows without bound while looking for something that grows without bound is
#: not funny twice. Reached only by a process minting classes at runtime --
#: which would itself be the finding, and `obj._untracked_types` says so.
OBJECT_CENSUS_LIMIT = 512


def _type_name(cls: type) -> str:
    module = getattr(cls, "__module__", "") or ""
    name = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", "?")
    if module in ("", "builtins"):
        return name
    return f"{module}.{name}"


class TypeCensus:
    """How many live objects of each type there are, on the soak's cadence.

    The instrument the rest of this file could not be. Forty-odd container
    gauges all read `settled` over two hours while RSS climbed forty-five
    megabytes an hour and `proc.py_blocks` fifty-two thousand: something was
    growing that no gauge named, and no amount of adding gauges finds a thing
    nobody has thought of. A histogram names it without being told.

    **What it cannot see.** `gc.get_objects()` returns only what the collector
    tracks, which excludes every atomic object -- `str`, `bytes`, `int`,
    `float`. A million leaked strings held by one list shows up here as that
    list's *type* being ordinary and `proc.py_blocks` climbing anyway. So a
    census that finds nothing is not "no leak": it is a leak in something
    untracked, and the next instrument after this one is `tracemalloc`.

    **What it costs.** A full traversal of the heap, which is why it is off
    unless asked for and why it times itself into `obj._census_ms`: a soak
    report that shows a two-second `longest_gap_s` should be able to tell a
    hitch in the viewer from the instrument stopping the world to count.

    **Why the names stick.** A type reported in one sample and not the next
    has no series, and a leak is exactly the type that climbs *into* the top
    forty halfway through a run. So every name once reported keeps being
    reported -- at zero if it is gone, which is a fact and not a gap.
    """

    def __init__(
        self,
        *,
        top: int = OBJECT_CENSUS_TOP,
        limit: int = OBJECT_CENSUS_LIMIT,
        objects: Callable[[], Iterable[object]] = gc.get_objects,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._top = top
        self._limit = limit
        self._objects = objects
        self._clock = clock
        self._tracked: list[str] = []
        self._tracked_set: set[str] = set()

    @property
    def tracked(self) -> tuple[str, ...]:
        return tuple(self._tracked)

    def counts(self) -> dict[str, int]:
        """The raw histogram, every type, no truncation."""
        tally: dict[str, int] = {}
        for obj in self._objects():
            name = _type_name(type(obj))
            tally[name] = tally.get(name, 0) + 1
        return tally

    def __call__(self) -> dict[str, float]:
        started = self._clock()
        tally = self.counts()
        for name, _count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[
            : self._top
        ]:
            if name in self._tracked_set or len(self._tracked) >= self._limit:
                continue
            self._tracked.append(name)
            self._tracked_set.add(name)
        out: dict[str, float] = {
            f"{OBJECT_CENSUS_PREFIX}{name}": float(tally.get(name, 0))
            for name in self._tracked
        }
        out[f"{OBJECT_CENSUS_PREFIX}_total"] = float(sum(tally.values()))
        out[f"{OBJECT_CENSUS_PREFIX}_types"] = float(len(tally))
        out[f"{OBJECT_CENSUS_PREFIX}_untracked_types"] = float(
            sum(1 for name in tally if name not in self._tracked_set)
        )
        out[f"{OBJECT_CENSUS_PREFIX}_census_ms"] = (self._clock() - started) * 1000.0
        return out


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
    #: Readings that come in bunches, because one traversal answers for all of
    #: them at once -- a type histogram is the reason this exists. Each is
    #: called once per sample and returns a whole mapping; a census that
    #: raises contributes nothing rather than ending the run, and a census may
    #: never overwrite a name already in the sample.
    censuses: Sequence[Census] = ()
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
        for census in self.censuses:
            for name, value in _read_census(census).items():
                # First writer wins. The prefix should make a collision
                # impossible; if one happens anyway, the declared gauge is the
                # one somebody is reading the report for.
                out.setdefault(name, value)
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


def _read_census(census: Census) -> Mapping[str, float]:
    try:
        reading = census()
    except Exception:
        # Same rule as `_read`, for the same reason, and it bites harder here:
        # a census walks the whole heap while the viewer is running, so it has
        # more ways to fail than any single gauge does.
        return {}
    if not isinstance(reading, Mapping):
        return {}
    return {
        name: value
        for name, value in reading.items()
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    }


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


#: How many times its own sampling interval a gap has to be before the run
#: is called starved. Healthy runs on this machine hold 30.0 s to a tenth of
#: a second across two hours, so this is far outside jitter; four was chosen
#: as the point where the loop has demonstrably missed more wall clock than
#: it got, rather than from any measurement of what a hitch looks like.
STARVED_GAP_FACTOR = 4.0


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
    #: The machine's one-minute load average, averaged over each half. Not the
    #: client's number: the *conditions'*. A run whose second half was
    #: measured under a build is a run whose second half says as much about
    #: the build as about the client, and the report has no other way to know.
    #: `None` for a log written before the probe recorded it.
    load_first_half: float | None = None
    load_second_half: float | None = None
    #: How much of one core this process itself used over each half, from its
    #: own CPU seconds. Load says the machine was busy; this says whether we
    #: were, which is the difference between starved and idle.
    cores_first_half: float | None = None
    cores_second_half: float | None = None

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
    def starved(self) -> bool:
        """Did the process stop getting the machine for long stretches?

        Samples come from inside the frame loop, so the interval the run asked
        for is also a claim about how often the loop runs. A gap several times
        that interval is not jitter -- it is the loop not being scheduled.

        Measured on 2026-09-08: run 5 sampled every 30.0 s for forty-five
        minutes, then produced a **single gap of 9,548 s** while the machine's
        load average went from 9 to 356 and this client drew 518 frames in
        two and a half hours -- 0.05 fps. Every gauge in that log kept its
        shape, so every verdict below it read as normal, and all fifty of them
        described a process that was barely running.

        Reported rather than corrected. There is no repairing a starved run:
        the only honest thing is to say the numbers are not about this client
        and to run it again on a quiet machine.
        """
        if self.interval_s is None or self.interval_s <= 0.0:
            return False
        return self.longest_gap_s > self.interval_s * STARVED_GAP_FACTOR

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
        load_first_half=_mean_of(samples[: mid + 1], "proc.load_1m"),
        load_second_half=_mean_of(samples[mid:], "proc.load_1m"),
        cores_first_half=_rate_of(samples[: mid + 1], "proc.cpu_seconds"),
        cores_second_half=_rate_of(samples[mid:], "proc.cpu_seconds"),
    )


def _mean_of(samples: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """The mean of one gauge over these samples, or `None` if it is not there."""
    values = [
        float(s[key])
        for s in samples
        if isinstance(s.get(key), (int, float)) and not isinstance(s.get(key), bool)
    ]
    if not values:
        return None
    return sum(values) / len(values)


def _rate_of(samples: Sequence[Mapping[str, Any]], key: str) -> float | None:
    """How fast a monotonic seconds-counter rose, per second of wall clock.

    For `proc.cpu_seconds` that is cores: 1.0 is one core saturated.
    """
    points = [
        (float(s["elapsed_s"]), float(s[key]))
        for s in samples
        if isinstance(s.get(key), (int, float))
        and not isinstance(s.get(key), bool)
        and isinstance(s.get("elapsed_s"), (int, float))
    ]
    if len(points) < 2:
        return None
    seconds = points[-1][0] - points[0][0]
    if seconds <= 0.0:
        return None
    return (points[-1][1] - points[0][1]) / seconds


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
    #: Least-squares rate over the *second half* of the run, per hour. The
    #: first half of any viewer run is caches filling, which is not a leak;
    #: what separates the two is whether it is still going at the end.
    late_rate_per_hour: float
    #: That rate's standard error, same units. Read the two together or
    #: neither: a rate of 1,737 an hour means one thing beside an error of 40
    #: and the opposite beside an error of 2,041, and the report has printed
    #: both. See `_fit`.
    late_rate_stderr_per_hour: float
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
    collections = _collection_times(samples)
    names: list[str] = []
    seen: set[str] = set()
    for sample in samples:
        for name in sample:
            if name in ("elapsed_s", "frame") or name in CONDITION_NAMES or name in seen:
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
        late_rate, late_stderr = _late_fit(points)
        report.append(
            Growth(
                name=name,
                kind=kind,
                samples=len(points),
                first=values[0],
                last=values[-1],
                low=min(values),
                peak=max(values),
                late_rate_per_hour=late_rate,
                late_rate_stderr_per_hour=late_stderr,
                verdict=_verdict(points, kind, collections),
            )
        )
    report.sort(key=lambda g: (g.kind != "gauge", -abs(g.late_rate_per_hour), g.name))
    return report


def _fit(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Least-squares slope through `points`, and that slope's standard error.

    Both per second. The pair is the whole point: a slope on its own cannot
    be told apart from the phase of whatever the series was doing when the
    run ended, and a soak's series oscillate.

    This replaced a subtraction of the two end samples, which is a line
    through two points chosen for when they happened rather than for what
    they say. Measured on the run that prompted it, where the heap was flat
    to the byte for the last twenty minutes: the endpoint rule reported
    `proc.py_blocks` climbing 8,183 an hour and `obj._total` 3,355. The fits
    are 1,737 +/- 2,041 and 416 +/- 884 -- both inside their own error, both
    reported as trends five to eight times larger than the fit by a rule that
    was reading which end of a sawtooth the last sample landed on.

    The error is the textbook one, ``resid_sd / sqrt(sum((t - mean_t)^2))``,
    and it assumes the residuals are independent. A sawtooth's are not, so on
    an oscillating series it understates -- it will call a wobble real before
    it calls a trend noise. That is the safe direction for a report whose job
    is to find leaks, and it is why a plain two-sigma cut is enough here
    rather than something with a name.
    """
    count = len(points)
    if count < 2:
        return 0.0, 0.0
    mean_t = sum(t for t, _ in points) / count
    mean_v = sum(v for _, v in points) / count
    spread = sum((t - mean_t) ** 2 for t, _ in points)
    if spread <= 0.0:
        return 0.0, 0.0
    slope = sum((t - mean_t) * (v - mean_v) for t, v in points) / spread
    if count < 3:
        # Two points are a line through anything, and it has no residual to
        # measure. Zero error means "believe the slope", which for two points
        # is the only honest answer available -- there is nothing to disagree
        # with it.
        return slope, 0.0
    residuals = sum((v - (mean_v + slope * (t - mean_t))) ** 2 for t, v in points)
    stderr = ((residuals / (count - 2)) / spread) ** 0.5
    return slope, stderr


def _rise_concentration(points: Sequence[tuple[float, float]]) -> float:
    """What share of the second half's samples carries half of what it gained.

    The shape a fit cannot see. A least-squares slope tells a trend from
    noise, and it says nothing about whether the trend arrived evenly or all
    at once -- two flat plateaus with one jump between them have a slope, and
    it is a large one.

    That is not a hypothetical. Run 4 sat at 630,185,984 bytes for eighty
    minutes, stepped once to 651,558,912, and sat there for the rest; the fit
    called it 31 MB an hour at nineteen sigma, which is exactly what a line
    through that shape is. The step lands on the one sample where the machine's
    load average hit 18.5 -- somebody else's build, on a machine this soak
    shares -- and a reader who took the slope at face value would have gone
    looking for a leak that is not there.

    Returns 1.0 for a gauge that gained nothing, so a flat series is never
    called a step.
    """
    half = points[len(points) // 2 :]
    rises = sorted(
        (max(0.0, later - earlier) for (_, earlier), (_, later) in zip(half, half[1:])),
        reverse=True,
    )
    total = sum(rises)
    if total <= 0.0 or not rises:
        return 1.0
    running = 0.0
    for count, rise in enumerate(rises, 1):
        running += rise
        if running >= total / 2.0:
            return count / len(rises)
    return 1.0


def _late_fit(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """The fit over the second half, per hour. See `Growth.late_rate_per_hour`."""
    if len(points) < 2:
        return 0.0, 0.0
    slope, stderr = _fit(points[len(points) // 2 :])
    return slope * 3600.0, stderr * 3600.0


def _rate(points: Sequence[tuple[float, float]]) -> float:
    """The fitted slope through a stretch, per second."""
    return _fit(points)[0]


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
    middle = _rate(points[first : second + 1])
    last = _rate(points[second:])
    return last < middle / 2.0


def _collection_times(samples: Sequence[Mapping[str, Any]]) -> frozenset[float]:
    """When the automatic collector ran, by elapsed time.

    Generation 1, for the reason given at `gc.auto_collections` above: on
    3.13 and later that is the one the interpreter runs on its own.
    """
    times: set[float] = set()
    previous: float | None = None
    for sample in samples:
        value = sample.get("gc.auto_collections")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if previous is not None and value > previous:
            times.add(float(sample.get("elapsed_s", 0.0)))
        previous = float(value)
    return frozenset(times)


def _reclaimed_fraction(
    points: Sequence[tuple[float, float]],
    collections: frozenset[float],
) -> float:
    """The largest share of a gauge that one collection took back.

    A leak survives collection by definition -- that is what makes it a leak
    -- so a row that falls by most of itself the moment the collector runs is
    reporting the collector's schedule and not the viewer's memory. Soak run
    6 climbed for thirty-five minutes across a dozen rows, dropped all of it
    at one collection, and started again; without this every one of those
    rows read `growing`, and the run's real finding -- a flat heap -- was
    underneath eighteen false ones.

    Measured against the value before the drop rather than against the row's
    range, because the range includes startup, where a viewer goes from
    nothing to a loaded scene and every row is at its smallest.
    """
    best = 0.0
    for (_, before), (when, after) in zip(points, points[1:], strict=False):
        if when not in collections or before <= 0.0:
            continue
        best = max(best, (before - after) / before)
    return best


def _verdict(
    points: Sequence[tuple[float, float]],
    kind: str,
    collections: frozenset[float] = frozenset(),
) -> str:
    values = [v for _, v in points]
    if len(points) < MIN_SAMPLES_FOR_VERDICT:
        return "too-short"
    if max(values) == min(values):
        return "flat"
    mid = len(points) // 2
    if kind == "counter":
        # A counter is supposed to climb. The failure is the opposite one: a
        # session that stopped receiving, or a loop that stopped drawing,
        # neither of which raises anything. Two points are enough here in a
        # way they are not for a gauge: a counter only ever goes up, so it has
        # no phase to be caught on the wrong side of.
        return "stalled" if values[-1] - values[mid] <= 0.0 else "rising"
    late, stderr = _late_fit(points)
    if late <= 0.0 or (stderr > 0.0 and late < TREND_SIGMA * stderr):
        # Either it did not climb, or it climbed by less than the scatter it
        # was measured through -- which are the same answer to the only
        # question this column is asked, and giving them separate words would
        # have put a healthy sawtooth in one or the other depending on where
        # the run happened to stop. A reader who wants to know which it was
        # reads the rate and its error, which is what they are printed for.
        return "settled"
    if _reclaimed_fraction(points, collections) >= CYCLIC_RECLAIM_FRACTION:
        # It climbed, and then a collection took most of it back. Reported
        # ahead of the sample-count rules on purpose: this is evidence about
        # what the row holds, and the rules below are guesses made in its
        # absence.
        return "cyclic"
    if len(points) < MIN_SAMPLES_FOR_TREND:
        # Not enough to see a trend in, so do not claim one. Of the two words
        # available the alarming one is the safe default: a short run that
        # says "growing" costs a second look, and one that says "settling"
        # costs the finding.
        return "growing"
    if _rise_concentration(points) <= STEP_CONCENTRATION:
        # It climbed, and the climb is real, and it happened at once. That is
        # a different thing from a leak and sends the reader somewhere else
        # entirely -- to *when*, and to what else was happening then, rather
        # than to what is being held on to. Unlike `steady`, which was tried
        # and dropped for being a second word for the same answer, this is a
        # genuinely different answer.
        return "stepped"
    return "settling" if _rate_is_converging(points) else "growing"


def format_growth_report(report: Sequence[Growth], *, limit: int = 0) -> str:
    rows = list(report)
    if limit:
        rows = rows[:limit]
    width = max((len(g.name) for g in rows), default=4)
    lines = [
        f"{'name':{width}s} {'kind':8s} {'first':>14s} {'last':>14s} "
        f"{'peak':>14s} {'per hour':>14s} {'+/-':>12s}  verdict"
    ]
    for g in rows:
        lines.append(
            f"{g.name:{width}s} {g.kind:8s} {_num(g.first):>14s} {_num(g.last):>14s} "
            f"{_num(g.peak):>14s} {_num(g.late_rate_per_hour):>14s} "
            f"{_num(g.late_rate_stderr_per_hour):>12s}  {g.verdict}"
        )
    return "\n".join(lines)


def _num(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,d}"
    return f"{value:,.2f}"


__all__ = [
    "Census",
    "Gauge",
    "GC_COUNTERS",
    "Growth",
    "Pace",
    "HealthProbe",
    "CYCLIC_RECLAIM_FRACTION",
    "MIN_SAMPLES_FOR_TREND",
    "MIN_SAMPLES_FOR_VERDICT",
    "STEP_CONCENTRATION",
    "TREND_SIGMA",
    "OBJECT_CENSUS_LIMIT",
    "OBJECT_CENSUS_PREFIX",
    "OBJECT_CENSUS_TOP",
    "PROCESS_GAUGES",
    "TypeCensus",
    "SoakLog",
    "format_growth_report",
    "freeze_static_heap",
    "growth_report",
    "pace_report",
    "machine_load_1m",
    "process_cpu_seconds",
    "process_rss_bytes",
    "read_soak_log",
]
