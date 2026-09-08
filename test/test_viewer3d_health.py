"""The health probe, and the report that reads its log.

Nothing here runs a viewer. The probe's whole contract is that it reads
numbers out of other people's containers without ever raising, and the
report's whole contract is that it can tell a cache filling from a cache
leaking -- both of which are testable against dictionaries and a list of
made-up samples, and neither of which becomes truer for having a GL context
in the room.

The one thing worth stating about the report's verdicts: they compare the
*second half* of a run against the first. A viewer's first minute is caches
filling, which every run does and no run should be flagged for; a leak is
the one that is still going at the end. Every verdict test below is built to
say which of the two it is describing.
"""

from __future__ import annotations

import json
import math
import mmap
import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.recent import SEQUENCE_MEMORY
from vibestorm.viewer3d.health import (
    CONDITION_NAMES,
    GC_COUNTERS,
    LIMIT_SUFFIX,
    MIN_SAMPLES_FOR_TREND,
    MIN_SAMPLES_FOR_VERDICT,
    OBJECT_CENSUS_PREFIX,
    PROCESS_GAUGES,
    STEP_CONCENTRATION,
    TREND_SIGMA,
    Growth,
    HealthProbe,
    SoakLog,
    TypeCensus,
    _declared_limits,
    _fit,
    _rise_concentration,
    format_growth_report,
    freeze_static_heap,
    growth_report,
    machine_load_1m,
    pace_report,
    process_cpu_seconds,
    process_rss_bytes,
    read_soak_log,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _samples(name: str, values, *, step: float = 30.0, counters=()) -> list[dict]:
    return [
        {"elapsed_s": i * step, "frame": i * 100, name: value} for i, value in enumerate(values)
    ]


def _samples_with_gc(name: str, values, collections, *, step: float = 30.0) -> list[dict]:
    """Samples carrying the collector counter the viewer really records.

    `collections` is one running total per sample, the way `gc.get_stats()`
    reports it: it goes up on the sample the collector ran on.
    """
    return [
        {
            "elapsed_s": i * step,
            "frame": i * 100,
            name: float(value),
            "gc.auto_collections": float(runs),
        }
        for i, (value, runs) in enumerate(zip(values, collections, strict=True))
    ]


class ProcessGaugeTests(unittest.TestCase):
    """The four numbers every sample carries, whatever the caller asked for."""

    def test_rss_is_a_plausible_number_of_bytes(self) -> None:
        rss = process_rss_bytes()
        # A Python process with pygame imported is megabytes, not kilobytes and
        # not gigabytes. The point is to catch the unit being pages: the raw
        # /proc/self/statm field is a page count, and forgetting the 4096 gives
        # a number about four thousand times too small.
        assert rss > 4_000_000.0
        assert rss < 100_000_000_000.0

    def test_it_is_the_resident_field_and_not_the_virtual_one(self) -> None:
        # /proc/self/statm's first field is the whole address space and its
        # second is what is actually in RAM. Both are byte counts of a
        # plausible size, so no range check tells them apart -- and reading
        # the first would make the soak report a reservation as a leak and
        # miss a real one behind an allocator that had already reserved the
        # room. An anonymous mapping nobody touches separates them exactly:
        # half a gigabyte of address space, no resident pages.
        before = process_rss_bytes()
        buf = mmap.mmap(-1, 512 * 1024 * 1024)
        try:
            after = process_rss_bytes()
        finally:
            buf.close()
        self.assertLess(after - before, 64 * 1024 * 1024)

    def test_the_noisy_gauge_is_not_among_them(self) -> None:
        # gc.get_count() swings on every allocation, so it reports "growing"
        # in about half of all runs whatever the viewer is doing. One false
        # row per report is how a report stops being read.
        self.assertNotIn("proc.gc_tracked", PROCESS_GAUGES)

    def test_every_process_gauge_reads(self) -> None:
        for name, read in PROCESS_GAUGES.items():
            value = float(read())
            assert math.isfinite(value), name
            assert value >= 0.0, name


class ProbeSampleTests(unittest.TestCase):
    def test_a_sample_carries_every_declared_name(self) -> None:
        probe = HealthProbe(gauges={"a": lambda: 1.0}, counters={"b": lambda: 2.0})
        sample = probe.sample(elapsed_s=12.0, frame=7)
        assert sample["a"] == 1.0
        assert sample["b"] == 2.0
        assert sample["elapsed_s"] == 12.0
        assert sample["frame"] == 7
        for name in PROCESS_GAUGES:
            assert name in sample

    def test_a_gauge_that_raises_does_not_end_the_run(self) -> None:
        # The whole reason the probe exists is runs that last hours. One that
        # dies at minute three because a cache was briefly None has told you
        # nothing at all about hour four.
        def explode() -> float:
            raise RuntimeError("the cache was swapped out")

        probe = HealthProbe(gauges={"boom": explode, "fine": lambda: 3.0})
        sample = probe.sample(elapsed_s=0.0, frame=0)
        assert sample["boom"] is None
        assert sample["fine"] == 3.0

    def test_a_gauge_that_returns_nonsense_is_recorded_as_unreadable(self) -> None:
        probe = HealthProbe(gauges={"nan": lambda: float("nan"), "inf": lambda: float("inf")})
        sample = probe.sample(elapsed_s=0.0, frame=0)
        assert sample["nan"] is None
        assert sample["inf"] is None

    def test_a_length_is_read_live_not_captured(self) -> None:
        bag: dict[int, int] = {}
        probe = HealthProbe(gauges={"bag": lambda: len(bag)})
        assert probe.sample(elapsed_s=0.0, frame=0)["bag"] == 0.0
        bag[1] = 1
        bag[2] = 2
        assert probe.sample(elapsed_s=1.0, frame=1)["bag"] == 2.0

    def test_counter_names_are_kept_apart_from_gauges(self) -> None:
        probe = HealthProbe(gauges={"a": lambda: 0.0}, counters={"b": lambda: 0.0})
        assert probe.counter_names == frozenset({"b"})


class ProbeCadenceTests(unittest.TestCase):
    def test_the_first_sample_is_always_due(self) -> None:
        # Without a reading from before anything grew there is no baseline to
        # measure the growth against.
        assert HealthProbe(interval_s=600.0).due(0.0) is True

    def test_it_is_not_due_again_until_the_interval_has_passed(self) -> None:
        probe = HealthProbe(interval_s=30.0)
        probe.sample(elapsed_s=100.0, frame=0)
        assert probe.due(120.0) is False
        assert probe.due(129.9) is False
        assert probe.due(130.0) is True

    def test_sampling_is_what_resets_the_cadence(self) -> None:
        probe = HealthProbe(interval_s=30.0)
        assert probe.due(0.0) is True
        # Asking twice without sampling must not consume the turn: a caller
        # that checks and then decides not to sample would otherwise skip an
        # interval silently.
        assert probe.due(0.0) is True
        probe.sample(elapsed_s=0.0, frame=0)
        assert probe.due(0.0) is False


class SoakLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp_path = Path(self._dir.name)

    def test_a_line_is_on_disk_before_the_run_ends(self) -> None:
        # The interesting run is the one that dies. A log only readable after
        # a clean exit says nothing about the hour that ended in a traceback.
        path = self.tmp_path / "nested" / "run.jsonl"
        log = SoakLog(path)
        log.write({"elapsed_s": 0.0, "a": 1})
        assert json.loads(path.read_text().strip())["a"] == 1
        log.close()

    def test_a_torn_final_line_does_not_lose_the_rest(self) -> None:
        path = self.tmp_path / "run.jsonl"
        path.write_text('{"elapsed_s": 0, "a": 1}\n{"elapsed_s": 30, "a":\n')
        samples = read_soak_log(path)
        assert [s["a"] for s in samples] == [1]

    def test_blank_lines_are_skipped(self) -> None:
        path = self.tmp_path / "run.jsonl"
        path.write_text('\n{"elapsed_s": 0, "a": 1}\n\n')
        assert len(read_soak_log(path)) == 1

    def test_it_appends_rather_than_truncating(self) -> None:
        path = self.tmp_path / "run.jsonl"
        with SoakLog(path) as log:
            log.write({"a": 1})
        with SoakLog(path) as log:
            log.write({"a": 2})
        assert [s["a"] for s in read_soak_log(path)] == [1, 2]


class GrowthVerdictTests(unittest.TestCase):
    """A cache filling is not a leak; a cache still filling at the end is."""

    def test_a_flat_series_is_flat(self) -> None:
        report = growth_report(_samples("a", [10, 10, 10, 10, 10]))
        assert report[0].verdict == "flat"

    def test_a_cache_that_fills_and_stops_is_settled(self) -> None:
        report = growth_report(_samples("a", [0, 40, 80, 100, 100, 100]))
        assert report[0].verdict == "settled"
        self.assertAlmostEqual(report[0].late_rate_per_hour, 0.0)

    def test_a_cache_still_climbing_at_the_end_is_growing(self) -> None:
        report = growth_report(_samples("a", [0, 100, 200, 300, 400, 500]))
        assert report[0].verdict == "growing"
        # Six samples, so the second half runs from the fourth (90 s, 300)
        # to the last (150 s, 500): 200 over a minute, 12,000 an hour.
        self.assertAlmostEqual(report[0].late_rate_per_hour, 12000.0)

    def test_a_series_that_decelerates_hard_is_settling_not_growing(self) -> None:
        # 500, 500, 200, 100, 40: still slowing at the end, which is what
        # settling has to mean.
        report = growth_report(_samples("a", [0, 500, 1000, 1200, 1300, 1340]))
        assert report[0].verdict == "settling"

    def test_a_burst_and_then_a_steady_climb_is_growing(self) -> None:
        # The shape that caught this out, taken from a real soak: RSS grew
        # 148 MB in the first third of a two-hour run, 24 in the second and
        # 42 in the third. Against the start that is a tiny second half and
        # reads as settling; against the middle it is a rate that stopped
        # falling, which is a leak with a loud first minute in front of it.
        report = growth_report(_samples("a", [0, 1000, 1010, 1020, 1030, 1040]))
        assert report[0].verdict == "growing"

    def test_a_flat_rate_that_wobbles_downward_is_still_growing(self) -> None:
        # The canonical leak: a container filling at a steady rate. Steady
        # rates wobble, and the wobble is as likely to go down as up -- this
        # one loses five per cent over the last stretch. Reading "the rate
        # fell" as settling calls half of all leaks settled. Taken from the
        # same soak: 773, 681 then 668 entries an hour, which is a flat leak
        # with noise on it and was reported as settling for one commit.
        report = growth_report(_samples("a", [0, 50, 100, 200, 300, 395]))
        assert report[0].verdict == "growing"

    def test_and_the_same_climb_without_the_burst_is_growing_too(self) -> None:
        # The control: the verdict must come from the trend, not from the
        # presence of a spike to be unimpressed by.
        report = growth_report(_samples("a", [0, 10, 20, 30, 40, 50]))
        assert report[0].verdict == "growing"

    def test_a_run_too_short_to_show_a_trend_does_not_claim_one(self) -> None:
        # Five samples is past `too-short` and short of a trend. Of the two
        # words left, the report says the one that costs a second look rather
        # than the one that costs the finding.
        #
        # These numbers converge hard -- 12.5 a second over the middle
        # stretch and 1.7 over the last -- so the trend test would call them
        # settling if it were allowed to run. That is the point: a fixture
        # that reads the same either way proves nothing about the guard, and
        # the first version of this test used one.
        short = _samples("a", [0, 1000, 1500, 1750, 1800])
        assert len(short) < MIN_SAMPLES_FOR_TREND
        assert growth_report(short)[0].verdict == "growing"

    def test_and_the_same_shape_with_one_more_sample_does(self) -> None:
        # The control: nothing about the numbers makes them growing, only the
        # length of the run.
        report = growth_report(_samples("a", [0, 1000, 1500, 1750, 1800, 1810]))
        assert report[0].verdict == "settling"

    def test_a_series_that_shrinks_back_is_settled(self) -> None:
        # Eviction working is exactly this shape, and calling it a leak is how
        # a report gets ignored.
        report = growth_report(_samples("a", [0, 100, 200, 150, 100, 50]))
        assert report[0].verdict == "settled"

    def test_too_few_samples_gets_no_verdict(self) -> None:
        report = growth_report(_samples("a", [0, 10, 20]))
        assert len(_samples("a", [0, 10, 20])) < MIN_SAMPLES_FOR_VERDICT
        assert report[0].verdict == "too-short"

    def test_the_peak_is_kept_even_when_it_came_back_down(self) -> None:
        report = growth_report(_samples("a", [0, 900, 100, 100, 100]))
        assert report[0].peak == 900.0
        assert report[0].last == 100.0
        assert report[0].low == 0.0


class FitTests(unittest.TestCase):
    """The arithmetic itself, against a fit worked by hand.

    Everything else in this file reads a verdict, which is the slope and its
    error passed through a threshold -- so a five per cent error in the error
    changes no verdict in any fixture and no test notices. A mutation battery
    found exactly that: dividing the residuals by `n` instead of `n - 2`
    survived the whole file. This is the level the mistake lives at, so this
    is where it gets pinned.
    """

    #: t = 0..4, v = 1, 3, 2, 5, 4. Sxx = 10, Sxy = 8, so the slope is 0.8;
    #: the residuals are -0.4, 0.8, -1.0, 1.2, -0.6, summing squared to 3.6,
    #: so s^2 = 3.6 / 3 and the error is sqrt(0.12).
    POINTS = [(0.0, 1.0), (1.0, 3.0), (2.0, 2.0), (3.0, 5.0), (4.0, 4.0)]

    def test_the_slope_is_the_least_squares_one(self) -> None:
        self.assertAlmostEqual(_fit(self.POINTS)[0], 0.8, places=12)

    def test_the_error_carries_the_right_degrees_of_freedom(self) -> None:
        """Two parameters were fitted, so three of the five points are free."""
        self.assertAlmostEqual(_fit(self.POINTS)[1], (1.2 / 10.0) ** 0.5, places=12)
        # And not the biased form, which is what survived the battery.
        self.assertNotAlmostEqual(_fit(self.POINTS)[1], ((3.6 / 5.0) / 10.0) ** 0.5, places=3)

    def test_a_straight_line_has_no_error_at_all(self) -> None:
        slope, stderr = _fit([(float(i), 2.0 * i + 5.0) for i in range(6)])
        self.assertAlmostEqual(slope, 2.0, places=12)
        self.assertAlmostEqual(stderr, 0.0, places=12)

    def test_two_points_get_a_slope_and_no_opinion_about_it(self) -> None:
        """There is nothing left over to disagree with a line through two points."""
        assert _fit([(0.0, 0.0), (10.0, 5.0)]) == (0.5, 0.0)

    def test_one_point_is_no_slope_rather_than_a_division_by_zero(self) -> None:
        assert _fit([(3.0, 9.0)]) == (0.0, 0.0)

    def test_samples_that_all_landed_at_the_same_moment_do_not_divide_by_zero(self) -> None:
        assert _fit([(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)]) == (0.0, 0.0)


class SteppedTests(unittest.TestCase):
    """Two plateaus and a jump have a slope, and it is not a leak.

    The fit told a trend from noise and could not tell a trend from a step.
    Run 4 is the demonstration: `proc.rss_bytes` sat at 630,185,984 for eighty
    minutes, stepped once to 651,558,912, and sat there for the remaining
    twenty. A line through that shape climbs 31 MB an hour at nineteen sigma,
    so the report said `growing` and a reader would have gone hunting a leak
    that does not exist -- the step falls on the one sample where the load
    average hit 18.5, which was another process on a shared machine.

    Shape is what separates them, and it separates them cleanly. Half of a
    leak's rise takes about half its samples; half of a step's takes one or
    two. Every soak on record agrees -- see `STEP_CONCENTRATION`.
    """

    @staticmethod
    def _plateaus(count: int, *, low: float, high: float):
        """Flat, one jump at the two-thirds mark, flat again. Run 4's shape."""
        return [low if i < (2 * count) // 3 else high for i in range(count)]

    @staticmethod
    def _climb(count: int, *, low: float, high: float):
        """The same total gain, arriving a little at a time. A leak's shape."""
        step = (high - low) / (count - 1)
        return [low + step * i for i in range(count)]

    def test_a_step_is_not_called_a_leak(self) -> None:
        values = self._plateaus(240, low=630_185_984.0, high=651_558_912.0)
        assert growth_report(_samples("a", values))[0].verdict == "stepped"

    def test_the_same_gain_spread_out_is(self) -> None:
        """The control, and it has to be the same numbers or it proves nothing."""
        values = self._climb(240, low=630_185_984.0, high=651_558_912.0)
        assert growth_report(_samples("a", values))[0].verdict == "growing"

    def test_a_step_still_reports_the_rate_it_measured(self) -> None:
        """`stepped` changes the word, not the arithmetic.

        A reader who wants to know how big the step was still gets the number
        and its error; what changes is that the column no longer tells them it
        is a leak.
        """
        values = self._plateaus(240, low=630_185_984.0, high=651_558_912.0)
        row = growth_report(_samples("a", values))[0]
        self.assertGreater(row.late_rate_per_hour, 0.0)
        self.assertEqual(row.peak, 651_558_912.0)

    def test_a_flat_gauge_is_flat_and_not_stepped(self) -> None:
        """Nothing gained is not a step, however concentrated nothing is."""
        assert growth_report(_samples("a", [7.0] * 40))[0].verdict == "flat"

    def test_a_gauge_that_only_falls_is_settled_and_not_stepped(self) -> None:
        values = [100.0 - i for i in range(40)]
        assert growth_report(_samples("a", values))[0].verdict == "settled"

    def test_one_step_on_top_of_a_real_leak_is_still_a_leak(self) -> None:
        """The failure that would make this a way to lose findings.

        A leak does not stop being one because something else jolted the
        machine in the middle of it. The rise here is a steady climb *plus* a
        jump, and the climb spreads across every sample, so the concentration
        never gets near the cut.
        """
        values = self._climb(240, low=0.0, high=240_000.0)
        values = [v + (0.0 if i < 160 else 100_000.0) for i, v in enumerate(values)]
        assert growth_report(_samples("a", values))[0].verdict == "growing"


    def test_a_step_followed_by_a_drift_is_still_a_step(self) -> None:
        """Half the rise, not all of it -- because all of it is never early.

        This gauge jumps 100 and then drifts up by 1 a sample. Half the rise
        is the jump, and it lands in one interval out of 119. The *whole* rise
        is only complete at the last drifting sample, so a detector asking
        when the total arrived would answer "at the end" for every series that
        has any drift at all -- which is every real gauge. Half is the
        question that has an informative answer.
        """
        values = [100.0] * 140 + [200.0] * 20 + [200.0 + i for i in range(1, 81)]
        row = growth_report(_samples("a", values))[0]
        assert row.verdict == "stepped", row.verdict

    def test_a_jump_at_startup_does_not_excuse_a_leak_after_it(self) -> None:
        """Why the statistic reads the second half, like everything else here.

        A client allocates its caches once, early, and that one jump is larger
        than anything that follows. Measured end to end it is half the rise on
        its own, so the run reads `stepped` and the steady climb underneath it
        is filed as explained. It is not explained. The second half contains
        no jump and a 100-a-sample climb, and that is what the reader needs to
        be shown.
        """
        values = [0.0] * 60 + [1_000_000.0] * 60 + [1_000_000.0 + 100.0 * i for i in range(120)]
        assert growth_report(_samples("a", values))[0].verdict == "growing"

    def test_a_transient_spike_is_settled_not_stepped(self) -> None:
        """Order matters: the noise cut comes first, and this is why.

        A gauge that jumps 45 bytes for one sample and comes straight back has
        the most concentrated rise a series can have -- one interval out of
        119 -- and has gone precisely nowhere. Shape alone cannot tell that
        from a step that stayed. Significance can, and so it is asked first: a
        rise this size is inside the run's own scatter, which is what
        `settled` means. Checking the shape first would put a `stepped` row in
        front of a reader for a spike that had already come back.
        """
        values = [400_000.0] * 240
        values[200] = 400_045.0
        row = growth_report(_samples("a", values))[0]
        assert row.verdict == "settled", (row.verdict, row.late_rate_per_hour)


class RiseConcentrationTests(unittest.TestCase):
    """The statistic itself, against the runs it was measured on."""

    def test_a_step_concentrates_its_rise_into_almost_nothing(self) -> None:
        values = SteppedTests._plateaus(240, low=630_185_984.0, high=651_558_912.0)
        points = [(i * 30.0, v) for i, v in enumerate(values)]
        self.assertLessEqual(_rise_concentration(points), STEP_CONCENTRATION)

    def test_a_leak_spreads_it_across_the_run(self) -> None:
        values = SteppedTests._climb(240, low=0.0, high=400_000.0)
        points = [(i * 30.0, v) for i, v in enumerate(values)]
        # Measured at 19 to 26 per cent on the real leaking soaks; a clean
        # ramp sits at about a half, and either is far above the cut.
        self.assertGreater(_rise_concentration(points), STEP_CONCENTRATION * 3)

    def test_a_gauge_that_gained_nothing_is_not_infinitely_concentrated(self) -> None:
        """Dividing by a total of zero is the obvious way to write this wrong.

        A flat gauge would come out at 0.0 -- maximally step-shaped -- and
        every unchanging row in the report would be relabelled a step.
        """
        points = [(i * 30.0, 5.0) for i in range(40)]
        self.assertEqual(_rise_concentration(points), 1.0)

    def test_a_gauge_that_only_falls_is_the_same(self) -> None:
        points = [(i * 30.0, 100.0 - i) for i in range(40)]
        self.assertEqual(_rise_concentration(points), 1.0)


class SawtoothTests(unittest.TestCase):
    """A rate is a number the reader acts on, so it may not be the noise.

    Every shape here is drawn from a real soak, because the failure this class
    pins was found in one and not in a fixture: run 4 held a heap that did not
    gain a byte over its last twenty minutes -- `proc.rss_bytes` reading
    630,185,984 on sample after sample -- while the report called
    `proc.py_blocks` and `obj._total` leaks at 8,183 and 3,355 an hour. The
    rate was the last sample minus the middle one, so on a series that
    oscillates it reported which end of the swing the run happened to stop on.
    """

    @staticmethod
    def _sawtooth(count: int, *, low: float, high: float, period: int, phase: int = 0):
        """A cache filling and being evicted, over and over, going nowhere.

        This is what a healthy viewer heap looks like sample to sample. The
        run it is drawn from swung `obj._total` between 11,597 and 16,722 for
        twenty minutes with the total unchanged either side.
        """
        step = (high - low) / period
        return [low + step * ((i + phase) % period) for i in range(count)]

    def test_a_sawtooth_going_nowhere_is_not_called_a_leak(self) -> None:
        values = self._sawtooth(40, low=11_597.0, high=16_722.0, period=7)
        report = growth_report(_samples("a", values))
        assert report[0].verdict == "settled"

    def test_and_where_the_run_stopped_does_not_change_the_answer(self) -> None:
        """The sharpest statement of the bug that prompted this.

        The same heap, sampled from seven different starting points in its
        swing: seven runs of a viewer that is doing nothing wrong. A verdict
        that depends on which of them you happened to record is not measuring
        the heap.
        """
        verdicts = {
            phase: growth_report(
                _samples(
                    "a",
                    self._sawtooth(40, low=11_597.0, high=16_722.0, period=7, phase=phase),
                )
            )[0].verdict
            for phase in range(7)
        }
        assert set(verdicts.values()) == {"settled"}, verdicts

    def test_the_rule_this_replaced_did_depend_on_it(self) -> None:
        """The control: the fixture has to be one the fix was needed for.

        Two points chosen by where the run stopped, which is what the rate
        used to be. Across the same seven phases it swings from a fall of
        nine thousand an hour to a climb of twenty-six thousand, through a
        heap that gained nothing in any of them.
        """
        rates = []
        for phase in range(7):
            values = self._sawtooth(40, low=11_597.0, high=16_722.0, period=7, phase=phase)
            points = [(i * 30.0, v) for i, v in enumerate(values)]
            mid = len(points) // 2
            rates.append(
                (points[-1][1] - points[mid][1]) * 3600.0 / (points[-1][0] - points[mid][0])
            )
        self.assertLess(min(rates), -5_000.0)
        self.assertGreater(max(rates), 5_000.0)

    def test_a_real_leak_under_the_same_noise_is_still_growing(self) -> None:
        """The other control, and the one that matters.

        Suppressing noise is easy; suppressing noise without suppressing the
        signal is the job. This is the same sawtooth with the leak that
        prompted all of this laid under it -- run 3 gained 64,263 `obj._total`
        an hour for two hours -- and the swing is larger than an hour's worth
        of the leak, so nothing about it is visible sample to sample.
        """
        wobble = self._sawtooth(40, low=0.0, high=5_125.0, period=7)
        values = [v + 64_263.0 * (i * 30.0) / 3600.0 for i, v in enumerate(wobble)]
        row = growth_report(_samples("a", values))[0]
        assert row.verdict == "growing"
        self.assertGreater(row.late_rate_per_hour, TREND_SIGMA * row.late_rate_stderr_per_hour)
        # And the rate it reports is the leak, not the leak plus the swing.
        self.assertAlmostEqual(row.late_rate_per_hour, 64_263.0, delta=8_000.0)

    def test_and_that_one_is_found_from_every_phase_too(self) -> None:
        for phase in range(7):
            wobble = self._sawtooth(40, low=0.0, high=5_125.0, period=7, phase=phase)
            values = [v + 64_263.0 * (i * 30.0) / 3600.0 for i, v in enumerate(wobble)]
            row = growth_report(_samples("a", values))[0]
            assert row.verdict == "growing", (phase, row.late_rate_per_hour)

    def test_a_full_run_finds_a_leak_a_fifth_the_size_of_the_swing(self) -> None:
        """Where the sensitivity actually is, measured rather than hoped for.

        Two hours at the default cadence -- 240 samples, which is what run 3
        was -- against a swing of 5,125. A climb of 1,000 an hour is a fifth
        of one swing and invisible sample to sample, and the fit finds it.
        """
        wobble = self._sawtooth(240, low=0.0, high=5_125.0, period=7)
        values = [v + 1_000.0 * (i * 30.0) / 3600.0 for i, v in enumerate(wobble)]
        row = growth_report(_samples("a", values))[0]
        assert row.verdict == "growing"
        self.assertAlmostEqual(row.late_rate_per_hour, 1_000.0, delta=200.0)

    def test_and_a_short_run_says_settled_rather_than_guessing(self) -> None:
        """The same leak in a twenty-minute run, which cannot see it.

        This is not a shortfall to be tuned away. Through that much swing a
        thousand an hour is a fifth of a standard error, and a rule that
        called it a leak would be calling every phase of every healthy heap
        one too -- the seven-phase test above is the same numbers with the
        leak set to zero. The honest reading is `settled` with an error of
        7,174 printed beside it, which tells a reader the run was too short
        to answer rather than answering wrongly.
        """
        wobble = self._sawtooth(40, low=0.0, high=5_125.0, period=7)
        values = [v + 1_000.0 * (i * 30.0) / 3600.0 for i, v in enumerate(wobble)]
        row = growth_report(_samples("a", values))[0]
        assert row.verdict == "settled"
        self.assertGreater(row.late_rate_stderr_per_hour, 1_000.0)

    def test_a_series_that_is_only_noise_gets_no_trend_claimed(self) -> None:
        """No periodicity to be caught by, and still nothing to report."""
        import random

        rng = random.Random(20260907)
        values = [400_000.0 + rng.uniform(-4_000.0, 4_000.0) for _ in range(60)]
        assert growth_report(_samples("a", values))[0].verdict == "settled"


class CounterVerdictTests(unittest.TestCase):
    """A counter that stops is its own failure, and nothing else would notice."""

    def test_a_rising_counter_is_rising(self) -> None:
        report = growth_report(_samples("udp.rx", [0, 100, 200, 300, 400]), counters=["udp.rx"])
        assert report[0].kind == "counter"
        assert report[0].verdict == "rising"

    def test_a_counter_that_stops_is_stalled(self) -> None:
        # The session went deaf an hour in. Nothing raises, no frame drops,
        # and every gauge in the report reads perfectly flat -- which is
        # indistinguishable from a quiet region unless the counters are read.
        report = growth_report(_samples("udp.rx", [0, 100, 200, 200, 200]), counters=["udp.rx"])
        assert report[0].verdict == "stalled"

    def test_a_rising_counter_is_not_ranked_as_a_leak(self) -> None:
        samples = [
            {"elapsed_s": i * 30.0, "frame": i, "udp.rx": i * 1_000_000, "cache": i * 2}
            for i in range(6)
        ]
        report = growth_report(samples, counters=["udp.rx"])
        # The counter climbs half a million times faster, and still the cache
        # is the first row: sorting on magnitude alone buries every finding
        # under the packet count.
        assert [g.name for g in report] == ["cache", "udp.rx"]

    def test_two_points_are_enough_because_a_counter_cannot_come_back_down(self) -> None:
        """Why the counter branch keeps the rule the gauge branch lost.

        A gauge's verdict now comes off a least-squares fit, because a gauge
        oscillates and two points read the phase of the swing. A counter does
        not oscillate: it is non-decreasing, so "higher at the end than in the
        middle" and "the fitted slope is positive" are the same statement, and
        the cheap one is kept.

        That is an argument, so it is checked rather than believed. Five
        hundred counter shapes -- bursts, long stalls, single late jumps, dead
        flat -- and the two rules have never disagreed. When they stop
        agreeing, something is emitting a counter that falls, and *that* is
        the finding rather than this line.
        """
        import random

        rng = random.Random(20260907)
        for _ in range(500):
            values = [0.0]
            for _ in range(rng.randint(3, 39)):
                values.append(values[-1] + rng.choice((0.0, 0.0, 1.0, 5.0, 100.0)))
            points = [(i * 30.0, v) for i, v in enumerate(values)]
            mid = len(values) // 2
            two_point = values[-1] - values[mid] <= 0.0
            fitted = _fit(points[len(points) // 2 :])[0] <= 0.0
            assert two_point == fitted, values



class ReportShapeTests(unittest.TestCase):
    def test_growth_is_ranked_by_the_late_rate_not_the_total(self) -> None:
        samples = [
            {
                "elapsed_s": i * 30.0,
                "frame": i,
                "filled_once": v_once,
                "still_going": v_going,
            }
            for i, (v_once, v_going) in enumerate(
                zip(
                    [0, 5000, 5000, 5000, 5000, 5000],
                    [0, 10, 20, 30, 40, 50],
                    strict=True,
                )
            )
        ]
        report = growth_report(samples)
        # `filled_once` changed a hundred times as much overall. It is the
        # viewer working. `still_going` is the bug, and it has to come first.
        assert report[0].name == "still_going"
        assert report[0].delta == 50.0
        assert report[1].delta == 5000.0

    def test_a_name_that_appears_only_later_is_still_reported(self) -> None:
        samples = [
            {"elapsed_s": 0.0, "frame": 0, "a": 1},
            {"elapsed_s": 30.0, "frame": 1, "a": 2, "b": 9},
        ]
        names = {g.name for g in growth_report(samples)}
        assert names == {"a", "b"}

    def test_unreadable_points_are_skipped_rather_than_read_as_zero(self) -> None:
        # A gauge that could not be read for one sample must not put a zero in
        # the middle of the series: that reads as a container being emptied
        # and refilled, which is a very different story from a missing point.
        samples = [
            {"elapsed_s": 0.0, "frame": 0, "a": 100},
            {"elapsed_s": 30.0, "frame": 1, "a": None},
            {"elapsed_s": 60.0, "frame": 2, "a": 102},
        ]
        report = growth_report(samples)
        assert report[0].samples == 2
        assert report[0].low == 100.0

    def test_a_name_that_was_never_readable_is_left_out(self) -> None:
        samples = [{"elapsed_s": float(i), "frame": i, "a": None} for i in range(5)]
        assert growth_report(samples) == []

    def test_booleans_are_not_counted_as_numbers(self) -> None:
        samples = [{"elapsed_s": float(i), "frame": i, "flag": True} for i in range(5)]
        assert growth_report(samples) == []

    def test_elapsed_and_frame_are_not_series(self) -> None:
        names = {g.name for g in growth_report(_samples("a", [1, 2, 3, 4]))}
        assert names == {"a"}


class PaceTests(unittest.TestCase):
    """What the loop did, as against what it was holding on to.

    A viewer that is fine for an hour and drawing at five frames a second by
    the fourth has failed in the way this whole file exists to catch, and not
    one gauge in the report says so: every container can be perfectly stable
    while the loop grinds to a halt.
    """

    def _run(self, fps_pairs, *, step: float = 30.0):
        samples = []
        frame = 0
        for i, fps in enumerate(fps_pairs):
            samples.append({"elapsed_s": i * step, "frame": frame})
            frame += int(fps * step)
        return samples

    def test_a_steady_run_reads_the_same_at_both_ends(self) -> None:
        pace = pace_report(self._run([30.0] * 8))
        assert pace is not None
        self.assertAlmostEqual(pace.fps_first_half, 30.0, places=1)
        self.assertAlmostEqual(pace.fps_second_half, 30.0, places=1)
        self.assertAlmostEqual(pace.slowed_by, 0.0, places=2)

    def test_a_run_that_grinds_down_says_so(self) -> None:
        # 30 fps for the first half, 10 for the second.
        pace = pace_report(self._run([30.0] * 4 + [10.0] * 4))
        assert pace is not None
        self.assertGreater(pace.fps_first_half, pace.fps_second_half)
        self.assertGreater(pace.slowed_by, 0.5)

    def test_a_run_that_speeds_up_reads_as_negative(self) -> None:
        pace = pace_report(self._run([10.0] * 4 + [30.0] * 4))
        assert pace is not None
        self.assertLess(pace.slowed_by, 0.0)

    def test_a_stall_shows_as_a_gap_a_mean_would_hide(self) -> None:
        # Samples are taken from inside the frame loop, so a gap far longer
        # than the interval is the loop having stopped. Averaged over an hour
        # a ten-second freeze is invisible.
        samples = [
            {"elapsed_s": 0.0, "frame": 0},
            {"elapsed_s": 30.0, "frame": 900},
            {"elapsed_s": 75.0, "frame": 1000},
            {"elapsed_s": 105.0, "frame": 1900},
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertAlmostEqual(pace.longest_gap_s, 45.0)
        self.assertAlmostEqual(pace.shortest_gap_s, 30.0)

    def test_the_cadence_comes_off_the_samples_not_out_of_the_gaps(self) -> None:
        # The shortest gap is not the cadence. The last sample is written from
        # the shutdown path, moments after a scheduled one, so on a real
        # two-hour run at thirty seconds the shortest gap was five -- and the
        # header said "asked for every 5 s", which is a report describing its
        # own artefact as the thing it was told to do.
        samples = [
            {"elapsed_s": 0.0, "frame": 0, "soak_interval_s": 30.0},
            {"elapsed_s": 30.0, "frame": 180, "soak_interval_s": 30.0},
            {"elapsed_s": 60.0, "frame": 360, "soak_interval_s": 30.0},
            {"elapsed_s": 65.0, "frame": 390, "soak_interval_s": 30.0},
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertAlmostEqual(pace.shortest_gap_s, 5.0)
        self.assertAlmostEqual(pace.interval_s, 30.0)

    def test_a_log_that_never_recorded_it_says_nothing_rather_than_guessing(self) -> None:
        # Logs written before the probe recorded the cadence still read, and
        # the one thing they must not do is produce a plausible number.
        samples = [
            {"elapsed_s": 0.0, "frame": 0},
            {"elapsed_s": 30.0, "frame": 180},
            {"elapsed_s": 60.0, "frame": 360},
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertIsNone(pace.interval_s)

    def test_the_probe_records_what_it_was_asked_for(self) -> None:
        probe = HealthProbe(interval_s=30.0)
        sample = probe.sample(elapsed_s=0.0, frame=1)
        self.assertAlmostEqual(sample["soak_interval_s"], 30.0)

    def test_too_few_samples_is_no_answer_rather_than_a_wrong_one(self) -> None:
        self.assertIsNone(pace_report([{"elapsed_s": 0.0, "frame": 0}]))

    def test_a_run_with_no_time_between_samples_does_not_divide_by_zero(self) -> None:
        samples = [{"elapsed_s": 0.0, "frame": i} for i in range(5)]
        pace = pace_report(samples)
        assert pace is not None
        self.assertEqual(pace.fps_first_half, 0.0)
        self.assertEqual(pace.slowed_by, 0.0)

    def test_a_sample_missing_its_frame_number_is_skipped_not_fatal(self) -> None:
        # Logs are appended to and concatenated, and this file has already
        # renamed series once. A line that is valid JSON but has not got the
        # fields must cost that line, not the whole report.
        samples = [
            {"elapsed_s": 0.0, "frame": 0},
            {"elapsed_s": 30.0},
            {"elapsed_s": 60.0, "frame": None},
            {"elapsed_s": 90.0, "frame": 900},
            {"elapsed_s": 120.0, "frame": 1200},
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertEqual(pace.frames, 1200)

    def test_the_frame_count_is_the_run_and_not_the_last_number(self) -> None:
        # A log appended to across two runs starts its frame count again;
        # taking the last value alone would report the second run's frames as
        # the whole thing.
        samples = [
            {"elapsed_s": 0.0, "frame": 1000},
            {"elapsed_s": 30.0, "frame": 1900},
            {"elapsed_s": 60.0, "frame": 2800},
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertEqual(pace.frames, 1800)


class FormatTests(unittest.TestCase):
    def test_every_row_is_one_line_naming_its_verdict(self) -> None:
        report = growth_report(_samples("cache.size", [0, 10, 20, 30, 40]))
        text = format_growth_report(report)
        lines = text.splitlines()
        assert lines[0].startswith("name")
        assert "cache.size" in lines[1]
        assert "growing" in lines[1]

    def test_the_limit_trims_rows_and_keeps_the_header(self) -> None:
        samples = [
            {"elapsed_s": i * 30.0, "frame": i, "a": i, "b": i * 2, "c": i * 3} for i in range(5)
        ]
        text = format_growth_report(growth_report(samples), limit=1)
        assert len(text.splitlines()) == 2

    def test_an_empty_report_formats_without_raising(self) -> None:
        assert format_growth_report([]).startswith("name")

    def test_large_numbers_stay_readable(self) -> None:
        row = Growth(
            name="rss",
            kind="gauge",
            samples=5,
            first=0.0,
            last=1_234_567_890.0,
            low=0.0,
            peak=1_234_567_890.0,
            late_rate_per_hour=1.5,
            late_rate_stderr_per_hour=0.25,
            verdict="growing",
        )
        text = format_growth_report([row])
        assert "1,234,567,890" in text
        assert "1.50" in text

    def test_a_rate_is_printed_beside_its_error(self) -> None:
        """Neither number means anything without the other. See `_fit`."""
        row = Growth(
            name="blocks",
            kind="gauge",
            samples=40,
            first=0.0,
            last=100.0,
            low=0.0,
            peak=100.0,
            late_rate_per_hour=1_737.0,
            late_rate_stderr_per_hour=2_041.0,
            verdict="steady",
        )
        header, line = format_growth_report([row]).splitlines()
        assert "+/-" in header
        assert "1,737" in line and "2,041" in line
        # And in that order, so the error reads as the error and not as a
        # second rate.
        assert line.index("1,737") < line.index("2,041")


class GaugeWiringTests(unittest.TestCase):
    """Every gauge the viewer declares must actually find its container.

    This is the test the whole `_MISSING` guard exists for. A gauge naming a
    cache that has since been renamed would otherwise read zero on every
    sample -- perfectly flat, which is indistinguishable in the report from a
    container that never grew, so a four-hour run would come back clean
    because it was blind. Here the pieces are all really built, so an
    unreadable gauge can only mean the name is wrong.
    """

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    def _hud(self):
        """A real HUD, because a `None` one would read as zero everywhere.

        Which is the exact blindness the rest of this class exists to rule
        out: every HUD gauge would be flat at nothing for the whole run and
        the report would call the viewer clean.
        """
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        from vibestorm.viewer3d.hud import HUD

        pygame.init()
        pygame.display.set_mode((800, 600))
        self.addCleanup(pygame.quit)
        return HUD((800, 600), on_chat_submit=lambda _text: None)

    def _probe_and_parts(self):
        from vibestorm.udp.dispatch import MessageDispatcher
        from vibestorm.viewer3d.app import build_health_probe
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        # No GL context: the renderer allocates its GL resources lazily, and
        # every cache this watches is a plain dict built in __init__.
        renderer = PerspectiveRenderer(Camera3D())
        session = _live_session(MessageDispatcher.from_repo_root(REPO_ROOT))
        client = _FakeClient(session)
        probe = build_health_probe(
            scene, renderer, client, self._hud(), interval_s=1.0
        )
        return probe, scene, session

    def test_no_gauge_is_unreadable_against_a_built_viewer(self) -> None:
        probe, _scene, _session = self._probe_and_parts()
        sample = probe.sample(elapsed_s=0.0, frame=0)
        unreadable = sorted(k for k, v in sample.items() if v is None)
        self.assertEqual(unreadable, [])

    def test_every_declared_name_reaches_the_log(self) -> None:
        probe, _scene, _session = self._probe_and_parts()
        sample = probe.sample(elapsed_s=0.0, frame=0)
        for name in list(probe.gauges) + list(probe.counters):
            self.assertIn(name, sample)

    def test_a_probe_before_login_reads_zero_rather_than_failing(self) -> None:
        # There is no circuit and no world view until login lands, and the
        # viewer draws frames in that window. A probe that goes unreadable
        # there loses the baseline the whole report is measured against.
        from vibestorm.viewer3d.app import build_health_probe
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        probe = build_health_probe(
            Scene(),
            PerspectiveRenderer(Camera3D()),
            _FakeClient(None),
            self._hud(),
            interval_s=1.0,
        )
        sample = probe.sample(elapsed_s=0.0, frame=0)
        self.assertEqual(sorted(k for k, v in sample.items() if v is None), [])
        self.assertEqual(sample["udp.seen_sequences"], 0.0)
        self.assertEqual(sample["world.objects"], 0.0)

    def test_a_renamed_cache_is_unreadable_rather_than_flat_zero(self) -> None:
        # The failure this file is guarding against, stated directly.
        from vibestorm.viewer3d.app import _len_of

        class Renderer:
            pass

        gauge = _len_of(lambda: Renderer(), "_a_cache_nobody_kept")
        with self.assertRaises(AttributeError):
            gauge()
        # And through the probe, it lands as unreadable -- not as a zero.
        probe = HealthProbe(gauges={"gl.gone": gauge})
        self.assertIsNone(probe.sample(elapsed_s=0.0, frame=0)["gl.gone"])

    def test_a_renamed_counter_is_unreadable_too(self) -> None:
        # The same trap on the counter side, and worse there: a counter that
        # reads zero for the whole run is reported as "stalled", which is a
        # loud and completely wrong finding about the session going deaf.
        from vibestorm.viewer3d.app import _int_of

        class Session:
            pass

        counter = _int_of(lambda: Session(), "a_counter_nobody_kept")
        with self.assertRaises(AttributeError):
            counter()
        probe = HealthProbe(counters={"udp.gone": counter})
        self.assertIsNone(probe.sample(elapsed_s=0.0, frame=0)["udp.gone"])

    def test_an_absent_session_still_reads_zero_from_a_counter(self) -> None:
        from vibestorm.viewer3d.app import _int_of

        self.assertEqual(_int_of(lambda: None, "anything")(), 0.0)

    def test_the_gauges_follow_the_containers_they_name(self) -> None:
        probe, _scene, session = self._probe_and_parts()
        before = probe.sample(elapsed_s=0.0, frame=0)
        session.seen_reliable_sequences.update({1, 2, 3})
        session.fetched_assets[UUID(int=1)] = b"x" * 100
        after = probe.sample(elapsed_s=1.0, frame=1)
        self.assertEqual(before["udp.seen_sequences"], 0.0)
        self.assertEqual(after["udp.seen_sequences"], 3.0)
        self.assertEqual(after["asset.fetched"], 1.0)
        self.assertEqual(after["asset.fetched_bytes"], 100.0)

    def test_the_sequence_window_logs_its_own_ceiling(self) -> None:
        """The `.limit` row the `bounded` verdict is read from.

        Without it in the log every run reports `udp.seen_sequences` as
        `growing` -- true, useless, and the way a report stops being read.
        The value has to come from the window itself rather than be written
        out here, or a window resized in `recent.py` leaves a stale ceiling
        in the log that the report would then hold the client to.
        """
        probe, _scene, session = self._probe_and_parts()
        sample = probe.sample(elapsed_s=0.0, frame=0)
        self.assertIn("udp.seen_sequences" + LIMIT_SUFFIX, sample)
        self.assertEqual(
            sample["udp.seen_sequences" + LIMIT_SUFFIX],
            float(session.seen_reliable_sequences.capacity),
        )

    def test_the_ceiling_is_above_what_the_window_can_hold(self) -> None:
        """The two halves of the claim meeting: a window filled past its own
        size, measured through the gauge the report reads."""
        probe, _scene, session = self._probe_and_parts()
        session.seen_reliable_sequences.update(range(SEQUENCE_MEMORY * 3))
        sample = probe.sample(elapsed_s=1.0, frame=1)
        self.assertLessEqual(
            sample["udp.seen_sequences"], sample["udp.seen_sequences" + LIMIT_SUFFIX]
        )
        self.assertGreater(sample["udp.seen_sequences"], 0.0)

    def test_the_repeat_counters_are_not_each_other(self) -> None:
        """Two counters on the same object that both exist and both read as
        numbers: swapping the pair passes every other check in this class.
        The only thing that can tell them apart is a scene driven through a
        pattern whose answer is known."""
        from vibestorm.world.models import WorldView

        probe, scene, _session = self._probe_and_parts()
        view = WorldView()
        scene.refresh_from_world_view(view)  # a build
        scene.refresh_from_world_view(view)  # a repeat
        scene.refresh_from_world_view(view)  # another repeat

        sample = probe.sample(elapsed_s=0.0, frame=0)

        self.assertEqual(sample["scene.repeat_frames"], 2.0)
        self.assertEqual(sample["scene.rebuilt_frames"], 1.0)

    def test_the_hud_gauges_watch_this_hud_and_not_nothing(self) -> None:
        """A gauge aimed at `None` is the `_MISSING` trap by another road.

        `_len_of` answers zero for an owner that is not there yet, which is
        right -- there is no circuit before login. But it means a gauge
        pointed at nothing at all reads zero for the whole run and looks
        exactly like a container that never grew. Nothing can tell the two
        apart from the log, so it has to be told apart here: fill the thing
        the gauge names and the gauge has to move.
        """
        from vibestorm.viewer3d.app import build_health_probe
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        hud = self._hud()
        probe = build_health_probe(
            Scene(), PerspectiveRenderer(Camera3D()), _FakeClient(None), hud, interval_s=1.0
        )
        before = probe.sample(elapsed_s=0.0, frame=0)
        hud._diagnostics_wrap_cache[("a line", 100)] = ["a line"]
        after = probe.sample(elapsed_s=1.0, frame=1)
        self.assertEqual(before["hud.wrap_cache"], 0.0)
        self.assertEqual(after["hud.wrap_cache"], 1.0)
        # And a HUD that is really there has widgets in it.
        self.assertGreater(after["hud.ui_elements"], 0.0)

    def test_the_two_ack_channels_are_not_the_same_number(self) -> None:
        # Acks arrive as a `PacketAck` message or appended to any packet.
        # Two gauges reading one field would make a circuit acked entirely
        # one way look like one acked both, or silent.
        probe, _scene, session = self._probe_and_parts()
        session.packet_acks_received = 3
        session.appended_acks_received = 7
        sample = probe.sample(elapsed_s=0.0, frame=0)
        self.assertEqual(sample["udp.packet_acks"], 3.0)
        self.assertEqual(sample["udp.appended_acks"], 7.0)


class _FakeClient:
    """The two things `build_health_probe` asks a WorldClient for."""

    def __init__(self, session) -> None:
        self._session = session

    @property
    def current(self):
        return self._session

    def world_view(self):
        return None if self._session is None else self._session.world_view


def _live_session(dispatcher):
    from vibestorm.login.models import LoginBootstrap
    from vibestorm.udp.session import LiveCircuitSession

    bootstrap = LoginBootstrap(
        agent_id=UUID(int=1),
        session_id=UUID(int=2),
        secure_session_id=UUID(int=3),
        circuit_code=0x12345678,
        sim_ip="127.0.0.1",
        sim_port=9000,
        seed_capability="http://127.0.0.1:9000/caps/seed",
        region_x=256000,
        region_y=256000,
        message="ok",
    )
    return LiveCircuitSession(bootstrap, dispatcher)


class CutShortTests(unittest.TestCase):
    """A soak that dies early reads exactly like a short soak.

    Which is how ninety minutes went missing on 2026-09-07: logging a probe
    in as the same avatar closed the running soak's circuit, the viewer shut
    down cleanly at thirteen minutes of a two-hour run, and the report said
    nothing whatsoever about it -- every verdict computed happily over an
    eighth of the data that was asked for.
    """

    def _samples(self, *, span_s: float, run_seconds: float | None) -> list[dict]:
        out = []
        elapsed = 0.0
        frame = 0
        while elapsed <= span_s:
            sample = {"elapsed_s": elapsed, "frame": frame, "soak_interval_s": 30.0}
            if run_seconds is not None:
                sample["soak_run_seconds"] = run_seconds
            out.append(sample)
            elapsed += 30.0
            frame += 180
        return out

    def test_a_run_that_stopped_early_says_so(self) -> None:
        pace = pace_report(self._samples(span_s=780.0, run_seconds=7200.0))
        assert pace is not None
        self.assertTrue(pace.cut_short)

    def test_a_run_that_went_the_distance_does_not(self) -> None:
        pace = pace_report(self._samples(span_s=7200.0, run_seconds=7200.0))
        assert pace is not None
        self.assertFalse(pace.cut_short)

    def test_landing_one_sample_short_is_not_being_cut_short(self) -> None:
        # The last sample lands wherever the loop happened to be, so the
        # tolerance is one interval. Without it every completed run in the
        # world reports as truncated and the warning stops meaning anything.
        pace = pace_report(self._samples(span_s=7180.0, run_seconds=7200.0))
        assert pace is not None
        self.assertFalse(pace.cut_short)

    def test_a_run_that_never_said_how_long_it_meant_to_be_claims_nothing(self) -> None:
        pace = pace_report(self._samples(span_s=780.0, run_seconds=None))
        assert pace is not None
        self.assertIsNone(pace.run_seconds)
        self.assertFalse(pace.cut_short)

    def test_a_run_the_machine_stopped_scheduling_says_so(self) -> None:
        """Run 5, on 2026-09-08: forty-five minutes at exactly 30.0 s, then a
        single gap of 9,548 s while the machine's load went from 9 to 356.
        Every gauge kept its shape, so every verdict read as normal."""
        samples = self._samples(span_s=2700.0, run_seconds=7200.0)
        last = samples[-1]
        samples.append(
            {**last, "elapsed_s": last["elapsed_s"] + 9548.3, "frame": last["frame"] + 518}
        )
        pace = pace_report(samples)
        assert pace is not None
        self.assertTrue(pace.starved)

    def test_a_run_that_kept_its_interval_is_not_starved(self) -> None:
        """The control. Without it the check could be reading the *length* of
        the run, which every long soak would trip."""
        pace = pace_report(self._samples(span_s=7200.0, run_seconds=7200.0))
        assert pace is not None
        self.assertFalse(pace.starved)

    def test_ordinary_jitter_is_not_starvation(self) -> None:
        """A hitch of three intervals is a hitch. Calling it starvation would
        make the banner appear on runs worth reading, which is the failure
        mode of every warning that fires too easily."""
        samples = self._samples(span_s=2700.0, run_seconds=7200.0)
        last = samples[-1]
        samples.append(
            {**last, "elapsed_s": last["elapsed_s"] + 90.0, "frame": last["frame"] + 900}
        )
        pace = pace_report(samples)
        assert pace is not None
        self.assertFalse(pace.starved)

    def test_exactly_the_factor_is_not_yet_starvation(self) -> None:
        """The boundary, pinned because the factor is a judgement call and a
        judgement call with a fuzzy edge gets re-litigated. Four times the
        interval is the largest gap a run may have and still be read."""
        samples = self._samples(span_s=2700.0, run_seconds=7200.0)
        last = samples[-1]
        samples.append(
            {**last, "elapsed_s": last["elapsed_s"] + 120.0, "frame": last["frame"] + 1200}
        )
        pace = pace_report(samples)
        assert pace is not None
        self.assertEqual(pace.longest_gap_s, 120.0)
        self.assertFalse(pace.starved)

    def test_a_log_that_never_said_its_interval_claims_nothing(self) -> None:
        """The gap alone means nothing without the interval it is measured
        against: a run sampling every ten minutes is not starved."""
        samples = [
            {"elapsed_s": e, "frame": f}
            for e, f in ((0.0, 0), (600.0, 1000), (1200.0, 2000), (12000.0, 2100))
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertIsNone(pace.interval_s)
        self.assertFalse(pace.starved)

    def test_the_probe_records_the_length_it_was_asked_for(self) -> None:
        probe = HealthProbe(interval_s=30.0, run_seconds=7200.0)
        self.assertEqual(probe.sample(elapsed_s=0.0, frame=1)["soak_run_seconds"], 7200.0)

    def test_and_records_nothing_when_it_was_not_asked_for_a_length(self) -> None:
        # `--run-seconds` is optional; a run with no end is not a truncated one.
        probe = HealthProbe(interval_s=30.0)
        self.assertNotIn("soak_run_seconds", probe.sample(elapsed_s=0.0, frame=1))


class _Alpha:
    pass


class _Beta:
    pass


#: The census qualifies a name with its module, and this file's module name
#: depends on how the suite was invoked. Ask the code rather than guess.
ALPHA = f"{_Alpha.__module__}._Alpha"
BETA = f"{_Beta.__module__}._Beta"


def _census_of(objects, **kwargs) -> TypeCensus:
    """A census over a fixed list, so the heap the test walks is the test's."""
    return TypeCensus(objects=lambda: list(objects), **kwargs)


class TypeCensusTests(unittest.TestCase):
    """The instrument for the leak nobody has a gauge for.

    Every named gauge read `settled` over two hours while RSS climbed forty-
    five megabytes an hour. Adding gauges cannot find a thing nobody has
    thought of; counting every live object by type can. What these tests hold
    it to is the two properties that make the record readable afterwards:
    the counts are the real counts, and a name once reported never disappears
    from the series.
    """

    def test_counts_are_the_counts(self):
        census = _census_of([_Alpha(), _Alpha(), _Beta()])
        self.assertEqual(census.counts()[ALPHA], 2)
        self.assertEqual(census.counts()[BETA], 1)

    def test_names_carry_their_module(self):
        """Two classes called `Node` in two modules are two leaks or none.

        A bare `__name__` merges them, and a merged series can climb while
        both halves sit still -- or hide one climbing inside the other.
        """
        census = _census_of([_Alpha()])
        self.assertIn(ALPHA, census.counts())
        self.assertNotIn("_Alpha", census.counts())

    def test_builtins_are_not_qualified(self):
        census = _census_of([{}, {}])
        self.assertEqual(census.counts()["dict"], 2)

    def test_reading_is_prefixed_and_numeric(self):
        reading = _census_of([_Alpha()])()
        self.assertTrue(all(name.startswith(OBJECT_CENSUS_PREFIX) for name in reading))
        self.assertEqual(reading[OBJECT_CENSUS_PREFIX + ALPHA], 1.0)

    def test_totals_are_reported(self):
        reading = _census_of([_Alpha(), _Alpha(), _Beta()])()
        self.assertEqual(reading[f"{OBJECT_CENSUS_PREFIX}_total"], 3.0)
        self.assertEqual(reading[f"{OBJECT_CENSUS_PREFIX}_types"], 2.0)

    def test_a_name_once_reported_keeps_being_reported(self):
        """The leak is the type that is *not* in the first top forty.

        Which makes the naive instrument -- report the current top N -- useless
        for exactly the case it was built for: the series starts halfway
        through the run, so there is nothing to compare the end against.
        """
        heap = [_Alpha(), _Alpha(), _Alpha()]
        census = _census_of(heap, top=1)
        first = census()
        self.assertIn(OBJECT_CENSUS_PREFIX + ALPHA, first)

        heap[:] = [_Beta()] * 9
        second = census()
        self.assertEqual(
            second[OBJECT_CENSUS_PREFIX + ALPHA], 0.0
        )
        self.assertEqual(
            second[OBJECT_CENSUS_PREFIX + BETA], 9.0
        )

    def test_only_the_top_types_are_followed(self):
        """The tail of a real histogram is thousands of names with two objects
        apiece, and the leak is not down there. A census that follows all of
        them buries the finding and fills the log with noise."""
        census = _census_of([_Alpha(), _Alpha(), _Alpha(), _Beta()], top=1)
        reading = census()
        self.assertIn(OBJECT_CENSUS_PREFIX + ALPHA, reading)
        self.assertNotIn(OBJECT_CENSUS_PREFIX + BETA, reading)

    def test_a_type_that_climbs_in_later_is_picked_up(self):
        heap = [_Alpha()]
        census = _census_of(heap, top=1)
        self.assertNotIn(
            OBJECT_CENSUS_PREFIX + BETA, census()
        )
        heap[:] = [_Beta()] * 4
        self.assertEqual(
            census()[OBJECT_CENSUS_PREFIX + BETA], 4.0
        )

    def test_the_instrument_does_not_grow_without_bound(self):
        """An unbounded thing hunting an unbounded thing is not funny twice."""
        heap = [_Alpha()]
        census = _census_of(heap, top=10, limit=2)
        census()
        heap[:] = [_Beta(), {}, [], (), set()]
        census()
        self.assertLessEqual(len(census.tracked), 2)

    def test_truncation_is_declared_rather_than_silent(self):
        heap = [_Alpha(), _Beta(), {}, []]
        census = _census_of(heap, top=10, limit=2)
        reading = census()
        self.assertEqual(len(census.tracked), 2)
        self.assertEqual(reading[f"{OBJECT_CENSUS_PREFIX}_untracked_types"], 2.0)

    def test_nothing_is_untracked_when_everything_fits(self):
        reading = _census_of([_Alpha(), _Beta()], top=10, limit=10)()
        self.assertEqual(reading[f"{OBJECT_CENSUS_PREFIX}_untracked_types"], 0.0)

    def test_the_cost_of_the_reading_is_in_the_reading(self):
        """So a hitch in the report can be told from the instrument itself."""
        ticks = iter([1.0, 1.25])
        census = TypeCensus(objects=lambda: [_Alpha()], clock=lambda: next(ticks))
        self.assertAlmostEqual(
            census()[f"{OBJECT_CENSUS_PREFIX}_census_ms"], 250.0, places=3
        )

    def test_the_real_heap_is_walkable(self):
        """The shipped default is `gc.get_objects`, not the test's list."""
        reading = TypeCensus()()
        self.assertGreater(reading[f"{OBJECT_CENSUS_PREFIX}_total"], 100.0)
        self.assertGreater(reading[f"{OBJECT_CENSUS_PREFIX}dict"], 0.0)


class CensusInProbeTests(unittest.TestCase):
    """A census may not overwrite a gauge, and may not end the run."""

    def test_census_names_reach_the_sample(self):
        probe = HealthProbe(censuses=[lambda: {"obj.Thing": 3.0}])
        self.assertEqual(probe.sample(elapsed_s=0.0, frame=1)["obj.Thing"], 3.0)

    def test_a_census_that_raises_costs_only_itself(self):
        def boom():
            raise RuntimeError("the heap moved")

        probe = HealthProbe(gauges={"a": lambda: 7.0}, censuses=[boom])
        sample = probe.sample(elapsed_s=0.0, frame=1)
        self.assertEqual(sample["a"], 7.0)
        self.assertNotIn("obj.Thing", sample)

    def test_one_census_raising_does_not_silence_another(self):
        def boom():
            raise RuntimeError("no")

        probe = HealthProbe(censuses=[boom, lambda: {"obj.Thing": 1.0}])
        self.assertEqual(probe.sample(elapsed_s=0.0, frame=1)["obj.Thing"], 1.0)

    def test_a_census_cannot_overwrite_a_declared_gauge(self):
        probe = HealthProbe(
            gauges={"scene.prims": lambda: 12.0},
            censuses=[lambda: {"scene.prims": 999.0}],
        )
        self.assertEqual(probe.sample(elapsed_s=0.0, frame=1)["scene.prims"], 12.0)

    def test_unreadable_census_values_are_dropped_not_written(self):
        """A `None` in a gauge means "unreadable"; in a census it is noise.

        A gauge is declared once and its absence is a wiring bug worth seeing.
        A census invents its own names every sample, so a junk value there has
        nothing to say and would only give the report a series of nulls to
        reason about.
        """
        probe = HealthProbe(
            censuses=[
                lambda: {
                    "obj.Good": 4.0,
                    "obj.Text": "many",
                    "obj.Flag": True,
                    "obj.Nan": float("nan"),
                }
            ]
        )
        sample = probe.sample(elapsed_s=0.0, frame=1)
        self.assertEqual(sample["obj.Good"], 4.0)
        for name in ("obj.Text", "obj.Flag", "obj.Nan"):
            self.assertNotIn(name, sample)

    def test_no_census_by_default(self):
        """The heap walk is opt-in, so an ordinary soak pays nothing for it."""
        probe = HealthProbe(gauges={"a": lambda: 1.0})
        self.assertEqual(
            [k for k in probe.sample(elapsed_s=0.0, frame=1) if k.startswith("obj.")],
            [],
        )


class CensusWiringTests(unittest.TestCase):
    """`--soak-objects` and nothing else turns the heap walk on."""

    def test_the_flag_parses(self):
        from vibestorm.viewer3d.app import build_parser

        args = build_parser().parse_args(["--soak-objects"])
        self.assertTrue(args.soak_objects)
        self.assertFalse(build_parser().parse_args([]).soak_objects)

    def test_the_flags_reach_the_probe(self):
        """The wiring, not the builder.

        A perfect `build_health_probe` called without the flag is a soak that
        comes back missing the one reading it was started for, and every test
        of the builder passes while it happens.
        """
        from vibestorm.viewer3d.app import build_parser, probe_for_args

        args = build_parser().parse_args(
            ["--soak-objects", "--soak-interval", "7", "--run-seconds", "60"]
        )
        probe = probe_for_args(args, None, None, None, None, soak_log=object())
        self.assertIsNotNone(probe)
        self.assertEqual(len(list(probe.censuses)), 1)
        self.assertEqual(probe.interval_s, 7.0)
        self.assertEqual(probe.run_seconds, 60.0)

    def test_no_soak_log_means_no_probe(self):
        from vibestorm.viewer3d.app import build_parser, probe_for_args

        args = build_parser().parse_args(["--soak-objects"])
        self.assertIsNone(
            probe_for_args(args, None, None, None, None, soak_log=None)
        )

    def test_without_the_flag_the_probe_has_no_census(self):
        from vibestorm.viewer3d.app import build_parser, probe_for_args

        args = build_parser().parse_args([])
        probe = probe_for_args(args, None, None, None, None, soak_log=object())
        self.assertEqual(list(probe.censuses), [])

    def test_the_probe_declares_a_census_only_when_asked(self):
        from vibestorm.viewer3d.app import build_health_probe

        off = build_health_probe(None, None, None, None, interval_s=30.0)
        self.assertEqual(list(off.censuses), [])
        on = build_health_probe(
            None, None, None, None, interval_s=30.0, census_objects=True
        )
        self.assertEqual(len(list(on.censuses)), 1)
        self.assertIsInstance(list(on.censuses)[0], TypeCensus)


class ConditionsTests(unittest.TestCase):
    """What the run was measured *under*, which is not what it was holding.

    A soak on a developer's own desktop shares the machine with a compiler, a
    browser and the test suite of the thing being soaked. Without these two
    the report cannot tell "the client started growing at minute fifty-five"
    from "something else started at minute fifty-five", and the two look
    identical -- which is how a soak comes to call a build a leak. Observed
    on 2026-09-07, on a run whose resident set was flat for thirty-five
    minutes and then climbed for the rest, with a 100%-CPU build in `ps` and
    nothing in the report that could say so.
    """

    def test_both_conditions_are_taken_on_every_sample(self) -> None:
        for name in ("proc.load_1m", "proc.cpu_seconds"):
            self.assertIn(name, PROCESS_GAUGES)
        probe = HealthProbe()
        sample = probe.sample(elapsed_s=1.0, frame=1)
        for name in CONDITION_NAMES:
            self.assertIn(name, sample)

    def test_a_condition_is_never_reported_as_a_leak(self) -> None:
        """The whole point of separating them.

        A load average that climbs because a build started passes every test
        for "growing" in this file, and `proc.cpu_seconds` only ever rises by
        definition. Either one in the growth table is a row that cries wolf on
        a run where nothing is wrong, and a report with one of those in it is
        a report nobody reads to the bottom of.
        """
        samples = [
            {
                "elapsed_s": i * 30.0,
                "frame": i * 900,
                "proc.load_1m": float(i),
                "proc.cpu_seconds": float(i) * 7.5,
                "world.prims": 100.0,
            }
            for i in range(10)
        ]
        names = [growth.name for growth in growth_report(samples)]
        self.assertEqual(names, ["world.prims"])

    def test_the_pace_says_what_the_machine_and_the_client_were_doing(self) -> None:
        samples = [
            {
                "elapsed_s": i * 30.0,
                "frame": i * 900,
                # Quiet, then a build starts halfway through.
                "proc.load_1m": 1.0 if i < 5 else 9.0,
                # A quarter of a core throughout: the client was never starved.
                "proc.cpu_seconds": i * 30.0 * 0.25,
            }
            for i in range(10)
        ]
        pace = pace_report(samples)
        assert pace is not None
        self.assertLess(pace.load_first_half, 5.0)
        self.assertGreater(pace.load_second_half, 5.0)
        self.assertAlmostEqual(pace.cores_first_half, 0.25, places=6)
        self.assertAlmostEqual(pace.cores_second_half, 0.25, places=6)

    def test_a_log_without_them_says_nothing_rather_than_guessing(self) -> None:
        # Every soak log written before 2026-09-07 is one of these, and a
        # report that filled in 0.0 would say those runs were measured on an
        # idle machine -- which is exactly the claim they cannot support.
        samples = [{"elapsed_s": i * 30.0, "frame": i * 900} for i in range(10)]
        pace = pace_report(samples)
        assert pace is not None
        self.assertIsNone(pace.load_first_half)
        self.assertIsNone(pace.cores_second_half)

    def test_the_load_is_the_machine_s_and_the_cpu_is_ours(self) -> None:
        """They have to be different numbers or one of them is redundant."""
        self.assertGreaterEqual(machine_load_1m(), 0.0)
        before = process_cpu_seconds()
        total = 0
        for i in range(400_000):
            total += i * i
        after = process_cpu_seconds()
        self.assertGreater(after, before, "this process burned CPU and said it did not")

    def test_a_platform_without_a_load_average_reads_zero(self) -> None:
        from unittest import mock

        with mock.patch("os.getloadavg", side_effect=OSError("no such thing here")):
            self.assertEqual(machine_load_1m(), 0.0)


def _row(report, name: str):
    """The one row for `name`. `growth_report` returns every gauge in the
    samples, and `_samples_with_gc` puts the collector counter in every one
    of them, so the row wanted here is never reliably the first."""
    for growth in report:
        if growth.name == name:
            return growth
    raise AssertionError(f"no row for {name}: {[g.name for g in report]}")


class CyclicGarbageTests(unittest.TestCase):
    """The leak that is not a leak, and the two things that expose it.

    pygame_gui builds a small graph of objects for every line of text it
    lays out, and those objects refer to each other -- so only the *cyclic*
    collector frees them. CPython runs its oldest generation when pending
    long-lived allocations pass a quarter of the long-lived total, and a
    viewer's static heap is most of that total and never garbage: it does
    nothing but push the threshold up until the collection that would free
    the text objects stops arriving in time. Soak run 3 was flat for
    fifty-five minutes and then climbed to a gigabyte.
    """

    def test_freezing_takes_what_is_alive_out_of_the_collector_s_reach(self) -> None:
        import gc

        self.addCleanup(gc.unfreeze)
        held = [object() for _ in range(64)]
        before = len(gc.get_objects())
        frozen = freeze_static_heap()

        self.assertGreater(frozen, 0)
        # `gc.get_objects()` does not report the permanent generation, which
        # is also what makes the object census in a soak read as the world
        # rather than as the viewer plus the world.
        self.assertLess(len(gc.get_objects()), before)
        self.assertEqual(len(held), 64)

    def test_the_collection_counts_are_readable_and_rise(self) -> None:
        import gc

        for name, read in GC_COUNTERS.items():
            value = float(read())
            assert math.isfinite(value), name
            assert value >= 0.0, name
        before = GC_COUNTERS["gc.full_collections"]()
        gc.collect()
        self.assertGreater(GC_COUNTERS["gc.full_collections"](), before)

    def test_the_names_match_what_the_indices_actually_count(self) -> None:
        """The mapping the names claim, pinned to the interpreter running.

        Python 3.13 replaced the three-generation collector with an
        incremental one, and `gc.get_stats()` kept its three entries while the
        meaning moved. Names that said `gen0`/`gen1`/`gen2` were describing a
        collector this client does not run on -- the same mistake as printing
        a wire byte where a reader expects metres, one layer down.

        What is checked here is what can be checked in a millisecond: the two
        indices an explicit call moves, and therefore -- by elimination -- the
        one it does not. `gc.auto_collections` is the counter no hand-written
        collection touches, which is what "automatic" means and what the name
        promises.

        What is deliberately *not* checked here is that the automatic
        collector does eventually count there, because how much garbage that
        takes is the very quantity this fix is about: on a small heap roughly
        twelve thousand objects, on a viewer-sized one four hundred thousand.
        The first version of this test allocated until the counter moved, and
        in the full suite -- where the heap is large -- it reached eight
        gigabytes before it was killed. That demonstration lives in
        `tools/gc_pressure.py --mode threshold`, where it is the measurement
        rather than a precondition.
        """
        import gc

        def counts() -> dict[str, float]:
            return {name: read() for name, read in GC_COUNTERS.items()}

        before = counts()
        gc.collect(0)
        after = counts()
        self.assertGreater(after["gc.young_collections"], before["gc.young_collections"])
        self.assertEqual(after["gc.auto_collections"], before["gc.auto_collections"])
        self.assertEqual(after["gc.full_collections"], before["gc.full_collections"])

        before = counts()
        gc.collect()
        after = counts()
        self.assertGreater(after["gc.full_collections"], before["gc.full_collections"])
        self.assertEqual(after["gc.auto_collections"], before["gc.auto_collections"])

    def test_every_collection_count_is_declared_a_counter(self) -> None:
        """Or the report calls the fix a leak.

        These only ever rise. Left as gauges they are three permanent
        `growing` rows at the top of every soak report, which is how a report
        stops being read -- and they are in it precisely so that a *slowing*
        `gc.gen2` against a climbing `obj._total` can be seen for what it is.
        """
        import sys
        from pathlib import Path as _Path

        sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "tools"))
        from soak_report import DEFAULT_COUNTERS

        for name in GC_COUNTERS:
            self.assertIn(name, DEFAULT_COUNTERS)

    def test_nothing_awaits_between_starting_the_session_and_freezing(self) -> None:
        """The freeze is safe only because the world cannot have arrived yet.

        `run_viewer` creates the session task and then freezes, and an
        `asyncio` task does not run a single step until its creator awaits.
        So at the moment of the freeze no packet has been decoded and there is
        nothing of the world on the heap. One `await` slipped between those
        two lines and prims start getting frozen -- which is a genuine leak,
        because frozen objects are never collected again and a region can go
        away. Nothing else in the file enforces the ordering, so this does.
        """
        import re
        from pathlib import Path as _Path

        source = (
            _Path(__file__).resolve().parents[1] / "src/vibestorm/viewer3d/app.py"
        ).read_text()
        start = source.index("session_task = asyncio.create_task(")
        end = source.index("freeze_static_heap()", start)
        # Comments stripped, or the paragraph explaining this rule trips it.
        span = "\n".join(line.split("#", 1)[0] for line in source[start:end].splitlines())
        self.assertIsNone(re.search(r"\bawait\b", span))

    def test_the_viewer_declares_them(self) -> None:
        from vibestorm.viewer3d.app import build_health_probe

        probe = build_health_probe(
            _StubScene(), _StubRenderer(), _StubClient(), _StubHud(), interval_s=30.0
        )
        for name in GC_COUNTERS:
            self.assertIn(name, probe.counters)


class _StubScene:
    object_entities: dict = {}
    avatar_entities: dict = {}


class _StubRenderer:
    pass


class _StubClient:
    current = None

    def world_view(self):
        return None


class CyclicVerdictTests(unittest.TestCase):
    """Telling a leak from the interval between collections.

    Soak run 6 is the case, and the numbers below are its own, every sixth
    sample of the seventy minutes it ran. A dozen rows climbed for
    thirty-five minutes, one collection ran, every one of them fell back to
    where it had started, and they climbed again -- while the process's
    resident size moved by eighty kilobytes in the half hour either side.
    The rows were pygame_gui's text layouts and the deques and lists inside
    them: reference cycles, which is what the collector is for and what
    nothing else frees. Reported as `growing`, eighteen of them buried the
    finding that the heap was flat.
    """

    #: `obj.pygame_gui...TextBoxLayout` from `local/soak/run6.jsonl`.
    RUN6 = [3, 64, 73, 102, 120, 134, 143, 152, 163, 175, 192,
            39, 46, 55, 65, 81, 94, 104, 109, 115, 121, 121, 127, 132]
    #: `gc.auto_collections` from the same samples. The collector ran once in
    #: the middle, and five times during startup before the first sample.
    RUN6_GC = [8] + [13] * 10 + [14] * 13

    def test_a_row_a_collection_takes_back_is_not_growing(self) -> None:
        samples = _samples_with_gc("obj.thing", self.RUN6, self.RUN6_GC)
        row = _row(growth_report(samples), "obj.thing")
        assert row.verdict == "cyclic", row

    def test_the_same_row_reads_as_growing_without_the_collector_series(self) -> None:
        """The evidence, not a change of heart about the shape.

        Soak logs written before this one have no `gc.auto_collections` in
        them, and nothing in the value column alone separates that shape from
        a leak. The verdict has to stay the alarming one when the evidence is
        missing, or reading an old log quietly becomes a clean bill of health.
        """
        row = _row(growth_report(_samples("obj.thing", self.RUN6)), "obj.thing")
        assert row.verdict == "growing", row

    def test_a_leak_is_still_a_leak_when_collections_happen(self) -> None:
        """The collector runs during a leak too. It just does not help."""
        values = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
        samples = _samples_with_gc("obj.leak", values, [13] * 5 + [14] * 5)
        row = _row(growth_report(samples), "obj.leak")
        assert row.verdict == "growing", row

    def test_a_partial_reclaim_is_still_reported(self) -> None:
        """Half is the line, and a row that keeps most of itself is over it.

        A cache that gives up a tenth at each collection and climbs past its
        old peak is holding on to something, and the reader has to see it.
        """
        values = [100, 200, 300, 400, 500, 600, 540, 640, 740, 840]
        samples = _samples_with_gc("obj.mostly", values, [13] * 6 + [14] * 4)
        row = _row(growth_report(samples), "obj.mostly")
        assert row.verdict == "growing", row

    def test_a_fall_that_is_not_a_collection_does_not_count(self) -> None:
        """A cache evicting itself is identical in the value column.

        It is a different finding -- somebody's eviction policy rather than
        the collector -- and calling it `cyclic` sends the reader to the
        wrong place. The counter is the only thing that separates them.
        """
        samples = _samples_with_gc("obj.cache", self.RUN6, [13] * len(self.RUN6))
        row = _row(growth_report(samples), "obj.cache")
        assert row.verdict == "growing", row

    def test_a_counter_is_never_cyclic(self) -> None:
        """Counters are read for the opposite failure and have their own words."""
        row = _row(
            growth_report(
                _samples_with_gc("udp.total_received", self.RUN6, self.RUN6_GC),
                counters=["udp.total_received"],
            ),
            "udp.total_received",
        )
        assert row.verdict == "rising", row



class CameraYawGaugeTests(unittest.TestCase):
    """The control for every other gauge in the log.

    A soak spends hours drawing whatever the camera is pointing at. If that
    never changes, then culling, sorting and every cache keyed on the view
    went the whole run without being asked to -- and every container gauge
    reading `flat` means only that nothing was asked of it. `render.camera_yaw`
    is how a run says which of the two it was, and it says it in the ordinary
    report: `flat` is a camera that never turned.
    """

    def test_it_reads_the_camera_the_renderer_draws_from(self) -> None:
        from vibestorm.viewer3d.app import _float_of

        class _Camera:
            yaw = 1.75

        class _Renderer:
            camera = _Camera()

        renderer = _Renderer()
        read = _float_of(lambda: renderer, "camera", "yaw")
        self.assertEqual(read(), 1.75)
        renderer.camera.yaw = 3.5
        self.assertEqual(read(), 3.5)

    def test_a_renamed_attribute_raises_rather_than_reading_zero(self) -> None:
        """The same rule the container gauges follow, and for the same reason.

        A gauge that answers zero for a name nobody has any more reads
        perfectly flat for the whole run, which is what a camera that turned
        all the way round and a gauge watching nothing have in common.
        """
        from vibestorm.viewer3d.app import _float_of

        class _Renderer:
            camera = object()

        with self.assertRaises(AttributeError):
            _float_of(lambda: _Renderer(), "camera", "yaw")()

    def test_an_owner_that_does_not_exist_yet_is_zero(self) -> None:
        """There is no renderer before the window opens, and that is ordinary."""
        from vibestorm.viewer3d.app import _float_of

        self.assertEqual(_float_of(lambda: None, "camera", "yaw")(), 0.0)

    def test_the_gauge_is_declared(self) -> None:
        from vibestorm.viewer3d.app import build_health_probe

        probe = build_health_probe(
            _StubScene(), _StubRenderer(), _StubClient(), _StubHud(), interval_s=1.0
        )
        self.assertIn("render.camera_yaw", probe.gauges)



class _StubHud:
    pass


def _samples_with_limit(name: str, values, limit, *, step: float = 30.0) -> list[dict]:
    """Samples carrying a gauge and the ceiling it declares for itself."""
    return [
        {
            "elapsed_s": i * step,
            "frame": i * 100,
            name: float(value),
            f"{name}{LIMIT_SUFFIX}": float(limit),
        }
        for i, value in enumerate(values)
    ]


class BoundedVerdictTests(unittest.TestCase):
    """A gauge that has somewhere to stop, and a report that knows it.

    `udp.seen_sequences` is the gauge that found the only real leak this
    instrument has found: a set of reliable sequence numbers that nothing
    emptied, climbing 671 an hour for as long as a session lasted. The fix
    replaced it with a two-window memory that cannot exceed 8,192 -- and the
    row still climbs towards that number on every run, and was still reported
    as `growing` on every run. A report that says the same true-but-useless
    thing forever is one that stops being read, which is what costs the next
    real finding.

    So the window now publishes its own ceiling as `udp.seen_sequences.limit`
    and the report reads it. That turns a claim in a docstring into something
    each run checks: under the bound is `bounded`, over it is `over-bound`,
    which is a different and much louder thing.
    """

    CLIMBING = [float(v) for v in range(0, 3000, 120)]  # 25 samples, still rising

    def test_a_gauge_climbing_towards_its_ceiling_is_bounded(self) -> None:
        report = growth_report(_samples_with_limit("udp.seen_sequences", self.CLIMBING, 8192))
        self.assertEqual(_row(report, "udp.seen_sequences").verdict, "bounded")

    def test_the_same_series_without_a_ceiling_is_growing(self) -> None:
        """The evidence, not a change of heart about the shape. A log written
        before the ceiling was recorded must not quietly become a clean bill
        of health."""
        samples = [
            {"elapsed_s": s["elapsed_s"], "frame": s["frame"], "udp.seen_sequences": s["udp.seen_sequences"]}
            for s in _samples_with_limit("udp.seen_sequences", self.CLIMBING, 8192)
        ]
        self.assertEqual(_row(growth_report(samples), "udp.seen_sequences").verdict, "growing")

    def test_passing_the_ceiling_is_reported_and_beats_every_other_shape(self) -> None:
        """The bound being wrong is not a trend, and no reading of the trend
        matters beside it -- including the flat tail this series ends on,
        which on its own would read as `settled`."""
        values = [float(v) for v in range(0, 12000, 500)] + [12000.0] * 12
        report = growth_report(_samples_with_limit("udp.seen_sequences", values, 8192))
        self.assertEqual(_row(report, "udp.seen_sequences").verdict, "over-bound")

    def test_an_overshoot_that_came_back_down_is_still_reported(self) -> None:
        """`over-bound` is read off the peak, not off the last sample.

        A bound that was exceeded once and recovered is still a bound that
        does not hold, and the recovery is what makes it easy to miss: the
        row ends under its ceiling and every other column looks ordinary.
        """
        values = [float(v) for v in range(0, 9000, 300)] + [400.0] * 12
        report = growth_report(_samples_with_limit("udp.seen_sequences", values, 8192))
        self.assertEqual(_row(report, "udp.seen_sequences").verdict, "over-bound")

    def test_reaching_the_ceiling_exactly_is_not_exceeding_it(self) -> None:
        """Both halves full *is* the capacity, so the boundary belongs on the
        legal side. Off by one here and every long run cries wolf again."""
        values = [float(v) for v in range(0, 8192, 300)] + [8192.0] * 12
        report = growth_report(_samples_with_limit("udp.seen_sequences", values, 8192))
        self.assertNotEqual(_row(report, "udp.seen_sequences").verdict, "over-bound")

    def test_a_ceiling_does_not_hide_a_row_that_settled(self) -> None:
        """`bounded` replaces the alarming words only. `settled` says more."""
        flat_tail = [float(v) for v in range(0, 1200, 100)] + [1200.0] * 20
        report = growth_report(_samples_with_limit("udp.seen_sequences", flat_tail, 8192))
        self.assertEqual(_row(report, "udp.seen_sequences").verdict, "settled")

    def test_a_ceiling_does_not_hide_a_collector_taking_it_back(self) -> None:
        samples = _samples_with_gc("obj.thing", CyclicVerdictTests.RUN6, CyclicVerdictTests.RUN6_GC)
        for sample in samples:
            sample["obj.thing" + LIMIT_SUFFIX] = 8192.0
        self.assertEqual(_row(growth_report(samples), "obj.thing").verdict, "cyclic")

    def test_the_ceiling_is_a_row_of_its_own(self) -> None:
        """So the reader can see how much of it is gone without going to the
        source for the number."""
        report = growth_report(_samples_with_limit("udp.seen_sequences", self.CLIMBING, 8192))
        self.assertEqual(_row(report, "udp.seen_sequences" + LIMIT_SUFFIX).last, 8192.0)


class DeclaredLimitTests(unittest.TestCase):
    """Reading the ceilings out of a log."""

    def limits(self, *samples: dict) -> dict:
        return _declared_limits(list(samples))

    def test_a_limit_row_names_the_row_it_bounds(self) -> None:
        self.assertEqual(self.limits({"a.b.limit": 10.0}), {"a.b": 10.0})

    def test_a_log_with_no_limits_declares_none(self) -> None:
        self.assertEqual(self.limits({"a.b": 10.0}), {})

    def test_the_largest_reading_wins(self) -> None:
        """A ceiling that appears to move is one read at different moments.
        The generous reading is the one that does not manufacture an
        `over-bound` out of a sampling artefact."""
        self.assertEqual(self.limits({"a.limit": 10.0}, {"a.limit": 8.0}), {"a": 10.0})

    def test_nonsense_ceilings_are_ignored(self) -> None:
        for value in (0, -1, float("nan"), float("inf"), True, "8192", None):
            with self.subTest(repr(value)):
                self.assertEqual(self.limits({"a.limit": value}), {})
