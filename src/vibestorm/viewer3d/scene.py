"""Render-side state derived from the WorldView + bus events.

Pygame-free. The viewer's main loop pumps bus events into Scene methods,
then the renderer reads Scene fields each frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibestorm.bus.events import (
        ChatAlert,
        ChatIM,
        ChatLocal,
        ChatOutbound,
        InventorySnapshotReady,
        RegionChanged,
        RegionMapTileReady,
    )
    from vibestorm.caps.inventory_client import InventoryFetchSnapshot

# Marker color per pcode (libomv pcode constants):
PCODE_PRIM = 9
PCODE_AVATAR = 47
PCODE_TREE = 95
PCODE_GRASS = 95  # alias; same byte in different contexts
PCODE_PARTICLE_SYSTEM = 143

PCODE_COLORS: dict[int, tuple[int, int, int]] = {
    PCODE_PRIM: (180, 180, 200),
    PCODE_AVATAR: (255, 200, 80),
    PCODE_TREE: (80, 160, 80),
    PCODE_PARTICLE_SYSTEM: (200, 80, 200),
}
DEFAULT_MARKER_COLOR: tuple[int, int, int] = (140, 140, 140)


@dataclass(slots=True, frozen=True)
class ChatLine:
    kind: str          # "local" | "im" | "alert" | "outbound"
    sender: str        # display name (or "" / "*system*")
    message: str


@dataclass(slots=True, frozen=True)
class Marker:
    """One render-time marker for an object or avatar."""
    local_id: int
    pcode: int
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation_z_radians: float          # extracted from quat for top-down draw
    name: str | None = None

    @property
    def color(self) -> tuple[int, int, int]:
        return PCODE_COLORS.get(self.pcode, DEFAULT_MARKER_COLOR)


@dataclass(slots=True)
class Scene:
    """Render-state aggregated from bus events + a live WorldView reference.

    The WorldView is the source of truth for object positions; ``refresh()``
    walks it and rebuilds markers. Bus events (chat, region change, map tile)
    update the rest of the scene incrementally.
    """

    region_handle: int | None = None
    region_name: str | None = None
    avatar_position: tuple[float, float, float] | None = None
    parcel_name: str | None = None
    map_tile_path: Path | None = None
    inventory_snapshot: InventoryFetchSnapshot | None = None
    object_markers: dict[int, Marker] = field(default_factory=dict)
    avatar_markers: dict[int, Marker] = field(default_factory=dict)
    chat_lines: deque[ChatLine] = field(default_factory=lambda: deque(maxlen=128))

    # ---- bus event handlers ----------------------------------------------

    def apply_region_changed(self, event: RegionChanged) -> None:
        self.region_handle = event.region_handle
        self.region_name = event.region_name
        self.avatar_position = None
        self.parcel_name = None
        self.object_markers.clear()
        self.avatar_markers.clear()
        # Map tile is region-scoped; clear so a stale tile from the old region isn't shown.
        self.map_tile_path = None

    def apply_map_tile_ready(self, event: RegionMapTileReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.map_tile_path = Path(event.cache_path)

    def apply_chat_local(self, event: ChatLocal) -> None:
        self.chat_lines.append(
            ChatLine(kind="local", sender=event.from_name, message=event.message)
        )

    def apply_chat_im(self, event: ChatIM) -> None:
        self.chat_lines.append(
            ChatLine(kind="im", sender=event.from_agent_name, message=event.message)
        )

    def apply_chat_alert(self, event: ChatAlert) -> None:
        self.chat_lines.append(ChatLine(kind="alert", sender="*system*", message=event.message))

    def apply_chat_outbound(self, event: ChatOutbound) -> None:
        self.chat_lines.append(ChatLine(kind="outbound", sender="me", message=event.message))

    def apply_inventory_snapshot_ready(self, event: InventorySnapshotReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.inventory_snapshot = event.snapshot

    # ---- WorldView snapshot ----------------------------------------------

    def refresh_from_world_view(self, world_view: object | None) -> None:
        """Re-derive markers from the current WorldView. Called once per frame.

        Idempotent: clears existing markers each call so removed objects
        disappear without an explicit kill event.
        """
        self.object_markers = {}
        self.avatar_markers = {}
        if world_view is None:
            return

        self.avatar_position = _self_avatar_position(world_view)

        # Full ObjectUpdate-derived objects (have rich data).
        for obj in getattr(world_view, "objects", {}).values():
            position = getattr(obj, "position", None)
            if position is None:
                continue
            scale = getattr(obj, "scale", (1.0, 1.0, 1.0))
            rot = getattr(obj, "rotation", None)
            yaw = _quat_to_yaw(rot)
            name = None
            properties = getattr(obj, "properties_family", None)
            if properties is not None:
                name = getattr(properties, "name", None) or None
            marker = Marker(
                local_id=obj.local_id,
                pcode=obj.pcode,
                position=position,
                scale=scale,
                rotation_z_radians=yaw,
                name=name,
            )
            if obj.pcode == PCODE_AVATAR:
                self.avatar_markers[obj.local_id] = marker
            else:
                self.object_markers[obj.local_id] = marker

        # Terse-only objects (no full ObjectUpdate seen yet) — render a placeholder.
        for terse in getattr(world_view, "terse_objects", {}).values():
            if terse.local_id in self.object_markers or terse.local_id in self.avatar_markers:
                continue
            yaw = _quat_to_yaw(terse.rotation)
            marker = Marker(
                local_id=terse.local_id,
                pcode=PCODE_AVATAR if terse.is_avatar else PCODE_PRIM,
                position=terse.position,
                scale=(0.5, 0.5, 0.5),  # terse-only: minimal placeholder
                rotation_z_radians=yaw,
                name=None,
            )
            if terse.is_avatar:
                self.avatar_markers[terse.local_id] = marker
            else:
                self.object_markers[terse.local_id] = marker

        if world_view.region is not None and self.region_name is None:
            self.region_name = world_view.region.name


def _self_avatar_position(world_view: object) -> tuple[float, float, float] | None:
    for coarse in getattr(world_view, "coarse_agents", ()):
        if getattr(coarse, "is_you", False):
            return (float(coarse.x), float(coarse.y), float(coarse.z))
    for terse in getattr(world_view, "terse_objects", {}).values():
        if getattr(terse, "is_avatar", False):
            return getattr(terse, "position", None)
    return None


def _quat_to_yaw(quat: tuple[float, float, float, float] | None) -> float:
    """Project a unit quaternion onto the z axis to get yaw in radians.

    The viewer is top-down; we only care about rotation around z. Returns 0
    for None or a non-finite quat — defensive default for terse decode edge
    cases.
    """
    import math

    if quat is None:
        return 0.0
    try:
        x, y, z, w = quat
    except (TypeError, ValueError):
        return 0.0
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    try:
        return math.atan2(siny_cosp, cosy_cosp)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["ChatLine", "Marker", "Scene", "PCODE_AVATAR", "PCODE_PRIM"]
