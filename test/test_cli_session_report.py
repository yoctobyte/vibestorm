import unittest
from uuid import UUID

from vibestorm.app.cli import format_session_report
from vibestorm.caps.inventory_client import InventoryFetchSnapshot, InventoryFolderContents, InventoryItemEntry
from vibestorm.udp.messages import (
    AgentCachedTextureResponseEntry,
    AgentCachedTextureResponseMessage,
    AgentWearableEntry,
    AgentWearablesUpdateMessage,
    AvatarAppearanceMessage,
)
from vibestorm.udp.session import SessionEvent, SessionReport
from vibestorm.world.models import (
    CoarseAgentLocation,
    ObjectUpdateSnapshot,
    RegionInfo,
    SimulatorTimeSnapshot,
    SimStatSnapshot,
    TerseWorldObject,
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
                CoarseAgentLocation(
                    agent_id=UUID("22222222-3333-4444-5555-666666666666"),
                    x=130,
                    y=131,
                    z=26,
                    is_you=False,
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
                    extra_params_entries=(),
                    default_texture_id=UUID("00895567-4724-cb43-ed92-0b47caed1546"),
                ),
            },
            terse_objects={
                11: TerseWorldObject(
                    local_id=11,
                    state=33,
                    is_avatar=True,
                    region_handle=1099511628032000,
                    time_dilation=42,
                    position=(130.0, 131.0, 26.0),
                    velocity=(0.0, 0.0, 0.0),
                    acceleration=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    angular_velocity=(0.0, 0.0, 0.0),
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
            resolved_capabilities=("EventQueueGet", "SimulatorFeatures", "UploadBakedTexture"),
            bootstrap_packed_appearance_present=True,
            inventory_fetch=InventoryFetchSnapshot(
                folders=(
                    InventoryFolderContents(
                        folder_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
                        owner_id=UUID("11111111-2222-3333-4444-555555555555"),
                        agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                        descendents=22,
                        version=22,
                        categories=(),
                        items=(),
                    ),
                    InventoryFolderContents(
                        folder_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
                        owner_id=UUID("11111111-2222-3333-4444-555555555555"),
                        agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                        descendents=4,
                        version=17,
                        categories=(),
                        items=(
                            InventoryItemEntry(
                                item_id=UUID("02385379-afb8-48b3-8848-47c8333fed2d"),
                                asset_id=UUID("1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b"),
                                parent_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
                                name="Shape",
                                description="",
                                type=18,
                                inv_type=24,
                                flags=None,
                            ),
                            InventoryItemEntry(
                                item_id=UUID("a860475e-6234-40b8-b5b1-3df8fb1d3049"),
                                asset_id=UUID("ffc4de4a-9845-41c1-9f9f-762a059d0bdc"),
                                parent_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
                                name="Skin",
                                description="",
                                type=18,
                                inv_type=24,
                                flags=None,
                            ),
                        ),
                    ),
                ),
                inventory_root_folder_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
                current_outfit_folder_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
                resolved_items=(
                    InventoryItemEntry(
                        item_id=UUID("12345678-1111-2222-3333-444444444444"),
                        asset_id=UUID("87654321-1111-2222-3333-444444444444"),
                        parent_id=UUID("3e28a3f4-411e-43ff-bcec-44a0b48a4dfb"),
                        name="Default Eyes",
                        description="",
                        type=13,
                        inv_type=18,
                        flags=3,
                    ),
                    InventoryItemEntry(
                        item_id=UUID("12345678-1111-2222-3333-555555555555"),
                        asset_id=UUID("87654321-1111-2222-3333-555555555555"),
                        parent_id=UUID("3e28a3f4-411e-43ff-bcec-44a0b48a4dfb"),
                        name="Default Skin",
                        description="",
                        type=13,
                        inv_type=18,
                        flags=1,
                    ),
                ),
            ),
            wearables_update=AgentWearablesUpdateMessage(
                agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                session_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                serial_num=4,
                wearables=(
                    AgentWearableEntry(
                        item_id=UUID("02385379-afb8-48b3-8848-47c8333fed2d"),
                        asset_id=UUID("1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b"),
                        wearable_type=0,
                    ),
                    AgentWearableEntry(
                        item_id=UUID("a860475e-6234-40b8-b5b1-3df8fb1d3049"),
                        asset_id=UUID("ffc4de4a-9845-41c1-9f9f-762a059d0bdc"),
                        wearable_type=1,
                    ),
                ),
            ),
            cached_texture_response=AgentCachedTextureResponseMessage(
                agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                session_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                serial_num=4,
                textures=(
                    AgentCachedTextureResponseEntry(
                        texture_index=0,
                        texture_id=UUID("11111111-1111-1111-1111-111111111111"),
                        host_name="",
                    ),
                    AgentCachedTextureResponseEntry(
                        texture_index=1,
                        texture_id=UUID(int=0),
                        host_name="",
                    ),
                ),
            ),
            avatar_appearance=AvatarAppearanceMessage(
                sender_id=UUID("11111111-2222-3333-4444-555555555555"),
                is_trial=False,
                texture_entry=b"\x01\x02\x03",
                visual_params=bytes(range(10)),
                appearance_version=1,
                cof_version=2,
                appearance_flags=0,
                hover_height=None,
                attachments=(
                    (UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"), 1),
                ),
            ),
            self_avatar_appearance=AvatarAppearanceMessage(
                sender_id=UUID("11111111-2222-3333-4444-555555555555"),
                is_trial=False,
                texture_entry=b"\x04\x05",
                visual_params=bytes(range(5)),
                appearance_version=1,
                cof_version=2,
                appearance_flags=0,
                hover_height=None,
                attachments=(),
            ),
            events=(SessionEvent(at_seconds=0.0, kind="session.started", detail=""),),
        )

        lines = format_session_report(report)

        self.assertIn("status=completed", lines)
        self.assertIn("caps[seed]=EventQueueGet,SimulatorFeatures,UploadBakedTexture", lines)
        self.assertIn("appearance[bootstrap]=packed:1", lines)
        self.assertIn("appearance[inventory]=folders:2 items:2", lines)
        self.assertTrue(any(line.startswith("appearance[cof]=") and "links:2" in line and "sample:Shape,Skin" in line for line in lines))
        self.assertIn("appearance[cof_resolved]=items:2 types:13 sample:Default Eyes,Default Skin", lines)
        self.assertIn("appearance[wearables]=serial:4 count:2 types:0,1", lines)
        self.assertIn("appearance[cached_textures]=serial:4 count:2 non_zero:1", lines)
        self.assertTrue(any(line.startswith("appearance[avatar]=") and "texture:3" in line and "visual:10" in line and "attachments:1" in line and "version:1" in line and "cof:2" in line and "flags:0" in line for line in lines))
        self.assertIn("appearance[self_avatar]=sender:11111111-2222-3333-4444-555555555555 texture:2 visual:5 attachments:0 version:1 cof:2 flags:0", lines)

    def test_format_session_report_shows_zero_cof_resolution_when_links_exist(self) -> None:
        report = SessionReport(
            elapsed_seconds=1.0,
            total_received=1,
            message_counts={},
            handshake_reply_sent=True,
            movement_completed=True,
            ping_requests_handled=0,
            appended_acks_received=0,
            packet_acks_received=0,
            agent_update_count=0,
            pending_reliable_sequences=(),
            last_region_name="Test",
            close_reason=None,
            world_view=WorldView(),
            resolved_capabilities=("FetchInventory2",),
            bootstrap_packed_appearance_present=False,
            inventory_fetch=InventoryFetchSnapshot(
                folders=(
                    InventoryFolderContents(
                        folder_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
                        owner_id=UUID("11111111-2222-3333-4444-555555555555"),
                        agent_id=UUID("11111111-2222-3333-4444-555555555555"),
                        descendents=1,
                        version=1,
                        categories=(),
                        items=(
                            InventoryItemEntry(
                                item_id=UUID("02385379-afb8-48b3-8848-47c8333fed2d"),
                                asset_id=UUID("1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b"),
                                parent_id=None,
                                name="Shape",
                                description="",
                                type=18,
                                inv_type=24,
                                flags=0,
                            ),
                        ),
                    ),
                ),
                current_outfit_folder_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
            ),
            wearables_update=None,
            cached_texture_response=None,
            avatar_appearance=None,
            self_avatar_appearance=None,
            events=(),
        )

        lines = format_session_report(report)

        self.assertIn("caps[seed]=FetchInventory2", lines)
        self.assertIn("appearance[cof_resolved]=items:0 types:- sample:-", lines)

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
            resolved_capabilities=(),
            bootstrap_packed_appearance_present=False,
            inventory_fetch=None,
            wearables_update=None,
            cached_texture_response=None,
            avatar_appearance=None,
            self_avatar_appearance=None,
            events=(),
        )

        lines = format_session_report(report, verbose=True)

        self.assertIn("packet_acks_received=1", lines)
        self.assertIn("pending_reliable=1", lines)
        self.assertIn("message[PacketAck]=1", lines)
