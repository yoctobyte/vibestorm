"""Adapter that applies parsed UDP messages into a normalized world view."""

from __future__ import annotations

from dataclasses import dataclass

from vibestorm.udp.messages import (
    MessageDecodeError,
    ObjectUpdateSummary,
    RegionHandshakeMessage,
    format_object_update_interest,
    parse_coarse_location_update,
    parse_improved_terse_object_update,
    parse_object_update,
    parse_object_update_summary,
    parse_sim_stats,
    parse_simulator_viewer_time,
)
from vibestorm.udp.template import MessageDispatch
from vibestorm.world.models import WorldView


@dataclass(slots=True, frozen=True)
class WorldUpdateEvent:
    kind: str
    detail: str


@dataclass(slots=True)
class WorldUpdater:
    world_view: WorldView

    def apply_region_handshake(
        self,
        handshake: RegionHandshakeMessage,
        *,
        region_x: int,
        region_y: int,
    ) -> WorldUpdateEvent:
        self.world_view.set_region(
            name=handshake.sim_name,
            grid_x=region_x // 256,
            grid_y=region_y // 256,
        )
        return WorldUpdateEvent(
            kind="handshake.region",
            detail=f"sim_name={handshake.sim_name} flags={handshake.region_flags}",
        )

    def apply_dispatch(self, dispatched: MessageDispatch) -> WorldUpdateEvent | None:
        if dispatched.summary.name == "SimStats":
            stats = parse_sim_stats(dispatched)
            self.world_view.apply_sim_stats(stats)
            return WorldUpdateEvent(
                kind="sim.stats",
                detail=(
                    f"region=({stats.region_x},{stats.region_y}) "
                    f"object_capacity={stats.object_capacity} stats={len(stats.stats)} pid={stats.pid}"
                ),
            )

        if dispatched.summary.name == "SimulatorViewerTimeMessage":
            time_info = parse_simulator_viewer_time(dispatched)
            self.world_view.apply_simulator_time(time_info)
            return WorldUpdateEvent(
                kind="sim.time",
                detail=(
                    f"usec_since_start={time_info.usec_since_start} "
                    f"sec_per_day={time_info.sec_per_day} sun_phase={time_info.sun_phase:.3f}"
                ),
            )

        if dispatched.summary.name == "CoarseLocationUpdate":
            coarse = parse_coarse_location_update(dispatched)
            self.world_view.apply_coarse_location_update(coarse)
            return WorldUpdateEvent(
                kind="world.coarse_location",
                detail=(
                    f"locations={len(coarse.locations)} agents={len(coarse.agent_ids)} "
                    f"you={coarse.you_index} prey={coarse.prey_index}"
                ),
            )

        if dispatched.summary.name == "ObjectUpdate":
            summary = parse_object_update_summary(dispatched)
            try:
                object_update = parse_object_update(dispatched)
            except MessageDecodeError as exc:
                self.world_view.apply_object_update_summary(summary)
                return WorldUpdateEvent(
                    kind="world.object_update_partial",
                    detail=(
                        f"region_handle={summary.region_handle} "
                        f"objects={summary.object_count} dilation={summary.time_dilation} "
                        f"reason={exc}"
                    ),
                )

            self.world_view.apply_object_update(object_update)
            has_rich_tail = any(
                obj.texture_entry_size > 0
                or obj.texture_anim_size > 0
                or obj.data_size > 0
                or obj.text_size > 0
                or obj.media_url_size > 0
                or obj.ps_block_size > 0
                or obj.extra_params_size > 0
                for obj in object_update.objects
            )
            interest_details = [
                detail
                for obj in object_update.objects
                for detail in [format_object_update_interest(obj)]
                if detail is not None
            ]
            detail = (
                f"region_handle={object_update.region_handle} "
                f"objects={len(object_update.objects)} dilation={object_update.time_dilation}"
            )
            if interest_details:
                detail += " " + " ; ".join(interest_details)
            return WorldUpdateEvent(
                kind="world.object_update_rich" if has_rich_tail else "world.object_update",
                detail=detail,
            )

        if dispatched.summary.name == "ImprovedTerseObjectUpdate":
            terse = parse_improved_terse_object_update(dispatched)
            self.world_view.apply_object_update_summary(
                ObjectUpdateSummary(
                    region_handle=terse.region_handle,
                    time_dilation=terse.time_dilation,
                    object_count=len(terse.objects),
                ),
            )
            rich_entries = sum(1 for obj in terse.objects if obj.texture_entry_size > 0)
            local_ids = [str(obj.local_id) for obj in terse.objects if obj.local_id is not None][:4]
            return WorldUpdateEvent(
                kind="world.improved_terse_object_update",
                detail=(
                    f"region_handle={terse.region_handle} "
                    f"objects={len(terse.objects)} dilation={terse.time_dilation} "
                    f"rich_entries={rich_entries}"
                    + (f" local_ids={','.join(local_ids)}" if local_ids else "")
                ),
            )

        return None
