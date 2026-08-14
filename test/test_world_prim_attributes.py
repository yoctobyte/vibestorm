"""Tests for prim material and click-action naming.

Values are OpenSim's LSL constants (``LSL_Constants.cs``); the source pin
re-parses them so the tables cannot drift.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.prim_attributes import (
    CLICK_ACTION_BUY,
    CLICK_ACTION_IGNORE,
    CLICK_ACTION_NAMES,
    CLICK_ACTION_SIT,
    CLICK_ACTION_TOUCH,
    DEFAULT_PRIM_MATERIAL,
    PRIM_MATERIAL_LIGHT,
    PRIM_MATERIAL_NAMES,
    PRIM_MATERIAL_STONE,
    PRIM_MATERIAL_WOOD,
    click_action_name,
    prim_material_name,
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
        name: int(value)
        for name, value in re.findall(
            rf"const int ({re.escape(prefix)}\w+)\s*=\s*(-?\d+)", text
        )
    }


class SourcePinTests(unittest.TestCase):
    def test_material_values_match_opensim(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = _constants("PRIM_MATERIAL_")

        self.assertTrue(source, "failed to parse PRIM_MATERIAL_ constants")
        for name, value in source.items():
            expected = name.removeprefix("PRIM_MATERIAL_").lower()
            self.assertEqual(prim_material_name(value), expected)

    def test_click_action_values_match_opensim(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        source = _constants("CLICK_ACTION_")

        self.assertTrue(source, "failed to parse CLICK_ACTION_ constants")
        # NONE and TOUCH share value 0; every other constant must be named.
        for name, value in source.items():
            if name == "CLICK_ACTION_NONE":
                continue
            expected = name.removeprefix("CLICK_ACTION_").lower().replace("_", " ")
            self.assertEqual(click_action_name(value), expected)

    def test_no_invented_values(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        material_values = set(_constants("PRIM_MATERIAL_").values())
        click_values = set(_constants("CLICK_ACTION_").values())

        self.assertEqual(set(PRIM_MATERIAL_NAMES) - material_values, set())
        self.assertEqual(set(CLICK_ACTION_NAMES) - click_values, set())


class MaterialTests(unittest.TestCase):
    def test_known_materials(self) -> None:
        self.assertEqual(prim_material_name(PRIM_MATERIAL_STONE), "stone")
        self.assertEqual(prim_material_name(PRIM_MATERIAL_WOOD), "wood")
        self.assertEqual(prim_material_name(PRIM_MATERIAL_LIGHT), "light")

    def test_default_is_wood(self) -> None:
        # SceneObjectPart.m_material initialises to Material.Wood.
        self.assertEqual(DEFAULT_PRIM_MATERIAL, PRIM_MATERIAL_WOOD)

    def test_unknown_material_keeps_its_number(self) -> None:
        self.assertEqual(prim_material_name(99), "unknown material 99")


class ClickActionTests(unittest.TestCase):
    def test_zero_is_touch_not_none(self) -> None:
        # CLICK_ACTION_NONE and CLICK_ACTION_TOUCH are both 0: touch is what an
        # unconfigured prim does, so naming 0 "none" would be misleading.
        self.assertEqual(CLICK_ACTION_TOUCH, 0)
        self.assertEqual(click_action_name(0), "touch")

    def test_known_actions(self) -> None:
        self.assertEqual(click_action_name(CLICK_ACTION_SIT), "sit")
        self.assertEqual(click_action_name(CLICK_ACTION_BUY), "buy")
        self.assertEqual(click_action_name(6), "open media")
        self.assertEqual(click_action_name(CLICK_ACTION_IGNORE), "ignore")

    def test_unknown_action_keeps_its_number(self) -> None:
        self.assertEqual(click_action_name(42), "unknown action 42")


if __name__ == "__main__":
    unittest.main()
