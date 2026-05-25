"""Tests for the full multi-section TextureEntry parser.

Each test constructs a minimal valid blob that exercises one or more of
the new sections (color, repeats, offsets, rotation, material, media,
glow, material_id) and verifies parse → query round-trips.

Reference byte layout per section:
    default_value (fixed width)
    ( face_mask_varint + value )*   — zero or more overrides
    0x00                            — zero face_mask terminator

Face mask encoding (MSB-first 7-bit groups):
    Face 0 → 0x01  (bit 0 set)
    Face 1 → 0x02  (bit 1 set)
    Face 8 → 0x82, 0x00  (bit 8 set)
"""

import math
import struct
import unittest
from uuid import UUID

from vibestorm.world.texture_entry import (
    MATERIAL_FLAG_FULLBRIGHT,
    TextureEntry,
    TextureEntryDecodeError,
    parse_texture_entry,
)

_DEFAULT_UUID = UUID("11111111-1111-1111-1111-111111111111")
_FACE_UUID = UUID("22222222-2222-2222-2222-222222222222")
_MATERIAL_UUID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

_NULL_UUID = UUID("00000000-0000-0000-0000-000000000000")


def _uuid_section(default_id: UUID, overrides: dict[int, UUID] | None = None) -> bytes:
    """Build the UUID section bytes (default + face overrides + 0x00 terminator)."""
    data = bytearray(default_id.bytes)
    for face_bits, uuid in (overrides or {}).items():
        data += _encode_face_mask(face_bits)
        data += uuid.bytes
    data += b"\x00"
    return bytes(data)


def _encode_face_mask(face_index: int) -> bytes:
    """Encode a single face index as a MSB-first 7-bit face mask."""
    bit = 1 << face_index
    groups = []
    while bit:
        groups.append(bit & 0x7F)
        bit >>= 7
    result = bytearray()
    for i, g in enumerate(reversed(groups)):
        if i < len(groups) - 1:
            result.append(g | 0x80)
        else:
            result.append(g)
    return bytes(result)


def _section(value: bytes, overrides: dict[int, bytes] | None = None) -> bytes:
    """Build a generic section: default + face overrides + 0x00 terminator."""
    data = bytearray(value)
    for face_index, val in (overrides or {}).items():
        data += _encode_face_mask(face_index)
        data += val
    data += b"\x00"
    return bytes(data)


def _f32(v: float) -> bytes:
    return struct.pack("<f", v)


def _i16(v: int) -> bytes:
    return struct.pack("<h", v)


def _full_blob(
    *,
    default_uuid: UUID = _DEFAULT_UUID,
    uuid_overrides: dict[int, UUID] | None = None,
    default_color: tuple[int, int, int, int] = (255, 255, 255, 255),
    color_overrides: dict[int, tuple[int, int, int, int]] | None = None,
    default_repeat_u: float = 1.0,
    repeat_u_overrides: dict[int, float] | None = None,
    default_repeat_v: float = 1.0,
    repeat_v_overrides: dict[int, float] | None = None,
    default_offset_u: float = 0.0,
    offset_u_overrides: dict[int, float] | None = None,
    default_offset_v: float = 0.0,
    offset_v_overrides: dict[int, float] | None = None,
    default_rotation: float = 0.0,
    rotation_overrides: dict[int, float] | None = None,
    default_material: int = 0,
    material_overrides: dict[int, int] | None = None,
    default_media: int = 0,
    media_overrides: dict[int, int] | None = None,
    default_glow: float = 0.0,
    glow_overrides: dict[int, float] | None = None,
    material_id: UUID | None = None,
    material_id_overrides: dict[int, UUID] | None = None,
) -> bytes:
    """Construct a full TE blob for testing."""
    blob = _uuid_section(default_uuid, uuid_overrides)
    blob += _section(bytes(default_color), {f: bytes(c) for f, c in (color_overrides or {}).items()})
    blob += _section(_f32(default_repeat_u), {f: _f32(v) for f, v in (repeat_u_overrides or {}).items()})
    blob += _section(_f32(default_repeat_v), {f: _f32(v) for f, v in (repeat_v_overrides or {}).items()})
    blob += _section(_i16(int(default_offset_u * 32767)), {f: _i16(int(v * 32767)) for f, v in (offset_u_overrides or {}).items()})
    blob += _section(_i16(int(default_offset_v * 32767)), {f: _i16(int(v * 32767)) for f, v in (offset_v_overrides or {}).items()})
    rot_raw = int(default_rotation / math.pi * 32768)
    blob += _section(_i16(rot_raw), {f: _i16(int(v / math.pi * 32768)) for f, v in (rotation_overrides or {}).items()})
    blob += _section(bytes([default_material]), {f: bytes([v]) for f, v in (material_overrides or {}).items()})
    blob += _section(bytes([default_media]), {f: bytes([v]) for f, v in (media_overrides or {}).items()})
    blob += _section(bytes([round(default_glow * 255)]), {f: bytes([round(v * 255)]) for f, v in (glow_overrides or {}).items()})
    if material_id is not None or material_id_overrides:
        mid = material_id or _NULL_UUID
        blob += _section(mid.bytes, {f: u.bytes for f, u in (material_id_overrides or {}).items()})
    return blob


class TextureEntryColorTests(unittest.TestCase):
    def test_default_color_parsed(self) -> None:
        blob = _full_blob(default_color=(200, 100, 50, 255))
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.default_color, (200, 100, 50, 255))
        self.assertEqual(te.color_for_face(0), (200, 100, 50, 255))
        self.assertEqual(te.color_for_face(5), (200, 100, 50, 255))

    def test_face_color_override(self) -> None:
        blob = _full_blob(
            default_color=(255, 255, 255, 255),
            color_overrides={2: (10, 20, 30, 128)},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.color_for_face(0), (255, 255, 255, 255))
        self.assertEqual(te.color_for_face(2), (10, 20, 30, 128))

    def test_color_fallback_when_section_absent(self) -> None:
        # UUID section only — no color section.
        blob = _uuid_section(_DEFAULT_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertIsNone(te.default_color)
        self.assertEqual(te.color_for_face(0), (255, 255, 255, 255))


class TextureEntryRepeatTests(unittest.TestCase):
    def test_default_repeat_u_and_v(self) -> None:
        blob = _full_blob(default_repeat_u=2.0, default_repeat_v=4.0)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.default_repeat_u, 2.0, places=5)
        self.assertAlmostEqual(te.default_repeat_v, 4.0, places=5)
        self.assertAlmostEqual(te.repeat_u_for_face(0), 2.0, places=5)
        self.assertAlmostEqual(te.repeat_v_for_face(0), 4.0, places=5)

    def test_face_repeat_override(self) -> None:
        blob = _full_blob(
            default_repeat_u=1.0,
            repeat_u_overrides={3: 0.5},
            default_repeat_v=1.0,
            repeat_v_overrides={3: 3.0},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.repeat_u_for_face(0), 1.0, places=5)
        self.assertAlmostEqual(te.repeat_u_for_face(3), 0.5, places=5)
        self.assertAlmostEqual(te.repeat_v_for_face(3), 3.0, places=5)

    def test_default_repeat_when_section_absent(self) -> None:
        blob = _uuid_section(_DEFAULT_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.repeat_u_for_face(0), 1.0)
        self.assertAlmostEqual(te.repeat_v_for_face(0), 1.0)


class TextureEntryOffsetTests(unittest.TestCase):
    def test_default_offset_zero(self) -> None:
        blob = _full_blob(default_offset_u=0.0, default_offset_v=0.0)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.default_offset_u, 0.0, places=4)
        self.assertAlmostEqual(te.default_offset_v, 0.0, places=4)

    def test_offset_half(self) -> None:
        blob = _full_blob(default_offset_u=0.5, default_offset_v=-0.5)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.offset_u_for_face(0), 0.5, places=3)
        self.assertAlmostEqual(te.offset_v_for_face(0), -0.5, places=3)

    def test_face_offset_override(self) -> None:
        blob = _full_blob(
            default_offset_u=0.0,
            offset_u_overrides={1: 0.25},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.offset_u_for_face(0), 0.0, places=3)
        self.assertAlmostEqual(te.offset_u_for_face(1), 0.25, places=3)


class TextureEntryRotationTests(unittest.TestCase):
    def test_default_rotation_zero(self) -> None:
        blob = _full_blob(default_rotation=0.0)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.default_rotation, 0.0, places=4)
        self.assertAlmostEqual(te.rotation_for_face(0), 0.0, places=4)

    def test_rotation_quarter_pi(self) -> None:
        blob = _full_blob(default_rotation=math.pi / 4)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.rotation_for_face(0), math.pi / 4, places=3)

    def test_face_rotation_override(self) -> None:
        blob = _full_blob(
            default_rotation=0.0,
            rotation_overrides={5: math.pi / 2},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.rotation_for_face(0), 0.0, places=3)
        self.assertAlmostEqual(te.rotation_for_face(5), math.pi / 2, places=3)


class TextureEntryMaterialTests(unittest.TestCase):
    def test_fullbright_flag(self) -> None:
        blob = _full_blob(default_material=MATERIAL_FLAG_FULLBRIGHT)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.default_material_flags, MATERIAL_FLAG_FULLBRIGHT)
        self.assertTrue(te.fullbright_for_face(0))

    def test_no_fullbright(self) -> None:
        blob = _full_blob(default_material=0)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertFalse(te.fullbright_for_face(0))

    def test_face_material_override(self) -> None:
        blob = _full_blob(
            default_material=0,
            material_overrides={4: MATERIAL_FLAG_FULLBRIGHT},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertFalse(te.fullbright_for_face(0))
        self.assertTrue(te.fullbright_for_face(4))

    def test_material_flags_absent_defaults_to_zero(self) -> None:
        blob = _uuid_section(_DEFAULT_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.material_flags_for_face(0), 0)
        self.assertFalse(te.fullbright_for_face(0))


class TextureEntryGlowTests(unittest.TestCase):
    def test_default_glow_zero(self) -> None:
        blob = _full_blob(default_glow=0.0)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.default_glow, 0.0, places=3)
        self.assertAlmostEqual(te.glow_for_face(0), 0.0, places=3)

    def test_default_glow_half(self) -> None:
        blob = _full_blob(default_glow=0.5)
        te = parse_texture_entry(blob)
        assert te is not None
        # 0.5 * 255 = 127.5 → rounds to 128, 128/255 ≈ 0.502
        self.assertAlmostEqual(te.glow_for_face(0), 0.5, delta=0.005)

    def test_face_glow_override(self) -> None:
        blob = _full_blob(default_glow=0.0, glow_overrides={2: 1.0})
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.glow_for_face(0), 0.0, places=3)
        self.assertAlmostEqual(te.glow_for_face(2), 1.0, places=3)

    def test_glow_absent_defaults_to_zero(self) -> None:
        blob = _uuid_section(_DEFAULT_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertAlmostEqual(te.glow_for_face(0), 0.0)


class TextureEntryMaterialIdTests(unittest.TestCase):
    def test_default_material_id(self) -> None:
        blob = _full_blob(material_id=_MATERIAL_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.default_material_id, _MATERIAL_UUID)
        self.assertEqual(te.material_id_for_face(0), _MATERIAL_UUID)

    def test_face_material_id_override(self) -> None:
        other = UUID("99999999-9999-9999-9999-999999999999")
        blob = _full_blob(
            material_id=_MATERIAL_UUID,
            material_id_overrides={1: other},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.material_id_for_face(0), _MATERIAL_UUID)
        self.assertEqual(te.material_id_for_face(1), other)

    def test_no_material_id_section(self) -> None:
        blob = _full_blob()  # no material_id arg → section absent
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertIsNone(te.default_material_id)
        self.assertIsNone(te.material_id_for_face(0))


class TextureEntryMultiFaceTests(unittest.TestCase):
    def test_face_8_mask_encoding(self) -> None:
        """Face 8 uses 2-byte mask [0x82, 0x00]."""
        blob = _full_blob(
            uuid_overrides={8: _FACE_UUID},
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.texture_for_face(8), _FACE_UUID)
        self.assertEqual(te.texture_for_face(0), _DEFAULT_UUID)

    def test_all_sections_combined(self) -> None:
        blob = _full_blob(
            default_uuid=_DEFAULT_UUID,
            uuid_overrides={2: _FACE_UUID},
            default_color=(128, 64, 32, 255),
            default_repeat_u=2.5,
            default_repeat_v=0.5,
            default_offset_u=0.25,
            default_offset_v=-0.25,
            default_rotation=math.pi / 3,
            default_material=MATERIAL_FLAG_FULLBRIGHT,
            default_glow=0.2,
            material_id=_MATERIAL_UUID,
        )
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.texture_for_face(0), _DEFAULT_UUID)
        self.assertEqual(te.texture_for_face(2), _FACE_UUID)
        self.assertEqual(te.color_for_face(0), (128, 64, 32, 255))
        self.assertAlmostEqual(te.repeat_u_for_face(0), 2.5, places=4)
        self.assertAlmostEqual(te.repeat_v_for_face(0), 0.5, places=4)
        self.assertAlmostEqual(te.offset_u_for_face(0), 0.25, places=3)
        self.assertAlmostEqual(te.offset_v_for_face(0), -0.25, places=3)
        self.assertAlmostEqual(te.rotation_for_face(0), math.pi / 3, places=3)
        self.assertTrue(te.fullbright_for_face(0))
        self.assertAlmostEqual(te.glow_for_face(0), 0.2, delta=0.005)
        self.assertEqual(te.material_id_for_face(0), _MATERIAL_UUID)

    def test_truncated_at_uuid_section_raises(self) -> None:
        with self.assertRaises(TextureEntryDecodeError):
            parse_texture_entry(b"\x00" * 15)

    def test_uuid_only_blob_gives_section_defaults(self) -> None:
        blob = _uuid_section(_DEFAULT_UUID)
        te = parse_texture_entry(blob)
        assert te is not None
        self.assertEqual(te.default_texture_id, _DEFAULT_UUID)
        self.assertIsNone(te.default_color)
        self.assertAlmostEqual(te.default_repeat_u, 1.0)
        self.assertAlmostEqual(te.default_repeat_v, 1.0)
        self.assertAlmostEqual(te.default_offset_u, 0.0)
        self.assertAlmostEqual(te.default_offset_v, 0.0)
        self.assertAlmostEqual(te.default_rotation, 0.0)
        self.assertEqual(te.default_material_flags, 0)
        self.assertEqual(te.default_media_flags, 0)
        self.assertAlmostEqual(te.default_glow, 0.0)
        self.assertIsNone(te.default_material_id)


if __name__ == "__main__":
    unittest.main()
