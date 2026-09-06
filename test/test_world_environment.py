"""Reading a region's own sky and water out of `ExtEnvironment`.

Everything above and below the prims -- the sky gradient, the sun in it, the
colour of the sea -- came out of constants in the shaders, so every region
looked like the same afternoon. `RegionHandshake` cannot fix that: it carries
terrain textures, the elevation bands they cover and the water *height*, and
nothing about colour. The colour is behind a capability.

The document under test is the real one, fetched from the local OpenSim on
2026-09-06 while the agent was standing in the region -- see
`test/fixtures/environment/README.md`. Synthetic documents beside it cover the
shapes a real one happens not to have.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.caps.llsd import parse_xml_value
from vibestorm.world.environment import (
    EnvironmentError,
    RegionEnvironment,
    SkySettings,
    WaterSettings,
    parse_environment_document,
)

FIXTURE = Path("test/fixtures/environment/ext-environment-opensim.xml")


def _live_environment() -> RegionEnvironment:
    return parse_environment_document(parse_xml_value(FIXTURE.read_bytes()))


def _document(tracks, frames, **environment) -> dict:
    body = {"day_cycle": {"frames": frames, "tracks": tracks, "type": "daycycle"}}
    body.update(environment)
    return {"environment": body, "success": True}


class LiveDocumentTests(unittest.TestCase):
    """What the region actually sent."""

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_it_finds_the_region_and_the_length_of_its_day(self) -> None:
        self.assertEqual(self.env.region_id, "607d469c-9949-45cf-97ee-eec289315d92")
        self.assertEqual(self.env.day_length, 14400.0)
        self.assertEqual(self.env.day_offset, 57600.0)

    def test_it_finds_the_altitudes_the_upper_tracks_belong_to(self) -> None:
        self.assertEqual(self.env.track_altitudes, (1000.0, 2000.0, 3000.0))

    def test_the_water_track_holds_one_keyframe_and_the_sky_track_eight(self) -> None:
        self.assertEqual([key for key, _ in self.env.water_track], [0.0])
        self.assertEqual(
            [round(key, 3) for key, _ in self.env.sky_track],
            [0.0, 0.05, 0.125, 0.3, 0.5, 0.7, 0.875, 0.95],
        )

    def test_the_sea_is_the_colour_the_region_says(self) -> None:
        # The thing the whole exercise is for: this is not the shader's blue.
        water = self.env.water_at(0.5)

        self.assertAlmostEqual(water.fog_color[0], 0.015686, places=5)
        self.assertAlmostEqual(water.fog_color[1], 0.149020, places=5)
        self.assertAlmostEqual(water.fog_color[2], 0.250980, places=5)
        self.assertEqual(water.fog_density, 16.0)

    def test_the_sky_colours_come_from_legacy_haze_not_the_frame_root(self) -> None:
        # `blue_horizon` and friends are nested one level down, beside the
        # atmospheric configs that describe the same sky a different way.
        midday = self.env.sky_at(0.5)

        self.assertAlmostEqual(midday.blue_horizon[2], 0.64, places=3)
        self.assertAlmostEqual(midday.haze_density, 0.7, places=3)
        self.assertAlmostEqual(midday.density_multiplier, 0.00018, places=6)

    def test_midnight_is_not_midday(self) -> None:
        # The reason for a day cycle at all. Two keyframes 0.5 apart in a
        # document read correctly cannot agree.
        midnight = self.env.sky_at(0.0)
        midday = self.env.sky_at(0.5)

        self.assertNotEqual(midnight.blue_horizon, midday.blue_horizon)
        self.assertGreater(midnight.haze_density, midday.haze_density)

    def test_a_time_between_keyframes_is_between_their_colours(self) -> None:
        # 0.2125 is exactly halfway between the keyframes at 0.125 and 0.3.
        before = self.env.sky_at(0.125)
        after = self.env.sky_at(0.3)

        middle = self.env.sky_at(0.2125)

        for index in range(3):
            self.assertAlmostEqual(
                middle.blue_horizon[index],
                (before.blue_horizon[index] + after.blue_horizon[index]) / 2.0,
                places=5,
            )

    def test_the_sunlight_colours_fourth_component_is_not_read_as_blue(self) -> None:
        # `sunlight_color` has four numbers where every other colour has three.
        # Taking the last three would give a blue channel of 0.3.
        midday = self.env.sky_at(0.5)

        self.assertAlmostEqual(midday.sunlight_color[2], 0.9, places=3)

    def test_the_day_wraps_at_midnight(self) -> None:
        # The span from the last keyframe to the first runs *through* 1.0 and
        # is 0.05 wide, not the 0.95 the two numbers subtract to. Measured the
        # long way it is negative, and the whole last twentieth of the night
        # sticks on one frame.
        #
        # The sun is what shows it: the two keyframes either side of the wrap
        # are turns about Y through 135 and 90 degrees, so a quarter of the way
        # across is a quarter of the way between those angles.
        last = self.env.sky_at(0.95).sun_rotation
        first = self.env.sky_at(0.0).sun_rotation
        quarter_across = self.env.sky_at(0.9625).sun_rotation

        half_angle = math.radians(135.0 - 0.25 * (135.0 - 90.0)) / 2.0
        self.assertAlmostEqual(quarter_across[1], math.sin(half_angle), places=4)
        self.assertAlmostEqual(quarter_across[3], math.cos(half_angle), places=4)
        self.assertNotAlmostEqual(quarter_across[1], last[1], places=3)
        self.assertNotAlmostEqual(quarter_across[1], first[1], places=3)

    def test_a_fraction_past_one_wraps_rather_than_clamping(self) -> None:
        self.assertEqual(self.env.sky_at(1.25), self.env.sky_at(0.25))

    def test_the_sun_rotation_is_a_unit_quaternion_all_day(self) -> None:
        # A straight average of two quaternions is shorter than either, so an
        # interpolated sun would sink towards zero between keyframes.
        for step in range(0, 20):
            rotation = self.env.sky_at(step / 20.0).sun_rotation
            length = math.sqrt(sum(component * component for component in rotation))
            self.assertAlmostEqual(length, 1.0, places=5, msg=f"at {step / 20.0}")


    def test_the_cloud_fields_come_off_the_frame_root(self) -> None:
        # Not from `legacy_haze`, where the sky colours live. Reading them
        # from there gives the defaults and nothing complains.
        midday = self.env.sky_at(0.5)

        self.assertAlmostEqual(midday.cloud_pos_density1[2], 1.0, places=4)
        self.assertAlmostEqual(midday.cloud_pos_density2[2], 0.125, places=4)
        self.assertAlmostEqual(midday.cloud_scroll_rate[0], 0.2, places=4)
        self.assertAlmostEqual(midday.cloud_variance, 0.0, places=5)

    def test_the_cloud_scroll_rate_is_a_pair_and_stays_one(self) -> None:
        # `cloud_scroll_rate` has two components where nearly every other
        # vector in the document has three, so a reader that assumes three
        # either raises or pads a zero into a real axis.
        rate = self.env.sky_at(0.5).cloud_scroll_rate

        self.assertEqual(len(rate), 2)

    def test_the_sun_and_moon_carry_their_own_size(self) -> None:
        """`sun_scale` and `moon_scale`, off the frame root like the clouds.

        Both are 1.0 through the whole of this cycle, which is exactly why
        they are worth a test: a parser that never looked at them would hand
        back 1.0 as well, and every reading of the drawn sky would agree. What
        this pins is that the fields are *found* -- a stub value put in their
        place has to arrive.
        """
        midday = self.env.sky_at(0.5)

        self.assertEqual(midday.sun_scale, 1.0)
        self.assertEqual(midday.moon_scale, 1.0)

    def test_a_region_that_asks_for_a_bigger_sun_gets_one(self) -> None:
        document = parse_xml_value(FIXTURE.read_bytes())
        for frame in document["environment"]["day_cycle"]["frames"].values():
            if frame.get("type") != "water":
                frame["sun_scale"] = 2.5
                frame["moon_scale"] = 0.4

        sky = parse_environment_document(document).sky_at(0.5)

        self.assertAlmostEqual(sky.sun_scale, 2.5, places=5)
        self.assertAlmostEqual(sky.moon_scale, 0.4, places=5)

    def test_the_cloud_densities_interpolate_between_keyframes(self) -> None:
        # The coarse density is one of the few cloud numbers that actually
        # moves across this cycle -- 0.88 at night, 1.0 by mid-morning -- so
        # it is the one that says the blend reaches these fields at all.
        before = self.env.sky_at(0.125).cloud_pos_density1[2]
        after = self.env.sky_at(0.3).cloud_pos_density1[2]
        middle = self.env.sky_at((0.125 + 0.3) / 2.0).cloud_pos_density1[2]

        self.assertNotAlmostEqual(before, after, places=3)
        self.assertAlmostEqual(middle, (before + after) / 2.0, places=5)

class DayCycleTextureTests(unittest.TestCase):
    """A day cycle names textures, and they are real assets.

    `moon_id`, `cloud_id` and the water's `normal_map` are ordinary texture
    ids behind the ordinary `GetTexture` capability -- fetched live from this
    OpenSim's own asset service (2026-09-06), so a client that ignores them is
    inventing a moon, a cloud layer and a wave shape it could have downloaded.
    """

    def test_the_live_document_names_three_textures(self) -> None:
        found = _live_environment().texture_assets()

        self.assertEqual(
            [str(asset) for asset in found],
            [
                "822ded49-9a6c-f61c-cb89-6df54f42cdf4",  # water normal_map
                "d07f6eed-b96a-47cd-b51d-400ad4a1c428",  # moon_id
                "1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b",  # cloud_id
            ],
        )

    def test_the_null_sun_id_is_not_an_asset(self) -> None:
        """Which the live document is the evidence for.

        `sun_id` is present in all eight of its sky keyframes and is the null
        UUID in every one. That is the document saying *no texture*, not a
        field it forgot; a client that queues it fetches nothing forever.
        """
        found = _live_environment().texture_assets()

        self.assertNotIn(UUID(int=0), found)

    def test_one_texture_used_all_day_is_listed_once(self) -> None:
        # The live cycle names the same moon in all eight keyframes.
        found = _live_environment().texture_assets()

        self.assertEqual(len(found), len(set(found)))

    def test_a_cycle_that_changes_its_moon_lists_both(self) -> None:
        # Nothing says a region keeps one moon all day, and the default cycle
        # doing so is not evidence that another cannot.
        first, second = UUID(int=0x11), UUID(int=0x22)
        environment = RegionEnvironment(
            sky_track=(
                (0.0, SkySettings(moon_id=str(first))),
                (0.5, SkySettings(moon_id=str(second))),
            ),
        )

        self.assertEqual(environment.texture_assets(), (first, second))

    def test_an_unreadable_id_is_skipped_rather_than_raising(self) -> None:
        environment = RegionEnvironment(
            sky_track=((0.0, SkySettings(moon_id="not-a-uuid")),),
        )

        self.assertEqual(environment.texture_assets(), ())

    def test_a_cycle_that_names_nothing_asks_for_nothing(self) -> None:
        self.assertEqual(RegionEnvironment().texture_assets(), ())


class DayFractionTests(unittest.TestCase):
    def test_the_offset_moves_where_the_day_starts(self) -> None:
        env = RegionEnvironment(day_length=100.0, day_offset=25.0)

        self.assertAlmostEqual(env.day_fraction_for(0.0), 0.25)
        self.assertAlmostEqual(env.day_fraction_for(50.0), 0.75)

    def test_it_wraps_rather_than_growing(self) -> None:
        env = RegionEnvironment(day_length=100.0, day_offset=0.0)

        self.assertAlmostEqual(env.day_fraction_for(1250.0), 0.5)

    def test_a_region_with_no_day_length_does_not_divide_by_zero(self) -> None:
        self.assertEqual(RegionEnvironment(day_length=0.0).day_fraction_for(9.0), 0.0)


class MalformedDocumentTests(unittest.TestCase):
    def test_a_document_that_is_not_a_map_is_refused(self) -> None:
        with self.assertRaises(EnvironmentError):
            parse_environment_document([1, 2, 3])

    def test_a_reply_with_no_environment_is_refused(self) -> None:
        with self.assertRaises(EnvironmentError):
            parse_environment_document({"success": False})

    def test_a_reply_with_no_day_cycle_is_refused(self) -> None:
        with self.assertRaises(EnvironmentError):
            parse_environment_document({"environment": {"region_id": "x"}})

    def test_missing_fields_take_defaults_rather_than_raising(self) -> None:
        # A region that leaves the sun colour out is not a broken region, and
        # refusing to draw a sky over it is worse than drawing the default.
        document = _document(
            [[], [{"key_keyframe": 0.0, "key_name": "bare"}]],
            {"bare": {"type": "sky"}},
        )

        sky = parse_environment_document(document).sky_at(0.0)

        self.assertEqual(sky, SkySettings())

    def test_a_track_that_is_not_a_list_is_empty_rather_than_fatal(self) -> None:
        document = _document(["nonsense", "nonsense"], {})

        env = parse_environment_document(document)

        self.assertEqual(env.sky_track, ())
        self.assertEqual(env.water_track, ())

    def test_a_keyframe_naming_a_frame_that_is_not_there_is_skipped(self) -> None:
        document = _document(
            [
                [],
                [
                    {"key_keyframe": 0.0, "key_name": "missing"},
                    {"key_keyframe": 0.5, "key_name": "real"},
                ],
            ],
            {"real": {"type": "sky", "cloud_shadow": 0.75}},
        )

        env = parse_environment_document(document)

        self.assertEqual(len(env.sky_track), 1)
        self.assertEqual(env.sky_at(0.9).cloud_shadow, 0.75)

    def test_a_water_frame_listed_in_the_sky_track_is_left_out(self) -> None:
        # The track numbering is positional and undocumented, so this is the
        # one place the document can contradict itself. Reading a water frame
        # as a sky produces a plausible, entirely wrong sky.
        document = _document(
            [[], [{"key_keyframe": 0.0, "key_name": "wet"}]],
            {"wet": {"type": "water", "water_fog_density": 3.0}},
        )

        self.assertEqual(parse_environment_document(document).sky_track, ())

    def test_keyframes_out_of_order_are_sorted(self) -> None:
        document = _document(
            [
                [],
                [
                    {"key_keyframe": 0.8, "key_name": "late"},
                    {"key_keyframe": 0.2, "key_name": "early"},
                ],
            ],
            {
                "late": {"type": "sky", "cloud_shadow": 1.0},
                "early": {"type": "sky", "cloud_shadow": 0.0},
            },
        )

        env = parse_environment_document(document)

        self.assertEqual([round(key, 3) for key, _ in env.sky_track], [0.2, 0.8])
        self.assertAlmostEqual(env.sky_at(0.5).cloud_shadow, 0.5)


class TrackSamplingTests(unittest.TestCase):
    def test_an_empty_track_gives_the_default(self) -> None:
        env = RegionEnvironment()

        self.assertEqual(env.sky_at(0.3), SkySettings())
        self.assertEqual(env.water_at(0.3), WaterSettings())

    def test_a_single_keyframe_holds_all_day(self) -> None:
        only = WaterSettings(fog_density=2.0)
        env = RegionEnvironment(water_track=((0.6, only),))

        self.assertIs(env.water_at(0.0), only)
        self.assertIs(env.water_at(0.99), only)

    def test_the_span_across_midnight_is_the_short_way_round(self) -> None:
        # The gap from the last keyframe to the first runs *through* 1.0. Here
        # that is 0.2 wide, not the 0.8 the two numbers subtract to. Measured
        # the long way, three quarters of the night plays the whole day
        # backwards and the last quarter is stuck on one frame.
        env = RegionEnvironment(
            sky_track=(
                (0.0, SkySettings(cloud_shadow=0.0)),
                (0.8, SkySettings(cloud_shadow=1.0)),
            )
        )

        self.assertAlmostEqual(env.sky_at(0.85).cloud_shadow, 0.75)
        self.assertAlmostEqual(env.sky_at(0.9).cloud_shadow, 0.5)
        self.assertAlmostEqual(env.sky_at(0.95).cloud_shadow, 0.25)

    def test_a_time_before_the_first_keyframe_is_in_the_wrap_too(self) -> None:
        env = RegionEnvironment(
            sky_track=(
                (0.2, SkySettings(cloud_shadow=0.0)),
                (0.4, SkySettings(cloud_shadow=1.0)),
            )
        )

        # 0.1 is halfway from the 0.4 keyframe round to the 0.2 one, a span of
        # 0.8 that starts at 0.4 and ends at 1.2.
        self.assertAlmostEqual(env.sky_at(0.1).cloud_shadow, 1.0 - 0.875)

    def test_a_texture_id_switches_rather_than_blending(self) -> None:
        # Half of one UUID and half of another names nothing.
        env = RegionEnvironment(
            sky_track=(
                (0.0, SkySettings(cloud_id="a")),
                (0.5, SkySettings(cloud_id="b")),
            )
        )

        self.assertEqual(env.sky_at(0.1).cloud_id, "a")
        self.assertEqual(env.sky_at(0.4).cloud_id, "b")


if __name__ == "__main__":
    unittest.main()
