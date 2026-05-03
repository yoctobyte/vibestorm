"""Typed commands sent to WorldClient.dispatch_command().

A command is just a payload — the WorldClient's registered handler decides
what UDP packets to build (returned as ``list[bytes]`` so the caller can
send them on whichever socket it owns).

Movement commands set state on the current circuit so the next periodic
AgentUpdate carries them. Chat commands build packets immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


# ---- movement (mutates session state; takes effect on next AgentUpdate) ----

@dataclass(slots=True, frozen=True)
class SetControlFlags:
    """Set the AgentUpdate.ControlFlags bitfield directly.

    Replaces all flags. Use ``AddControlFlags`` / ``RemoveControlFlags`` for
    modifying individual bits.
    """
    flags: int


@dataclass(slots=True, frozen=True)
class AddControlFlags:
    flags: int


@dataclass(slots=True, frozen=True)
class RemoveControlFlags:
    flags: int


@dataclass(slots=True, frozen=True)
class ClearControlFlags:
    pass


@dataclass(slots=True, frozen=True)
class SetBodyRotation:
    rotation: tuple[float, float, float]  # x,y,z; w reconstructed at the receiver


@dataclass(slots=True, frozen=True)
class SetHeadRotation:
    rotation: tuple[float, float, float]


@dataclass(slots=True, frozen=True)
class SetCamera:
    """Override the camera vectors carried in the next AgentUpdate."""
    center: tuple[float, float, float]
    at_axis: tuple[float, float, float]
    left_axis: tuple[float, float, float]
    up_axis: tuple[float, float, float]


# ---- chat (builds and returns an outbound packet) -------------------------

@dataclass(slots=True, frozen=True)
class SendChat:
    """Send a ChatFromViewer to the current region."""
    message: str
    chat_type: int = 1  # 0=whisper, 1=normal, 2=shout
    channel: int = 0


__all__ = [
    "AddControlFlags",
    "ClearControlFlags",
    "RemoveControlFlags",
    "SendChat",
    "SetBodyRotation",
    "SetCamera",
    "SetControlFlags",
    "SetHeadRotation",
]
