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
from vibestorm.viewer3d.linkset import resolve_world_transforms
from vibestorm.world.chat_types import (
    CHAT_TYPE_SAY,
    CHAT_TYPE_START_TYPING,
    chat_type_name,
    is_typing_notification,
)
from vibestorm.world.environment import RegionEnvironment
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
    #: The prims and avatars standing in the regions next door, keyed by
    #: ``(region handle, local id)``. Deliberately not merged into
    #: ``object_entities``: that dict is keyed by a bare local id, and it is
    #: what ``pick()`` walks -- a neighbour prim is drawn but not selectable,
    #: because the only circuit that sends ``ObjectSelect`` is the root one
    #: and it would select whatever prim holds that id underfoot.
    neighbour_object_entities: dict[tuple[int, int], SceneEntity] = field(default_factory=dict)
    neighbour_avatar_entities: dict[tuple[int, int], SceneEntity] = field(default_factory=dict)
    #: One entity cache and one placement map per neighbouring region, kept
    #: beside the offset they were built at. Per region because both are keyed
    #: by local id, and thrown away when the offset moves -- which is what
    #: walking across a region boundary does to every one of them.
    _neighbour_caches: dict[
        int, tuple[tuple[float, float], dict, dict]
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
        fresh: dict[int, tuple[tuple[float, float], dict, dict]] = {}
        for handle, circuit in sorted(neighbours.items()):
            offset = circuit.offset_from(root)
            remembered = self._neighbour_caches.get(handle)
            if remembered is None or remembered[0] != offset:
                cache: dict = {}
                placement: dict = {}
            else:
                _, cache, placement = remembered
            built = _build_entities(
                getattr(circuit, "world_view", None),
                cache=cache,
                previous_placement=placement,
                offset=offset,
                region_handle=handle,
            )
            fresh[handle] = (offset, built.cache, built.placement)
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
        self._refresh_environment(world_view, time_snapshot)

        built = _build_entities(
            world_view,
            cache=self._entity_cache,
            previous_placement=self._placement,
        )
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



def _self_avatar_position(world_view: object) -> tuple[float, float, float] | None:
    for coarse in getattr(world_view, "coarse_agents", ()):
        if getattr(coarse, "is_you", False):
            return (float(coarse.x), float(coarse.y), float(coarse.z))
    for terse in getattr(world_view, "terse_objects", {}).values():
        if getattr(terse, "is_avatar", False):
            return getattr(terse, "position", None)
    return None


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


@dataclass(slots=True)
class _BuiltEntities:
    """What one region's `WorldView` came to this frame.

    `cache` and `placement` are what the next frame is handed back: keeping
    them beside the entities is what lets a second region be walked with the
    same code and its own memory, rather than sharing the root region's.
    """

    objects: dict[int, SceneEntity]
    avatars: dict[int, SceneEntity]
    cache: dict[int, tuple[object, object, SceneEntity]]
    placement: dict[int, tuple[tuple[float, float, float], tuple[float, float, float, float]]]


def _build_entities(
    world_view: object,
    *,
    cache: dict[int, tuple[object, object, SceneEntity]],
    previous_placement: dict,
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
    object_entities: dict[int, SceneEntity] = {}
    avatar_entities: dict[int, SceneEntity] = {}
    objects = getattr(world_view, "objects", {})
    terse_objects = getattr(world_view, "terse_objects", {})
    offset_x, offset_y = offset
    shifted = bool(offset_x or offset_y)
    # Empty unless something in view has a parent, so a region of
    # unlinked prims never pays for this.
    placed = _region_frame_transforms(
        objects, terse_objects, cache=cache, previous=previous_placement
    )

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
    for terse in terse_objects.values():
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
    )


def _region_frame_transforms(
    objects: dict,
    terse_objects: dict,
    *,
    cache: dict,
    previous: dict,
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

    ``cache`` is the caller's entity cache from last frame and ``previous`` the
    answer this gave then. Between them they say which prims are still the
    objects they were, and composing is skipped for those -- a still linkset
    keeps the exact transform tuples it had, which is what lets the caller
    recognise its children as unchanged in turn.
    """
    transforms: dict[int, tuple[int, tuple[float, float, float], object]] = {}
    unchanged: set[int] = set()
    parented = False
    for obj in objects.values():
        position = getattr(obj, "position", None)
        if position is None:
            continue
        local_id = obj.local_id
        parent_id = int(getattr(obj, "parent_id", 0) or 0)
        if parent_id:
            parented = True
        transforms[local_id] = (parent_id, position, getattr(obj, "rotation", None))
        was = cache.get(local_id)
        if was is not None and was[0] is obj:
            unchanged.add(local_id)
    if not parented:
        # Nothing to compose, and the resolve would walk every prim in the
        # region to say so.
        return {}
    for terse in terse_objects.values():
        if terse.local_id in transforms:
            continue
        # No entry in the entity cache to compare against, so a terse-only
        # root always counts as moved. It is a prim whose full update has not
        # arrived; there is rarely anything hanging off one.
        transforms[terse.local_id] = (0, terse.position, terse.rotation)
    return resolve_world_transforms(  # type: ignore[arg-type]
        transforms, unchanged=unchanged, previous=previous
    )


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
