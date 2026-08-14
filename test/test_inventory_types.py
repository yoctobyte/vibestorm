"""Tests for inventory asset type naming.

Values are OpenSim's ``INVENTORY_*`` LSL constants, re-parsed from source by
the pin test. The subtle part is *which field* they name: the wire carries both
``type`` (asset type) and ``inv_type`` (inventory type), and those enumerations
disagree exactly where it is hardest to notice.
"""

import re
import unittest
from pathlib import Path

from vibestorm.caps.inventory_types import (
    ASSET_TYPE_NAMES,
    INVENTORY_ANIMATION,
    INVENTORY_BODYPART,
    INVENTORY_GESTURE,
    INVENTORY_OBJECT,
    INVENTORY_SOUND,
    asset_type_name,
    count_asset_types,
    missing_gap_closing_types,
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


class _Item:
    def __init__(self, type_value):
        self.type = type_value


class SourcePinTests(unittest.TestCase):
    def test_values_match_the_lsl_constants(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        text = _LSL_CONSTANTS.read_text(encoding="utf-8", errors="replace")
        source = {
            name: int(value)
            for name, value in re.findall(r"const int (INVENTORY_\w+)\s*=\s*(-?\d+)", text)
        }

        self.assertTrue(source, "failed to parse INVENTORY_ constants")
        for name, value in source.items():
            # ALL and NONE are sentinels (-1), not asset types.
            if value < 0:
                self.assertNotIn(value, ASSET_TYPE_NAMES)
                continue
            self.assertIn(value, ASSET_TYPE_NAMES, f"{name} ({value}) is unnamed")

    def test_no_invented_values(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        text = _LSL_CONSTANTS.read_text(encoding="utf-8", errors="replace")
        source = {
            int(value)
            for _name, value in re.findall(r"const int (INVENTORY_\w+)\s*=\s*(-?\d+)", text)
        }

        self.assertEqual(sorted(set(ASSET_TYPE_NAMES) - source), [])


class AssetTypeNameTests(unittest.TestCase):
    def test_known_types(self) -> None:
        self.assertEqual(asset_type_name(INVENTORY_SOUND), "sound")
        self.assertEqual(asset_type_name(INVENTORY_OBJECT), "object")
        self.assertEqual(asset_type_name(INVENTORY_BODYPART), "body part")

    def test_animation_and_gesture_use_asset_not_inventory_numbering(self) -> None:
        # This is the whole hazard. In the *inventory* type enum an animation
        # is 19 and a gesture 20; in the *asset* type enum they are 20 and 21.
        # llGetInventoryType returns item.Type, which is what pins these to
        # the asset numbering — so 20 must be animation, not gesture.
        self.assertEqual(INVENTORY_ANIMATION, 20)
        self.assertEqual(INVENTORY_GESTURE, 21)
        self.assertEqual(asset_type_name(20), "animation")
        self.assertEqual(asset_type_name(21), "gesture")

    def test_unknown_type_keeps_its_number(self) -> None:
        self.assertEqual(asset_type_name(99), "unknown type 99")

    def test_missing_type_is_named_rather_than_crashing(self) -> None:
        self.assertEqual(asset_type_name(None), "untyped")


class CountingTests(unittest.TestCase):
    def test_counts_by_name(self) -> None:
        counts = count_asset_types(
            [_Item(INVENTORY_SOUND), _Item(INVENTORY_SOUND), _Item(INVENTORY_OBJECT)]
        )

        self.assertEqual(counts["sound"], 2)
        self.assertEqual(counts["object"], 1)

    def test_empty_inventory_counts_nothing(self) -> None:
        self.assertEqual(count_asset_types([]), {})
        self.assertEqual(count_asset_types(None), {})


class GapClosingTests(unittest.TestCase):
    def test_an_empty_account_is_missing_everything_useful(self) -> None:
        absent = missing_gap_closing_types([])

        self.assertIn("sound", absent)
        self.assertIn("object", absent)
        self.assertIn("animation", absent)

    def test_holding_a_type_removes_it_from_the_absent_list(self) -> None:
        absent = missing_gap_closing_types([_Item(INVENTORY_SOUND)])

        self.assertNotIn("sound", absent)
        self.assertIn("object", absent)

    def test_body_parts_do_not_count_as_gap_closing(self) -> None:
        # A default avatar's wearables are present in every account and close
        # no gap; reporting them as coverage would be actively misleading.
        absent = missing_gap_closing_types([_Item(INVENTORY_BODYPART)])

        self.assertIn("sound", absent)
        self.assertIn("object", absent)


if __name__ == "__main__":
    unittest.main()
