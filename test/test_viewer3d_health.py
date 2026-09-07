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

from vibestorm.viewer3d.health import (
    CONDITION_NAMES,
    MIN_SAMPLES_FOR_TREND,
    MIN_SAMPLES_FOR_VERDICT,
    OBJECT_CENSUS_PREFIX,
    PROCESS_GAUGES,
    Growth,
    HealthProbe,
    SoakLog,
    TypeCensus,
    format_growth_report,
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
            verdict="growing",
        )
        text = format_growth_report([row])
        assert "1,234,567,890" in text
        assert "1.50" in text


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
