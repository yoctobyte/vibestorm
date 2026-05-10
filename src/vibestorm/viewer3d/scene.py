"""Render-side state derived from the WorldView + bus events.

Pygame-free. The viewer's main loop pumps bus events into Scene methods,
then the renderer reads Scene fields each frame.

This is the viewer3d fork's version. It keeps the 2D viewer's per-frame
``refresh_from_world_view`` flow but exposes a richer ``SceneEntity`` DTO
(replacing the 2D-flavoured ``Marker``) that 3D renderers can consume
directly. The 2D top-down draw inside this fork still works against the
same data.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

if TYPE_CHECKING:
    from vibestorm.bus.events import (
        ChatAlert,
        ChatIM,
        ChatLocal,
        ChatOutbound,
        InventorySnapshotReady,
        LayerDataReceived,
        ObjectInventorySnapshotReady,
        RegionChanged,
        RegionMapTileReady,
        TextureAssetReady,
    )
    from vibestorm.caps.inventory_client import InventoryFetchSnapshot
    from vibestorm.world.object_inventory import ObjectInventorySnapshot
    from vibestorm.world.terrain import RegionHeightmap
    from vibestorm.world.texture_entry import TextureEntry

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
DEFAULT_WATER_HEIGHT_M: float = 20.0


EntityKind = Literal["prim", "avatar", "tree", "grass", "particle", "unknown"]
PrimShape = Literal["cube", "sphere", "cylinder", "torus", "prism", "ring", "tube"]

# Path/profile curve constants from libomv (PathCurve U8, ProfileCurve & 0x07).
PATH_CURVE_LINE = 0x10
PATH_CURVE_CIRCLE = 0x20
PATH_CURVE_CIRCLE2 = 0x30
PATH_CURVE_TEST = 0x40
PATH_CURVE_FLEXIBLE = 0x80

PROFILE_CURVE_CIRCLE = 0
PROFILE_CURVE_SQUARE = 1
PROFILE_CURVE_ISO_TRIANGLE = 2
PROFILE_CURVE_EQUIL_TRIANGLE = 3
PROFILE_CURVE_RIGHT_TRIANGLE = 4
PROFILE_CURVE_HALF_CIRCLE = 5


def _kind_for_pcode(pcode: int) -> EntityKind:
    if pcode == PCODE_AVATAR:
        return "avatar"
    if pcode == PCODE_PRIM:
        return "prim"
    if pcode == PCODE_TREE:
        return "tree"
    if pcode == PCODE_PARTICLE_SYSTEM:
        return "particle"
    return "unknown"


def classify_prim_shape(path_curve: int, profile_curve: int) -> PrimShape | None:
    """Map (PathCurve, ProfileCurve) to a primitive shape category.

    Best-effort classification suitable for approximate rendering. Encodes
    the common cube/sphere/cylinder/torus/prism cases observed in libomv;
    returns ``None`` for combinations the renderer should treat as a
    fallback box.
    """
    profile = profile_curve & 0x07
    if path_curve == PATH_CURVE_LINE:
        if profile == PROFILE_CURVE_SQUARE:
            return "cube"
        if profile == PROFILE_CURVE_CIRCLE:
            return "cylinder"
        if profile in (
            PROFILE_CURVE_ISO_TRIANGLE,
            PROFILE_CURVE_EQUIL_TRIANGLE,
            PROFILE_CURVE_RIGHT_TRIANGLE,
        ):
            return "prism"
        if profile == PROFILE_CURVE_HALF_CIRCLE:
            return "cylinder"
    if path_curve in (PATH_CURVE_CIRCLE, PATH_CURVE_CIRCLE2):
        if profile == PROFILE_CURVE_CIRCLE:
            return "torus"
        if profile == PROFILE_CURVE_HALF_CIRCLE:
            return "sphere"
        if profile == PROFILE_CURVE_SQUARE:
            return "tube"
        if profile in (
            PROFILE_CURVE_ISO_TRIANGLE,
            PROFILE_CURVE_EQUIL_TRIANGLE,
            PROFILE_CURVE_RIGHT_TRIANGLE,
        ):
            return "ring"
    return None


@dataclass(slots=True, frozen=True)
class ChatLine:
    kind: str          # "local" | "im" | "alert" | "outbound"
    sender: str        # display name (or "" / "*system*")
    message: str


@dataclass(slots=True, frozen=True)
class SceneEntity:
    """Renderer-agnostic entity. Both 2D top-down and future 3D renderers
    consume this. Coordinates stay in the SL world frame (X east, Y north,
    Z up); 3D renderers remap to GL frame internally.
    """
    local_id: int
    pcode: int
    kind: EntityKind
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: tuple[float, float, float, float] | None  # quat (x, y, z, w)
    rotation_z_radians: float                           # yaw, derived from rotation
    name: str | None = None
    default_texture_id: UUID | None = None
    texture_entry: TextureEntry | None = None
    shape: PrimShape | None = None  # populated once parser surfaces path/profile curves
    tint: tuple[int, int, int] = DEFAULT_MARKER_COLOR

    @property
    def color(self) -> tuple[int, int, int]:
        """Backwards-compatible alias for the 2D draw path."""
        return self.tint


@dataclass(slots=True)
class Scene:
    """Render-state aggregated from bus events + a live WorldView reference.

    The WorldView is the source of truth for object positions; ``refresh()``
    walks it and rebuilds entities. Bus events (chat, region change, map
    tile) update the rest of the scene incrementally.
    """

    region_handle: int | None = None
    region_name: str | None = None
    water_height: float = DEFAULT_WATER_HEIGHT_M
    avatar_position: tuple[float, float, float] | None = None
    parcel_name: str | None = None
    map_tile_path: Path | None = None
    texture_paths: dict[UUID, Path] = field(default_factory=dict)
    inventory_snapshot: InventoryFetchSnapshot | None = None
    object_inventory_snapshots: dict[int, ObjectInventorySnapshot] = field(default_factory=dict)
    terrain_heightmap: RegionHeightmap | None = None
    debug_terrain_source: str | None = None
    terrain_z_scale: float = 1.0
    render_terrain: bool = True
    render_terrain_lines: bool = True
    render_water: bool = True
    render_objects: bool = True
    water_alpha: float = 0.72
    object_entities: dict[int, SceneEntity] = field(default_factory=dict)
    avatar_entities: dict[int, SceneEntity] = field(default_factory=dict)
    sun_phase: float | None = None
    sun_direction: tuple[float, float, float] | None = None
    chat_lines: deque[ChatLine] = field(default_factory=lambda: deque(maxlen=128))

    # ---- bus event handlers ----------------------------------------------

    def apply_region_changed(self, event: RegionChanged) -> None:
        debug_heightmap = self.terrain_heightmap if self.debug_terrain_source is not None else None
        debug_source = self.debug_terrain_source
        self.region_handle = event.region_handle
        self.region_name = event.region_name
        self.water_height = DEFAULT_WATER_HEIGHT_M
        self.avatar_position = None
        self.parcel_name = None
        self.object_entities.clear()
        self.avatar_entities.clear()
        self.texture_paths.clear()
        self.object_inventory_snapshots.clear()
        self.terrain_heightmap = debug_heightmap
        self.debug_terrain_source = debug_source
        # Map tile is region-scoped; clear so a stale tile from the old region isn't shown.
        self.map_tile_path = None

    def apply_map_tile_ready(self, event: RegionMapTileReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.map_tile_path = Path(event.cache_path)

    def apply_texture_asset_ready(self, event: TextureAssetReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.texture_paths[event.texture_id] = Path(event.cache_path)

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

    def apply_object_inventory_snapshot_ready(self, event: ObjectInventorySnapshotReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.object_inventory_snapshots[event.snapshot.local_id] = event.snapshot
            print(
                "[viewer3d] object_inventory.scene "
                f"region={event.region_handle:#018x} scene_region={self.region_handle} "
                f"local_id={event.snapshot.local_id} items={event.snapshot.item_count}",
                flush=True,
            )
            return
        print(
            "[viewer3d] object_inventory.scene_ignored "
            f"region={event.region_handle:#018x} scene_region={self.region_handle} "
            f"local_id={event.snapshot.local_id} items={event.snapshot.item_count}",
            flush=True,
        )

    def apply_layer_data_received(self, event: LayerDataReceived) -> None:
        if event.region_handle != self.region_handle and self.region_handle is not None:
            return
        from vibestorm.world.terrain import (
            LAYER_TYPE_LAND,
            LAYER_TYPE_LAND_EXTENDED,
            RegionHeightmap,
            TerrainDecodeError,
        )

        if event.layer_type not in (LAYER_TYPE_LAND, LAYER_TYPE_LAND_EXTENDED):
            return
        heightmap = self.terrain_heightmap
        if self.debug_terrain_source is not None:
            return
        if heightmap is None:
            heightmap = RegionHeightmap()
            self.terrain_heightmap = heightmap
        try:
            heightmap.apply_layer_blob(event.data)
        except TerrainDecodeError:
            # Bad terrain packets should not take down the viewer loop;
            # packet-level logging already records decode failures.
            return

    # ---- WorldView snapshot ----------------------------------------------

    def refresh_from_world_view(self, world_view: object | None) -> None:
        """Re-derive entities from the current WorldView. Called once per frame.

        Idempotent: clears existing entities each call so removed objects
        disappear without an explicit kill event.
        """
        self.object_entities = {}
        self.avatar_entities = {}
        if world_view is None:
            return

        self.avatar_position = _self_avatar_position(world_view)

        time_snapshot = getattr(world_view, "latest_time", None)
        self.sun_phase = (
            float(time_snapshot.sun_phase) if time_snapshot is not None else None
        )
        raw_sun_direction = (
            getattr(time_snapshot, "sun_direction", None) if time_snapshot is not None else None
        )
        self.sun_direction = _as_vec3(raw_sun_direction)

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
            shape_data = getattr(obj, "shape", None)
            shape: PrimShape | None = None
            if shape_data is not None:
                shape = classify_prim_shape(shape_data.path_curve, shape_data.profile_curve)
            entity = SceneEntity(
                local_id=obj.local_id,
                pcode=obj.pcode,
                kind=_kind_for_pcode(obj.pcode),
                position=position,
                scale=scale,
                rotation=rot,
                rotation_z_radians=yaw,
                name=name,
                default_texture_id=getattr(obj, "default_texture_id", None),
                texture_entry=getattr(obj, "texture_entry", None),
                shape=shape,
                tint=PCODE_COLORS.get(obj.pcode, DEFAULT_MARKER_COLOR),
            )
            if obj.pcode == PCODE_AVATAR:
                self.avatar_entities[obj.local_id] = entity
            else:
                self.object_entities[obj.local_id] = entity

        # Terse-only objects (no full ObjectUpdate seen yet) — render a placeholder.
        for terse in getattr(world_view, "terse_objects", {}).values():
            if terse.local_id in self.object_entities or terse.local_id in self.avatar_entities:
                continue
            yaw = _quat_to_yaw(terse.rotation)
            pcode = PCODE_AVATAR if terse.is_avatar else PCODE_PRIM
            entity = SceneEntity(
                local_id=terse.local_id,
                pcode=pcode,
                kind=_kind_for_pcode(pcode),
                position=terse.position,
                scale=(0.5, 0.5, 0.5),  # terse-only: minimal placeholder
                rotation=terse.rotation,
                rotation_z_radians=yaw,
                name=None,
                default_texture_id=None,
                shape=None,
                tint=PCODE_COLORS.get(pcode, DEFAULT_MARKER_COLOR),
            )
            if terse.is_avatar:
                self.avatar_entities[terse.local_id] = entity
            else:
                self.object_entities[terse.local_id] = entity

        if world_view.region is not None and self.region_name is None:
            self.region_name = world_view.region.name
        if world_view.region is not None:
            water_height = getattr(world_view.region, "water_height", None)
            if water_height is not None:
                self.water_height = float(water_height)


def _self_avatar_position(world_view: object) -> tuple[float, float, float] | None:
    for coarse in getattr(world_view, "coarse_agents", ()):
        if getattr(coarse, "is_you", False):
            return (float(coarse.x), float(coarse.y), float(coarse.z))
    for terse in getattr(world_view, "terse_objects", {}).values():
        if getattr(terse, "is_avatar", False):
            return getattr(terse, "position", None)
    return None


def _as_vec3(value: object | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        x, y, z = value  # type: ignore[misc]
        return (float(x), float(y), float(z))
    except (TypeError, ValueError):
        return None


def _quat_to_yaw(quat: tuple[float, float, float, float] | None) -> float:
    """Project a unit quaternion onto the z axis to get yaw in radians.

    The viewer's 2D mode is top-down; we only care about rotation around z.
    Returns 0 for None or a non-finite quat — defensive default for terse
    decode edge cases.
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


__all__ = [
    "ChatLine",
    "EntityKind",
    "PrimShape",
    "SceneEntity",
    "Scene",
    "PCODE_AVATAR",
    "PCODE_PRIM",
    "DEFAULT_WATER_HEIGHT_M",
    "classify_prim_shape",
]
