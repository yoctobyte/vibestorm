"""Tests for the gesture asset decoder.

The fixture is a real asset — ``can_we_move_along`` from the OpenSim library,
fetched over `ViewerAsset` on 2026-08-14. The format is line-oriented with no
lengths or offsets, so the failure mode is not mojibake but *misalignment*: a
step whose field count is read wrong shifts every following step by a line
while still producing plausible-looking output.
"""

import re
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.assets.gesture import (
    HEADER_LINE_COUNT,
    STEP_ANIMATION,
    STEP_CHAT,
    STEP_SOUND,
    STEP_WAIT,
    GestureDecodeError,
    decode_gesture,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "test" / "fixtures" / "library" / "gesture-can_we_move_along.bin"
_GATHERER = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "Framework" / "Scenes"
    / "UuidGatherer.cs"
)

_ANIM_ID = UUID("b906c4ba-703b-1940-32a3-0c7f7d791510")


def _gesture(*, steps: str, trigger: str = "/hi", count: int | None = None) -> bytes:
    lines = steps.split("\n") if steps else []
    step_count = count if count is not None else _step_count(steps)
    body = "\n".join(["2", "255", "0", trigger, "", str(step_count)] + lines)
    return (body + "\n").encode()


def _step_count(steps: str) -> int:
    """How many steps `steps` actually spells out, by walking its type lines."""
    lines = steps.split("\n") if steps else []
    index = count = 0
    while index < len(lines):
        index += 4 if int(lines[index]) in (STEP_ANIMATION, STEP_SOUND) else 3
        count += 1
    return count


class SourcePinTests(unittest.TestCase):
    """The whole specification available in this tree is one UUID scraper."""

    def test_the_header_lines_match_the_ones_opensim_skips(self) -> None:
        if not _GATHERER.exists():
            self.skipTest("opensim-source not present")
        text = _GATHERER.read_text(encoding="utf-8", errors="replace")
        walker = text[text.index("RecordGestureAssetUuids(AssetBase") :][:900]

        # Five skips before the step count, and the source names each one.
        skipped = re.findall(r"SkipLine\(\)\) // (\w+)", walker)

        self.assertEqual(skipped[:5], ["version", "key", "mask", "trigger", "replace"])
        self.assertEqual(HEADER_LINE_COUNT, 5)

    def test_the_four_step_types_are_the_ones_opensim_switches_on(self) -> None:
        if not _GATHERER.exists():
            self.skipTest("opensim-source not present")
        text = _GATHERER.read_text(encoding="utf-8", errors="replace")

        self.assertIn("case 0: // animation", text)
        self.assertIn("case 1: // sound", text)
        self.assertIn("case 2: // chat", text)
        self.assertIn("case 3: // wait", text)
        self.assertEqual(
            (STEP_ANIMATION, STEP_SOUND, STEP_CHAT, STEP_WAIT), (0, 1, 2, 3)
        )

    def test_opensim_gives_up_on_an_unknown_type_too(self) -> None:
        # This decoder raises there, and the source is why: an unknown type has
        # an unknown field count, so nothing after it can be located.
        if not _GATHERER.exists():
            self.skipTest("opensim-source not present")
        text = _GATHERER.read_text(encoding="utf-8", errors="replace")

        self.assertIn("return; // no idea", text)


class RealAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _FIXTURE.exists():
            self.skipTest("library gesture fixture not present")
        self.gesture = decode_gesture(_FIXTURE.read_bytes())

    def test_the_header_decodes(self) -> None:
        self.assertEqual(self.gesture.version, 2)
        self.assertEqual(self.gesture.key, 255)
        self.assertEqual(self.gesture.mask, 0)
        self.assertEqual(self.gesture.trigger, "/bored")
        self.assertEqual(self.gesture.replace_with, "")

    def test_the_single_step_plays_an_animation(self) -> None:
        (step,) = self.gesture.steps

        self.assertEqual(step.step_type, STEP_ANIMATION)
        self.assertEqual(step.name, "Bored")
        self.assertEqual(step.asset_id, _ANIM_ID)
        self.assertEqual(step.flags, 0)

    def test_the_referenced_asset_is_what_opensim_reads_the_file_for(self) -> None:
        self.assertEqual(self.gesture.asset_ids, (_ANIM_ID,))

    def test_the_trailing_nul_line_does_not_become_a_step(self) -> None:
        # The asset ends with a NUL byte on its own line after the last step.
        # A decoder that kept reading to end-of-file rather than stopping at
        # the declared count would try to parse it as a step type.
        self.assertTrue(_FIXTURE.read_bytes().rstrip(b"\n").endswith(b"\x00"))
        self.assertEqual(len(self.gesture.steps), 1)

    def test_it_describes_itself_by_trigger(self) -> None:
        description = self.gesture.describe()

        self.assertIn("trigger=/bored", description)
        self.assertIn("animation:Bored", description)


class StepTypeTests(unittest.TestCase):
    def test_a_sound_step_reads_the_same_shape_as_an_animation(self) -> None:
        gesture = decode_gesture(
            _gesture(steps="1\nDing\n11111111-2222-3333-4444-555555555555\n0")
        )
        (step,) = gesture.steps

        self.assertEqual(step.step_type, STEP_SOUND)
        self.assertEqual(step.name, "Ding")
        self.assertEqual(gesture.asset_ids, (UUID("11111111-2222-3333-4444-555555555555"),))

    def test_chat_and_wait_steps_carry_no_asset(self) -> None:
        gesture = decode_gesture(_gesture(steps="2\nhello there\n0\n3\n2.5\n0"))
        chat, wait = gesture.steps

        self.assertEqual((chat.step_type, chat.value), (STEP_CHAT, "hello there"))
        self.assertEqual((wait.step_type, wait.value), (STEP_WAIT, "2.5"))
        self.assertEqual(gesture.asset_ids, ())

    def test_a_two_line_step_does_not_shift_the_steps_after_it(self) -> None:
        # The load-bearing check. Chat and wait have three lines to an
        # animation's four; reading either at the wrong width would make the
        # following step's type line land on a value line, and the result
        # would still look like a gesture.
        gesture = decode_gesture(
            _gesture(steps="2\nfirst\n0\n0\nWave\n" + str(_ANIM_ID) + "\n0\n3\n1\n0")
        )

        self.assertEqual(
            [step.step_type for step in gesture.steps],
            [STEP_CHAT, STEP_ANIMATION, STEP_WAIT],
        )
        self.assertEqual(gesture.steps[1].name, "Wave")
        self.assertEqual(gesture.steps[2].value, "1")

    def test_a_chat_step_containing_a_number_is_not_mistaken_for_a_type(self) -> None:
        # Chat text is arbitrary, so a step whose text is "0" would look
        # exactly like an animation type line to anything scanning for types
        # rather than counting fields.
        gesture = decode_gesture(_gesture(steps="2\n0\n0\n3\n5\n0"))

        self.assertEqual(
            [step.step_type for step in gesture.steps], [STEP_CHAT, STEP_WAIT]
        )


class MalformedInputTests(unittest.TestCase):
    def test_an_empty_asset_raises(self) -> None:
        with self.assertRaises(GestureDecodeError):
            decode_gesture(b"")

    def test_a_header_with_no_step_count_raises(self) -> None:
        with self.assertRaises(GestureDecodeError):
            decode_gesture(b"2\n255\n0\n/hi\n")

    def test_an_unknown_step_type_raises_rather_than_being_skipped(self) -> None:
        # Skipping it would need a field count nothing supplies.
        with self.assertRaisesRegex(GestureDecodeError, "unknown type 9"):
            decode_gesture(_gesture(steps="9\nwhat\n0", count=1))

    def test_a_truncated_step_raises_rather_than_returning_fewer(self) -> None:
        # A gesture that plays two animations is not a gesture that plays one.
        with self.assertRaisesRegex(GestureDecodeError, "truncated"):
            decode_gesture(_gesture(steps="0\nWave\n" + str(_ANIM_ID) + "\n0", count=2))

    def test_an_unreadable_asset_id_raises(self) -> None:
        with self.assertRaisesRegex(GestureDecodeError, "asset id"):
            decode_gesture(_gesture(steps="0\nWave\nnot-a-uuid\n0", count=1))

    def test_a_non_numeric_version_raises(self) -> None:
        with self.assertRaises(GestureDecodeError):
            decode_gesture(b"x\n255\n0\n/hi\n\n0\n")


class SessionSummaryTests(unittest.TestCase):
    def test_a_gesture_is_summarised(self) -> None:
        if not _FIXTURE.exists():
            self.skipTest("library gesture fixture not present")
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(21, _FIXTURE.read_bytes())

        self.assertIn("trigger=/bored", summary)
        self.assertIn("steps=1", summary)

    def test_undecodable_bytes_do_not_fail_a_good_fetch(self) -> None:
        from vibestorm.udp.session import _summarize_fetched_asset

        self.assertIn("undecodable gesture", _summarize_fetched_asset(21, b"nope"))


if __name__ == "__main__":
    unittest.main()
