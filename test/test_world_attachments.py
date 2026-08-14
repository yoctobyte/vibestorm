"""Tests for attachment point decoding.

The state byte carries the attachment point with its nibbles swapped. The
dangerous property of that swap is that forgetting it does not produce
garbage: point 1 (chest) arrives as 0x10 = 16, which is itself a valid point
(right eye). So these tests assert the round trip explicitly, and assert that
the raw byte is *not* the answer.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.attachments import (
    ATTACH_ITEM_ID_KEY,
    ATTACHMENT_POINT_NAMES,
    attachment_point_name,
    decode_attachment_point,
    describe_attachment,
    is_attachment,
    is_hud_attachment,
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

ATTACHED = {ATTACH_ITEM_ID_KEY: "0d1a4e1a-0000-0000-0000-000000000001"}


class SourcePinTests(unittest.TestCase):
    def test_every_attach_constant_is_named(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        text = _LSL_CONSTANTS.read_text(encoding="utf-8", errors="replace")
        source = {
            int(value)
            for _, value in re.findall(r"const int (ATTACH_\w+)\s*=\s*(\d+)", text)
        }

        self.assertTrue(source, "failed to parse ATTACH_ constants")
        missing = sorted(source - set(ATTACHMENT_POINT_NAMES))
        self.assertEqual(missing, [], f"unnamed attachment points: {missing}")

    def test_no_invented_points(self) -> None:
        if not _LSL_CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        text = _LSL_CONSTANTS.read_text(encoding="utf-8", errors="replace")
        source = {
            int(value)
            for _, value in re.findall(r"const int (ATTACH_\w+)\s*=\s*(\d+)", text)
        }

        self.assertEqual(sorted(set(ATTACHMENT_POINT_NAMES) - source), [])


class NibbleSwapTests(unittest.TestCase):
    def test_chest_round_trips(self) -> None:
        # OpenSim sends chest (1) as 0x10.
        self.assertEqual(decode_attachment_point(0x10), 1)

    def test_the_raw_byte_is_not_the_answer(self) -> None:
        # 0x10 read raw is 16, which is "right eye" — a valid point, so a
        # missing swap looks like a different attachment, not like a bug.
        self.assertEqual(attachment_point_name(16), "right eye")
        self.assertEqual(attachment_point_name(decode_attachment_point(0x10)), "chest")

    def test_swap_is_its_own_inverse(self) -> None:
        for point in ATTACHMENT_POINT_NAMES:
            wire = ((point >> 4) | (point << 4)) & 0xFF
            self.assertEqual(decode_attachment_point(wire), point, f"point {point}")

    def test_two_digit_points_swap_both_nibbles(self) -> None:
        # 40 = 0x28 goes on the wire as 0x82.
        self.assertEqual(decode_attachment_point(0x82), 40)
        self.assertEqual(attachment_point_name(40), "avatar center")

    def test_zero_stays_zero(self) -> None:
        self.assertEqual(decode_attachment_point(0), 0)


class NameTests(unittest.TestCase):
    def test_known_points(self) -> None:
        self.assertEqual(attachment_point_name(2), "head")
        self.assertEqual(attachment_point_name(6), "right hand")
        self.assertEqual(attachment_point_name(55), "hind right foot")

    def test_unknown_point_keeps_its_number(self) -> None:
        self.assertEqual(attachment_point_name(200), "unknown point 200")

    def test_hud_points_are_identified(self) -> None:
        self.assertTrue(is_hud_attachment(31))
        self.assertTrue(is_hud_attachment(38))
        self.assertFalse(is_hud_attachment(30))
        self.assertFalse(is_hud_attachment(39))


class IsAttachmentTests(unittest.TestCase):
    def test_attach_item_id_marks_an_attachment(self) -> None:
        self.assertTrue(is_attachment(ATTACHED))

    def test_ordinary_prim_is_not_an_attachment(self) -> None:
        self.assertFalse(is_attachment({}))
        self.assertFalse(is_attachment(None))
        self.assertFalse(is_attachment({"Something": "else"}))

    def test_a_tree_is_not_read_as_an_attachment(self) -> None:
        # A tree prim also has a non-zero state byte (its species), so the
        # state alone must never be the test.
        self.assertIsNone(describe_attachment(0x30, None))


class DescribeTests(unittest.TestCase):
    def test_describes_a_body_attachment(self) -> None:
        # 0x60 unswaps to 6, right hand.
        self.assertEqual(describe_attachment(0x60, ATTACHED), "right hand (6)")

    def test_marks_hud_attachments(self) -> None:
        # 33 = 0x21 goes on the wire as 0x12.
        self.assertEqual(describe_attachment(0x12, ATTACHED), "HUD: hud top center (33)")

    def test_non_attachment_returns_none(self) -> None:
        self.assertIsNone(describe_attachment(0x60, {}))


if __name__ == "__main__":
    unittest.main()
