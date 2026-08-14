"""Tests for parcel and region flag decoding.

Bit values are OpenSim's LSL constants, re-parsed from source by the pin test.
The sourced sets are partial by design — LSL exposes what a script can ask
about, not the whole bitfield — so the assertion that matters most is that
unnamed bits survive in ``unknown_bits`` instead of vanishing.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.land_flags import (
    PARCEL_FLAG_ALLOW_FLY,
    PARCEL_FLAG_ALLOW_GROUP_OBJECT_ENTRY,
    PARCEL_FLAG_ALLOW_SCRIPTS,
    PARCEL_FLAG_USE_BAN_LIST,
    REGION_FLAG_ALLOW_DAMAGE,
    REGION_FLAG_BLOCK_FLY,
    REGION_FLAG_SANDBOX,
    decode_parcel_flags,
    decode_region_flags,
)

_LSL_CONSTANTS = (
    Path(__file__).resolve().parents[1]
    / "opensim-source"
    / "OpenSim"
    / "Region"
    / "ScriptEngine"
    / "Shared"
    / "Api"
    / "Runtime"
    / "LSL_Constants.cs"
)


def _constants(prefix: str) -> dict[str, int]:
    text = _LSL_CONSTANTS.read_text(encoding="utf-8", errors="replace")
    return {
        name: int(value, 0)
        for name, value in re.findall(
            rf"const int ({re.escape(prefix)}\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", text
        )
    }


class SourcePinTests(unittest.TestCase):
    def test_every_parcel_flag_constant_is_named(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = _constants("PARCEL_FLAG_")

        self.assertTrue(source, "failed to parse PARCEL_FLAG_ constants")
        for name, bit in source.items():
            decoded = decode_parcel_flags(bit)
            self.assertEqual(
                decoded.unknown_bits, 0, f"{name} ({bit:#x}) is not named"
            )
            self.assertEqual(len(decoded.set_flags), 1, name)

    def test_every_region_flag_constant_is_named(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = _constants("REGION_FLAG_")

        self.assertTrue(source, "failed to parse REGION_FLAG_ constants")
        for name, bit in source.items():
            decoded = decode_region_flags(bit)
            self.assertEqual(
                decoded.unknown_bits, 0, f"{name} ({bit:#x}) is not named"
            )


class ParcelFlagTests(unittest.TestCase):
    def test_single_flag(self) -> None:
        decoded = decode_parcel_flags(PARCEL_FLAG_ALLOW_FLY)

        self.assertEqual(decoded.set_flags, ("allow fly",))
        self.assertFalse(decoded.has_unknown)

    def test_combined_flags_keep_table_order(self) -> None:
        decoded = decode_parcel_flags(
            PARCEL_FLAG_ALLOW_SCRIPTS | PARCEL_FLAG_ALLOW_FLY | PARCEL_FLAG_USE_BAN_LIST
        )

        self.assertEqual(
            decoded.set_flags, ("allow fly", "allow scripts", "ban list")
        )

    def test_zero_reads_as_none(self) -> None:
        decoded = decode_parcel_flags(0)

        self.assertEqual(decoded.set_flags, ())
        self.assertEqual(decoded.describe(), "none")

    def test_high_bit_flag_is_not_truncated(self) -> None:
        # 0x10000000 is past where a careless 16-bit assumption would stop.
        decoded = decode_parcel_flags(PARCEL_FLAG_ALLOW_GROUP_OBJECT_ENTRY)

        self.assertEqual(decoded.set_flags, ("allow group object entry",))

    def test_unnamed_bit_is_reported_not_dropped(self) -> None:
        decoded = decode_parcel_flags(PARCEL_FLAG_ALLOW_FLY | 0x4)

        self.assertEqual(decoded.set_flags, ("allow fly",))
        self.assertEqual(decoded.unknown_bits, 0x4)
        self.assertIn("unknown 0x4", decoded.describe())

    def test_raw_value_is_preserved(self) -> None:
        self.assertEqual(decode_parcel_flags(0x1234).raw, 0x1234)


class RegionFlagTests(unittest.TestCase):
    def test_named_bits(self) -> None:
        decoded = decode_region_flags(REGION_FLAG_SANDBOX | REGION_FLAG_BLOCK_FLY)

        self.assertEqual(decoded.set_flags, ("sandbox", "block fly"))

    def test_unnamed_bits_survive(self) -> None:
        # OpenSim sets many bits LSL never exposes; a real sim will hit this.
        decoded = decode_region_flags(REGION_FLAG_ALLOW_DAMAGE | 0x2)

        self.assertEqual(decoded.set_flags, ("allow damage",))
        self.assertEqual(decoded.unknown_bits, 0x2)
        self.assertTrue(decoded.has_unknown)

    def test_parcel_and_region_tables_are_not_interchangeable(self) -> None:
        # 0x40 is "allow create objects" for a parcel and "block terraform"
        # for a region. Decoding one word with the other table would be wrong
        # in a way that still produces a plausible-looking name.
        self.assertEqual(decode_parcel_flags(0x40).set_flags, ("allow create objects",))
        self.assertEqual(decode_region_flags(0x40).set_flags, ("block terraform",))


if __name__ == "__main__":
    unittest.main()
