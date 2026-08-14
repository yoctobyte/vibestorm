"""Tests for SimStats naming.

The wire carries stats as bare ``(id, value)`` pairs, so a mis-keyed table
silently mislabels every number — and does it plausibly, since a frame time
relabelled as an agent count still looks like a number. There are two enums in
OpenSim's SimStats.cs that could each be mistaken for the id source:
``StatsIndex`` (the sim's internal array slot) and ``StatsID`` (what is written
to the wire). They agree for 0-3, so a table built on the wrong one survives a
casual check. These tests re-parse ``StatsID`` from source and explicitly
assert the point where the two enums diverge.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.sim_stats import (
    KEY_SIM_STAT_IDS,
    SIM_EXTRA_COUNT_START,
    SIM_STAT_NAMES,
    NamedSimStat,
    is_viewer_stat,
    name_sim_stats,
    sim_stat_name,
    summarize_sim_stats,
)

_ENUM_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "opensim-source"
    / "OpenSim"
    / "Framework"
    / "SimStats.cs"
)

#: Markers, not stats: they delimit the id range rather than naming a value.
_ENUM_MARKERS = {"SimExtraCountStart", "SimExtraCountEnd"}


class _StatEntry:
    """Stand-in for the decoder's SimStatEntry."""

    def __init__(self, stat_id: int, stat_value: float) -> None:
        self.stat_id = stat_id
        self.stat_value = stat_value


def _parse_enum(enum_name: str) -> dict[int, str]:
    text = _ENUM_SOURCE.read_text(encoding="utf-8", errors="replace")
    body = text.split(f"enum {enum_name}", 1)[1].split("}", 1)[0]
    found: dict[int, str] = {}
    for name, value in re.findall(r"(\w+)\s*=\s*(\d+)\s*[,\n]", body):
        if name in _ENUM_MARKERS:
            continue
        # internalLSLScriptLinesPerSecond shares 1000 with SimExtraCountStart;
        # first name wins so the marker never displaces a real stat.
        found.setdefault(int(value), name)
    return found


def _parse_opensim_enum() -> dict[int, str]:
    """The wire ids — ``StatsID``, not the internal ``StatsIndex`` slots."""
    return _parse_enum("StatsID")


class SourcePinTests(unittest.TestCase):
    def test_every_opensim_stat_id_is_named(self) -> None:
        if not _ENUM_SOURCE.exists():
            self.skipTest("opensim-source not present")
        source_ids = _parse_opensim_enum()

        self.assertTrue(source_ids, "failed to parse StatsIndex")
        missing = sorted(set(source_ids) - set(SIM_STAT_NAMES))
        self.assertEqual(missing, [], f"unnamed stat ids: {missing}")

    def test_table_invents_no_ids(self) -> None:
        if not _ENUM_SOURCE.exists():
            self.skipTest("opensim-source not present")
        extra = sorted(set(SIM_STAT_NAMES) - set(_parse_opensim_enum()))

        self.assertEqual(extra, [], f"named ids OpenSim does not define: {extra}")

    def test_table_is_not_keyed_on_the_internal_array_index(self) -> None:
        # StatsIndex is the sim's internal float[] slot; StatsID is the wire id.
        # The wire carries StatsID (LLClientView.SendSimStats writes
        # StatsIndexID[i]). Both enums start 0..3 identically, so this asserts
        # the divergence directly rather than trusting the overlap.
        if not _ENUM_SOURCE.exists():
            self.skipTest("opensim-source not present")
        slots = _parse_enum("StatsIndex")

        # In StatsIndex, 4 is Agents; on the wire, 4 is FrameMS.
        self.assertEqual(slots[4], "Agents")
        self.assertEqual(sim_stat_name(4), "frame ms")
        # And the extras live at 1000+, not packed just past the viewer range.
        self.assertIn(1000, SIM_STAT_NAMES)
        self.assertNotIn(41, SIM_STAT_NAMES)


class StatIdTests(unittest.TestCase):
    def test_key_ids_map_to_expected_names(self) -> None:
        # The ones the status line leans on; a shift here is the failure that
        # would otherwise go unnoticed because every value stays plausible.
        self.assertEqual(sim_stat_name(0), "time dilation")
        self.assertEqual(sim_stat_name(1), "sim fps")
        self.assertEqual(sim_stat_name(2), "physics fps")
        self.assertEqual(sim_stat_name(13), "agents")
        self.assertEqual(sim_stat_name(11), "total prims")
        self.assertEqual(sim_stat_name(15), "active scripts")

    def test_unacked_bytes_is_labelled_in_kb(self) -> None:
        # SendSimStats divides this one by 1024 before writing it, alone among
        # the stats; labelling it "bytes" would be off by three orders.
        self.assertEqual(sim_stat_name(24), "unacked kb")

    def test_unknown_id_keeps_its_number(self) -> None:
        self.assertEqual(sim_stat_name(9999), "unknown stat 9999")

    def test_viewer_boundary(self) -> None:
        self.assertEqual(SIM_EXTRA_COUNT_START, 1000)
        self.assertTrue(is_viewer_stat(40))
        self.assertFalse(is_viewer_stat(1000))

    def test_key_ids_are_all_named(self) -> None:
        for stat_id in KEY_SIM_STAT_IDS:
            self.assertIn(stat_id, SIM_STAT_NAMES)


class NamingTests(unittest.TestCase):
    def test_entries_are_named_and_valued(self) -> None:
        named = name_sim_stats([_StatEntry(1, 44.5), _StatEntry(13, 2.0)])

        self.assertEqual([n.name for n in named], ["sim fps", "agents"])
        self.assertEqual([n.value for n in named], [44.5, 2.0])
        self.assertTrue(all(n.is_known for n in named))

    def test_unknown_entry_is_flagged_not_dropped(self) -> None:
        named = name_sim_stats([_StatEntry(9999, 1.0)])

        self.assertEqual(len(named), 1)
        self.assertFalse(named[0].is_known)

    def test_no_entries_is_not_an_error(self) -> None:
        self.assertEqual(name_sim_stats(None), ())
        self.assertEqual(name_sim_stats([]), ())


class DescribeTests(unittest.TestCase):
    def test_integral_value_has_no_decimals(self) -> None:
        self.assertEqual(NamedSimStat(11, "total prims", 32.0).describe(), "total prims=32")

    def test_fractional_value_keeps_precision(self) -> None:
        self.assertEqual(NamedSimStat(1, "sim fps", 44.53).describe(), "sim fps=44.53")


class SummaryTests(unittest.TestCase):
    def test_summary_follows_key_order_not_id_order(self) -> None:
        named = name_sim_stats([_StatEntry(0, 1.0), _StatEntry(1, 45.0), _StatEntry(2, 45.0)])

        summary = summarize_sim_stats(named)

        self.assertLess(summary.index("sim fps"), summary.index("time dilation"))

    def test_summary_omits_stats_the_sim_did_not_send(self) -> None:
        summary = summarize_sim_stats(name_sim_stats([_StatEntry(1, 45.0)]))

        self.assertEqual(summary, "sim fps=45")

    def test_empty_stats_give_empty_summary(self) -> None:
        self.assertEqual(summarize_sim_stats(()), "")

    def test_naming_twice_raises_instead_of_reporting_zeros(self) -> None:
        # The first live run of this feature printed a plausible line of all
        # zeros because already-named stats were fed back through the namer and
        # a missing attribute defaulted to 0.0. Silence is the danger here.
        named = name_sim_stats([_StatEntry(1, 45.0)])

        with self.assertRaises(AttributeError):
            name_sim_stats(named)


if __name__ == "__main__":
    unittest.main()
