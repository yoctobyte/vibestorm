"""World-facing normalized models."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from uuid import UUID

from vibestorm.udp.messages import (
    ExtraParamEntry,
    ImprovedTerseObjectUpdateMessage,
    KillObjectMessage,
    ObjectPropertiesFamilyMessage,
    ObjectUpdateMessage,
    ObjectUpdateSummary,
    PrimShapeData,
    SimStatsMessage,
    SimulatorViewerTimeMessage,
)


@dataclass(slots=True, frozen=True)
class RegionInfo:
    name: str
    grid_x: int
    grid_y: int


@dataclass(slots=True, frozen=True)
class SimStatSnapshot:
    region_x: int
    region_y: int
    object_capacity: int
    stats_count: int
    pid: int


@dataclass(slots=True, frozen=True)
class SimulatorTimeSnapshot:
    usec_since_start: int
    sec_per_day: int
    sec_per_year: int
    sun_phase: float


@dataclass(slots=True, frozen=True)
class CoarseAgentLocation:
    agent_id: UUID | None
    x: int
    y: int
    z: int
    is_you: bool
    is_prey: bool


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
    shape: PrimShapeData | None = None
    properties_family: ObjectPropertiesFamilyMessage | None = None


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

    @property
    def terse_avatar_count(self) -> int:
        return sum(1 for obj in self.terse_objects.values() if obj.is_avatar)

    @property
    def terse_prim_count(self) -> int:
        return sum(1 for obj in self.terse_objects.values() if not obj.is_avatar)

    def nearest_coarse_agent_for_terse(self, local_id: int) -> tuple[CoarseAgentLocation, float] | None:
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

    def set_region(self, *, name: str, grid_x: int, grid_y: int) -> None:
        self.region = RegionInfo(name=name, grid_x=grid_x, grid_y=grid_y)

    def apply_sim_stats(self, message: SimStatsMessage) -> None:
        self.latest_sim_stats = SimStatSnapshot(
            region_x=message.region_x,
            region_y=message.region_y,
            object_capacity=message.object_capacity,
            stats_count=len(message.stats),
            pid=message.pid,
        )
        self.sim_stats_updates += 1

    def apply_simulator_time(self, message: SimulatorViewerTimeMessage) -> None:
        self.latest_time = SimulatorTimeSnapshot(
            usec_since_start=message.usec_since_start,
            sec_per_day=message.sec_per_day,
            sec_per_year=message.sec_per_year,
            sun_phase=message.sun_phase,
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
                shape=obj.shape,
                properties_family=self.objects.get(obj.full_id).properties_family if obj.full_id in self.objects else None,
            )
            self.objects[obj.full_id] = new_obj
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
                shape=obj.shape,
                properties_family=obj.properties_family,
            )

    def apply_kill_object(self, message: KillObjectMessage) -> None:
        for local_id in message.local_ids:
            full_id = self.local_id_to_full_id.pop(local_id, None)
            if full_id is not None:
                self.objects.pop(full_id, None)
            self.terse_objects.pop(local_id, None)

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
            shape=existing.shape,
            properties_family=message,
        )
