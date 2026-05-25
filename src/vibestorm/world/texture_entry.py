"""Parser for Second Life/OpenSim TextureEntry blobs.

Wire format — sections are contiguous, each with the same structure:
    default_value (fixed width per section)
    (face_mask_varint + value)* — zero or more per-face overrides
    0x00                        — zero face_mask terminates the section

Sections in order:
    1.  Texture UUID       16 bytes
    2.  Color RGBA          4 bytes  (R G B A uint8)
    3.  Repeat U (scale)    4 bytes  float32 LE; default 1.0
    4.  Repeat V            4 bytes  float32 LE; default 1.0
    5.  Offset U            2 bytes  int16 LE / 32767.0 → −1..1; default 0.0
    6.  Offset V            2 bytes  int16 LE / 32767.0 → −1..1; default 0.0
    7.  Rotation            2 bytes  int16 LE / 32768.0 * π → −π..π; default 0.0
    8.  Material flags      1 byte   uint8 (shiny/bump/fullbright packed)
    9.  Media flags         1 byte   uint8
   10.  Glow                1 byte   uint8 / 255.0 → 0..1; default 0.0
   11.  Material ID UUID   16 bytes  optional; absent in pre-materials blobs

Face masks use MSB-first 7-bit group encoding (same as AgentSetAppearance TE):
    faceBits = (faceBits << 7) | (byte & 0x7F),  bit-7 = continuation flag.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from uuid import UUID

# Material flags byte bit layout.
MATERIAL_FLAG_FULLBRIGHT = 0x20   # bit 5
MATERIAL_FLAG_SHINY_MASK = 0xC0   # bits 6-7
MATERIAL_FLAG_BUMP_MASK = 0x1F    # bits 0-4


class TextureEntryDecodeError(ValueError):
    """Raised when a TextureEntry blob is structurally truncated."""


@dataclass(slots=True, frozen=True)
class TextureEntry:
    # --- Section 1: Texture UUIDs ---
    default_texture_id: UUID | None
    face_texture_ids: tuple[tuple[int, UUID], ...] = ()

    # --- Section 2: Color (RGBA uint8 each) ---
    default_color: tuple[int, int, int, int] | None = None
    face_colors: tuple[tuple[int, tuple[int, int, int, int]], ...] = ()

    # --- Section 3: Repeat U ---
    default_repeat_u: float = 1.0
    face_repeat_us: tuple[tuple[int, float], ...] = ()

    # --- Section 4: Repeat V ---
    default_repeat_v: float = 1.0
    face_repeat_vs: tuple[tuple[int, float], ...] = ()

    # --- Section 5: Offset U ---
    default_offset_u: float = 0.0
    face_offset_us: tuple[tuple[int, float], ...] = ()

    # --- Section 6: Offset V ---
    default_offset_v: float = 0.0
    face_offset_vs: tuple[tuple[int, float], ...] = ()

    # --- Section 7: Rotation (radians) ---
    default_rotation: float = 0.0
    face_rotations: tuple[tuple[int, float], ...] = ()

    # --- Section 8: Material flags (raw uint8) ---
    default_material_flags: int = 0
    face_material_flags: tuple[tuple[int, int], ...] = ()

    # --- Section 9: Media flags (raw uint8) ---
    default_media_flags: int = 0
    face_media_flags_list: tuple[tuple[int, int], ...] = ()

    # --- Section 10: Glow ---
    default_glow: float = 0.0
    face_glows: tuple[tuple[int, float], ...] = ()

    # --- Section 11: Material ID (optional) ---
    default_material_id: UUID | None = None
    face_material_ids: tuple[tuple[int, UUID], ...] = ()

    # ---------------------------------------------------------------- queries

    def texture_for_face(self, face_index: int) -> UUID | None:
        for face, texture_id in self.face_texture_ids:
            if face == face_index:
                return texture_id
        return self.default_texture_id

    def color_for_face(self, face_index: int) -> tuple[int, int, int, int]:
        for face, color in self.face_colors:
            if face == face_index:
                return color
        return self.default_color if self.default_color is not None else (255, 255, 255, 255)

    def repeat_u_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_repeat_us, self.default_repeat_u)

    def repeat_v_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_repeat_vs, self.default_repeat_v)

    def offset_u_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_offset_us, self.default_offset_u)

    def offset_v_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_offset_vs, self.default_offset_v)

    def rotation_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_rotations, self.default_rotation)

    def material_flags_for_face(self, face_index: int) -> int:
        return _face_lookup(face_index, self.face_material_flags, self.default_material_flags)

    def media_flags_for_face(self, face_index: int) -> int:
        return _face_lookup(face_index, self.face_media_flags_list, self.default_media_flags)

    def glow_for_face(self, face_index: int) -> float:
        return _face_lookup(face_index, self.face_glows, self.default_glow)

    def material_id_for_face(self, face_index: int) -> UUID | None:
        for face, mat_id in self.face_material_ids:
            if face == face_index:
                return mat_id
        return self.default_material_id

    def fullbright_for_face(self, face_index: int) -> bool:
        return bool(self.material_flags_for_face(face_index) & MATERIAL_FLAG_FULLBRIGHT)


def _face_lookup(
    face: int,
    overrides: tuple[tuple[int, object], ...],
    default: object,
) -> object:
    for f, v in overrides:
        if f == face:
            return v
    return default


def parse_texture_entry(data: bytes | None) -> TextureEntry | None:
    """Decode a full TextureEntry blob.

    Returns None for empty/None input. Raises TextureEntryDecodeError for
    structural truncation. Sections absent due to data exhaustion silently
    use field defaults.
    """
    if not data:
        return None

    pos = 0
    kw: dict[str, object] = {}

    # Section 1: Texture UUID (16 bytes) — required
    pos, b, ovr = _read_section(data, pos, 16, required=True)
    kw["default_texture_id"] = UUID(bytes=b)
    kw["face_texture_ids"] = tuple((f, UUID(bytes=vb)) for f, vb in ovr)

    # Section 2: Color (4 bytes RGBA)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 4)
    kw["default_color"] = _b2color(b)
    kw["face_colors"] = tuple((f, _b2color(vb)) for f, vb in ovr)

    # Section 3: Repeat U (float32 LE)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 4)
    kw["default_repeat_u"] = _b2f32(b)
    kw["face_repeat_us"] = tuple((f, _b2f32(vb)) for f, vb in ovr)

    # Section 4: Repeat V (float32 LE)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 4)
    kw["default_repeat_v"] = _b2f32(b)
    kw["face_repeat_vs"] = tuple((f, _b2f32(vb)) for f, vb in ovr)

    # Section 5: Offset U (int16 LE / 32767)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 2)
    kw["default_offset_u"] = _b2i16(b) / 32767.0
    kw["face_offset_us"] = tuple((f, _b2i16(vb) / 32767.0) for f, vb in ovr)

    # Section 6: Offset V (int16 LE / 32767)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 2)
    kw["default_offset_v"] = _b2i16(b) / 32767.0
    kw["face_offset_vs"] = tuple((f, _b2i16(vb) / 32767.0) for f, vb in ovr)

    # Section 7: Rotation (int16 LE / 32768 * π)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 2)
    kw["default_rotation"] = _b2i16(b) / 32768.0 * math.pi
    kw["face_rotations"] = tuple((f, _b2i16(vb) / 32768.0 * math.pi) for f, vb in ovr)

    # Section 8: Material flags (uint8)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 1)
    kw["default_material_flags"] = b[0]
    kw["face_material_flags"] = tuple((f, vb[0]) for f, vb in ovr)

    # Section 9: Media flags (uint8)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 1)
    kw["default_media_flags"] = b[0]
    kw["face_media_flags_list"] = tuple((f, vb[0]) for f, vb in ovr)

    # Section 10: Glow (uint8 / 255)
    if pos >= len(data):
        return TextureEntry(**kw)
    pos, b, ovr = _read_section(data, pos, 1)
    kw["default_glow"] = b[0] / 255.0
    kw["face_glows"] = tuple((f, vb[0] / 255.0) for f, vb in ovr)

    # Section 11: Material ID UUID (16 bytes, optional)
    if pos < len(data):
        pos, b, ovr = _read_section(data, pos, 16)
        kw["default_material_id"] = UUID(bytes=b)
        kw["face_material_ids"] = tuple((f, UUID(bytes=vb)) for f, vb in ovr)

    return TextureEntry(**kw)


# ------------------------------------------------------------------ internal helpers

def _read_section(
    data: bytes,
    pos: int,
    value_size: int,
    *,
    required: bool = False,
) -> tuple[int, bytes, list[tuple[int, bytes]]]:
    """Read one TE section: default value bytes + per-face override list.

    Returns (new_pos, default_bytes, [(face_index, value_bytes), ...]).
    """
    if pos + value_size > len(data):
        if required:
            raise TextureEntryDecodeError(
                f"TextureEntry truncated: need {value_size} bytes at pos={pos}, "
                f"total={len(data)}"
            )
        return pos, b"\x00" * value_size, []
    default = data[pos : pos + value_size]
    pos += value_size
    overrides: list[tuple[int, bytes]] = []
    while pos < len(data):
        face_bits, pos = _read_face_mask(data, pos)
        if face_bits == 0:
            break
        if pos + value_size > len(data):
            raise TextureEntryDecodeError(
                f"TextureEntry face value truncated: need {value_size} at pos={pos}"
            )
        value = data[pos : pos + value_size]
        pos += value_size
        for face_index in range(face_bits.bit_length()):
            if face_bits & (1 << face_index):
                overrides.append((face_index, value))
    return pos, default, overrides


def _read_face_mask(data: bytes, offset: int) -> tuple[int, int]:
    face_bits = 0
    pos = offset
    while pos < len(data):
        byte = data[pos]
        pos += 1
        face_bits = (face_bits << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return face_bits, pos
    raise TextureEntryDecodeError("TextureEntry face mask is truncated")


def _b2color(b: bytes) -> tuple[int, int, int, int]:
    return (b[0], b[1], b[2], b[3])


def _b2f32(b: bytes) -> float:
    return struct.unpack_from("<f", b)[0]


def _b2i16(b: bytes) -> int:
    return struct.unpack_from("<h", b)[0]


__all__ = [
    "MATERIAL_FLAG_BUMP_MASK",
    "MATERIAL_FLAG_FULLBRIGHT",
    "MATERIAL_FLAG_SHINY_MASK",
    "TextureEntry",
    "TextureEntryDecodeError",
    "parse_texture_entry",
]
