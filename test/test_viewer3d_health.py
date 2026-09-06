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
    MIN_SAMPLES_FOR_VERDICT,
    PROCESS_GAUGES,
    Growth,
    HealthProbe,
    SoakLog,
    format_growth_report,
    growth_report,
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
        report = growth_report(_samples("a", [0, 500, 1000, 1010, 1020, 1030]))
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
        return build_health_probe(scene, renderer, client, interval_s=1.0), scene, session

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
            Scene(), PerspectiveRenderer(Camera3D()), _FakeClient(None), interval_s=1.0
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
