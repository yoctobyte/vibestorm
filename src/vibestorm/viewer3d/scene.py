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

import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from vibestorm.viewer3d.avatar_pose import (
    AvatarMotion,
    advance_all,
    pose_for_motion,
    sit_pose,
)
from vibestorm.viewer3d.linkset import resolve_world_transforms
from vibestorm.world.chat_types import (
    CHAT_TYPE_SAY,
    CHAT_TYPE_START_TYPING,
    chat_type_name,
    is_typing_notification,
)
from vibestorm.world.extra_params import DecodedExtraParams, decode_extra_params
from vibestorm.world.land_flags import DecodedFlags, decode_parcel_flags
from vibestorm.world.parcel_overlay import (
    ParcelOverlay,
    ParcelOverlayDecodeError,
    decode_parcel_bitmap,
    decode_parcel_overlay,
)
from vibestorm.world.physics_shape import PhysicsProperties, physics_properties_from_event
from vibestorm.world.sim_stats import summarize_sim_stats
from vibestorm.world.sound_flags import decode_sound_flags

if TYPE_CHECKING:
    from vibestorm.bus.events import (
        ChatAlert,
        ChatIM,
        ChatLocal,
        ChatOutbound,
        EventQueueEventReceived,
        InventorySnapshotReady,
        LayerDataReceived,
        MeshAssetReady,
        ObjectInventorySnapshotReady,
        ParcelOverlayReceived,
        ParcelPropertiesReceived,
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
PrimShape = Literal[
    "cube",
    "sphere",
    "cylinder",
    "torus",
    "prism",
    "ring",
    "tube",
    "mesh",
]
MeshSourceKind = Literal["primitive", "sculpt", "mesh"]

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

EXTRA_PARAM_SCULPT = 0x30
SCULPT_TYPE_SPHERE = 1
SCULPT_TYPE_TORUS = 2
SCULPT_TYPE_PLANE = 3
SCULPT_TYPE_CYLINDER = 4
SCULPT_TYPE_MESH = 5
SCULPT_TYPE_MASK = 0x0F


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
    # A flexible prim is a straight extrusion that bends at runtime, so its
    # cross-section is classified exactly like a linear one; the flexi
    # ExtraParams block carries the bending. OpenSim's Extrusion enum
    # (PrimitiveBaseShape.cs) is Straight=0x10, Curve1=0x20, Curve2=0x30,
    # Flexible=0x80 -- 0x80 is a path mode, not a shape of its own, and
    # leaving it out sent every flexi prim to the unclassified fallback.
    if path_curve in (PATH_CURVE_LINE, PATH_CURVE_FLEXIBLE):
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
class SculptMeshHint:
    """Approximate render hint decoded from the sculpt extra-param block."""

    source_kind: MeshSourceKind
    asset_id: UUID
    sculpt_type: int
    shape: PrimShape


def decode_sculpt_mesh_hint(extra_params: object) -> SculptMeshHint | None:
    """Decode the sculpt/mesh extra-param into a placeholder mesh hint.

    SL mesh objects ride the same sculpt extra-param lane with sculpt
    type 5. Until the real sculpt-map and mesh-asset decoders exist,
    this keeps them out of the anonymous cube bucket and preserves the
    asset UUID for the future fetch/decode path.
    """
    for entry in extra_params or ():
        if getattr(entry, "param_type", None) != EXTRA_PARAM_SCULPT:
            continue
        if not getattr(entry, "param_in_use", True):
            continue
        data = getattr(entry, "param_data", b"")
        if not isinstance(data, (bytes, bytearray)) or len(data) < 17:
            continue
        asset_id = UUID(bytes=bytes(data[:16]))
        sculpt_type = int(data[16])
        base_type = sculpt_type & SCULPT_TYPE_MASK
        if base_type == SCULPT_TYPE_MESH:
            return SculptMeshHint(
                source_kind="mesh",
                asset_id=asset_id,
                sculpt_type=sculpt_type,
                shape="mesh",
            )
        return SculptMeshHint(
            source_kind="sculpt",
            asset_id=asset_id,
            sculpt_type=sculpt_type,
            shape=_shape_for_sculpt_type(base_type),
        )
    return None


def _shape_for_sculpt_type(sculpt_type: int) -> PrimShape:
    if sculpt_type == SCULPT_TYPE_TORUS:
        return "torus"
    if sculpt_type == SCULPT_TYPE_CYLINDER:
        return "cylinder"
    if sculpt_type == SCULPT_TYPE_PLANE:
        return "cube"
    return "sphere"


def avatar_display_name(name_values: object) -> str | None:
    """Build an avatar's display name from its ``ObjectUpdate`` NameValues.

    The pairs arrive as ``FirstName`` / ``LastName`` (plus an optional group
    ``Title``). A last name of ``Resident`` is SL's placeholder for a
    single-name account and is dropped rather than shown, matching what
    viewers display.
    """
    if not isinstance(name_values, dict):
        return None
    first = (name_values.get("FirstName") or "").strip()
    last = (name_values.get("LastName") or "").strip()
    if last.lower() == "resident":
        last = ""
    full = " ".join(part for part in (first, last) if part)
    if not full:
        return None
    title = (name_values.get("Title") or "").strip()
    return f"{title}\n{full}" if title else full


#: How many one-shot SoundTrigger events to keep. They are transient by
#: nature; the tail exists so a HUD can answer "did anything just play?".
SOUND_TRIGGER_HISTORY = 32


@dataclass(slots=True, frozen=True)
class AttachedSoundState:
    """The looping sound currently bound to an object."""

    sound_id: UUID
    owner_id: UUID | None
    gain: float
    flags: int

    @property
    def is_silent(self) -> bool:
        """Whether this object is currently making no sound.

        A null sound id is how a sim clears an object's looping sound, and the
        STOP flag says the same thing while still naming the sound — both mean
        silence, so both have to be checked.
        """
        return self.sound_id.int == 0 or decode_sound_flags(self.flags).is_stop

    def describe_flags(self) -> str:
        return decode_sound_flags(self.flags).describe()


@dataclass(slots=True, frozen=True)
class ChatLine:
    kind: str          # "local" | "im" | "alert" | "outbound"
    sender: str        # display name (or "" / "*system*")
    message: str
    # ChatFromSimulator's chat type, for local chat only. None elsewhere, since
    # IMs, alerts and our own outbound lines have no such byte on the wire.
    chat_type: int | None = None

    def delivery(self) -> str | None:
        """"whisper"/"shout"/… when that differs from an ordinary say."""
        if self.chat_type is None or self.chat_type == CHAT_TYPE_SAY:
            return None
        return chat_type_name(self.chat_type)


@dataclass(slots=True, frozen=True)
class SceneEntity:
    """Renderer-agnostic entity. Both 2D top-down and future 3D renderers
    consume this. Coordinates stay in the SL world frame (X east, Y north,
    Z up); 3D renderers remap to GL frame internally.
    """
    local_id: int
    pcode: int
    kind: EntityKind
    #: Region coordinates. The *update* reports a child's position in its
    #: parent's frame; by the time an entity exists that has been composed
    #: back through the parent, so everything here is in one frame.
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation: tuple[float, float, float, float] | None  # quat (x, y, z, w)
    rotation_z_radians: float                           # yaw, derived from rotation
    name: str | None = None
    default_texture_id: UUID | None = None
    texture_entry: TextureEntry | None = None
    shape: PrimShape | None = None  # populated once parser surfaces path/profile curves
    mesh_source_kind: MeshSourceKind = "primitive"
    mesh_asset_id: UUID | None = None
    sculpt_type: int | None = None
    extra_params: DecodedExtraParams | None = None
    hover_text: str | None = None
    hover_text_color: tuple[int, int, int, int] | None = None
    tint: tuple[int, int, int] = DEFAULT_MARKER_COLOR
    #: 0 for a root. Kept after composition because the inspector and the
    #: sync path both address objects by their root.
    parent_id: int = 0

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
    # Region-side health, mirrored from the WorldView each refresh. The HUD's
    # own fps says nothing about whether a stutter is the client or the sim.
    sim_health: str = ""
    water_height: float = DEFAULT_WATER_HEIGHT_M
    avatar_position: tuple[float, float, float] | None = None
    parcel_name: str | None = None
    # Set once ParcelProperties arrives; None means "not asked or not answered
    # yet", which is not the same as a parcel with no flags set.
    parcel_flags: "DecodedFlags | None" = None
    # Region-wide parcel ownership grid, reassembled from the sequenced
    # ParcelOverlay packets, plus its property-line segments in region meters.
    parcel_overlay_packets: dict[int, bytes] = field(default_factory=dict)
    parcel_overlay: ParcelOverlay | None = None
    parcel_borders: tuple[tuple[float, float, float, float], ...] = ()
    render_parcel_borders: bool = True
    render_hover_text: bool = True
    render_avatar_names: bool = True

    # Live world activity, keyed by the object or avatar it belongs to. These
    # are *current state*, not a log: an AvatarAnimation or AttachedSound
    # message replaces whatever was there, which is how a sim stops an anim or
    # clears a sound. A trailing log would show a stopped animation forever.
    avatar_animations: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    # How each avatar has been moving, and the pose that follows from it.
    # Keyed by local_id, like the entities themselves. The renderer reads
    # ``avatar_poses``; nothing else should need ``avatar_motion``.
    avatar_motion: dict[int, AvatarMotion] = field(default_factory=dict)
    avatar_poses: dict[int, dict[str, float]] = field(default_factory=dict)
    object_animations: dict[UUID, tuple[UUID, ...]] = field(default_factory=dict)
    attached_sounds: dict[UUID, "AttachedSoundState"] = field(default_factory=dict)
    # Physics material per object local_id. Keyed by local_id rather than UUID
    # because ObjectPhysicsProperties identifies the prim that way.
    object_physics: dict[int, PhysicsProperties] = field(default_factory=dict)
    # Neighbouring regions the sim has told us about, handle -> "ip:port".
    # Region-scoped: the neighbours of the region just left are not ours.
    neighbour_regions: dict[int, str] = field(default_factory=dict)
    # One-shot sounds have no lasting state, so these are a bounded tail.
    recent_sound_triggers: deque = field(
        default_factory=lambda: deque(maxlen=SOUND_TRIGGER_HISTORY)
    )
    map_tile_path: Path | None = None
    texture_paths: dict[UUID, Path] = field(default_factory=dict)
    mesh_paths: dict[UUID, Path] = field(default_factory=dict)
    inventory_snapshot: InventoryFetchSnapshot | None = None
    object_inventory_snapshots: dict[int, ObjectInventorySnapshot] = field(default_factory=dict)
    terrain_heightmap: RegionHeightmap | None = None
    #: The region's four ground textures, once their bytes have been cached,
    #: and the elevation band each covers. All four have to be present before
    #: the blend means anything, so the renderer checks for a full set.
    terrain_texture_paths: tuple[Path | None, Path | None, Path | None, Path | None] = (
        None,
        None,
        None,
        None,
    )
    terrain_start_height: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    terrain_height_range: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    debug_terrain_source: str | None = None
    terrain_z_scale: float = 1.0
    render_terrain: bool = True
    #: Terrain wireframe. A decode-debugging aid, off by default: it draws a
    #: bright green line per heightfield edge, which at 64x64 covers the whole
    #: region and reads as the world being broken rather than as an overlay.
    #: The HUD "Mesh Lines" button turns it back on.
    render_terrain_lines: bool = False
    render_water: bool = True
    render_objects: bool = True
    render_sky: bool = True
    water_alpha: float = 0.72
    object_entities: dict[int, SceneEntity] = field(default_factory=dict)
    avatar_entities: dict[int, SceneEntity] = field(default_factory=dict)
    # Last frame's entities, each kept beside the ``WorldObject`` it was built
    # from and the placement it was given, so an unchanged object can be handed
    # back rather than rebuilt. See ``refresh_from_world_view``.
    _entity_cache: dict[int, tuple[object, object, SceneEntity]] = field(
        default_factory=dict, repr=False
    )
    sun_phase: float | None = None
    sun_direction: tuple[float, float, float] | None = None
    chat_lines: deque[ChatLine] = field(default_factory=lambda: deque(maxlen=128))
    # Who is currently typing, from the start/stop-typing chat types. Kept as a
    # dict rather than a set so insertion order gives a stable display order.
    typing_senders: dict[str, bool] = field(default_factory=dict)

    # ---- bus event handlers ----------------------------------------------

    def apply_region_changed(self, event: RegionChanged) -> None:
        debug_heightmap = self.terrain_heightmap if self.debug_terrain_source is not None else None
        debug_source = self.debug_terrain_source
        self.region_handle = event.region_handle
        self.region_name = event.region_name
        self.water_height = DEFAULT_WATER_HEIGHT_M
        self.avatar_position = None
        self.parcel_name = None
        self.parcel_flags = None
        # Region health belongs to the region we just left.
        self.sim_health = ""
        self.parcel_overlay_packets.clear()
        self.parcel_overlay = None
        self.parcel_borders = ()
        self.object_entities.clear()
        self.avatar_entities.clear()
        self.texture_paths.clear()
        self.mesh_paths.clear()
        self.object_inventory_snapshots.clear()
        # Per-object side state. The entity dicts above are rebuilt every frame
        # from the WorldView, but these are not — they accumulate from bus
        # events and would otherwise outlive the region they describe.
        #
        # object_physics is the dangerous one: it is keyed by local_id, and
        # local ids are assigned per region session. Object 42 in the new
        # region would silently inherit object 42's physics from the old one.
        self.object_physics.clear()
        self.neighbour_regions.clear()
        self.attached_sounds.clear()
        self.object_animations.clear()
        self.avatar_animations.clear()
        # Local ids are per region session, so a stride belonging to whoever
        # was local_id 42 over there must not carry over to whoever it is here.
        self.avatar_motion.clear()
        self.avatar_poses.clear()
        self.recent_sound_triggers.clear()
        # Whoever was mid-sentence in the old region is not typing here.
        self.typing_senders.clear()
        self.terrain_heightmap = debug_heightmap
        self.debug_terrain_source = debug_source
        # Map tile is region-scoped; clear so a stale tile from the old region isn't shown.
        self.map_tile_path = None

    def apply_map_tile_ready(self, event: RegionMapTileReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.map_tile_path = Path(event.cache_path)

    def apply_parcel_properties(self, event: ParcelPropertiesReceived) -> None:
        """Set the parcel identity shown in the HUD status bar.

        A region-wide request draws one reply per parcel, so prefer the parcel
        whose Bitmap actually covers the avatar; fall back to the first reply
        while the avatar position is still unknown.
        """
        if event.region_handle != self.region_handle and self.region_handle is not None:
            return
        properties = event.properties
        if self.avatar_position is not None and properties.bitmap:
            try:
                mask = decode_parcel_bitmap(properties.bitmap)
            except ParcelOverlayDecodeError:
                mask = None
            if mask is not None and not mask.contains_meters(
                self.avatar_position[0], self.avatar_position[1]
            ):
                return
        self.parcel_name = properties.name or None
        self.parcel_flags = decode_parcel_flags(properties.parcel_flags)

    def apply_parcel_overlay(self, event: ParcelOverlayReceived) -> None:
        """Accumulate ParcelOverlay pieces and decode the grid once complete.

        The simulator splits the region-wide ownership grid across several
        sequenced packets (four 1024-byte pieces for a standard 256 m region).
        Decode is attempted after each piece and simply fails until the set is
        whole, so a late or reordered packet still lands.
        """
        if event.region_handle != self.region_handle and self.region_handle is not None:
            return
        self.parcel_overlay_packets[event.sequence_id] = event.data
        packets = sorted(self.parcel_overlay_packets.items())
        try:
            overlay = decode_parcel_overlay(packets)
        except ParcelOverlayDecodeError:
            return  # incomplete set; retry when the next piece arrives
        self.parcel_overlay = overlay
        self.parcel_borders = overlay.border_segments()

    def apply_texture_asset_ready(self, event: TextureAssetReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.texture_paths[event.texture_id] = Path(event.cache_path)

    def apply_mesh_asset_ready(self, event: MeshAssetReady) -> None:
        if event.region_handle == self.region_handle or self.region_handle is None:
            self.mesh_paths[event.mesh_id] = Path(event.cache_path)

    def apply_chat_local(self, event: ChatLocal) -> None:
        # Start/stop-typing arrive as ChatFromSimulator with no message. They
        # are not chat, and appending them puts blank rows in the log.
        if is_typing_notification(event.chat_type):
            if event.chat_type == CHAT_TYPE_START_TYPING:
                self.typing_senders[event.from_name] = True
            else:
                self.typing_senders.pop(event.from_name, None)
            return
        # Someone who was typing has now said it.
        self.typing_senders.pop(event.from_name, None)
        self.chat_lines.append(
            ChatLine(
                kind="local",
                sender=event.from_name,
                message=event.message,
                chat_type=event.chat_type,
            )
        )

    def apply_chat_im(self, event: ChatIM) -> None:
        self.chat_lines.append(
            ChatLine(kind="im", sender=event.from_agent_name, message=event.message)
        )

    def apply_chat_alert(self, event: ChatAlert) -> None:
        self.chat_lines.append(ChatLine(kind="alert", sender="*system*", message=event.message))

    def apply_chat_outbound(self, event: ChatOutbound) -> None:
        self.chat_lines.append(ChatLine(kind="outbound", sender="me", message=event.message))

    def apply_event_queue_event(self, event: EventQueueEventReceived) -> None:
        """Surface the event-queue messages a user can act on as chat lines.

        Only the ones that mean something to a person are reported.
        ``TeleportFinish`` confirms a teleport the user asked for, and
        ``ScriptRunningReply`` is the sim confirming a script's state after an
        object-inventory upload — the feedback the object-sync flow needs.
        ``EnableSimulator`` announces a neighbouring region, one event per
        neighbour, so it is recorded as state rather than announced — eight
        alerts on arriving in a region surrounded by neighbours would be noise.
        ``CrossedRegion`` is the opposite: it happens rarely and means the
        avatar has just walked into a different region, which is worth saying.
        """
        from vibestorm.event_queue.events import (
            CrossedRegionEvent,
            EnableSimulatorEvent,
            ObjectPhysicsPropertiesEvent,
            ScriptRunningReplyEvent,
            TeleportFinishEvent,
        )

        payload = event.event
        # Not chat-worthy: this is per-object detail for the inspector, and it
        # arrives unprompted whenever a prim's physics change.
        if isinstance(payload, ObjectPhysicsPropertiesEvent):
            self.object_physics[payload.local_id] = physics_properties_from_event(payload)
            return
        if isinstance(payload, EnableSimulatorEvent):
            # One per neighbour, and the sim re-announces them, so this is a
            # set of what is adjacent rather than a log of announcements.
            self.neighbour_regions[payload.handle] = f"{payload.ip}:{payload.port}"
            return
        if isinstance(payload, CrossedRegionEvent):
            self.chat_lines.append(
                ChatLine(
                    kind="alert",
                    sender="*system*",
                    message=(
                        f"Crossed into region {payload.region_handle:#x} "
                        f"at {payload.sim_ip}:{payload.sim_port}"
                    ),
                )
            )
            return
        if isinstance(payload, TeleportFinishEvent):
            self.chat_lines.append(
                ChatLine(
                    kind="alert",
                    sender="*system*",
                    message=(
                        f"Teleport complete: region {payload.region_handle:#x} "
                        f"at {payload.sim_ip}:{payload.sim_port}"
                    ),
                )
            )
        elif isinstance(payload, ScriptRunningReplyEvent):
            state = "running" if payload.running else "stopped"
            engine = "Mono" if payload.mono else "LSL"
            self.chat_lines.append(
                ChatLine(
                    kind="alert",
                    sender="*system*",
                    message=(
                        f"Script {payload.item_id} on object {payload.object_id}: "
                        f"{state} ({engine})"
                    ),
                )
            )

    def apply_avatar_animation(self, event: object) -> None:
        """Record which animations an avatar is currently running."""
        animation = getattr(event, "animation", None)
        if animation is None:
            return
        self.avatar_animations[animation.sender_id] = tuple(
            entry.anim_id for entry in animation.animations
        )

    def apply_object_animation(self, event: object) -> None:
        """Record which animations an object is currently running."""
        animation = getattr(event, "animation", None)
        if animation is None:
            return
        self.object_animations[animation.sender_id] = tuple(
            entry.anim_id for entry in animation.animations
        )

    def apply_attached_sound(self, event: object) -> None:
        """Bind or clear an object's looping sound.

        A null sound id is the sim clearing the sound, so the entry is dropped
        rather than stored as a zero UUID — otherwise "silent" and "playing
        asset 0" look identical to every consumer.
        """
        sound = getattr(event, "sound", None)
        if sound is None:
            return
        if sound.sound_id.int == 0:
            self.attached_sounds.pop(sound.object_id, None)
            return
        self.attached_sounds[sound.object_id] = AttachedSoundState(
            sound_id=sound.sound_id,
            owner_id=sound.owner_id,
            gain=sound.gain,
            flags=sound.flags,
        )

    def apply_attached_sound_gain_change(self, event: object) -> None:
        """Update the gain of a sound already bound to an object.

        A gain change for an object with no known sound is ignored: inventing
        a state entry from it would claim a sound whose id we never saw.
        """
        change = getattr(event, "change", None)
        if change is None:
            return
        existing = self.attached_sounds.get(change.object_id)
        if existing is None:
            return
        self.attached_sounds[change.object_id] = AttachedSoundState(
            sound_id=existing.sound_id,
            owner_id=existing.owner_id,
            gain=change.gain,
            flags=existing.flags,
        )

    def apply_sound_trigger(self, event: object) -> None:
        """Append a one-shot world sound to the bounded recent tail."""
        sound = getattr(event, "sound", None)
        if sound is None:
            return
        self.recent_sound_triggers.append(sound)

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

    def advance_avatar_poses(self, dt_seconds: float) -> None:
        """Fold this frame's avatar positions into their gaits.

        Called once per frame, after ``refresh_from_world_view`` has put the
        current positions in ``avatar_entities``. Split out rather than folded
        into the refresh because it is the one part that needs to know how much
        time passed, and because a test can then step it deliberately.
        """
        positions = {
            local_id: entity.position for local_id, entity in self.avatar_entities.items()
        }
        self.avatar_motion = advance_all(self.avatar_motion, positions, dt_seconds)
        # An avatar with a parent is an avatar sitting on something: the
        # simulator reparents it onto the seat, which is the one thing about
        # what an avatar is *doing* that can be read without decoding an
        # animation asset. The gait still runs underneath -- a seated avatar
        # carried by a moving vehicle is still moving -- but the pose is not
        # the gait's to give.
        seated = {
            local_id
            for local_id, entity in self.avatar_entities.items()
            if entity.parent_id
        }
        self.avatar_poses = {
            local_id: sit_pose() if local_id in seated else pose_for_motion(motion)
            for local_id, motion in self.avatar_motion.items()
        }

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

        region = getattr(world_view, "region", None)
        if region is not None:
            self.terrain_texture_paths = tuple(  # type: ignore[assignment]
                self.texture_paths.get(texture_id)
                for texture_id in getattr(region, "terrain_detail", ())
            ) or (None, None, None, None)
            self.terrain_start_height = getattr(
                region, "terrain_start_height", self.terrain_start_height
            )
            self.terrain_height_range = getattr(
                region, "terrain_height_range", self.terrain_height_range
            )

        time_snapshot = getattr(world_view, "latest_time", None)
        self.sun_phase = (
            float(time_snapshot.sun_phase) if time_snapshot is not None else None
        )
        raw_sun_direction = (
            getattr(time_snapshot, "sun_direction", None) if time_snapshot is not None else None
        )
        self.sun_direction = _as_vec3(raw_sun_direction)

        objects = getattr(world_view, "objects", {})
        terse_objects = getattr(world_view, "terse_objects", {})
        # Empty unless something in view has a parent, so a region of
        # unlinked prims never pays for this.
        placed = _region_frame_transforms(objects, terse_objects)

        # Full ObjectUpdate-derived objects (have rich data).
        #
        # Rebuilding all of these every frame is what a 15,000-prim region
        # costs: decoding extra params, classifying the shape and constructing
        # the entity came to a quarter of a second per frame, for a world in
        # which a couple of dozen objects had actually moved. So each entity is
        # kept beside the ``WorldObject`` it came from. Every update replaces
        # that object with a new instance (``WorldView`` holds frozen
        # dataclasses and never edits one in place), so ``is`` is an exact
        # answer to "has anything about this prim changed?" -- and a child also
        # has to be rebuilt when its *parent* moved, which the placement
        # carries.
        cache = self._entity_cache
        fresh_cache: dict[int, tuple[object, object, SceneEntity]] = {}
        for obj in objects.values():
            local_id = obj.local_id
            cached = cache.get(local_id)
            if cached is not None and cached[0] is obj:
                # Its own data is untouched. A root is then finished -- nothing
                # else feeds its transform -- and only a child has to check
                # whether its parent moved underneath it.
                was_placed = cached[1]
                if was_placed is None or placed.get(local_id) == was_placed:
                    entity = cached[2]
                    fresh_cache[local_id] = cached
                    if obj.pcode == PCODE_AVATAR:
                        self.avatar_entities[local_id] = entity
                    else:
                        self.object_entities[local_id] = entity
                    continue

            position = getattr(obj, "position", None)
            if position is None:
                continue
            rot = getattr(obj, "rotation", None)
            parent_id = int(getattr(obj, "parent_id", 0) or 0)
            if parent_id:
                lifted = placed.get(local_id)
                if lifted is None:
                    # Its parent has not arrived. Updates are not ordered, so
                    # this happens for a frame or two routinely; the next frame
                    # has the parent, and drawing the child at the raw
                    # parent-relative position it reported would put it by the
                    # region corner, which is the bug being avoided.
                    continue
                position, rot = lifted
            else:
                lifted = None

            scale = getattr(obj, "scale", (1.0, 1.0, 1.0))
            yaw = _quat_to_yaw(rot)
            name = None
            properties = getattr(obj, "properties_family", None)
            if properties is not None:
                name = getattr(properties, "name", None) or None
            if name is None:
                # Avatars never get an ObjectPropertiesFamily; their name
                # rides the ObjectUpdate NameValue block instead.
                name = avatar_display_name(getattr(obj, "name_values", None))
            shape_data = getattr(obj, "shape", None)
            shape: PrimShape | None = None
            if shape_data is not None:
                shape = classify_prim_shape(shape_data.path_curve, shape_data.profile_curve)
            extra_param_entries = getattr(obj, "extra_params_entries", ())
            mesh_hint = decode_sculpt_mesh_hint(extra_param_entries)
            extra_params = decode_extra_params(extra_param_entries)
            if mesh_hint is not None:
                shape = mesh_hint.shape
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
                mesh_source_kind=mesh_hint.source_kind if mesh_hint is not None else "primitive",
                mesh_asset_id=mesh_hint.asset_id if mesh_hint is not None else None,
                sculpt_type=mesh_hint.sculpt_type if mesh_hint is not None else None,
                extra_params=extra_params,
                hover_text=getattr(obj, "hover_text", None),
                hover_text_color=getattr(obj, "hover_text_color", None),
                tint=PCODE_COLORS.get(obj.pcode, DEFAULT_MARKER_COLOR),
                parent_id=parent_id,
            )
            fresh_cache[local_id] = (obj, lifted, entity)
            if obj.pcode == PCODE_AVATAR:
                self.avatar_entities[local_id] = entity
            else:
                self.object_entities[local_id] = entity
        self._entity_cache = fresh_cache

        # Terse-only objects (no full ObjectUpdate seen yet) — render a placeholder.
        for terse in terse_objects.values():
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

        sim_stats = getattr(world_view, "latest_sim_stats", None)
        if sim_stats is not None:
            self.sim_health = summarize_sim_stats(sim_stats.stats)

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


def _region_frame_transforms(
    objects: dict, terse_objects: dict
) -> dict[int, tuple[tuple[float, float, float], tuple[float, float, float, float]]]:
    """Where everything parented actually is, in the region's frame.

    A prim with a parent reports where it is *relative to that parent* --
    observed live, see :mod:`vibestorm.viewer3d.linkset` -- so without this
    every child of every linkset, and every attachment on every avatar, is
    drawn a few metres from the region corner.

    Returns ``{}`` when nothing in view has a parent, which is the whole of a
    region of unlinked prims and the whole of the local test region. The
    caller only looks anything up for a parented object, so an empty result
    and a region with no parents are the same thing.

    Terse-only objects are included as roots: ``ImprovedTerseObjectUpdate``
    carries no parent id, and a linkset root seen only tersely is still the
    frame its children hang off.
    """
    transforms: dict[int, tuple[int, tuple[float, float, float], object]] = {}
    parented = False
    for obj in objects.values():
        position = getattr(obj, "position", None)
        if position is None:
            continue
        parent_id = int(getattr(obj, "parent_id", 0) or 0)
        parented = parented or bool(parent_id)
        transforms[obj.local_id] = (parent_id, position, getattr(obj, "rotation", None))
    if not parented:
        # Nothing to compose, and the resolve would walk every prim in the
        # region to say so.
        return {}
    for terse in terse_objects.values():
        if terse.local_id in transforms:
            continue
        transforms[terse.local_id] = (0, terse.position, terse.rotation)
    return resolve_world_transforms(transforms)  # type: ignore[arg-type]


def _quat_to_yaw(quat: tuple[float, float, float, float] | None) -> float:
    """Project a unit quaternion onto the z axis to get yaw in radians.

    The viewer's 2D mode is top-down; we only care about rotation around z.
    Returns 0 for None or a non-finite quat — defensive default for terse
    decode edge cases.
    """
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
    "SculptMeshHint",
    "MeshSourceKind",
    "PCODE_AVATAR",
    "PCODE_PRIM",
    "DEFAULT_WATER_HEIGHT_M",
    "classify_prim_shape",
    "decode_sculpt_mesh_hint",
]
