"""Tests for the TeleportFlags bitfield.

Unlike the parcel and region flag tables, this one is complete: OpenSim's
``Constants.cs`` defines ``TeleportFlags`` outright, so the pin test can demand
every flag be named rather than tolerating a sourced subset.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.teleport_flags import (
    TELEPORT_FLAG_FINISHED_VIA_SAME_SIM,
    TELEPORT_FLAG_VIA_LOCATION,
    TELEPORT_NOT_VIA_HG_LOGIN_MASK,
    _TELEPORT_FLAG_NAMES,
    decode_teleport_flags,
)

_CONSTANTS = (
    Path(__file__).resolve().parents[1]
    / "opensim-source" / "OpenSim" / "Framework" / "Constants.cs"
)


def _source_flags() -> dict[str, int]:
    text = _CONSTANTS.read_text(encoding="utf-8", errors="replace")
    body = text.split("enum TeleportFlags", 1)[1].split("}", 1)[0]
    flags: dict[str, int] = {}
    for name, shift in re.findall(r"(\w+)\s*=\s*1 << (\d+)", body):
        flags[name] = 1 << int(shift)
    return flags


class SourcePinTests(unittest.TestCase):
    def test_every_source_flag_is_named(self) -> None:
        if not _CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = _source_flags()

        self.assertTrue(source, "failed to parse the TeleportFlags enum")
        named = {bit for bit, _name in _TELEPORT_FLAG_NAMES}
        for name, bit in source.items():
            self.assertIn(bit, named, f"{name} ({bit:#x}) is unnamed")

    def test_no_invented_flags(self) -> None:
        if not _CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = set(_source_flags().values())

        named = {bit for bit, _name in _TELEPORT_FLAG_NAMES}
        self.assertEqual(sorted(named - source), [])

    def test_the_mask_is_not_treated_as_a_flag(self) -> None:
        # notViaHGLogin = 0xbffffff is a mask, not a bit. Folding it into the
        # table would name almost every teleport word after it, so the decoder
        # must not know about it -- and it is not a power of two, which is the
        # property that makes it obviously wrong to include.
        self.assertNotEqual(
            TELEPORT_NOT_VIA_HG_LOGIN_MASK & (TELEPORT_NOT_VIA_HG_LOGIN_MASK - 1), 0
        )
        named = {bit for bit, _name in _TELEPORT_FLAG_NAMES}
        self.assertNotIn(TELEPORT_NOT_VIA_HG_LOGIN_MASK, named)

        decoded = decode_teleport_flags(TELEPORT_NOT_VIA_HG_LOGIN_MASK)

        self.assertNotEqual(decoded.unknown_bits, 0)


class DecodeTests(unittest.TestCase):
    def test_a_local_teleport_word(self) -> None:
        decoded = decode_teleport_flags(
            TELEPORT_FLAG_VIA_LOCATION | TELEPORT_FLAG_FINISHED_VIA_SAME_SIM
        )

        self.assertEqual(decoded.set_flags, ("via location", "finished, same sim"))
        self.assertEqual(decoded.unknown_bits, 0)

    def test_no_flags_reads_as_none(self) -> None:
        decoded = decode_teleport_flags(0)

        self.assertEqual(decoded.set_flags, ())
        self.assertEqual(decoded.describe(), "none")

    def test_an_unnamed_bit_is_reported_not_dropped(self) -> None:
        decoded = decode_teleport_flags(1 << 25)

        self.assertEqual(decoded.set_flags, ())
        self.assertEqual(decoded.unknown_bits, 1 << 25)
        self.assertIn("unknown", decoded.describe())


class FinishBitTests(unittest.TestCase):
    """OpenSim never sets a FinishedVia* bit, so nothing may depend on one.

    This module first shipped ``is_same_region_teleport`` and
    ``is_region_crossing_teleport`` reading these bits, which looked right and
    was dead code: a live local teleport on 2026-08-14 reported ``via
    location`` and nothing else. The flags are still named, because another
    grid may send them; the predicates are gone.
    """

    def test_no_opensim_source_file_sets_a_finish_bit(self) -> None:
        if not _CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        root = _CONSTANTS.parents[2]

        setters = [
            path
            for path in root.rglob("*.cs")
            if path != _CONSTANTS
            and re.search(r"FinishedVia(SameSim|NewSim|Lure)", path.read_text(encoding="utf-8", errors="replace"))
        ]

        self.assertEqual([str(p) for p in setters], [])

    def test_the_flags_are_still_named_for_other_grids(self) -> None:
        decoded = decode_teleport_flags(TELEPORT_FLAG_FINISHED_VIA_SAME_SIM)

        self.assertEqual(decoded.set_flags, ("finished, same sim",))
        self.assertEqual(decoded.unknown_bits, 0)

    def test_the_flags_are_not_re_exported_as_predicates(self) -> None:
        import vibestorm.world.teleport_flags as module

        for name in ("is_same_region_teleport", "is_region_crossing_teleport"):
            self.assertFalse(hasattr(module, name), name)


if __name__ == "__main__":
    unittest.main()
