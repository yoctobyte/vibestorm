"""Decoder for SL/OpenSim animation assets (the ``.animatn`` binary BVH form).

`AvatarAnimation` tells this client *which* animations are playing, by asset
id, and nothing about what they do. This decodes the asset itself: how long it
runs, whether it loops, which joints it drives, and the keyframes for each.

Every field is sourced from OpenSim's
``Region/Framework/Scenes/Animation/BinBVHAnimation.cs``, which reads the same
format. Including the quantisation: keyframe components are 16-bit, and the
mapping back to a float is recoverable from ``BinBVHUtil.FloatToUInt16`` in the
same file, which shifts ``lower`` to zero, divides by the range and scales by
``UInt16.MaxValue``. So the inverse is
``raw / 65535 * (upper - lower) + lower``.

The ranges are literals at the call sites, and they differ per key type:
rotations use -1..1, positions -5..5, and a key's *time* is scaled against the
animation's own ``InPoint``/``OutPoint`` rather than a constant. Both comments
in the source ("argh! floats into two bytes!", "After fighting with it for a
while.. -1, to 1 seems to give the best results") suggest the ranges were
arrived at empirically upstream; they are reproduced, not improved on.

Verified against a real asset: the OpenSim library's ``place_marker``, 600
bytes, 14 joints.
"""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack_from

#: ``BinBVHUtil.ONE_OVER_U16_MAX``.
U16_MAX = 0xFFFF

#: The ranges the joint key readers are called with in `BinBVHAnimation.cs`.
ROTATION_RANGE = (-1.0, 1.0)
POSITION_RANGE = (-5.0, 5.0)

#: Bytes per keyframe: a U16 time plus three U16 components.
KEYFRAME_SIZE = 8


class AnimationDecodeError(ValueError):
    """Raised when animation asset bytes cannot be decoded."""


def unquantise(raw: int, lower: float, upper: float) -> float:
    """Undo ``BinBVHUtil.FloatToUInt16``.

    Note the upstream encoder divides by the *shifted* upper bound rather than
    by ``upper - lower``, which is the same number only because it shifts
    ``lower`` to zero first. Written here as the range so the intent is legible.
    """
    return raw / U16_MAX * (upper - lower) + lower


@dataclass(slots=True, frozen=True)
class Keyframe:
    time: float
    x: float
    y: float
    z: float


@dataclass(slots=True, frozen=True)
class AnimationJoint:
    name: str
    priority: int
    rotations: tuple[Keyframe, ...]
    positions: tuple[Keyframe, ...]


@dataclass(slots=True, frozen=True)
class Animation:
    """A decoded animation asset."""

    version: int
    sub_version: int
    priority: int
    length: float
    expression_name: str
    in_point: float
    out_point: float
    loop: bool
    ease_in_time: float
    ease_out_time: float
    hand_pose: int
    joints: tuple[AnimationJoint, ...]
    #: Bytes left over after the last joint.
    #:
    #: OpenSim's reader stops at the end of the joint list and never looks at
    #: these, so this tree cannot say what they are. The library's
    #: ``place_marker`` has exactly four, all zero — consistent with a count of
    #: something that follows and is empty, but that is a guess, and guessing
    #: is how a wrong layout gets written down as fact. They are surfaced
    #: rather than dropped so a non-zero value shows up as unread data instead
    #: of silently disappearing.
    trailing: bytes = b""

    @property
    def has_unread_trailing_data(self) -> bool:
        """True when bytes remain that are not simply zero padding.

        All-zero trailing bytes are the observed normal case. Anything else is
        content this decoder is not reading, and a caller reporting on an
        animation should say so rather than imply the decode was complete.
        """
        return any(self.trailing)

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints)

    def describe(self) -> str:
        parts = [
            f"length={self.length:g}s",
            f"priority={self.priority}",
            f"joints={len(self.joints)}",
        ]
        if self.loop:
            parts.append(f"loop={self.in_point:g}-{self.out_point:g}s")
        if self.ease_in_time or self.ease_out_time:
            parts.append(f"ease={self.ease_in_time:g}/{self.ease_out_time:g}")
        if self.expression_name:
            parts.append(f"expression={self.expression_name}")
        if self.has_unread_trailing_data:
            parts.append(f"unread={len(self.trailing)}b")
        return " ".join(parts)


def _read_cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise AnimationDecodeError("unterminated string in animation asset")
    return data[offset:end].decode("utf-8", errors="replace"), end + 1


def _read_keys(
    data: bytes, offset: int, count: int, lower: float, upper: float,
    in_point: float, out_point: float,
) -> tuple[tuple[Keyframe, ...], int]:
    if count < 0:
        raise AnimationDecodeError(f"negative keyframe count {count}")
    needed = count * KEYFRAME_SIZE
    if len(data) < offset + needed:
        raise AnimationDecodeError(
            f"animation asset truncated: {count} keyframes need {needed} bytes, "
            f"{len(data) - offset} remain"
        )
    keys: list[Keyframe] = []
    for _ in range(count):
        raw_time, raw_x, raw_y, raw_z = unpack_from("<4H", data, offset)
        offset += KEYFRAME_SIZE
        keys.append(
            Keyframe(
                # A key's time is scaled against the animation's own loop
                # points, not against a fixed range like its components.
                time=unquantise(raw_time, in_point, out_point),
                x=unquantise(raw_x, lower, upper),
                y=unquantise(raw_y, lower, upper),
                z=unquantise(raw_z, lower, upper),
            )
        )
    return tuple(keys), offset


def decode_animation(data: bytes) -> Animation:
    """Decode a ``.animatn`` asset.

    Raises rather than returning a partial animation: a half-read joint list
    would look like an animation that drives fewer joints, which is a
    meaningful and wrong statement rather than an obvious failure.
    """
    if len(data) < 4:
        raise AnimationDecodeError("animation asset is too short for a header")
    version, sub_version = unpack_from("<2H", data, 0)
    offset = 4
    if len(data) < offset + 8:
        raise AnimationDecodeError("animation asset header is truncated")
    priority, length = unpack_from("<if", data, offset)
    offset += 8

    expression_name, offset = _read_cstring(data, offset)

    if len(data) < offset + 24:
        raise AnimationDecodeError("animation asset timing block is truncated")
    in_point, out_point, loop_raw, ease_in, ease_out, hand_pose = unpack_from(
        "<ffiffI", data, offset
    )
    offset += 24

    if len(data) < offset + 4:
        raise AnimationDecodeError("animation asset joint count is missing")
    (joint_count,) = unpack_from("<I", data, offset)
    offset += 4
    # A corrupt count would otherwise drive a very long loop before failing on
    # a read; the smallest possible joint is a 1-byte name plus 8 bytes of
    # counts, so this bounds it by what the remaining bytes could hold.
    if joint_count > (len(data) - offset) // 9 + 1:
        raise AnimationDecodeError(
            f"animation asset claims {joint_count} joints, more than "
            f"{len(data) - offset} remaining bytes can hold"
        )

    joints: list[AnimationJoint] = []
    for _ in range(joint_count):
        name, offset = _read_cstring(data, offset)
        if len(data) < offset + 8:
            raise AnimationDecodeError(f"joint {name!r} header is truncated")
        joint_priority, rotation_count = unpack_from("<ii", data, offset)
        offset += 8
        rotations, offset = _read_keys(
            data, offset, rotation_count, *ROTATION_RANGE, in_point, out_point
        )
        if len(data) < offset + 4:
            raise AnimationDecodeError(f"joint {name!r} position count is missing")
        (position_count,) = unpack_from("<i", data, offset)
        offset += 4
        positions, offset = _read_keys(
            data, offset, position_count, *POSITION_RANGE, in_point, out_point
        )
        joints.append(
            AnimationJoint(
                name=name,
                priority=joint_priority,
                rotations=rotations,
                positions=positions,
            )
        )

    return Animation(
        version=version,
        sub_version=sub_version,
        priority=priority,
        length=length,
        expression_name=expression_name,
        in_point=in_point,
        out_point=out_point,
        loop=loop_raw != 0,
        ease_in_time=ease_in,
        ease_out_time=ease_out,
        hand_pose=hand_pose,
        joints=tuple(joints),
        trailing=bytes(data[offset:]),
    )


__all__ = [
    "Animation",
    "AnimationDecodeError",
    "AnimationJoint",
    "KEYFRAME_SIZE",
    "Keyframe",
    "POSITION_RANGE",
    "ROTATION_RANGE",
    "U16_MAX",
    "decode_animation",
    "unquantise",
]
