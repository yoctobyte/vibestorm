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
from vibestorm.world.models import (
    COARSE_HEIGHT_STEP_M,
    CoarseAgentLocation,
    WorldView,
    self_avatar_position,
)


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
        self.assertEqual(world.latest_time.sun_direction, (1.0, 0.0, 0.0))
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
                        texture_entry=None,
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
        self.assertIsNone(world.objects[object_id].texture_entry)


def _coarse(x: int, y: int, z: int, *, is_you: bool = True, agent_id=None):
    return CoarseAgentLocation(
        agent_id=agent_id, x=x, y=y, z=z, is_you=is_you, is_prey=False
    )


class _Stub:
    """A world view with only the fields this reads."""

    def __init__(self, **fields) -> None:
        self.coarse_agents = ()
        self.objects = {}
        self.terse_objects = {}
        self.__dict__.update(fields)


class _At:
    def __init__(self, position, *, is_avatar: bool = True, parent_id: int = 0) -> None:
        self.position = position
        self.is_avatar = is_avatar
        self.parent_id = parent_id


class CoarseHeightTests(unittest.TestCase):
    """One byte for 1024 metres, and what that costs.

    `CoarseLocationUpdate` spends a byte per axis. X and Y are whole metres on
    a 256 m region; Z is four of them, because 256 values have to cover the
    height a region is allowed. OpenSim writes the byte as `Z * 0.25f` --
    pinned in `test_opensim_source_pins.py`.
    """

    def test_the_height_byte_is_four_metres(self) -> None:
        self.assertEqual(_coarse(128, 128, 6).position_m, (128.0, 128.0, 24.0))

    def test_x_and_y_are_plain_metres(self) -> None:
        self.assertEqual(_coarse(200, 37, 0).position_m[:2], (200.0, 37.0))

    def test_the_step_is_the_constant_the_docstrings_quote(self) -> None:
        self.assertEqual(COARSE_HEIGHT_STEP_M, 4.0)

    def test_a_zero_height_is_not_a_height(self) -> None:
        # `Z > 1024 ? (byte)0 : ...` -- so zero means "on the ground" or "in
        # orbit", and the message does not say which.
        self.assertFalse(_coarse(128, 128, 0).height_is_certain)
        self.assertTrue(_coarse(128, 128, 1).height_is_certain)


class SelfAvatarPositionTests(unittest.TestCase):
    """Where the viewer thinks it is, and which source it believed.

    The bug this fixes: the height byte was read as metres, so a viewer
    standing at 25.9 m displayed 6.0 -- and told the owner it was under a sea
    whose surface is at 20 m while drawing itself on a hilltop in the sun.
    """

    def setUp(self) -> None:
        self.me = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_our_own_object_update_beats_the_coarse_blip(self) -> None:
        view = _Stub(
            coarse_agents=(_coarse(128, 128, 6, agent_id=self.me),),
            objects={self.me: _At((128.0, 128.0, 25.944))},
        )
        self.assertEqual(self_avatar_position(view), (128.0, 128.0, 25.944))

    def test_the_coarse_blip_serves_until_that_arrives(self) -> None:
        view = _Stub(coarse_agents=(_coarse(128, 128, 6, agent_id=self.me),))
        position = self_avatar_position(view)
        assert position is not None
        self.assertEqual(position, (128.0, 128.0, 24.0))
        self.assertNotEqual(position[2], 6.0)

    def test_a_coarse_entry_with_no_agent_id_still_places_us(self) -> None:
        # The ids are a separate block and can be shorter than the locations.
        view = _Stub(coarse_agents=(_coarse(64, 64, 3),))
        self.assertEqual(self_avatar_position(view), (64.0, 64.0, 12.0))

    def test_an_avatar_is_assumed_to_be_us_only_before_any_coarse_update(self) -> None:
        view = _Stub(terse_objects={7: _At((10.0, 11.0, 12.0))})
        self.assertEqual(self_avatar_position(view), (10.0, 11.0, 12.0))

    def test_the_last_resort_is_still_an_avatar_and_not_a_crate(self) -> None:
        # The terse dictionary is mostly prims, and they come in whatever
        # order the region sent them. Guessing that the first entry is us puts
        # the viewer inside a packing crate.
        view = _Stub(
            terse_objects={
                3: _At((70.0, 70.0, 22.0), is_avatar=False),
                7: _At((10.0, 11.0, 12.0)),
            }
        )
        self.assertEqual(self_avatar_position(view), (10.0, 11.0, 12.0))

    def test_a_crowd_that_does_not_include_us_is_not_guessed_at(self) -> None:
        # Coarse entries exist but none is ours, so the terse fallback would
        # be somebody else. It is still the last resort and still reached --
        # what must not happen is picking one of *their* coarse entries.
        other = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        view = _Stub(
            coarse_agents=(_coarse(200, 200, 50, is_you=False, agent_id=other),),
            objects={other: _At((200.0, 200.0, 200.0))},
            terse_objects={7: _At((10.0, 11.0, 12.0))},
        )
        self.assertEqual(self_avatar_position(view), (10.0, 11.0, 12.0))

    def test_sitting_down_does_not_move_us_to_the_seat_s_frame(self) -> None:
        """A seated avatar is a child of its seat and says so in the seat's frame.

        `tools/verify_seated_avatar.py` measured it: a region position near
        (128, 128, 25) becomes half a metre the moment the avatar sits. That
        half-metre is not a place in the region, and the coarse entry -- which
        is still a region position while seated -- takes over rather than
        composing anything here.
        """
        view = _Stub(
            coarse_agents=(_coarse(130, 64, 7, agent_id=self.me),),
            objects={self.me: _At((0.4, 0.0, 0.55), parent_id=222273993)},
        )
        self.assertEqual(self_avatar_position(view), (130.0, 64.0, 28.0))

    def test_standing_up_hands_the_precise_source_back(self) -> None:
        view = _Stub(
            coarse_agents=(_coarse(130, 64, 7, agent_id=self.me),),
            objects={self.me: _At((130.2, 64.9, 28.31))},
        )
        self.assertEqual(self_avatar_position(view), (130.2, 64.9, 28.31))

    def test_an_empty_view_says_it_does_not_know(self) -> None:
        self.assertIsNone(self_avatar_position(_Stub()))
