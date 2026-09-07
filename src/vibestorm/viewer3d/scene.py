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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple
from uuid import UUID

from vibestorm.viewer3d.atmosphere import (
    CLOUD_DRIFT_PER_SECOND,
    DEFAULT_CELESTIAL_AXES,
    DEFAULT_MOON_DISC,
    DEFAULT_MOON_FACE_AXES,
    DEFAULT_SKY_HORIZON_COLOR,
    DEFAULT_SKY_ZENITH_COLOR,
    DEFAULT_SUN_DISC,
    DEFAULT_UNDERWATER_REACH,
    DEFAULT_WATER_FOG,
    DEFAULT_WATER_FRESNEL,
    DEFAULT_WATER_RIPPLE,
    DEFAULT_WATER_RIPPLE_BELOW,
    DEFAULT_WATER_TINT,
    DEFAULT_WATER_WAVE_SPEED,
    DEFAULT_WATER_WAVES,
    cloud_cover,
    cloud_hue,
    cloud_offsets,
    cloud_shadow_scale,
    cloud_size,
    daylight_scale,
    light_hues,
    moon_disc,
    moon_face_axes,
    moon_level,
    sky_gradient,
    star_level,
    sun_disc,
    underwater_reach,
    water_fog,
    water_fresnel,
    water_wave_number,
    water_wave_slope,
    water_wave_slope_below,
    water_wave_speed,
    water_waves,
)
from vibestorm.viewer3d.atmosphere import (
    celestial_axes as celestial_axes_for,
)
from vibestorm.viewer3d.atmosphere import (
    moon_direction as moon_direction_for,
)
from vibestorm.viewer3d.atmosphere import (
    sun_direction as sun_direction_for,
)
from vibestorm.viewer3d.atmosphere import (
    water_tint as water_tint_for,
)
from vibestorm.viewer3d.avatar_pose import (
    AvatarMotion,
    advance_all,
    pose_for_motion,
    sit_pose,
)
from vibestorm.viewer3d.linkset import IDENTITY, compose, resolve_world_transforms
from vibestorm.world.chat_types import (
    CHAT_TYPE_SAY,
    CHAT_TYPE_START_TYPING,
    chat_type_name,
    is_typing_notification,
)
from vibestorm.world.environment import RegionEnvironment
from vibestorm.world.extra_params import DecodedExtraParams, decode_extra_params
from vibestorm.world.land_flags import DecodedFlags, decode_parcel_flags
from vibestorm.world.models import self_avatar_position
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
    #: Which region this prim belongs to, and 0 for the one the avatar is
    #: standing in. Local ids are assigned per region, so 42 next door and 42
    #: underfoot are two different prims: anything that remembers an entity by
    #: its local id has to remember this beside it. A non-zero value also
    #: means "not addressable" -- the root circuit is the only one that sends
    #: `ObjectSelect`, and it would select the wrong prim.
    region_handle: int = 0

    @property
    def color(self) -> tuple[int, int, int]:
        """Backwards-compatible alias for the 2D draw path."""
        return self.tint


@dataclass(slots=True, frozen=True)
class NeighbourTerrain:
    """One neighbouring region's ground, and where it sits relative to ours.

    `offset` is in metres and signed, straight from
    `NeighbourCircuit.offset_from`: the region due north of a 256 m region is
    at (0, 256). The heightmap is the circuit's own, by reference rather than
    by copy -- it keeps arriving in patches after the first frame that draws
    it, and `revision` is what tells the renderer to rebuild.
    """

    handle: int
    offset: tuple[float, float]
    heightmap: RegionHeightmap
    region_name: str = ""
    #: This region's own four ground textures and elevation bands, once the
    #: bytes have arrived. All four or none: a partial set blends against
    #: whatever the last region left in that texture unit.
    texture_paths: tuple[Path | None, Path | None, Path | None, Path | None] = (
        None,
        None,
        None,
        None,
    )
    start_height: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    height_range: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    #: The sea level this region announced in its own handshake, and `None`
    #: until one arrives. Water height is per region, not per grid: a region
    #: whose sea is a metre below ours has our plane drawn a metre up its
    #: beach, which is enough to make an island next door look sunk.
    water_height: float | None = None


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
    #: What the renderer's uploaded prim textures are costing, written back
    #: by the 3D pass once a frame. It reads oddly on a scene -- everything
    #: else here comes from the simulator -- but the diagnostics panel is
    #: built from the scene and nothing else, and a memory budget nobody can
    #: see is a budget nobody notices thrashing against.
    texture_vram_summary: str = ""
    mesh_paths: dict[UUID, Path] = field(default_factory=dict)
    inventory_snapshot: InventoryFetchSnapshot | None = None
    object_inventory_snapshots: dict[int, ObjectInventorySnapshot] = field(default_factory=dict)
    terrain_heightmap: RegionHeightmap | None = None
    #: The regions next door, each with its own ground and where that ground
    #: sits relative to this one. Rebuilt from the session every frame, which
    #: is cheap: it is a list of references, and the renderer rebuilds a mesh
    #: only when a heightmap's revision moves.
    neighbour_terrain: tuple[NeighbourTerrain, ...] = ()
    #: Draw them at all. Off is what the viewer looked like before there were
    #: any: sea past the region edge.
    render_neighbours: bool = True
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
    # The cloud layer costs about five milliseconds of a 1280x800 frame on
    # llvmpipe, which is what this runs on. That is worth having and worth
    # being able to turn off, which is why it is a setting rather than a
    # constant.
    render_clouds: bool = True
    water_alpha: float = 0.72
    object_entities: dict[int, SceneEntity] = field(default_factory=dict)
    avatar_entities: dict[int, SceneEntity] = field(default_factory=dict)
    # Last frame's entities, each kept beside the ``WorldObject`` it was built
    # from and the placement it was given, so an unchanged object can be handed
    # back rather than rebuilt. See ``refresh_from_world_view``.
    _entity_cache: dict[int, tuple[object, object, SceneEntity]] = field(
        default_factory=dict, repr=False
    )
    # Last frame's region-frame transforms, so a linkset nothing touched is
    # carried across rather than recomposed. See ``_region_frame_transforms``.
    _placement: dict[int, tuple[tuple[float, float, float], tuple[float, float, float, float]]] = (
        field(default_factory=dict, repr=False)
    )
    # Everything last frame's build came to, kept whole so a frame in which
    # nothing moved can hand the same four dictionaries straight back rather
    # than rebuild four equal ones. See ``_nothing_moved``.
    _built: object | None = field(default=None, repr=False)
    #: Frames whose entity build was skipped because nothing had moved, and
    #: frames that did the work. Counters, not gauges: both only rise, and it
    #: is the *ratio* that says whether the fast path is worth its check.
    repeat_frames: int = 0
    rebuilt_frames: int = 0
    #: The prims and avatars standing in the regions next door, keyed by
    #: ``(region handle, local id)``. Deliberately not merged into
    #: ``object_entities``: that dict is keyed by a bare local id, and it is
    #: what ``pick()`` walks -- a neighbour prim is drawn but not selectable,
    #: because the only circuit that sends ``ObjectSelect`` is the root one
    #: and it would select whatever prim holds that id underfoot.
    neighbour_object_entities: dict[tuple[int, int], SceneEntity] = field(default_factory=dict)
    neighbour_avatar_entities: dict[tuple[int, int], SceneEntity] = field(default_factory=dict)
    #: Last frame's build per neighbouring region, kept beside the offset it
    #: was built at. Per region because everything in it is keyed by local id,
    #: and thrown away when the offset moves -- which is what walking across a
    #: region boundary does to every one of them.
    _neighbour_caches: dict[
        int, tuple[tuple[float, float], object]
    ] = field(default_factory=dict, repr=False)
    sun_phase: float | None = None
    sun_direction: tuple[float, float, float] | None = None
    # The region's own weather, from the ExtEnvironment capability, and where
    # in its day cycle this frame is. Both None until the fetch lands.
    environment: RegionEnvironment | None = None
    day_fraction: float | None = None
    # Where the day cycle puts the sun. Not the same thing as `sun_direction`,
    # which is what the simulator said -- and OpenSim says (0, 0, 0).
    environment_sun_direction: tuple[float, float, float] | None = None
    # The colours the sky and water are drawn in this frame. They start as the
    # ones this viewer chose by eye and become the region's own once its day
    # cycle arrives.
    sky_horizon_color: tuple[float, float, float] = DEFAULT_SKY_HORIZON_COLOR
    sky_zenith_color: tuple[float, float, float] = DEFAULT_SKY_ZENITH_COLOR
    water_tint: tuple[float, float, float] = DEFAULT_WATER_TINT
    # The sea's surface, for the renderer that can shade one. `water_fog` is
    # the colour looking *through* the water and `water_tint` above is the same
    # colour with a fixed share of sky already in it -- the second is what a
    # flat plane needs, the first is what a Fresnel term needs. `water_fresnel`
    # is (straight down, grazing) reflectance, `water_waves` is the two wave
    # directions as unit vectors, and `water_ripple` is (radians of wave per
    # metre for each of the two waves, how far the surface leans at the
    # steepest). Two wave numbers rather than one because the two waves are
    # not the same length -- see `water_wave_number`.
    water_fog: tuple[float, float, float] = DEFAULT_WATER_FOG
    water_fresnel: tuple[float, float] = DEFAULT_WATER_FRESNEL
    water_waves: tuple[float, float, float, float] = DEFAULT_WATER_WAVES
    water_ripple: tuple[float, float, float] = DEFAULT_WATER_RIPPLE
    # How far the surface leans seen from *below*, which the document gives
    # separately and larger, and how far a viewer under it can see.
    water_ripple_below: float = DEFAULT_WATER_RIPPLE_BELOW
    # The sea's own surface. `normal_map` is a tangent-space normal map that
    # repeats, which the sines in the water shader stand in for.
    water_normal_id: UUID | None = None
    water_reach: float = DEFAULT_UNDERWATER_REACH
    # How far each of the two waves has travelled since the viewer started, in
    # radians, and how fast it is going. Accumulated per frame for the reason
    # the clouds are: see `advance_water`.
    water_phase: tuple[float, float] = (0.0, 0.0)
    water_wave_speed: tuple[float, float] = DEFAULT_WATER_WAVE_SPEED
    # How brightly to light everything solid. 1.0 is full day.
    light_level: float = 1.0
    # And in what colour: the light off the sky, and the light off the sun.
    # White until a region's day cycle says otherwise.
    ambient_light_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    diffuse_light_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    # The night sky. `star_level` is zero all day in the default cycle and one
    # in both night keyframes; the moon is up whenever its own rotation puts
    # it up, which is the opposite half of the day from the sun.
    moon_direction: tuple[float, float, float] | None = None
    moon_level: float = 0.0
    star_level: float = 0.0
    # The celestial sphere's own three axes, in world space. The stars are
    # fixed to the sphere and the day cycle turns it, so a ray is turned into
    # this frame before the field is hashed -- see `celestial_axes`.
    celestial_axes: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] = DEFAULT_CELESTIAL_AXES
    # How large each is drawn, as the cosines of the disc's outer and inner
    # edge -- which is what a shader with no disc geometry can compare a dot
    # product against.
    sun_disc: tuple[float, float] = DEFAULT_SUN_DISC
    moon_disc: tuple[float, float] = DEFAULT_MOON_DISC
    # The moon's own face. `moon_id` is an ordinary texture asset behind the
    # ordinary GetTexture capability, so the disc need not be a flat circle:
    # this is the id, and `texture_paths` says whether it has arrived yet.
    moon_texture_id: UUID | None = None
    # Which way up the moon hangs: two world axes across its own face,
    # from the same quaternion that says where it is.
    moon_face_axes: tuple[tuple[float, float, float], tuple[float, float, float]] = (
        DEFAULT_MOON_FACE_AXES
    )
    # The cloud layer's own field, and where in it the layer starts. The
    # offsets are the first two components of each `cloud_pos_density`, which
    # were parsed and unusable while there was no texture to offset into.
    cloud_texture_id: UUID | None = None
    cloud_offsets: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # How much of the direct sun the region's cloud layer leaves on the
    # ground. 1.0 is a clear sky.
    cloud_shadow: float = 1.0
    # The cloud layer. `cloud_cover` is (coarse, fine, variance) and
    # `cloud_scale_drift` is (metres across a cell, drift x, drift y).
    cloud_color: tuple[float, float, float] = (0.41, 0.41, 0.41)
    cloud_cover: tuple[float, float, float] = (0.0, 0.0, 0.0)
    cloud_scale_drift: tuple[float, float, float] = (900.0, 0.0, 0.0)
    # How far the layer has drifted since the viewer started, in cell widths.
    # Accumulated per frame rather than derived from the simulator's clock:
    # that clock arrives every few seconds, and clouds that jump once a second
    # look worse than clouds that do not move at all.
    _cloud_drift: tuple[float, float] = (0.0, 0.0)
    chat_lines: deque[ChatLine] = field(default_factory=lambda: deque(maxlen=128))
    # Who is currently typing, from the start/stop-typing chat types. Kept as a
    # dict rather than a set so insertion order gives a stable display order.
    typing_senders: dict[str, bool] = field(default_factory=dict)

    # ---- bus event handlers ----------------------------------------------

    def _refresh_environment(self, world_view, time_snapshot) -> None:
        """Take this frame's sky, sun and sea from the region's day cycle.

        The time comes from the simulator's own clock. ``UsecSinceStart`` is
        misnamed -- it is a Unix timestamp in microseconds, not an uptime --
        and it is the only clock either side agrees on, so it is what indexes
        the cycle.

        A region with an environment but no clock yet is drawn at midday
        rather than at fraction zero: zero is midnight, and a viewer that
        blacks out for the second before the first time message is a worse
        answer than one that is briefly too bright.
        """
        environment = getattr(world_view, "environment", None)
        self.environment = environment
        if environment is None:
            self.day_fraction = None
            self.environment_sun_direction = None
            self.sky_horizon_color = DEFAULT_SKY_HORIZON_COLOR
            self.sky_zenith_color = DEFAULT_SKY_ZENITH_COLOR
            self.water_tint = DEFAULT_WATER_TINT
            self.water_fog = DEFAULT_WATER_FOG
            self.water_fresnel = DEFAULT_WATER_FRESNEL
            self.water_waves = DEFAULT_WATER_WAVES
            self.water_ripple = DEFAULT_WATER_RIPPLE
            self.water_ripple_below = DEFAULT_WATER_RIPPLE_BELOW
            self.water_normal_id = None
            self.water_reach = DEFAULT_UNDERWATER_REACH
            self.water_wave_speed = DEFAULT_WATER_WAVE_SPEED
            self.light_level = 1.0
            self.ambient_light_color = (1.0, 1.0, 1.0)
            self.diffuse_light_color = (1.0, 1.0, 1.0)
            self.moon_direction = None
            self.moon_level = 0.0
            self.star_level = 0.0
            self.celestial_axes = DEFAULT_CELESTIAL_AXES
            self.sun_disc = DEFAULT_SUN_DISC
            self.moon_disc = DEFAULT_MOON_DISC
            self.moon_texture_id = None
            self.moon_face_axes = DEFAULT_MOON_FACE_AXES
            self.cloud_texture_id = None
            self.cloud_offsets = (0.0, 0.0, 0.0, 0.0)
            self.cloud_shadow = 1.0
            self.cloud_cover = (0.0, 0.0, 0.0)
            return

        clock = (
            getattr(time_snapshot, "usec_since_start", None)
            if time_snapshot is not None
            else None
        )
        # `is not None`, not truthiness: a clock reading of zero is a real
        # moment in the day -- the top of it -- and testing it as a flag put
        # midnight at midday.
        if clock is not None:
            fraction = environment.day_fraction_for(float(clock) / 1_000_000.0)
        else:
            fraction = 0.5
        self.day_fraction = fraction

        sky = environment.sky_at(fraction)
        horizon, zenith = sky_gradient(sky)
        self.sky_horizon_color = horizon
        self.sky_zenith_color = zenith
        water = environment.water_at(fraction)
        self.water_tint = water_tint_for(water, horizon)
        self.water_fog = water_fog(water)
        self.water_fresnel = water_fresnel(water)
        self.water_waves = water_waves(water)
        self.water_ripple = (*water_wave_number(water), water_wave_slope(water))
        self.water_ripple_below = water_wave_slope_below(water)
        self.water_normal_id = _asset_id(water.normal_map)
        self.water_reach = underwater_reach(water)
        self.water_wave_speed = water_wave_speed(water)
        self.environment_sun_direction = sun_direction_for(sky)
        self.light_level = daylight_scale(sky)
        self.ambient_light_color, self.diffuse_light_color = light_hues(sky)
        self.moon_direction = moon_direction_for(sky)
        self.moon_level = moon_level(sky)
        self.star_level = star_level(sky)
        self.celestial_axes = celestial_axes_for(sky)
        self.sun_disc = sun_disc(sky)
        self.moon_disc = moon_disc(sky)
        self.moon_texture_id = _asset_id(sky.moon_id)
        self.moon_face_axes = moon_face_axes(sky)
        self.cloud_texture_id = _asset_id(sky.cloud_id)
        self.cloud_offsets = cloud_offsets(sky)
        self.cloud_shadow = cloud_shadow_scale(sky)
        coarse, fine = cloud_cover(sky)
        self.cloud_color = cloud_hue(sky)
        self.cloud_cover = (coarse, fine, max(0.0, sky.cloud_variance))
        self.cloud_scale_drift = (cloud_size(sky), *self._cloud_drift)

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
        # The per-frame reuse caches. Both are guarded by object identity, so
        # a new region's objects could not be served an old region's entity
        # even if these survived -- but they are keyed by local id, which the
        # next region reassigns from scratch, and that is the shape of bug the
        # rest of this method exists to avoid.
        self._entity_cache.clear()
        self._placement.clear()
        # And last frame's build. It holds those two dictionaries by
        # reference, so clearing them empties it anyway and dropping this line
        # changes nothing a test can see -- which is the point: the next
        # region's safety should not rest on an aliasing accident two lines
        # up. Stated, not inferred.
        self._built = None
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

    def advance_clouds(self, dt_seconds: float) -> None:
        """Drift the cloud layer by this frame's share of its scroll rate.

        `cloud_scroll_rate` is a bare pair of numbers in the document with no
        unit attached, so `CLOUD_DRIFT_PER_SECOND` turns it into a speed. Read
        as a fraction of a cell per second it would be a gale; read as this,
        the default cycle's 0.5 crosses one cell of cloud in about a minute.

        Accumulated rather than computed from the region's clock. The clock is
        a real time and would give the right answer, but it only arrives with a
        time message every few seconds, so the layer would sit still and then
        jump.
        """
        environment = self.environment
        if environment is None or self.day_fraction is None:
            return
        rate = environment.sky_at(self.day_fraction).cloud_scroll_rate
        step = max(0.0, float(dt_seconds)) * CLOUD_DRIFT_PER_SECOND
        self._cloud_drift = (
            self._cloud_drift[0] + rate[0] * step,
            self._cloud_drift[1] + rate[1] * step,
        )
        self.cloud_scale_drift = (self.cloud_scale_drift[0], *self._cloud_drift)

    def advance_water(self, dt_seconds: float) -> None:
        """Move the sea's two waves on by this frame's share of their speed.

        Accumulated per frame rather than taken from the region's clock, for
        the reason `advance_clouds` gives: the clock arrives every few seconds
        and a sea that jumps once a second looks worse than a still one.

        Wrapped at a full turn, which the clouds are not and do not need to be.
        A phase is an angle fed straight to a sine in a shader, and shader
        floats are single precision: left to run, an hour of viewing puts it
        past ten thousand radians, where the gap between representable angles
        is wide enough to show as the waves quantising.
        """
        speed = self.water_wave_speed
        step = max(0.0, float(dt_seconds))
        self.water_phase = (
            (self.water_phase[0] + speed[0] * step) % math.tau,
            (self.water_phase[1] + speed[1] * step) % math.tau,
        )

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

    def drawable_entities(self) -> list[SceneEntity]:
        """Everything with geometry this frame: here first, then next door.

        The renderer groups by shape and by texture, and neither grouping
        cares which region a prim came from, so the neighbours ride through
        the same passes rather than getting a second set of their own.
        ``render_neighbours`` is honoured here, so turning them off costs
        nothing further down.
        """
        entities = [*self.object_entities.values(), *self.avatar_entities.values()]
        if self.render_neighbours:
            entities.extend(self.neighbour_object_entities.values())
            entities.extend(self.neighbour_avatar_entities.values())
        return entities

    def drawable_entity_count(self) -> int:
        """How many of those there are, without building the list."""
        count = len(self.object_entities) + len(self.avatar_entities)
        if self.render_neighbours:
            count += len(self.neighbour_object_entities)
            count += len(self.neighbour_avatar_entities)
        return count

    def refresh_neighbours(self, session: object | None) -> None:
        """Re-derive the regions next door from the live session.

        Two halves, on different conditions. The *ground* only counts once
        its patches have actually arrived: the circuit opens, is answered,
        and stays empty for a second or two, and drawing it at that point
        paints a flat sheet at zero metres over the sea, which looks far more
        broken than the sea did. The *objects* have no such wait -- a prim
        arrives when it arrives, and one standing over ground that has not
        landed yet is still in the right place.
        """
        self.neighbour_object_entities = {}
        self.neighbour_avatar_entities = {}
        neighbours = getattr(session, "neighbours", None)
        if not neighbours:
            self.neighbour_terrain = ()
            self._neighbour_caches = {}
            return
        root = getattr(session, "region_handle", None)
        if root is None:
            self.neighbour_terrain = ()
            self._neighbour_caches = {}
            return
        self._refresh_neighbour_entities(neighbours, root)
        paths = getattr(session, "texture_paths", {})
        self.neighbour_terrain = tuple(
            NeighbourTerrain(
                handle=handle,
                offset=circuit.offset_from(root),
                heightmap=circuit.heightmap,
                region_name=circuit.region_name,
                texture_paths=tuple(  # type: ignore[arg-type]
                    paths.get(texture_id)
                    for texture_id in (circuit.terrain_detail or (None,) * 4)
                ),
                start_height=circuit.terrain_start_height or (0.0, 0.0, 0.0, 0.0),
                height_range=circuit.terrain_height_range or (0.0, 0.0, 0.0, 0.0),
                water_height=circuit.water_height,
            )
            for handle, circuit in sorted(neighbours.items())
            if circuit.heightmap.patch_count > 0
        )

    def _refresh_neighbour_entities(self, neighbours: dict, root: int) -> None:
        """Build every neighbouring region's prims, in this region's frame.

        Each region is walked with its own cache and its own placement map.
        Sharing the root region's would be wrong twice over: the caches are
        keyed by local id, which is per region, and the ``is`` fast path would
        hand a neighbour's entity back for a prim underfoot.
        """
        fresh: dict[int, tuple[tuple[float, float], object]] = {}
        for handle, circuit in sorted(neighbours.items()):
            offset = circuit.offset_from(root)
            remembered = self._neighbour_caches.get(handle)
            if remembered is None or remembered[0] != offset:
                previous = None
            else:
                previous = remembered[1]
            # `previous` and not just its cache: a region next door is the same
            # shape of work as the one underfoot and was paying full price for
            # every frame of it -- no repeat frame, no patched transforms, no
            # patched entities -- times however many regions are in view. The
            # build carries its own cache and placement, so handing the whole
            # record back is also less bookkeeping than handing back two
            # pieces of it.
            built = _build_entities(
                getattr(circuit, "world_view", None),
                cache=previous.cache if previous is not None else {},  # type: ignore[union-attr]
                previous_placement=(
                    previous.placement if previous is not None else {}  # type: ignore[union-attr]
                ),
                previous=previous,  # type: ignore[arg-type]
                offset=offset,
                region_handle=handle,
            )
            fresh[handle] = (offset, built)
            for local_id, entity in built.objects.items():
                self.neighbour_object_entities[(handle, local_id)] = entity
            for local_id, entity in built.avatars.items():
                self.neighbour_avatar_entities[(handle, local_id)] = entity
        self._neighbour_caches = fresh

    def refresh_from_world_view(self, world_view: object | None) -> None:
        """Re-derive entities from the current WorldView. Called once per frame.

        Idempotent: clears existing entities each call so removed objects
        disappear without an explicit kill event.
        """
        self.object_entities = {}
        self.avatar_entities = {}
        if world_view is None:
            # Belt and braces, and said to be: the repeat check also asks
            # whether every prim is the same *instance* it cached, and a new
            # world's prims never are, so dropping this line changes nothing a
            # test can see. It is here because the invalidation belongs where
            # the invalidation happens rather than resting on a second
            # mechanism noticing in time.
            self._built = None
            return

        self.avatar_position = self_avatar_position(world_view)

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
        self._refresh_environment(world_view, time_snapshot)

        was = self._built
        built = _build_entities(
            world_view,
            cache=self._entity_cache,
            previous_placement=self._placement,
            previous=self._built,
        )
        # Counted rather than asserted. The bench says a repeat frame costs
        # 3 ms instead of 31 on a still 15,000-prim region; what it cannot say
        # is how often a *live* region has one, and a saving that never
        # happens is not a saving. These two are the ratio, in the soak log,
        # from the run rather than from a benchmark's idea of a quiet world.
        if built is was:
            self.repeat_frames += 1
        else:
            self.rebuilt_frames += 1
        self._built = built
        self.object_entities = built.objects
        self.avatar_entities = built.avatars
        self._entity_cache = built.cache
        self._placement = built.placement

        sim_stats = getattr(world_view, "latest_sim_stats", None)
        if sim_stats is not None:
            self.sim_health = summarize_sim_stats(sim_stats.stats)

        if world_view.region is not None and self.region_name is None:
            self.region_name = world_view.region.name
        if world_view.region is not None:
            water_height = getattr(world_view.region, "water_height", None)
            if water_height is not None:
                self.water_height = float(water_height)



def _asset_id(raw: str) -> UUID | None:
    """A day cycle's texture id, or None where there is not one.

    The null id is the document's way of writing "no texture" -- `sun_id` is
    null in every keyframe of the default cycle -- so it reads the same here as
    an absent field or an unparsable one: draw the client's own.
    """
    if not raw:
        return None
    try:
        asset_id = UUID(raw)
    except ValueError:
        return None
    return None if asset_id.int == 0 else asset_id


def _as_vec3(value: object | None) -> tuple[float, float, float] | None:
    if value is None:
        return None
    try:
        x, y, z = value  # type: ignore[misc]
        return (float(x), float(y), float(z))
    except (TypeError, ValueError):
        return None


@dataclass(slots=True, eq=False)
class _BuiltEntities:
    """What one region's `WorldView` came to this frame.

    ``eq=False`` on purpose. This is a record of *which* build, not a value:
    the only question ever asked of two of them is whether the frame handed
    the previous one straight back, and the generated ``__eq__`` answers that
    by walking four dictionaries of fifteen thousand entries -- a deeper walk
    than the one the fast path exists to avoid, arriving at the same answer.
    Falling back to identity makes writing it the slow way impossible rather
    than merely unwise.

    `cache` and `placement` are what the next frame is handed back: keeping
    them beside the entities is what lets a second region be walked with the
    same code and its own memory, rather than sharing the root region's.
    """

    objects: dict[int, SceneEntity]
    avatars: dict[int, SceneEntity]
    cache: dict[int, tuple[object, object, SceneEntity]]
    placement: dict[int, tuple[tuple[float, float, float], tuple[float, float, float, float]]]
    #: The terse-only objects this was built from, by local id. Kept because
    #: some entities come from them and nothing else records that they were
    #: the ones -- `_nothing_moved` needs both halves of the world to say the
    #: frame is a repeat.
    terse: dict[int, object]
    #: Every prim's own transform in the region's frame, and the instance each
    #: one was read off. Carried so the next frame can patch what moved
    #: instead of rebuilding all of it: measured at 6.77 ms per 15,000 prims
    #: to build against 0.06 ms to patch the 150 that moved.
    transforms: dict[int, tuple[int, object, object]] = field(default_factory=dict)
    sources: dict[int, object] = field(default_factory=dict)
    #: Which ids in `transforms` came from a terse update. A full update takes
    #: the entry off a terse one for the same id, and this is what says an
    #: entry is still the terse one's to overwrite -- `objects` is keyed by
    #: full id, so it cannot be asked whether a local id is in it.
    terse_only: set[int] = field(default_factory=set)
    #: Which prims hang off which, by local id. What lets the next frame
    #: recompose downward from what moved instead of walking the region.
    children: dict[int, set[int]] = field(default_factory=dict)
    #: How many entries in `transforms` have a parent. Kept as a count rather
    #: than a flag so patching can maintain it exactly -- unparenting the last
    #: linked prim in the region has to be able to turn the composing back off.
    parented: int = 0


def _build_entities(
    world_view: object,
    *,
    cache: dict[int, tuple[object, object, SceneEntity]],
    previous_placement: dict,
    previous: _BuiltEntities | None = None,
    offset: tuple[float, float] = (0.0, 0.0),
    region_handle: int = 0,
) -> _BuiltEntities:
    """Turn one region's objects into entities, in the root region's frame.

    `offset` is where this region's origin sits relative to the one the
    avatar is in, in metres -- (0, 0) for that region itself, and straight
    from `NeighbourCircuit.offset_from` for the ones next door. It is added
    to the *final* position, after a child has been composed through its
    parent, because a child's parent-relative position is in its own region's
    frame either way.

    `region_handle` is stamped on every entity built here. Local ids are
    assigned per region, so 42 next door and 42 underfoot are different
    prims; anything keyed by local id downstream -- the renderer's packed
    instance cache above all -- has to key by the pair or it hands one prim's
    model matrix to the other.
    """
    objects = getattr(world_view, "objects", {})
    terse_objects = getattr(world_view, "terse_objects", {})
    if previous is not None and _nothing_moved(objects, terse_objects, previous):
        # Every dict below would be rebuilt to hold exactly what it holds now.
        # On a still 15,000-prim region that is 30 ms a frame spent arriving
        # back where it started: 69,000 dictionary lookups and 60,000 inserts
        # to produce four dictionaries equal to the four from last frame.
        # Measured at 30.1 ms against 2.4 ms for the check that says so.
        return previous
    offset_x, offset_y = offset
    shifted = bool(offset_x or offset_y)
    # Empty unless something in view has a parent, so a region of
    # unlinked prims never pays for this.
    #
    # Two ways in. The frame is not a repeat, but "not a repeat" on a live
    # region means a few dozen prims moved out of fifteen thousand, and
    # rebuilding every prim's own transform to change fifty of them is 6.77 ms
    # a frame of arriving back where it started. `_changed_transform_sources`
    # names the ones that moved for the price of the scan that already had to
    # happen; when it can account for every prim it did *not* name, last
    # frame's transforms are patched instead of rebuilt.
    frame = None
    if previous is not None:
        frame = _patched_region_frame(
            objects, terse_objects, previous, previous_placement
        )
    if frame is None:
        frame = _region_frame_transforms(
            objects, terse_objects, cache=cache, previous=previous_placement
        )
    placed = frame.placed

    object_entities: dict[int, SceneEntity] = {}
    avatar_entities: dict[int, SceneEntity] = {}
    fresh_cache: dict[int, tuple[object, object, SceneEntity]] = {}
    if frame.rebuild is None:
        full_todo: Iterable = objects.values()
        terse_todo: Iterable = terse_objects.values()
    else:
        # The same argument as the transforms one level down. Deciding that a
        # prim's entity is still good costs two dictionary lookups and an
        # identity check, which is nothing -- until it is fifteen thousand of
        # them, sixty times a second, to arrive back at the entity already in
        # hand. The frame that patched the transforms already knows which prims
        # those two lookups would have said anything about.
        object_entities = dict(previous.objects)  # type: ignore[union-attr]
        avatar_entities = dict(previous.avatars)  # type: ignore[union-attr]
        fresh_cache = dict(previous.cache)  # type: ignore[union-attr]
        sources = frame.sources
        terse_only = frame.terse_only
        terse_get = terse_objects.get
        full_list: list = []
        terse_list: list = []
        for local_id in frame.rebuild:
            # Dropped first and unconditionally. A prim that changed pcode
            # swaps which dict it belongs in, and one whose entity cannot be
            # built this frame -- a child whose parent has just gone -- has to
            # leave rather than keep the one it had.
            object_entities.pop(local_id, None)
            avatar_entities.pop(local_id, None)
            fresh_cache.pop(local_id, None)
            source = sources.get(local_id)
            if source is not None and local_id not in terse_only:
                full_list.append(source)
            # Not `elif`. The terse pass below is not only for ids no full
            # update has been seen for: it is also the fallback for a full
            # update that could not be built -- an orphaned child draws as a
            # terse placeholder rather than not at all -- and the guard it
            # opens with is what decides between the two. Splitting the ids by
            # which half of the world owns the *transform* skips that fallback
            # and the prim disappears.
            terse = terse_get(local_id)
            if terse is not None:
                terse_list.append(terse)
        full_todo = full_list
        terse_todo = terse_list

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
    cache_get = cache.get
    placed_get = placed.get
    for obj in full_todo:
        local_id = obj.local_id
        cached = cache_get(local_id)
        if cached is not None and cached[0] is obj:
            # Its own data is untouched. A root is then finished -- nothing
            # else feeds its transform -- and only a child has to check
            # whether its parent moved underneath it.
            #
            # `is`, not `==`: `resolve_world_transforms` hands back the very
            # tuple it returned last time for anything that did not move, and
            # says so. Comparing by value instead walks two nested tuples of
            # floats for every child in the region, every frame, to reach the
            # same answer. Where it is wrong it is wrong in the safe
            # direction -- a rebuilt-but-equal transform rebuilds an entity
            # that need not have been.
            was_placed = cached[1]
            if was_placed is None or placed_get(local_id) is was_placed:
                entity = cached[2]
                fresh_cache[local_id] = cached
                if obj.pcode == PCODE_AVATAR:
                    avatar_entities[local_id] = entity
                else:
                    object_entities[local_id] = entity
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
        if shifted:
            position = (position[0] + offset_x, position[1] + offset_y, position[2])

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
            region_handle=region_handle,
        )
        fresh_cache[local_id] = (obj, lifted, entity)
        if obj.pcode == PCODE_AVATAR:
            avatar_entities[local_id] = entity
        else:
            object_entities[local_id] = entity

    # Terse-only objects (no full ObjectUpdate seen yet) — render a placeholder.
    for terse in terse_todo:
        if terse.local_id in object_entities or terse.local_id in avatar_entities:
            continue
        yaw = _quat_to_yaw(terse.rotation)
        pcode = PCODE_AVATAR if terse.is_avatar else PCODE_PRIM
        position = terse.position
        if shifted:
            position = (position[0] + offset_x, position[1] + offset_y, position[2])
        entity = SceneEntity(
            local_id=terse.local_id,
            pcode=pcode,
            kind=_kind_for_pcode(pcode),
            position=position,
            scale=(0.5, 0.5, 0.5),  # terse-only: minimal placeholder
            rotation=terse.rotation,
            rotation_z_radians=yaw,
            name=None,
            default_texture_id=None,
            shape=None,
            tint=PCODE_COLORS.get(pcode, DEFAULT_MARKER_COLOR),
            region_handle=region_handle,
        )
        if terse.is_avatar:
            avatar_entities[terse.local_id] = entity
        else:
            object_entities[terse.local_id] = entity

    return _BuiltEntities(
        objects=object_entities,
        avatars=avatar_entities,
        cache=fresh_cache,
        placement=placed,
        terse=dict(terse_objects),
        transforms=frame.transforms,
        sources=frame.sources,
        terse_only=frame.terse_only,
        children=frame.children,
        parented=frame.parented,
    )


def _nothing_moved(objects: dict, terse_objects: dict, previous: _BuiltEntities) -> bool:
    """Would rebuilding produce exactly what was built last time?

    `WorldView` never edits an object in place -- every update replaces the
    instance -- so `is` is an exact answer to "has this prim changed?", and
    the counts matching means no prim was added or removed. Together those two
    say the whole frame is a repeat.

    It answers `False` on the first prim that moved rather than counting them,
    so a busy region pays for a handful of comparisons and then does the work
    it was going to do anyway. A still one pays 15,000 comparisons instead of
    30 ms.

    `previous.cache` holds only the prims an entity was *built* for, so a
    region with a child whose parent has not arrived has fewer cached entries
    than objects and takes the slow path until it does. That is the safe
    direction, and it lasts a frame or two.
    """
    cache = previous.cache
    if len(cache) != len(objects):
        return False
    # The counts above are the entity cache's, and the entity cache cannot see
    # a prim it never built an entity for -- a child whose parent has not
    # arrived. Remove that orphan and the two lengths agree again while the
    # region has in fact changed, which is a frame called a repeat that is not
    # one. Every prim on this path is positioned and cached (an unpositioned or
    # orphaned one has no cache entry, and the length check above would have
    # caught it), so what `transforms` should hold is exactly the objects plus
    # the terse-only ids -- and if it holds more, something left the region.
    if len(previous.sources) != len(objects) + len(previous.terse_only):
        return False
    cache_get = cache.get
    for obj in objects.values():
        was = cache_get(obj.local_id)
        if was is None or was[0] is not obj:
            return False
    was_terse = previous.terse
    if len(was_terse) != len(terse_objects):
        return False
    terse_get = was_terse.get
    for local_id, terse in terse_objects.items():
        if terse_get(local_id) is not terse:
            return False
    return True


class _RegionFrame(NamedTuple):
    """One frame's answer to "where is everything, in the region's frame?"

    `placed` is what the entity build reads. The other three are what the
    *next* frame reads, to patch this answer rather than recompute it.
    """

    placed: dict[int, tuple[tuple[float, float, float], tuple[float, float, float, float]]]
    transforms: dict[int, tuple[int, object, object]]
    sources: dict[int, object]
    terse_only: set[int]
    #: Which prims hang off which, by local id, for every entry in
    #: `transforms` with a parent -- including parents that are not in view.
    #: An absent parent is the interesting case: it is how the prims waiting on
    #: one are found the moment it arrives.
    children: dict[int, set[int]]
    parented: int
    #: Whose *entity* has to be built again -- the prims whose own data changed
    #: and the prims the composing moved, which is not the same set: a child
    #: that did not change is somewhere else entirely if its root did. `None`
    #: means "all of them", which is what a rebuilt frame says and what a
    #: neighbouring region says every frame.
    rebuild: set[int] | None


def _region_frame_transforms(
    objects: dict,
    terse_objects: dict,
    *,
    cache: dict,
    previous: dict,
) -> _RegionFrame:
    """Where everything parented actually is, in the region's frame.

    A prim with a parent reports where it is *relative to that parent* --
    observed live, see :mod:`vibestorm.viewer3d.linkset` -- so without this
    every child of every linkset, and every attachment on every avatar, is
    drawn a few metres from the region corner.

    ``placed`` is ``{}`` when nothing in view has a parent, which is the whole
    of a region of unlinked prims and the whole of the local test region. The
    caller only looks anything up for a parented object, so an empty result
    and a region with no parents are the same thing.

    Terse-only objects are included as roots: ``ImprovedTerseObjectUpdate``
    carries no parent id, and a linkset root seen only tersely is still the
    frame its children hang off. They go in whether or not anything is
    parented, so that what is handed to the next frame is the whole region
    either way -- a linkset arriving must not find half a map waiting for it.

    ``cache`` is the caller's entity cache from last frame and ``previous`` the
    answer this gave then. Between them they say which prims are still the
    objects they were, and composing is skipped for those -- a still linkset
    keeps the exact transform tuples it had, which is what lets the caller
    recognise its children as unchanged in turn.
    """
    transforms: dict[int, tuple[int, object, object]] = {}
    sources: dict[int, object] = {}
    terse_only: set[int] = set()
    children: dict[int, set[int]] = {}
    unchanged: set[int] = set()
    parented = 0
    # Straight attribute access, not `getattr(obj, "position", None)`. This
    # loop is the one thing in a frame that runs for *every* prim in view
    # whether or not anything moved, and the defensive form costs three times
    # as much: 5.2 ms against 1.7 ms per 15,000 prims, measured, for the three
    # fields read here. They are required fields on `WorldObject` and the only
    # thing that puts an object in this dict is `remember_object`, so the
    # default was never reachable -- and a stub that lacks one should raise
    # here rather than be quietly left out of the world.
    cache_get = cache.get
    unchanged_add = unchanged.add
    for obj in objects.values():
        position = obj.position
        if position is None:
            continue
        local_id = obj.local_id
        parent_id = obj.parent_id
        if parent_id:
            parented += 1
            kin = children.get(parent_id)
            if kin is None:
                children[parent_id] = {local_id}
            else:
                kin.add(local_id)
        transforms[local_id] = (parent_id, position, obj.rotation)
        sources[local_id] = obj
        was = cache_get(local_id)
        if was is not None and was[0] is obj:
            unchanged_add(local_id)
    for terse in terse_objects.values():
        local_id = terse.local_id
        if local_id in transforms:
            continue
        # No entry in the entity cache to compare against, so a terse-only
        # root always counts as moved. It is a prim whose full update has not
        # arrived; there is rarely anything hanging off one.
        transforms[local_id] = (0, terse.position, terse.rotation)
        sources[local_id] = terse
        terse_only.add(local_id)
    placed = (
        resolve_world_transforms(  # type: ignore[arg-type]
            transforms, unchanged=unchanged, previous=previous
        )
        if parented
        # Nothing to compose, and the resolve would walk every prim in the
        # region to say so.
        else {}
    )
    return _RegionFrame(placed, transforms, sources, terse_only, children, parented, None)


def _patched_region_frame(
    objects: dict,
    terse_objects: dict,
    previous: _BuiltEntities,
    previous_placement: dict,
) -> _RegionFrame | None:
    """Last frame's transforms with only what moved written over.

    Returns ``None`` when last frame's answer cannot be patched into this one
    and has to be rebuilt from scratch.

    The frame is not a repeat, but "not a repeat" on a live region means a few
    dozen prims moved out of fifteen thousand, and rebuilding every prim's own
    transform to change fifty of them is 6.77 ms a frame of arriving back
    where it started. The scan that finds the fifty costs exactly what the
    repeat check costs -- 2.32 ms either way, measured, because bailing early
    and collecting as it goes are the same walk -- so the patch is very nearly
    free once the frame has been shown not to be a repeat.

    What it must not do is patch through a *removal*. An id that has gone is
    still sitting in last frame's transforms and nothing here would ever visit
    it, so it would go on composing its children forever. It is caught by
    arithmetic rather than by a second walk over the whole region: every prim
    that should have an entry is either one this found in last frame's sources
    or one it did not, so

        len(previous.sources) + added == live

    exactly when nothing was dropped. An add and a remove in the same frame do
    not cancel, because the added one is counted on the left as well.

    A prim whose position is ``None`` has no entry -- `_region_frame_transforms`
    skips it -- so it reads as added on every frame it is in view, which is
    what keeps the arithmetic balanced for it. Nothing is written for it.

    Copied rather than edited in place. `_BuiltEntities` is a record of one
    frame -- the repeat path hands the very same one back -- and a frame that
    reaches into the previous frame's dictionaries has made the record of what
    was drawn a lie. The copy is 0.2 ms per 15,000 prims against the 6.77 ms
    it stands in for, and the decline path throws it away.
    """
    old_get = previous.sources.get
    transforms = dict(previous.transforms)
    sources = dict(previous.sources)
    terse_only = set(previous.terse_only)
    # Copied one branch at a time. A shallow copy of the index shares its
    # sets, and editing one of those edits the record of the frame before
    # this. Only the parents something moved under are copied, which is a
    # handful, against three thousand for copying the lot.
    children = dict(previous.children)
    copied: set[int] = set()

    def kin_of(parent_id: int) -> set[int]:
        if parent_id not in copied:
            children[parent_id] = set(children.get(parent_id, ()))
            copied.add(parent_id)
        return children[parent_id]

    parented = previous.parented
    changed_ids: set[int] = set()
    #: Terse updates for ids a full update owns. They change no transform --
    #: the full update's entry stands -- but the terse pass is what draws a
    #: full update that could not be resolved, so a changed one still means a
    #: changed entity. Kept apart from `changed_ids` until after the composing:
    #: putting them in before it would tell the resolve that the *full* prim's
    #: transform changed, and recompose its whole linkset for nothing.
    shadowed: list[int] = []
    added = 0
    live = 0
    for obj in objects.values():
        local_id = obj.local_id
        position = obj.position
        if position is None:
            # It has no entry of its own and never counts as one, exactly as
            # the full build skips it. If it *had* one it has to go -- but
            # only if the entry is still its own: a terse update for the same
            # local id owns that entry, and deleting it here would both drop a
            # prim and, because the terse loop puts it straight back, count it
            # twice. That miscount is what let a removal elsewhere in the
            # region patch straight through: the two cancelled.
            if local_id not in terse_only:
                entry = transforms.pop(local_id, None)
                if entry is not None:
                    del sources[local_id]
                    if entry[0]:
                        parented -= 1
                        kin_of(entry[0]).discard(local_id)
            continue
        live += 1
        was = old_get(local_id)
        if was is obj:
            continue
        if was is None:
            added += 1
        changed_ids.add(local_id)
        entry = transforms.get(local_id)
        if entry is not None and entry[0]:
            parented -= 1
            kin_of(entry[0]).discard(local_id)
        parent_id = obj.parent_id
        if parent_id:
            parented += 1
            kin_of(parent_id).add(local_id)
        transforms[local_id] = (parent_id, position, obj.rotation)
        sources[local_id] = obj
        terse_only.discard(local_id)
    for terse in terse_objects.values():
        local_id = terse.local_id
        current = sources.get(local_id)
        if current is not None and local_id not in terse_only:
            # A full update owns this id, exactly as `_region_frame_transforms`
            # gives the full object the entry. Counting it here as well would
            # say the region had grown by one.
            if old_get(local_id) is not terse:
                shadowed.append(local_id)
            continue
        live += 1
        if current is terse:
            continue
        if old_get(local_id) is None:
            added += 1
        changed_ids.add(local_id)
        entry = transforms.get(local_id)
        if entry is not None and entry[0]:
            parented -= 1
            kin_of(entry[0]).discard(local_id)
        transforms[local_id] = (0, terse.position, terse.rotation)
        sources[local_id] = terse
        terse_only.add(local_id)
    # A terse update that went away. If it was terse-only, its entry went with
    # it and the count below declines the frame. If a full update owned the id,
    # nothing above notices at all -- and the placeholder it was drawing, for a
    # full update that could not be resolved, would stay on screen for the rest
    # of the session. The terse half of a region is the prims currently moving,
    # so this difference is over dozens, not over the region.
    shadowed.extend(previous.terse.keys() - terse_objects.keys())
    if len(previous.sources) + added != live:
        return None
    if not parented:
        changed_ids.update(shadowed)
        return _RegionFrame(
            {}, transforms, sources, terse_only, children, parented, changed_ids
        )
    placed = _patched_placement(
        transforms, children, changed_ids, previous_placement, previous.parented
    )
    if placed is None:
        # Only a parent cycle gets here, and only on the frame it forms. The
        # full resolve leaves everything caught in one unplaced, which is what
        # this cannot work out by walking downward from what changed.
        # `changed_ids` doubles as the rebuild set, so the resolve is asked to
        # add every id the composing moved: working that out afterwards means
        # comparing a tuple per prim against last frame's, a second walk over
        # the whole region to recover what this walk already knew.
        #
        # `unchanged` is a set difference at C speed rather than 15,000
        # `set.add` calls in the loop above -- the complement is the big side
        # here. Measured: a `__contains__` wrapper standing in for the set is
        # *slower* than the set, 2.80 ms against 1.85 for 15,000 tests.
        placed = resolve_world_transforms(  # type: ignore[arg-type]
            transforms,
            unchanged=transforms.keys() - changed_ids,
            previous=previous_placement,
            moved=changed_ids,
        )
    changed_ids.update(shadowed)
    return _RegionFrame(
        placed, transforms, sources, terse_only, children, parented, changed_ids
    )


def _patched_placement(
    transforms: dict,
    children: dict[int, set[int]],
    changed_ids: set[int],
    previous_placement: dict,
    previously_parented: int,
) -> dict | None:
    """Last frame's composed positions, recomposed only where they moved.

    The third and last of the region-sized walks. `resolve_world_transforms`
    is already careful -- a prim whose own transform is unchanged keeps the
    very tuple it had -- but it still visits every prim in the region to hand
    14,850 of them back what they already held, and that walk is now most of
    what a frame costs.

    A prim's world transform changes only if its own did or an ancestor's did,
    so the work is a walk *downward* from what changed, which needs the one
    thing the resolve never kept: which prims hang off which. `children` is
    that index, maintained beside `transforms` for the price of the edits.

    `changed_ids` is both the input and the output. It arrives holding the
    prims whose own transform changed and leaves holding every prim whose
    *world* transform is not what it was -- their descendants included, which
    is the caller's answer to whose entity has to be built again.

    Returns `None` when it cannot answer, which means a parent cycle: the walk
    would go round it forever, so it stops and lets the full resolve say what
    it says. A simulator should never send one; the randomised differential
    makes them on purpose.
    """
    if not previously_parented:
        # Last frame composed nothing -- `placed` was `{}` because the region
        # held no parented prim -- so there is no answer to patch. Every prim
        # in the region would have to be walked to build one, which is the
        # thing this exists not to do.
        return None
    placed = dict(previous_placement)
    stack = list(changed_ids)
    # A cycle is the only way the walk does not terminate, and it announces
    # itself by revisiting: four passes over the region is far more than any
    # honest linkset depth and cheap to check against.
    budget = 4 * len(transforms) + 64
    while stack:
        budget -= 1
        if budget < 0:
            return None
        local_id = stack.pop()
        entry = transforms.get(local_id)
        if entry is None:
            # A guard rather than a case. Every id in the index has an entry
            # when it goes in, and the two ways one leaves -- a prim removed,
            # and a prim whose position went away -- both decline the frame
            # before reaching here, so nothing patched should ever find this
            # true. It is here because "should" is doing the work in that
            # sentence, and the cost of being wrong is a prim composing its
            # children out of a transform that is not there.
            gone = placed.pop(local_id, None)
            if gone is not None:
                changed_ids.add(local_id)
                stack.extend(children.get(local_id, ()))
            continue
        parent_id, position, rotation = entry
        turn = rotation if rotation is not None else IDENTITY
        if parent_id:
            parent = placed.get(parent_id)
            if parent is None:
                # Its parent is not placed, so neither is it -- and neither is
                # anything below it, which is why the descent carries on.
                if placed.pop(local_id, None) is not None:
                    changed_ids.add(local_id)
                    stack.extend(children.get(local_id, ()))
                continue
            here = compose(parent, (position, turn))
        else:
            here = (position, turn)
        placed[local_id] = here
        changed_ids.add(local_id)
        kin = children.get(local_id)
        if kin:
            stack.extend(kin)
    return placed


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
    "NeighbourTerrain",
    "Scene",
    "SculptMeshHint",
    "MeshSourceKind",
    "PCODE_AVATAR",
    "PCODE_PRIM",
    "DEFAULT_WATER_HEIGHT_M",
    "classify_prim_shape",
    "decode_sculpt_mesh_hint",
]
