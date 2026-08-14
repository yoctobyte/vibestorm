"""Tests for the TextureAnim block.

Layout and flag values come from OpenSim, not from memory:
``SceneObjectPart.AddTextureAnimation`` writes the 16 bytes, and the mode bits
are the LSL constants ANIM_ON..SCALE in ``LSL_Constants.cs``.
"""

import struct
import unittest

from vibestorm.world.texture_anim import (
    TEXTURE_ANIM_ALL_FACES,
    TEXTURE_ANIM_LOOP,
    TEXTURE_ANIM_ON,
    TEXTURE_ANIM_PING_PONG,
    TEXTURE_ANIM_REVERSE,
    TEXTURE_ANIM_ROTATE,
    TEXTURE_ANIM_SCALE,
    TEXTURE_ANIM_SMOOTH,
    decode_texture_animation,
)


def _block(flags=TEXTURE_ANIM_ON, face=0, size_x=4, size_y=2,
           start=0.0, length=8.0, rate=1.5) -> bytes:
    return (
        bytes([flags])
        + struct.pack("<b", face)
        + bytes([size_x, size_y])
        + struct.pack("<fff", start, length, rate)
    )


class FlagValueTests(unittest.TestCase):
    def test_mode_bits_match_the_lsl_constants(self) -> None:
        self.assertEqual(TEXTURE_ANIM_ON, 1)
        self.assertEqual(TEXTURE_ANIM_LOOP, 2)
        self.assertEqual(TEXTURE_ANIM_REVERSE, 4)
        self.assertEqual(TEXTURE_ANIM_PING_PONG, 8)
        self.assertEqual(TEXTURE_ANIM_SMOOTH, 16)
        self.assertEqual(TEXTURE_ANIM_ROTATE, 32)
        self.assertEqual(TEXTURE_ANIM_SCALE, 64)


class DecodeTests(unittest.TestCase):
    def test_all_fields_land_in_their_slots(self) -> None:
        animation = decode_texture_animation(
            _block(flags=TEXTURE_ANIM_ON | TEXTURE_ANIM_LOOP, face=3,
                   size_x=4, size_y=2, start=1.0, length=8.0, rate=2.5)
        )

        self.assertEqual(animation.flags, TEXTURE_ANIM_ON | TEXTURE_ANIM_LOOP)
        self.assertEqual(animation.face, 3)
        self.assertEqual(animation.size_x, 4)
        self.assertEqual(animation.size_y, 2)
        self.assertAlmostEqual(animation.start, 1.0)
        self.assertAlmostEqual(animation.length, 8.0)
        self.assertAlmostEqual(animation.rate, 2.5)

    def test_face_is_signed_so_minus_one_means_all_faces(self) -> None:
        # Reading Face as unsigned turns "every face" into face 255.
        animation = decode_texture_animation(_block(face=-1))

        self.assertEqual(animation.face, TEXTURE_ANIM_ALL_FACES)
        self.assertTrue(animation.applies_to_all_faces)

    def test_a_specific_face_is_not_all_faces(self) -> None:
        self.assertFalse(decode_texture_animation(_block(face=0)).applies_to_all_faces)

    def test_switched_off_arrives_as_an_empty_block(self) -> None:
        # OpenSim sends no bytes rather than 16 bytes with a cleared flag, so
        # "absent" and "off" are the same wire state.
        self.assertIsNone(decode_texture_animation(b""))
        self.assertIsNone(decode_texture_animation(None))

    def test_a_short_block_is_not_an_error(self) -> None:
        self.assertIsNone(decode_texture_animation(b"\x01\x00\x04"))

    def test_mode_names_list_only_the_set_bits(self) -> None:
        animation = decode_texture_animation(
            _block(flags=TEXTURE_ANIM_ON | TEXTURE_ANIM_SMOOTH | TEXTURE_ANIM_ROTATE)
        )

        self.assertEqual(animation.mode_names(), ("on", "smooth", "rotate"))
        self.assertTrue(animation.is_rotation)
        self.assertFalse(animation.is_scale)


class DescribeTests(unittest.TestCase):
    def test_flipbook_reports_its_grid(self) -> None:
        text = decode_texture_animation(
            _block(flags=TEXTURE_ANIM_ON | TEXTURE_ANIM_LOOP, face=-1, size_x=4, size_y=2)
        ).describe()

        self.assertIn("on, loop", text)
        self.assertIn("all faces", text)
        self.assertIn("grid 4x2", text)

    def test_rotation_reports_angles_not_a_grid(self) -> None:
        # Under ROTATE and SCALE the grid is unused, so printing "0x0 frames"
        # there would be actively misleading.
        text = decode_texture_animation(
            _block(flags=TEXTURE_ANIM_ON | TEXTURE_ANIM_ROTATE, size_x=0, size_y=0,
                   start=0.5, length=3.0)
        ).describe()

        self.assertNotIn("grid", text)
        self.assertIn("start 0.50", text)

    def test_a_cleared_flag_word_reads_as_off(self) -> None:
        self.assertIn("off", decode_texture_animation(_block(flags=0)).describe())


class InspectorRowTests(unittest.TestCase):
    def test_the_inspector_shows_the_animation(self) -> None:
        from vibestorm.viewer3d.hud import _inspector_detail_html

        class _Entity:
            local_id = 1
            pcode = 9
            kind = "prim"
            position = (0.0, 0.0, 0.0)
            scale = (1.0, 1.0, 1.0)
            rotation = (0.0, 0.0, 0.0, 1.0)
            rotation_z_radians = 0.0
            name = "animated"
            shape = "cube"
            default_texture_id = None
            texture_entry = None
            extra_params = None
            hover_text = None
            hover_text_color = None

        class _World:
            texture_animation = decode_texture_animation(
                _block(flags=TEXTURE_ANIM_ON | TEXTURE_ANIM_LOOP, face=-1)
            )
            media_url = None
            sound_id = None
            full_id = None

        html = _inspector_detail_html(_Entity(), _World())

        self.assertIn("Texture Anim:", html)
        self.assertIn("all faces", html)


if __name__ == "__main__":
    unittest.main()
