"""Decoder for a prim's ``TextureAnim`` block.

Both update paths carried this as a byte count and nothing else. The block is
how a prim scrolls, rotates, scales or flipbook-animates its texture, which is
most of what makes a region look alive — vendor signs, water, screens.

Layout and flag values are taken from OpenSim rather than reconstructed:
``SceneObjectPart.AddTextureAnimation`` writes the 16 bytes, and the mode bits
are the LSL constants in ``LSL_Constants.cs`` (``ANIM_ON`` .. ``SCALE``).

An animation that is switched off is sent as an *empty* block rather than as
16 bytes with a cleared flag, so "absent" and "off" are the same wire state.
"""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack_from

# Mode bits (LSL_Constants.cs). ROTATE and SCALE reinterpret Start/Length/Rate
# as angles and scale factors rather than frame indices, which is why the
# decoder keeps them raw instead of naming them "frames".
TEXTURE_ANIM_ON = 0x01
TEXTURE_ANIM_LOOP = 0x02
TEXTURE_ANIM_REVERSE = 0x04
TEXTURE_ANIM_PING_PONG = 0x08
TEXTURE_ANIM_SMOOTH = 0x10
TEXTURE_ANIM_ROTATE = 0x20
TEXTURE_ANIM_SCALE = 0x40

#: A face of -1 means "every face", which is why Face is signed.
TEXTURE_ANIM_ALL_FACES = -1

TEXTURE_ANIM_BLOCK_SIZE = 16

_MODE_NAMES: tuple[tuple[int, str], ...] = (
    (TEXTURE_ANIM_ON, "on"),
    (TEXTURE_ANIM_LOOP, "loop"),
    (TEXTURE_ANIM_REVERSE, "reverse"),
    (TEXTURE_ANIM_PING_PONG, "ping-pong"),
    (TEXTURE_ANIM_SMOOTH, "smooth"),
    (TEXTURE_ANIM_ROTATE, "rotate"),
    (TEXTURE_ANIM_SCALE, "scale"),
)


@dataclass(slots=True, frozen=True)
class TextureAnimation:
    """One prim's texture animation settings."""

    flags: int
    face: int
    size_x: int
    size_y: int
    start: float
    length: float
    rate: float

    @property
    def is_on(self) -> bool:
        return bool(self.flags & TEXTURE_ANIM_ON)

    @property
    def applies_to_all_faces(self) -> bool:
        return self.face == TEXTURE_ANIM_ALL_FACES

    @property
    def is_rotation(self) -> bool:
        return bool(self.flags & TEXTURE_ANIM_ROTATE)

    @property
    def is_scale(self) -> bool:
        return bool(self.flags & TEXTURE_ANIM_SCALE)

    def mode_names(self) -> tuple[str, ...]:
        return tuple(name for bit, name in _MODE_NAMES if self.flags & bit)

    def describe(self) -> str:
        modes = ", ".join(self.mode_names()) or "off"
        face = "all faces" if self.applies_to_all_faces else f"face {self.face}"
        # Under ROTATE/SCALE the grid is unused, so reporting "0x0 frames"
        # there would be actively misleading.
        if self.is_rotation or self.is_scale:
            extent = f"start {self.start:.2f}, length {self.length:.2f}"
        else:
            extent = f"grid {self.size_x}x{self.size_y}"
        return f"{modes} on {face}, {extent}, rate {self.rate:.2f}"


def decode_texture_animation(payload: bytes | None) -> TextureAnimation | None:
    """Decode a ``TextureAnim`` block, or ``None`` when there is no animation.

    OpenSim sends an empty block to mean "switched off", so an empty or short
    payload is not an error — it is the common case for almost every prim.
    """
    if not payload or len(payload) < TEXTURE_ANIM_BLOCK_SIZE:
        return None
    start, length, rate = unpack_from("<fff", payload, 4)
    return TextureAnimation(
        flags=payload[0],
        # Face is signed: -1 addresses every face at once.
        face=unpack_from("<b", payload, 1)[0],
        size_x=payload[2],
        size_y=payload[3],
        start=start,
        length=length,
        rate=rate,
    )


__all__ = [
    "TEXTURE_ANIM_ALL_FACES",
    "TEXTURE_ANIM_BLOCK_SIZE",
    "TEXTURE_ANIM_LOOP",
    "TEXTURE_ANIM_ON",
    "TEXTURE_ANIM_PING_PONG",
    "TEXTURE_ANIM_REVERSE",
    "TEXTURE_ANIM_ROTATE",
    "TEXTURE_ANIM_SCALE",
    "TEXTURE_ANIM_SMOOTH",
    "TextureAnimation",
    "decode_texture_animation",
]
