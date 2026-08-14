"""Tests for the animation asset decoder.

The fixture is a real asset — ``place_marker`` from the OpenSim library,
fetched over `ViewerAsset` on 2026-08-14. That matters more than usual here:
the format is a chain of variable-length blocks with no internal offsets, so
any field decoded at the wrong width silently shifts everything after it. The
strongest evidence the layout is right is that fourteen consecutive joint names
come out as real SL skeleton bones.
"""

import unittest
from pathlib import Path

from vibestorm.assets.animation import (
    KEYFRAME_SIZE,
    POSITION_RANGE,
    ROTATION_RANGE,
    U16_MAX,
    AnimationDecodeError,
    decode_animation,
    unquantise,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "test" / "fixtures" / "library" / "animation-place_marker.bin"
_BIN_BVH = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "Framework" / "Scenes"
    / "Animation" / "BinBVHAnimation.cs"
)

#: The full SL lower/upper body joint set this animation drives, in file order.
_EXPECTED_JOINTS = (
    "mCollarLeft", "mShoulderLeft", "mElbowLeft", "mWristLeft",
    "mCollarRight", "mShoulderRight", "mElbowRight", "mWristRight",
    "mHipLeft", "mKneeLeft", "mAnkleLeft",
    "mHipRight", "mKneeRight", "mAnkleRight",
)


def _fixture() -> bytes:
    return _FIXTURE.read_bytes()


class SourcePinTests(unittest.TestCase):
    def test_the_key_ranges_match_the_call_sites(self) -> None:
        if not _BIN_BVH.exists():
            self.skipTest("opensim-source not present")
        text = _BIN_BVH.read_text(encoding="utf-8", errors="replace")

        self.assertIn("readKeys(data, ref i, rotationkeys, -1f, 1f)", text)
        self.assertIn("readKeys(data, ref i, positionkeys, -5f, 5f)", text)
        self.assertEqual(ROTATION_RANGE, (-1.0, 1.0))
        self.assertEqual(POSITION_RANGE, (-5.0, 5.0))

    def test_the_quantisation_inverts_opensim_s_encoder(self) -> None:
        # FloatToUInt16 shifts lower to zero, divides by the range, scales by
        # UInt16.MaxValue. Round-tripping the endpoints and the midpoint is
        # what makes the inverse checkable without reimplementing it here.
        for lower, upper in (ROTATION_RANGE, POSITION_RANGE):
            self.assertAlmostEqual(unquantise(0, lower, upper), lower, places=5)
            self.assertAlmostEqual(unquantise(U16_MAX, lower, upper), upper, places=5)
            self.assertAlmostEqual(
                unquantise(U16_MAX // 2, lower, upper), (lower + upper) / 2, places=3
            )

    def test_opensim_stops_reading_at_the_end_of_the_joints(self) -> None:
        """Why the trailing bytes are surfaced rather than named.

        OpenSim's constructor ends its joint loop and returns; nothing in this
        tree reads what follows, so nothing in this tree can say what it is.
        """
        if not _BIN_BVH.exists():
            self.skipTest("opensim-source not present")
        text = _BIN_BVH.read_text(encoding="utf-8", errors="replace")

        self.assertNotIn("constraint", text.lower())


class RealAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _FIXTURE.exists():
            self.skipTest("library animation fixture not present")
        self.animation = decode_animation(_fixture())

    def test_the_header_decodes(self) -> None:
        self.assertEqual(self.animation.version, 1)
        self.assertEqual(self.animation.sub_version, 0)
        self.assertEqual(self.animation.priority, 2)
        self.assertAlmostEqual(self.animation.length, 0.1, places=4)
        self.assertEqual(self.animation.expression_name, "")
        self.assertTrue(self.animation.loop)
        self.assertAlmostEqual(self.animation.ease_in_time, 0.05, places=4)
        self.assertAlmostEqual(self.animation.ease_out_time, 0.05, places=4)
        self.assertEqual(self.animation.hand_pose, 1)

    def test_every_joint_name_is_a_real_skeleton_bone(self) -> None:
        # The load-bearing assertion. These are variable-length names read in
        # sequence with no offsets to resynchronise on, so a single field
        # decoded at the wrong width turns the rest into mojibake.
        self.assertEqual(self.animation.joint_names, _EXPECTED_JOINTS)

    def test_a_pose_animation_has_rotations_and_no_positions(self) -> None:
        for joint in self.animation.joints:
            self.assertEqual(len(joint.rotations), 2, joint.name)
            self.assertEqual(joint.positions, (), joint.name)
            self.assertEqual(joint.priority, 2, joint.name)

    def test_rotation_components_are_inside_the_declared_range(self) -> None:
        lower, upper = ROTATION_RANGE
        for joint in self.animation.joints:
            for key in joint.rotations:
                for component in (key.x, key.y, key.z):
                    self.assertGreaterEqual(component, lower, joint.name)
                    self.assertLessEqual(component, upper, joint.name)

    def test_key_times_lie_within_the_animation_s_own_loop_points(self) -> None:
        # A key's time is scaled against InPoint/OutPoint rather than a fixed
        # range; using a constant range instead would put times outside the
        # animation entirely.
        for joint in self.animation.joints:
            for key in joint.rotations:
                self.assertGreaterEqual(key.time, self.animation.in_point)
                self.assertLessEqual(key.time, self.animation.out_point)

    def test_the_decoder_consumes_all_but_the_four_trailing_bytes(self) -> None:
        data = _fixture()
        consumed = 4 + 8 + len(self.animation.expression_name) + 1 + 24 + 4
        for joint in self.animation.joints:
            consumed += (
                len(joint.name) + 1 + 8
                + len(joint.rotations) * KEYFRAME_SIZE
                + 4
                + len(joint.positions) * KEYFRAME_SIZE
            )

        self.assertEqual(len(data) - consumed, 4)
        self.assertEqual(self.animation.trailing, b"\x00\x00\x00\x00")

    def test_all_zero_trailing_bytes_do_not_read_as_unread_content(self) -> None:
        self.assertFalse(self.animation.has_unread_trailing_data)
        self.assertNotIn("unread", self.animation.describe())


class TrailingDataTests(unittest.TestCase):
    def test_non_zero_trailing_bytes_are_reported(self) -> None:
        # If an animation ever carries something after the joints, saying so is
        # the whole point of keeping the bytes. Silence would claim a complete
        # decode this module cannot deliver.
        if not _FIXTURE.exists():
            self.skipTest("library animation fixture not present")
        data = _fixture()[:-4] + b"\x01\x00\x00\x00"

        animation = decode_animation(data)

        self.assertTrue(animation.has_unread_trailing_data)
        self.assertIn("unread=4b", animation.describe())


class MalformedInputTests(unittest.TestCase):
    def test_an_empty_asset_raises(self) -> None:
        with self.assertRaises(AnimationDecodeError):
            decode_animation(b"")

    def test_a_truncated_asset_raises_rather_than_returning_fewer_joints(self) -> None:
        # A partial decode would read as an animation that drives fewer bones,
        # which is a specific wrong claim rather than an obvious failure.
        if not _FIXTURE.exists():
            self.skipTest("library animation fixture not present")

        with self.assertRaises(AnimationDecodeError):
            decode_animation(_fixture()[:200])

    def test_an_absurd_joint_count_fails_fast(self) -> None:
        header = (
            (1).to_bytes(2, "little") + (0).to_bytes(2, "little")
            + (2).to_bytes(4, "little") + b"\x00\x00\x00\x00"
            + b"\x00"
            + b"\x00" * 24
            + (0xFFFFFF).to_bytes(4, "little")
        )

        with self.assertRaisesRegex(AnimationDecodeError, "more than"):
            decode_animation(header)


if __name__ == "__main__":
    unittest.main()


_POSITION_FIXTURE = (
    _ROOT / "test" / "fixtures" / "library" / "animation-bouncy_ball_super.bin"
)


class PositionKeyTests(unittest.TestCase):
    """place_marker is a pose: it has no position keyframes at all.

    So the position range went untested against it, and a decoder that read
    positions with the rotation range passed every other test. This fixture is
    the largest library animation that actually moves the avatar — 43 position
    keys across 19 joints.
    """

    def setUp(self) -> None:
        if not _POSITION_FIXTURE.exists():
            self.skipTest("position-key animation fixture not present")
        self.animation = decode_animation(_POSITION_FIXTURE.read_bytes())

    def test_it_really_does_carry_position_keys(self) -> None:
        total = sum(len(joint.positions) for joint in self.animation.joints)

        self.assertEqual(total, 43)

    def test_positions_use_the_wider_range(self) -> None:
        # The load-bearing check, and a range assertion cannot make it: every
        # position component in this animation happens to fall inside -1..1
        # too, so decoding them with the rotation range yields wrong numbers
        # that still look plausible. What separates the two is the factor of
        # five between the ranges, so the value itself has to be pinned.
        pelvis = next(j for j in self.animation.joints if j.name == "mPelvis")
        first = pelvis.positions[0]

        self.assertAlmostEqual(first.x, -0.11940, places=5)
        self.assertAlmostEqual(first.y, +0.06493, places=5)
        self.assertAlmostEqual(first.z, -0.41451, places=5)

        # Decoding with the rotation range would have given exactly a fifth of
        # each, which is the failure this pin exists to catch.
        self.assertNotAlmostEqual(first.x, -0.11940 / 5, places=5)

    def test_the_pelvis_actually_moves(self) -> None:
        # Semantic evidence that these are real position samples rather than
        # correctly-shaped noise: this is a bouncing-ball animation, and the
        # pelvis height climbs over its first keyframes.
        pelvis = next(j for j in self.animation.joints if j.name == "mPelvis")
        heights = [key.z for key in pelvis.positions[:4]]

        self.assertEqual(heights, sorted(heights))
        self.assertGreater(heights[-1] - heights[0], 0.05)

    def test_a_moving_animation_still_ends_with_four_trailing_bytes(self) -> None:
        # Every one of the twelve library animations does, across 600b-4030b
        # and 8-19 joints. Consistent enough to be structural rather than an
        # accident of one file.
        self.assertEqual(self.animation.trailing, b"\x00\x00\x00\x00")


class SessionSummaryTests(unittest.TestCase):
    """The decoder's production caller: the session's asset-fetch log line."""

    def test_an_animation_is_summarised(self) -> None:
        if not _POSITION_FIXTURE.exists():
            self.skipTest("position-key animation fixture not present")
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(20, _POSITION_FIXTURE.read_bytes())

        self.assertIn("joints=19", summary)
        self.assertIn("length=2.33", summary)

    def test_a_type_with_no_decoder_is_left_alone(self) -> None:
        # Not "decoded and found empty" — nothing here has anything to say
        # about LSL source, and a note implying it looked would be noise on
        # every script fetch.
        #
        # This test named notecards until the notecard decoder landed, at
        # which point it failed for the right reason: the claim it encoded had
        # stopped being true.
        from vibestorm.udp.session import _summarize_fetched_asset

        self.assertEqual(_summarize_fetched_asset(10, b"default\n{\n}\n"), "")

    def test_undecodable_bytes_do_not_fail_a_good_fetch(self) -> None:
        # The fetch itself succeeded; a decoder that cannot read the bytes
        # must report that, not turn a delivered asset into an error.
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(20, b"not an animation")

        self.assertIn("undecodable", summary)
