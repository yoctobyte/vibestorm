import unittest
from uuid import UUID

from vibestorm.app.cli import format_session_report
from vibestorm.udp.session import SessionEvent, SessionReport
from vibestorm.world.models import (
    CoarseAgentLocation,
    ObjectUpdateSnapshot,
    RegionInfo,
    SimulatorTimeSnapshot,
    SimStatSnapshot,
    WorldObject,
    WorldView,
)


class CliSessionReportTests(unittest.TestCase):
    def test_default_report_is_world_facing(self) -> None:
        world = WorldView(
            region=RegionInfo(name="Vibestorm Test", grid_x=1000, grid_y=1000),
            latest_sim_stats=SimStatSnapshot(
                region_x=1000,
                region_y=1000,
                object_capacity=15000,
                stats_count=41,
                pid=1234,
            ),
            latest_time=SimulatorTimeSnapshot(
                usec_since_start=123,
                sec_per_day=14400,
                sec_per_year=31536000,
                sun_phase=0.326,
            ),
            latest_object_update=ObjectUpdateSnapshot(
                region_handle=1099511628032000,
                time_dilation=42,
                object_count=1,
            ),
            coarse_agents=(
                CoarseAgentLocation(
                    agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                    x=128,
                    y=128,
                    z=6,
                    is_you=True,
                    is_prey=False,
                ),
            ),
            objects={
                UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"): WorldObject(
                    full_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                    local_id=7,
                    parent_id=0,
                    pcode=9,
                    material=3,
                    click_action=1,
                    scale=(1.0, 1.0, 1.0),
                    state=0,
                    crc=99,
                    update_flags=0,
                    region_handle=1099511628032000,
                    time_dilation=42,
                    object_data_size=60,
                    position=(128.0, 129.0, 25.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    variant="prim_basic",
                    name_values={},
                    texture_entry_size=0,
                    texture_anim_size=0,
                    data_size=0,
                    text_size=0,
                    media_url_size=0,
                    ps_block_size=0,
                    extra_params_size=0,
                    default_texture_id=UUID("00895567-4724-cb43-ed92-0b47caed1546"),
                ),
            },
            sim_stats_updates=20,
            time_updates=23,
            coarse_location_updates=13,
            object_update_events=3,
        )
        report = SessionReport(
            elapsed_seconds=60.0,
            total_received=97,
            message_counts={"PacketAck": 3, "ObjectUpdate": 3},
            handshake_reply_sent=True,
            movement_completed=True,
            ping_requests_handled=11,
            appended_acks_received=0,
            packet_acks_received=3,
            agent_update_count=55,
            pending_reliable_sequences=(),
            last_region_name="Vibestorm Test",
            close_reason=None,
            world_view=world,
            events=(SessionEvent(at_seconds=0.0, kind="session.started", detail=""),),
        )

        lines = format_session_report(report)

        self.assertIn("status=completed", lines)
        self.assertIn("world[region]=Vibestorm Test grid=(1000,1000)", lines)
        self.assertIn("world[object_update]=events:3 objects:1 region_handle:1099511628032000", lines)
        self.assertIn("world[objects]=tracked:1", lines)
        self.assertTrue(
            any(
                "variant=prim_basic" in line
                and "pos=(128.00,129.00,25.00)" in line
                and "texture=00895567-4724-cb43-ed92-0b47caed1546" in line
                for line in lines
            ),
        )
        self.assertNotIn("packet_acks_received=3", lines)
        self.assertFalse(any(line.startswith("message[") for line in lines))

    def test_verbose_report_includes_transport_diagnostics(self) -> None:
        report = SessionReport(
            elapsed_seconds=5.0,
            total_received=4,
            message_counts={"PacketAck": 1},
            handshake_reply_sent=True,
            movement_completed=False,
            ping_requests_handled=1,
            appended_acks_received=2,
            packet_acks_received=1,
            agent_update_count=0,
            pending_reliable_sequences=(7,),
            last_region_name=None,
            close_reason="simulator closed circuit",
            world_view=WorldView(),
            events=(),
        )

        lines = format_session_report(report, verbose=True)

        self.assertIn("packet_acks_received=1", lines)
        self.assertIn("pending_reliable=1", lines)
        self.assertIn("message[PacketAck]=1", lines)
