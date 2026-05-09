"""Partial parser for Second Life/OpenSim TextureEntry blobs.

This intentionally starts with the texture UUID section only: a default
UUID followed by zero or more face-mask UUID overrides and a zero mask
terminator. Later sections carry color, repeats, offsets, rotation, and
other material controls.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class TextureEntryDecodeError(ValueError):
    """Raised when a TextureEntry blob is structurally truncated."""


@dataclass(slots=True, frozen=True)
class TextureEntry:
    default_texture_id: UUID | None
    face_texture_ids: tuple[tuple[int, UUID], ...] = ()

    def texture_for_face(self, face_index: int) -> UUID | None:
        for face, texture_id in self.face_texture_ids:
            if face == face_index:
                return texture_id
        return self.default_texture_id


def parse_texture_entry(data: bytes | None) -> TextureEntry | None:
    """Decode the TextureEntry image UUID section.

    The face mask uses OpenMetaverse's MSB-first 7-bit groups. A zero
    mask terminates the image UUID section; trailing bytes are material
    sections not decoded by this first pass.
    """
    if not data:
        return None
    if len(data) < 16:
        raise TextureEntryDecodeError("TextureEntry default texture UUID is truncated")

    default_texture_id = UUID(bytes=data[:16])
    pos = 16
    face_texture_ids: list[tuple[int, UUID]] = []
    while pos < len(data):
        face_bits, pos = _read_face_mask(data, pos)
        if face_bits == 0:
            break
        if pos + 16 > len(data):
            raise TextureEntryDecodeError("TextureEntry face texture UUID is truncated")
        texture_id = UUID(bytes=data[pos : pos + 16])
        pos += 16
        for face_index in range(face_bits.bit_length()):
            if face_bits & (1 << face_index):
                face_texture_ids.append((face_index, texture_id))

    return TextureEntry(
        default_texture_id=default_texture_id,
        face_texture_ids=tuple(face_texture_ids),
    )


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


__all__ = [
    "TextureEntry",
    "TextureEntryDecodeError",
    "parse_texture_entry",
]
