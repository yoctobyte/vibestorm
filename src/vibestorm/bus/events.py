"""Typed event payloads published on the WorldClient bus.

These are the rich, structured equivalents of the existing string-keyed
``SessionEvent`` stream. The string stream stays for compatibility; typed
events are published *additionally* for consumers (the pygame viewer, future
tk tools) that want type-safe access to the underlying data.

Design rules:
- Events are immutable dataclasses. Subscribers may keep references.
- Field names match the wire / OpenSim vocabulary where reasonable.
- Add new event types here; do not subclass — keep the discriminator the
  Python type itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID  # noqa: F401  - used by RegionMapTileReady etc.

from vibestorm.caps.inventory_client import InventoryFetchSnapshot

# ---- session lifecycle ----------------------------------------------------

@dataclass(slots=True, frozen=True)
class LoginComplete:
    region_handle: int
    region_name: str | None
    sim_ip: str
    sim_port: int


@dataclass(slots=True, frozen=True)
class RegionChanged:
    """Emitted when WorldClient.current_handle changes."""
    region_handle: int
    region_name: str | None


@dataclass(slots=True, frozen=True)
class SessionClosed:
    region_handle: int
    reason: str


# ---- chat / IM / alert ----------------------------------------------------

@dataclass(slots=True, frozen=True)
class ChatLocal:
    region_handle: int
    from_name: str
    chat_type: int
    audible: int
    message: str


@dataclass(slots=True, frozen=True)
class ChatIM:
    region_handle: int
    from_agent_name: str
    to_agent_id: UUID
    message: str
    dialog: int


@dataclass(slots=True, frozen=True)
class ChatAlert:
    region_handle: int
    message: str
    is_agent_alert: bool = False


@dataclass(slots=True, frozen=True)
class ChatOutbound:
    region_handle: int
    chat_type: int
    channel: int
    message: str


# ---- world state ---------------------------------------------------------

@dataclass(slots=True, frozen=True)
class WorldStateChanged:
    """Coarse signal: the current circuit's WorldView mutated.

    Fine-grained per-object events (ObjectAdded/Moved/Removed) can be added
    later when a renderer needs them; for v1 a redraw-the-whole-scene
    signal is enough.
    """
    region_handle: int
    reason: str  # "object_update" | "kill_object" | "terse_update" | "coarse_location" | …


@dataclass(slots=True, frozen=True)
class InventorySnapshotReady:
    region_handle: int
    snapshot: InventoryFetchSnapshot


# ---- map / texture -------------------------------------------------------

@dataclass(slots=True, frozen=True)
class RegionMapTileReady:
    region_handle: int
    image_id: UUID
    cache_path: str  # absolute path to the cached PNG


@dataclass(slots=True, frozen=True)
class TextureAssetReady:
    region_handle: int
    texture_id: UUID
    cache_path: str  # absolute path to the cached PNG


# ---- terrain --------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class LayerDataReceived:
    """A LayerData packet arrived on the wire.

    The payload is the **raw, undecoded** Data block — patch decoding
    (DCT coefficients + IDCT) lives in ``vibestorm.world.terrain``
    rather than here so subscribers that only care about packet
    receipt (logging, capture, replay) don't pay the decode cost.
    Real consumers (the 3D renderer's terrain mesh) re-decode the
    payload through that module.
    """
    region_handle: int
    layer_type: int
    data: bytes


__all__ = [
    "ChatAlert",
    "ChatIM",
    "ChatLocal",
    "ChatOutbound",
    "InventorySnapshotReady",
    "LayerDataReceived",
    "LoginComplete",
    "RegionChanged",
    "RegionMapTileReady",
    "SessionClosed",
    "TextureAssetReady",
    "WorldStateChanged",
]
