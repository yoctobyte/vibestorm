"""Tests for the ObjectUpdateCompressed data blob decoder.

Compressed updates carry the bulk of object traffic in a populated region, so
anything this decoder drops is invisible for most prims. The shape block was
skipped outright until 2026-08-14: every compressed prim reported
``shape=None`` and the renderer fell back to a cube. Against the live test
region that was 30 of 32 prims.

The compressed block also does *not* use the message template's field order,
which is the trap these tests exist to hold shut: parsing it with the template
layout raises nothing, it just reports a profile curve read out of the middle
of PathBegin.
"""

import struct
import unittest
from uuid import UUID

from vibestorm.udp.messages import decode_compressed_object_data

FULL_ID = UUID("11111111-2222-3333-4444-555555555555")
OWNER_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")

PATH_CURVE_LINE = 0x10
PATH_CURVE_CIRCLE = 0x20
PROFILE_CURVE_CIRCLE = 0
PROFILE_CURVE_SQUARE = 1


def _shape_block(
    *,
    path_curve: int = PATH_CURVE_LINE,
    profile_curve: int = PROFILE_CURVE_SQUARE,
    path_begin: int = 0x0005,
    path_end: int = 0x0006,
) -> bytes:
    """23 bytes in ObjectUpdateCompressed's order (path group, then profile).

    Mirrors ``LLClientView.CreateCompressedUpdateBlockZC``.
    """
    return (
        bytes([path_curve])
        + struct.pack("<HH", path_begin, path_end)
        + bytes([100, 101, 1, 2])  # PathScaleX/Y, PathShearX/Y
        + struct.pack("<bbbbb", -3, -4, 5, -6, 7)  # twist, twist_begin, radius, taper x/y
        + bytes([1])  # PathRevolutions
        + struct.pack("<b", -8)  # PathSkew
        + bytes([profile_curve])
        + struct.pack("<HHH", 0x0009, 0x000A, 0x000B)  # profile begin/end/hollow
    )


COMPRESSED_HAS_TEXT = 0x0004
COMPRESSED_MEDIA_URL = 0x0200


def _compressed_blob(
    shape: bytes,
    *,
    pcode: int = 9,
    texture_entry: bytes = b"",
    compressed_flags: int = 0,
    optional: bytes = b"",
) -> bytes:
    """A minimal compressed entry.

    ``optional`` is inserted where the flag-gated fields sit: after OwnerID
    and before the always-present ExtraParams count byte.
    """
    blob = (
        FULL_ID.bytes
        + struct.pack("<I", 4242)  # LocalID
        + bytes([pcode, 0])  # PCode, State
        + struct.pack("<I", 7)  # CRC
        + bytes([3, 0])  # Material, ClickAction
        + struct.pack("<fff", 2.0, 2.0, 4.0)  # Scale
        + struct.pack("<fff", 10.0, 20.0, 30.0)  # Position
        + struct.pack("<fff", 0.0, 0.0, 0.0)  # Rotation XYZ
        + struct.pack("<I", compressed_flags)
        + OWNER_ID.bytes
        + optional
        + bytes([0])  # ExtraParams: count 0
        + shape
    )
    if texture_entry:
        blob += struct.pack("<HH", len(texture_entry), 0) + texture_entry
    else:
        blob += bytes(4)
    return blob


class CompressedShapeBlockTests(unittest.TestCase):
    def test_shape_block_is_decoded_not_skipped(self) -> None:
        entry = decode_compressed_object_data(
            _compressed_blob(_shape_block()), region_handle=1, time_dilation=0, update_flags=0
        )

        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.shape, "compressed prims must carry shape data")
        self.assertEqual(entry.shape.path_curve, PATH_CURVE_LINE)
        self.assertEqual(entry.shape.profile_curve, PROFILE_CURVE_SQUARE)

    def test_every_shape_field_lands_in_its_own_slot(self) -> None:
        entry = decode_compressed_object_data(
            _compressed_blob(_shape_block()), region_handle=1, time_dilation=0, update_flags=0
        )
        shape = entry.shape

        self.assertEqual(shape.path_begin, 0x0005)
        self.assertEqual(shape.path_end, 0x0006)
        self.assertEqual(shape.path_scale_x, 100)
        self.assertEqual(shape.path_scale_y, 101)
        self.assertEqual(shape.path_shear_x, 1)
        self.assertEqual(shape.path_shear_y, 2)
        self.assertEqual(shape.path_twist, -3)
        self.assertEqual(shape.path_twist_begin, -4)
        self.assertEqual(shape.path_radius_offset, 5)
        self.assertEqual(shape.path_taper_x, -6)
        self.assertEqual(shape.path_taper_y, 7)
        self.assertEqual(shape.path_revolutions, 1)
        self.assertEqual(shape.path_skew, -8)
        self.assertEqual(shape.profile_begin, 0x0009)
        self.assertEqual(shape.profile_end, 0x000A)
        self.assertEqual(shape.profile_hollow, 0x000B)

    def test_compressed_layout_is_not_the_message_template_layout(self) -> None:
        # Under the ObjectUpdate template order, profile_curve is the second
        # byte of the block — which here is the low byte of PathBegin. The
        # blob is authored so the two readings disagree, so a decoder that
        # reuses the template parser cannot pass.
        entry = decode_compressed_object_data(
            _compressed_blob(_shape_block(path_begin=0x00FE)),
            region_handle=1,
            time_dilation=0,
            update_flags=0,
        )

        self.assertEqual(entry.shape.profile_curve, PROFILE_CURVE_SQUARE)
        self.assertNotEqual(entry.shape.profile_curve, 0xFE)
        self.assertEqual(entry.shape.path_begin, 0x00FE)

    def test_shape_drives_the_renderer_classification(self) -> None:
        from vibestorm.viewer3d.scene import classify_prim_shape

        cases = {
            (PATH_CURVE_LINE, PROFILE_CURVE_SQUARE): "cube",
            (PATH_CURVE_LINE, PROFILE_CURVE_CIRCLE): "cylinder",
            (PATH_CURVE_CIRCLE, PROFILE_CURVE_CIRCLE): "torus",
        }
        for (path_curve, profile_curve), expected in cases.items():
            with self.subTest(path_curve=path_curve, profile_curve=profile_curve):
                entry = decode_compressed_object_data(
                    _compressed_blob(
                        _shape_block(path_curve=path_curve, profile_curve=profile_curve)
                    ),
                    region_handle=1,
                    time_dilation=0,
                    update_flags=0,
                )
                self.assertEqual(
                    classify_prim_shape(
                        entry.shape.path_curve, entry.shape.profile_curve
                    ),
                    expected,
                )

    def test_truncated_blob_leaves_shape_unset_rather_than_raising(self) -> None:
        # A short blob should cost the shape, not the whole update: position
        # and identity are still worth having.
        blob = _compressed_blob(_shape_block())[:90]

        entry = decode_compressed_object_data(
            blob, region_handle=1, time_dilation=0, update_flags=0
        )

        self.assertIsNotNone(entry)
        self.assertEqual(entry.local_id, 4242)
        self.assertIsNone(entry.shape)

    def test_texture_entry_still_decodes_after_the_shape_block(self) -> None:
        # The shape parse must not disturb the cursor the TextureEntry read
        # depends on.
        texture_id = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        entry = decode_compressed_object_data(
            _compressed_blob(_shape_block(), texture_entry=texture_id.bytes + bytes(9)),
            region_handle=1,
            time_dilation=0,
            update_flags=0,
        )

        self.assertEqual(entry.default_texture_id, texture_id)


class CompressedHoverTextTests(unittest.TestCase):
    """Floating text was skipped over rather than decoded.

    The alpha byte is the trap: OpenSim writes ``argb ^ 0xff000000``
    (``LLUDPZeroEncoder.AddColorArgb``), so opaque text goes out as alpha 0.
    Reading it as-is makes every ordinary hover text look fully transparent.
    """

    def _decode(self, *, text: bytes, color: bytes):
        return decode_compressed_object_data(
            _compressed_blob(
                _shape_block(),
                compressed_flags=COMPRESSED_HAS_TEXT,
                optional=text + b"\x00" + color,
            ),
            region_handle=1,
            time_dilation=0,
            update_flags=0,
        )

    def test_hover_text_is_decoded(self) -> None:
        entry = self._decode(text="Vendor: 250 L$".encode(), color=bytes([255, 128, 0, 0]))

        self.assertEqual(entry.hover_text, "Vendor: 250 L$")
        self.assertEqual(entry.text_size, len("Vendor: 250 L$") + 1)

    def test_opaque_text_arrives_as_alpha_zero_on_the_wire(self) -> None:
        entry = self._decode(text=b"hi", color=bytes([255, 128, 0, 0x00]))

        self.assertEqual(entry.hover_text_color, (255, 128, 0, 255))

    def test_transparent_text_arrives_as_alpha_255_on_the_wire(self) -> None:
        entry = self._decode(text=b"hi", color=bytes([1, 2, 3, 0xFF]))

        self.assertEqual(entry.hover_text_color, (1, 2, 3, 0))

    def test_text_flag_does_not_disturb_the_shape_or_texture_cursor(self) -> None:
        texture_id = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        entry = decode_compressed_object_data(
            _compressed_blob(
                _shape_block(profile_curve=PROFILE_CURVE_CIRCLE),
                compressed_flags=COMPRESSED_HAS_TEXT,
                optional=b"floating" + b"\x00" + bytes([9, 9, 9, 0]),
                texture_entry=texture_id.bytes + bytes(9),
            ),
            region_handle=1,
            time_dilation=0,
            update_flags=0,
        )

        self.assertEqual(entry.hover_text, "floating")
        self.assertEqual(entry.shape.profile_curve, PROFILE_CURVE_CIRCLE)
        self.assertEqual(entry.default_texture_id, texture_id)

    def test_media_url_is_decoded(self) -> None:
        entry = decode_compressed_object_data(
            _compressed_blob(
                _shape_block(),
                compressed_flags=COMPRESSED_MEDIA_URL,
                optional=b"http://example.invalid/stream\x00",
            ),
            region_handle=1,
            time_dilation=0,
            update_flags=0,
        )

        self.assertEqual(entry.media_url, "http://example.invalid/stream")

    def test_prim_without_the_text_flag_reports_no_hover_text(self) -> None:
        entry = decode_compressed_object_data(
            _compressed_blob(_shape_block()), region_handle=1, time_dilation=0, update_flags=0
        )

        self.assertIsNone(entry.hover_text)
        self.assertIsNone(entry.hover_text_color)
        self.assertEqual(entry.text_size, 0)


if __name__ == "__main__":
    unittest.main()
