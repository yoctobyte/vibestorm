import unittest
from uuid import UUID

from vibestorm.udp.messages import (
    CoarseLocation,
    CoarseLocationUpdateMessage,
    ObjectUpdateEntry,
    ObjectUpdateMessage,
    SimStatEntry,
    SimStatsMessage,
    SimulatorViewerTimeMessage,
)
from vibestorm.world.models import WorldView


class WorldViewTests(unittest.TestCase):
    def test_world_view_applies_sim_stats(self) -> None:
        world = WorldView()
        world.apply_sim_stats(
            SimStatsMessage(
                region_x=1000,
                region_y=1001,
                region_flags=9,
                object_capacity=15000,
                stats=(SimStatEntry(stat_id=1, stat_value=10.0),),
                pid=0,
                region_flags_extended=(),
            ),
        )
        assert world.latest_sim_stats is not None
        self.assertEqual(world.latest_sim_stats.object_capacity, 15000)
        self.assertEqual(world.sim_stats_updates, 1)

    def test_world_view_applies_time_and_coarse_location(self) -> None:
        world = WorldView()
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        world.apply_simulator_time(
            SimulatorViewerTimeMessage(
                usec_since_start=123,
                sec_per_day=14400,
                sec_per_year=31536000,
                sun_direction=(1.0, 0.0, 0.0),
                sun_phase=5.5,
                sun_angular_velocity=(0.0, 1.0, 0.0),
            ),
        )
        world.apply_coarse_location_update(
            CoarseLocationUpdateMessage(
                locations=(CoarseLocation(x=128, y=129, z=8),),
                you_index=0,
                prey_index=-1,
                agent_ids=(agent_id,),
            ),
        )
        assert world.latest_time is not None
        self.assertEqual(world.latest_time.sun_phase, 5.5)
        self.assertEqual(len(world.coarse_agents), 1)
        self.assertTrue(world.coarse_agents[0].is_you)
        self.assertIn(agent_id, world.agent_presences)

    def test_world_view_applies_object_update(self) -> None:
        world = WorldView()
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        world.apply_object_update(
            ObjectUpdateMessage(
                region_handle=123456789,
                time_dilation=42,
                objects=(
                    ObjectUpdateEntry(
                        local_id=7,
                        state=3,
                        full_id=object_id,
                        crc=99,
                        pcode=9,
                        material=3,
                        click_action=1,
                        scale=(1.0, 2.0, 3.0),
                        object_data_size=60,
                        parent_id=0,
                        update_flags=5,
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
                        interesting_payloads=(),
                    ),
                ),
            ),
        )
        assert world.latest_object_update is not None
        self.assertEqual(world.latest_object_update.object_count, 1)
        self.assertEqual(world.object_update_events, 1)
        self.assertIn(object_id, world.objects)
        assert world.objects[object_id].position is not None
        self.assertEqual(world.objects[object_id].position, (128.0, 129.0, 25.0))
        self.assertEqual(
            world.objects[object_id].default_texture_id,
            UUID("00895567-4724-cb43-ed92-0b47caed1546"),
        )
