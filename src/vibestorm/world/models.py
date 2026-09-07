"""World-facing normalized models."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from uuid import UUID

from vibestorm.udp.messages import (
    CoarseLocationUpdateMessage,
    ExtraParamEntry,
    ImprovedTerseObjectUpdateMessage,
    KillObjectMessage,
    ObjectPropertiesEntry,
    ObjectPropertiesFamilyMessage,
    ObjectPropertiesMessage,
    ObjectUpdateMessage,
    ObjectUpdateSummary,
    PrimShapeData,
    SimStatsMessage,
    SimulatorViewerTimeMessage,
)
from vibestorm.world.environment import RegionEnvironment
from vibestorm.world.sim_stats import NamedSimStat, name_sim_stats
from vibestorm.world.texture_anim import TextureAnimation
from vibestorm.world.texture_entry import TextureEntry, parse_texture_entry

#: An avatar's pcode, from the ObjectUpdate PCode field. Named here because a
#: seated avatar is a child of its seat and must not be swept away with it.
PCODE_AVATAR = 47


@dataclass(slots=True, frozen=True)
class RegionInfo:
    name: str
    grid_x: int
    grid_y: int
    water_height: float | None = None
    # RegionHandshake's flag word. Kept raw here; world/land_flags.py names the
    # bits LSL exposes and reports the rest as unknown.
    region_flags: int = 0
    #: The region's four ground textures, and the elevation band each covers.
    #: A region that names none sends zero UUIDs, which is a real answer.
    #: ``start_height`` and ``height_range`` are per region corner, in the
    #: template's 00, 01, 10, 11 order -- (x=0,y=0), (0,1), (1,0), (1,1).
    terrain_detail: tuple[UUID, UUID, UUID, UUID] = (
        UUID(int=0),
        UUID(int=0),
        UUID(int=0),
        UUID(int=0),
    )
    terrain_start_height: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    terrain_height_range: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass(slots=True, frozen=True)
class SimStatSnapshot:
    region_x: int
    region_y: int
    object_capacity: int
    stats_count: int
    pid: int
    #: The named stat values themselves. These were previously decoded and then
    #: dropped, leaving only the count — every measure of region health the sim
    #: reports arrived and was discarded one step short of being usable.
    stats: tuple[NamedSimStat, ...] = ()

    def stat(self, stat_id: int) -> float | None:
        """The value of one stat by ``StatsIndex`` id, or None if not sent."""
        for entry in self.stats:
            if entry.stat_id == stat_id:
                return entry.value
        return None

    def unknown_stats(self) -> tuple[NamedSimStat, ...]:
        """Stats whose id is not in the known table — a protocol-drift signal."""
        return tuple(entry for entry in self.stats if not entry.is_known)


@dataclass(slots=True, frozen=True)
class SimulatorTimeSnapshot:
    usec_since_start: int
    sec_per_day: int
    sec_per_year: int
    sun_phase: float
    sun_direction: tuple[float, float, float] | None = None


#: What one unit of a coarse location's height byte is worth, in metres.
#:
#: `CoarseLocationUpdate` spends one byte per axis on a 256 m region, so x and
#: y are whole metres and z is *four* of them -- OpenSim writes
#: `(byte)(CoarseLocations[i].Z * 0.25f)`, pinned in
#: `test_opensim_source_pins.py`. Reading the byte as metres puts an avatar
#: standing at 25.9 m at 6.0 m, which is exactly what the viewer's position
#: readout said while drawing it correctly on a hilltop.
COARSE_HEIGHT_STEP_M = 4.0

#: The height byte a simulator sends for anything above 1024 m. Zero: the same
#: byte it sends for an avatar standing on a beach, because the encoder is
#: `Z > 1024 ? (byte)0 : ...`. So a zero height is not a height, it is two
#: possibilities, and nothing in the message separates them.
COARSE_HEIGHT_UNKNOWN = 0


@dataclass(slots=True, frozen=True)
class CoarseAgentLocation:
    """One avatar's whereabouts, to the nearest metre and the nearest four.

    The fields are the bytes off the wire, unconverted, because that is what
    was received and a decode that quietly rescales is a decode nobody can
    check against a capture. `position_m` is the reading of them.
    """

    agent_id: UUID | None
    x: int
    y: int
    z: int
    is_you: bool
    is_prey: bool

    @property
    def position_m(self) -> tuple[float, float, float]:
        """Region metres. Quantised, and the height doubly so.

        Good to a metre horizontally and four vertically -- it is a radar
        blip, not a position, and anything that has the avatar's own
        `ObjectUpdate` should use that instead. See `height_is_certain` for
        the case this cannot express.
        """
        return (float(self.x), float(self.y), float(self.z) * COARSE_HEIGHT_STEP_M)

    @property
    def height_is_certain(self) -> bool:
        """False for a zero byte, which means "on the ground" *or* "above 1024 m"."""
        return self.z != COARSE_HEIGHT_UNKNOWN


@dataclass(slots=True, frozen=True)
class AgentPresence:
    agent_id: UUID
    coarse: CoarseAgentLocation


@dataclass(slots=True, frozen=True)
class ObjectUpdateSnapshot:
    region_handle: int
    time_dilation: int
    object_count: int


@dataclass(slots=True, frozen=True)
class WorldObject:
    full_id: UUID
    local_id: int
    parent_id: int
    pcode: int
    material: int
    click_action: int
    scale: tuple[float, float, float]
    state: int
    crc: int
    update_flags: int
    region_handle: int
    time_dilation: int
    object_data_size: int
    position: tuple[float, float, float] | None
    rotation: tuple[float, float, float, float] | None
    variant: str
    name_values: dict[str, str]
    texture_entry_size: int
    texture_anim_size: int
    data_size: int
    text_size: int
    media_url_size: int
    ps_block_size: int
    extra_params_size: int
    extra_params_entries: tuple[ExtraParamEntry, ...]
    default_texture_id: UUID | None
    texture_entry: TextureEntry | None = None
    shape: PrimShapeData | None = None
    properties_family: ObjectPropertiesFamilyMessage | None = None
    hover_text: str | None = None
    hover_text_color: tuple[int, int, int, int] | None = None
    media_url: str | None = None
    sound_id: UUID | None = None
    sound_gain: float = 0.0
    sound_flags: int = 0
    sound_radius: float = 0.0
    texture_animation: TextureAnimation | None = None


@dataclass(slots=True, frozen=True)
class TerseWorldObject:
    local_id: int
    state: int
    is_avatar: bool
    region_handle: int
    time_dilation: int
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    acceleration: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    angular_velocity: tuple[float, float, float]
    collision_plane: tuple[float, float, float, float] | None = None
    texture_entry_size: int = 0


@dataclass(slots=True)
class WorldView:
    region: RegionInfo | None = None
    latest_sim_stats: SimStatSnapshot | None = None
    latest_time: SimulatorTimeSnapshot | None = None
    latest_object_update: ObjectUpdateSnapshot | None = None
    latest_object_properties_family: ObjectPropertiesFamilyMessage | None = None
    coarse_agents: tuple[CoarseAgentLocation, ...] = ()
    agent_presences: dict[UUID, AgentPresence] = field(default_factory=dict)
    objects: dict[UUID, WorldObject] = field(default_factory=dict)
    terse_objects: dict[int, TerseWorldObject] = field(default_factory=dict)
    local_id_to_full_id: dict[int, UUID] = field(default_factory=dict)
    sim_stats_updates: int = 0
    time_updates: int = 0
    coarse_location_updates: int = 0
    object_update_events: int = 0
    object_properties_family_events: int = 0
    #: Objects whose textures and mesh assets nobody has looked at yet: the
    #: queue behind the two asset fetches, which drain one asset per tick.
    #:
    #: A queue rather than a scan because a scan does not fit in a tick. The
    #: fetches used to find their next asset by walking every object in the
    #: region, which at 15,000 prims measured 135 ms a tick -- and once the
    #: regions next door were being walked too, 1.15 s with a region on every
    #: side. That is time the session spends not reading UDP and not sending
    #: `AgentUpdate`, which a simulator reads as a viewer that has gone away.
    #:
    #: Only `remember_object` adds to these, and so only a full `ObjectUpdate`
    #: does. A terse update replaces the object but carries its asset fields
    #: over by reference, and so does `ObjectPropertiesFamily`; neither can
    #: introduce an asset that was not already queued.
    objects_pending_textures: set[UUID] = field(default_factory=set)
    objects_pending_meshes: set[UUID] = field(default_factory=set)
    #: Long-form properties per object, from `ObjectProperties`. Only ever
    #: populated for objects that have been selected.
    object_properties: dict[UUID, ObjectPropertiesEntry] = field(default_factory=dict)
    object_properties_events: int = 0
    #: The region's own sky and water, from the `ExtEnvironment` capability.
    #: Not from any UDP message: `RegionHandshake` carries the terrain
    #: textures, the bands they cover and the water *height*, and nothing at
    #: all about colour. `None` until the fetch lands, or if it never does.
    environment: RegionEnvironment | None = None

    @property
    def terse_avatar_count(self) -> int:
        return sum(1 for obj in self.terse_objects.values() if obj.is_avatar)

    @property
    def terse_prim_count(self) -> int:
        return sum(1 for obj in self.terse_objects.values() if not obj.is_avatar)

    def nearest_coarse_agent_for_terse(
        self, local_id: int
    ) -> tuple[CoarseAgentLocation, float] | None:
        terse = self.terse_objects.get(local_id)
        if terse is None or not terse.is_avatar or not self.coarse_agents:
            return None

        nearest: CoarseAgentLocation | None = None
        nearest_distance: float | None = None
        for agent in self.coarse_agents:
            dx = terse.position[0] - float(agent.x)
            dy = terse.position[1] - float(agent.y)
            distance = sqrt((dx * dx) + (dy * dy))
            if nearest_distance is None or distance < nearest_distance:
                nearest = agent
                nearest_distance = distance

        if nearest is None or nearest_distance is None:
            return None
        return nearest, nearest_distance

    def set_region(
        self,
        *,
        name: str,
        grid_x: int,
        grid_y: int,
        water_height: float | None = None,
        region_flags: int = 0,
        terrain_detail: tuple[UUID, UUID, UUID, UUID] | None = None,
        terrain_start_height: tuple[float, float, float, float] | None = None,
        terrain_height_range: tuple[float, float, float, float] | None = None,
    ) -> None:
        defaults = RegionInfo(name=name, grid_x=grid_x, grid_y=grid_y)
        self.region = RegionInfo(
            name=name,
            grid_x=grid_x,
            grid_y=grid_y,
            water_height=water_height,
            region_flags=region_flags,
            terrain_detail=terrain_detail or defaults.terrain_detail,
            terrain_start_height=terrain_start_height or defaults.terrain_start_height,
            terrain_height_range=terrain_height_range or defaults.terrain_height_range,
        )

    def apply_sim_stats(self, message: SimStatsMessage) -> None:
        self.latest_sim_stats = SimStatSnapshot(
            region_x=message.region_x,
            region_y=message.region_y,
            object_capacity=message.object_capacity,
            stats_count=len(message.stats),
            pid=message.pid,
            stats=name_sim_stats(message.stats),
        )
        self.sim_stats_updates += 1

    def apply_simulator_time(self, message: SimulatorViewerTimeMessage) -> None:
        self.latest_time = SimulatorTimeSnapshot(
            usec_since_start=message.usec_since_start,
            sec_per_day=message.sec_per_day,
            sec_per_year=message.sec_per_year,
            sun_phase=message.sun_phase,
            sun_direction=message.sun_direction,
        )
        self.time_updates += 1

    def apply_coarse_location_update(self, message: CoarseLocationUpdateMessage) -> None:
        coarse_agents: list[CoarseAgentLocation] = []
        for index, location in enumerate(message.locations):
            agent_id = message.agent_ids[index] if index < len(message.agent_ids) else None
            coarse = CoarseAgentLocation(
                agent_id=agent_id,
                x=location.x,
                y=location.y,
                z=location.z,
                is_you=index == message.you_index,
                is_prey=index == message.prey_index,
            )
            coarse_agents.append(coarse)
            if agent_id is not None:
                self.agent_presences[agent_id] = AgentPresence(agent_id=agent_id, coarse=coarse)
        self.coarse_agents = tuple(coarse_agents)
        self.coarse_location_updates += 1

    def apply_object_update_summary(self, message: ObjectUpdateSummary) -> None:
        self.latest_object_update = ObjectUpdateSnapshot(
            region_handle=message.region_handle,
            time_dilation=message.time_dilation,
            object_count=message.object_count,
        )
        self.object_update_events += 1

    def remember_object(self, obj: WorldObject) -> None:
        """Put an object in the world, and in the queue behind the asset fetches.

        The one door for a *new or re-described* object. Assigning
        ``objects[...]`` directly puts a prim in the world that the texture
        and mesh fetches will never look at, because they read the queue
        rather than walking the region.
        """
        self.objects[obj.full_id] = obj
        self.objects_pending_textures.add(obj.full_id)
        self.objects_pending_meshes.add(obj.full_id)

    def apply_object_update(self, message: ObjectUpdateMessage) -> None:
        self.apply_object_update_summary(
            ObjectUpdateSummary(
                region_handle=message.region_handle,
                time_dilation=message.time_dilation,
                object_count=len(message.objects),
            ),
        )
        for obj in message.objects:
            if obj.full_id.int == 0:
                continue
            new_obj = WorldObject(
                full_id=obj.full_id,
                local_id=obj.local_id,
                parent_id=obj.parent_id,
                pcode=obj.pcode,
                material=obj.material,
                click_action=obj.click_action,
                scale=obj.scale,
                state=obj.state,
                crc=obj.crc,
                update_flags=obj.update_flags,
                region_handle=message.region_handle,
                time_dilation=message.time_dilation,
                object_data_size=obj.object_data_size,
                position=obj.position,
                rotation=obj.rotation,
                variant=obj.variant,
                name_values=dict(obj.name_values),
                texture_entry_size=obj.texture_entry_size,
                texture_anim_size=obj.texture_anim_size,
                data_size=obj.data_size,
                text_size=obj.text_size,
                media_url_size=obj.media_url_size,
                ps_block_size=obj.ps_block_size,
                extra_params_size=obj.extra_params_size,
                extra_params_entries=obj.extra_params_entries,
                default_texture_id=obj.default_texture_id,
                texture_entry=obj.texture_entry,
                shape=obj.shape,
                hover_text=obj.hover_text,
                hover_text_color=obj.hover_text_color,
                media_url=obj.media_url,
                sound_id=obj.sound_id,
                sound_gain=obj.sound_gain,
                sound_flags=obj.sound_flags,
                sound_radius=obj.sound_radius,
                texture_animation=obj.texture_animation,
                properties_family=(
                    self.objects.get(obj.full_id).properties_family
                    if obj.full_id in self.objects
                    else None
                ),
            )
            self.remember_object(new_obj)
            self.local_id_to_full_id[obj.local_id] = obj.full_id
            self.terse_objects.pop(obj.local_id, None)

    def apply_improved_terse_object_update(self, message: ImprovedTerseObjectUpdateMessage) -> None:
        for entry in message.objects:
            full_id = self.local_id_to_full_id.get(entry.local_id)
            if full_id is None:
                self.terse_objects[entry.local_id] = TerseWorldObject(
                    local_id=entry.local_id,
                    state=entry.state,
                    is_avatar=entry.is_avatar,
                    region_handle=message.region_handle,
                    time_dilation=message.time_dilation,
                    position=entry.position,
                    velocity=entry.velocity,
                    acceleration=entry.acceleration,
                    rotation=entry.rotation,
                    angular_velocity=entry.angular_velocity,
                    collision_plane=entry.collision_plane,
                    texture_entry_size=len(entry.texture_entry) if entry.texture_entry else 0,
                )
                continue

            obj = self.objects[full_id]
            # Replace with updated transform
            self.objects[full_id] = WorldObject(
                full_id=obj.full_id,
                local_id=obj.local_id,
                parent_id=obj.parent_id,
                pcode=obj.pcode,
                material=obj.material,
                click_action=obj.click_action,
                scale=obj.scale,
                state=entry.state,  # Updated
                crc=obj.crc,
                update_flags=obj.update_flags,
                region_handle=message.region_handle,
                time_dilation=message.time_dilation,
                object_data_size=obj.object_data_size,
                position=entry.position,  # Updated
                rotation=entry.rotation,  # Updated
                variant=obj.variant,
                name_values=obj.name_values,
                texture_entry_size=len(entry.texture_entry) if entry.texture_entry else 0,
                texture_anim_size=obj.texture_anim_size,
                data_size=obj.data_size,
                text_size=obj.text_size,
                media_url_size=obj.media_url_size,
                ps_block_size=obj.ps_block_size,
                extra_params_size=obj.extra_params_size,
                extra_params_entries=obj.extra_params_entries,
                default_texture_id=UUID(bytes=entry.texture_entry[:16])
                if entry.texture_entry and len(entry.texture_entry) >= 16
                else obj.default_texture_id,
                texture_entry=_parse_texture_entry_or_none(entry.texture_entry)
                if entry.texture_entry and len(entry.texture_entry) >= 16
                else obj.texture_entry,
                shape=obj.shape,
                hover_text=obj.hover_text,
                hover_text_color=obj.hover_text_color,
                media_url=obj.media_url,
                sound_id=obj.sound_id,
                sound_gain=obj.sound_gain,
                sound_flags=obj.sound_flags,
                sound_radius=obj.sound_radius,
                texture_animation=obj.texture_animation,
                properties_family=obj.properties_family,
            )

    def apply_kill_object(self, message: KillObjectMessage) -> None:
        """Remove the named objects, and everything hanging off them.

        A linkset's children are never named. Observed live: rezzing two prims,
        linking them and then taking the root away produced exactly one
        ``KillObject``, carrying the root's local id and nothing else, while
        the child stayed in the world view for the rest of the session. A
        client that removes only what it is told accumulates a phantom prim for
        every linkset that ever leaves view -- which, walking a real region, is
        most of them.

        An avatar is the exception. A seated avatar is a child of its seat, and
        an avatar whose seat is deleted stands up rather than ceasing to exist;
        dropping it here would make whoever was sitting on it vanish.
        """
        doomed = set(message.local_ids)
        children: dict[int, list[int]] = {}
        for obj in self.objects.values():
            if obj.parent_id and obj.pcode != PCODE_AVATAR:
                children.setdefault(obj.parent_id, []).append(obj.local_id)
        pending = list(doomed)
        while pending:
            for child in children.get(pending.pop(), ()):
                if child not in doomed:
                    doomed.add(child)
                    pending.append(child)

        for local_id in doomed:
            full_id = self.local_id_to_full_id.pop(local_id, None)
            if full_id is not None:
                self.objects.pop(full_id, None)
                self.objects_pending_textures.discard(full_id)
                self.objects_pending_meshes.discard(full_id)
            self.terse_objects.pop(local_id, None)

    def apply_object_properties(self, message: ObjectPropertiesMessage) -> None:
        """Keep the long-form properties for each object the message names.

        Kept beside the objects rather than folded into ``WorldObject``: this
        arrives only for an object that has been *selected*, so it exists for a
        handful of prims at a time while ``objects`` holds the whole region,
        and a field on every prim that is almost always ``None`` says less
        than a table of the ones actually asked about.
        """
        for entry in message.objects:
            self.object_properties[entry.object_id] = entry
        self.object_properties_events += 1

    def apply_object_properties_family(self, message: ObjectPropertiesFamilyMessage) -> None:
        self.latest_object_properties_family = message
        self.object_properties_family_events += 1
        existing = self.objects.get(message.object_id)
        if existing is None:
            return
        self.objects[message.object_id] = WorldObject(
            full_id=existing.full_id,
            local_id=existing.local_id,
            parent_id=existing.parent_id,
            pcode=existing.pcode,
            material=existing.material,
            click_action=existing.click_action,
            scale=existing.scale,
            state=existing.state,
            crc=existing.crc,
            update_flags=existing.update_flags,
            region_handle=existing.region_handle,
            time_dilation=existing.time_dilation,
            object_data_size=existing.object_data_size,
            position=existing.position,
            rotation=existing.rotation,
            variant=existing.variant,
            name_values=existing.name_values,
            texture_entry_size=existing.texture_entry_size,
            texture_anim_size=existing.texture_anim_size,
            data_size=existing.data_size,
            text_size=existing.text_size,
            media_url_size=existing.media_url_size,
            ps_block_size=existing.ps_block_size,
            extra_params_size=existing.extra_params_size,
            extra_params_entries=existing.extra_params_entries,
            default_texture_id=existing.default_texture_id,
            texture_entry=existing.texture_entry,
            shape=existing.shape,
            hover_text=existing.hover_text,
            hover_text_color=existing.hover_text_color,
            media_url=existing.media_url,
            sound_id=existing.sound_id,
            sound_gain=existing.sound_gain,
            sound_flags=existing.sound_flags,
            sound_radius=existing.sound_radius,
            texture_animation=existing.texture_animation,
            properties_family=message,
        )


def _parse_texture_entry_or_none(data: bytes | None) -> TextureEntry | None:
    try:
        return parse_texture_entry(data)
    except ValueError:
        return None


def self_avatar_position(world_view: object) -> tuple[float, float, float] | None:
    """Where we are, from the most precise source that has it.

    Three sources, and the order is the whole of it:

    1. **Our own object.** `CoarseLocationUpdate` names which entry is us, and
       that entry carries an agent id; the object dictionary is keyed by it.
       This is metres, from `ObjectUpdate`, and terse updates keep it current.
    2. **The coarse entry itself**, converted -- see `position_m`. Right until
       the first `ObjectUpdate` for our own avatar arrives, and quantised to
       a metre horizontally and four vertically after that, which is why it is
       second and not first.
    3. **Any avatar at all**, from the terse dictionary. A guess, and only
       reached before a single coarse update has landed, when nothing in the
       view says which avatar is ours. In a region holding one avatar it is
       right; in a crowd it is somebody else, so it is last.

    Written once and shared by both viewers. It used to be copied into each of
    them, which is how reading the height byte as metres was wrong twice.

    ``world_view`` is read with `getattr` rather than typed, because the
    viewers hand this stand-ins as often as they hand it the real thing.
    """
    coarse_self = None
    for coarse in getattr(world_view, "coarse_agents", ()):
        if getattr(coarse, "is_you", False):
            coarse_self = coarse
            break

    if coarse_self is not None:
        agent_id = getattr(coarse_self, "agent_id", None)
        if agent_id is not None:
            me = getattr(world_view, "objects", {}).get(agent_id)
            position = getattr(me, "position", None)
            # Unless we are sitting on something. A seated avatar is a child
            # of its seat and reports its position in the seat's frame -- half
            # a metre, not a region coordinate -- which composing would undo
            # and this module has no business doing. The coarse entry is still
            # a region position while seated, so it takes over.
            if position is not None and not getattr(me, "parent_id", 0):
                return position
        return coarse_self.position_m

    for terse in getattr(world_view, "terse_objects", {}).values():
        if getattr(terse, "is_avatar", False):
            return getattr(terse, "position", None)
    return None
