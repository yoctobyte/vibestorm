"""Typed commands sent to WorldClient.dispatch_command().

A command is just a payload — the WorldClient's registered handler decides
what UDP packets to build (returned as ``list[bytes]`` so the caller can
send them on whichever socket it owns).

Movement commands set state on the current circuit so the next periodic
AgentUpdate carries them. Chat commands build packets immediately.
"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(slots=True, frozen=True)
class TeleportLocation:
    """Request a teleport to a local position in a region handle."""
    position: tuple[float, float, float]
    region_handle: int | None = None
    look_at: tuple[float, float, float] = (1.0, 0.0, 0.0)


@dataclass(slots=True, frozen=True)
class RequestObjectInventory:
    """Request task inventory for an in-world object by simulator local ID."""
    local_id: int


__all__ = [
    "AddControlFlags",
    "ClearControlFlags",
    "RemoveControlFlags",
    "RequestObjectInventory",
    "SendChat",
    "SetBodyRotation",
    "SetCamera",
    "SetControlFlags",
    "SetHeadRotation",
    "TeleportLocation",
]
