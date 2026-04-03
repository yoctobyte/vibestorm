"""World-facing normalized models."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from vibestorm.udp.messages import (
    CoarseLocationUpdateMessage,
    ObjectUpdateMessage,
    ObjectUpdateSummary,
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
    default_texture_id: UUID | None


@dataclass(slots=True)
class WorldView:
    region: RegionInfo | None = None
    latest_sim_stats: SimStatSnapshot | None = None
    latest_time: SimulatorTimeSnapshot | None = None
    latest_object_update: ObjectUpdateSnapshot | None = None
    coarse_agents: tuple[CoarseAgentLocation, ...] = ()
    agent_presences: dict[UUID, AgentPresence] = field(default_factory=dict)
    objects: dict[UUID, WorldObject] = field(default_factory=dict)
    sim_stats_updates: int = 0
    time_updates: int = 0
    coarse_location_updates: int = 0
    object_update_events: int = 0

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
            self.objects[obj.full_id] = WorldObject(
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
                default_texture_id=obj.default_texture_id,
            )
