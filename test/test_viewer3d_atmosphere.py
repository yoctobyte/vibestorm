"""Turning a region's day cycle into a sky, a sun and a sea.

The sun matters most here, and not for the reason it looks like. The simulator
does not send one: `SimulatorViewerTimeMessage.SunDirection` arrives from
OpenSim as `(0, 0, 0)` every time, which is not a missing answer -- it is a
well-formed direction of length nothing, and the renderer's normalise stepped
over it into a fixed fallback. So the sun had never moved in any session.

The day cycle does carry one, once per sky keyframe, as a rotation. Which
vector it rotates is not written down anywhere, so it was found by trying each
axis against all eight keyframes of the default cycle and seeing which produces
an arc: down at midnight, level at dawn, up at noon. That is the test below,
and it is the evidence for `SUN_REFERENCE_DIRECTION`.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from vibestorm.caps.llsd import parse_xml_value
from vibestorm.viewer3d.atmosphere import (
    CLOUD_SCALE_METRES,
    DEFAULT_SKY_HORIZON_COLOR,
    MOON_REFERENCE_DIRECTION,
    NIGHT_LIGHT_FLOOR,
    STAR_BRIGHTNESS_FULL,
    SUN_REFERENCE_DIRECTION,
    WATER_WAVE_LENGTH_SPREAD,
    cloud_cover,
    cloud_hue,
    cloud_shadow_scale,
    cloud_size,
    daylight_scale,
    light_hues,
    moon_direction,
    moon_disc,
    moon_level,
    sky_gradient,
    star_level,
    sun_direction,
    sun_disc,
    underwater_reach,
    water_fog,
    water_fresnel,
    water_tint,
    water_wave_number,
    water_wave_slope,
    water_wave_slope_below,
    water_wave_speed,
    water_waves,
)
from vibestorm.world.environment import (
    RegionEnvironment,
    SkySettings,
    WaterSettings,
    parse_environment_document,
)

FIXTURE = Path("test/fixtures/environment/ext-environment-opensim.xml")


def _live_environment() -> RegionEnvironment:
    return parse_environment_document(parse_xml_value(FIXTURE.read_bytes()))


def _elevation_degrees(direction) -> float:
    return math.degrees(math.asin(max(-1.0, min(1.0, direction[2]))))


class SunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_reference_direction_is_east_and_level(self) -> None:
        self.assertEqual(SUN_REFERENCE_DIRECTION, (1.0, 0.0, 0.0))

    def test_the_default_cycle_traces_a_day(self) -> None:
        # Every keyframe at once, because one of them agreeing is an accident.
        # Straight down at midnight, level either side of it, straight up at
        # noon -- the shape that says the reference vector is the right one.
        elevations = [
            round(_elevation_degrees(sun_direction(sky)), 1)
            for _, sky in self.env.sky_track
        ]

        self.assertEqual(
            elevations, [-90.0, -45.0, 5.4, 45.0, 90.0, 45.0, 4.3, -45.0]
        )

    def test_the_sun_rises_in_the_east_and_sets_in_the_west(self) -> None:
        # The keyframes at 0.125 and 0.875 are both within six degrees of the
        # horizon, and they are on opposite sides of the sky.
        dawn = sun_direction(self.env.sky_at(0.125))
        dusk = sun_direction(self.env.sky_at(0.875))

        self.assertGreater(dawn[0], 0.9)
        self.assertLess(dusk[0], -0.9)

    def test_it_stays_a_unit_vector_all_day(self) -> None:
        for step in range(20):
            direction = sun_direction(self.env.sky_at(step / 20.0))
            length = math.sqrt(sum(c * c for c in direction))
            self.assertAlmostEqual(length, 1.0, places=5, msg=f"at {step / 20.0}")

    def test_it_climbs_through_the_morning_and_falls_through_the_evening(self) -> None:
        morning = [_elevation_degrees(sun_direction(self.env.sky_at(f / 100.0)))
                   for f in range(15, 50, 5)]
        evening = [_elevation_degrees(sun_direction(self.env.sky_at(f / 100.0)))
                   for f in range(75, 95, 5)]

        self.assertEqual(morning, sorted(morning))
        self.assertEqual(evening, sorted(evening, reverse=True))


class SkyGradientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _live_environment()

    def test_night_is_darker_than_day(self) -> None:
        # The single thing a fixed sky colour cannot do. `ambient` is what
        # carries it: 1.05 at noon against 0.26 at midnight.
        night_horizon, night_zenith = sky_gradient(self.env.sky_at(0.0))
        day_horizon, day_zenith = sky_gradient(self.env.sky_at(0.5))

        self.assertLess(sum(night_horizon), sum(day_horizon) / 2.0)
        self.assertLess(sum(night_zenith), sum(day_zenith) / 2.0)

    def test_the_midday_sky_is_blue_at_the_zenith(self) -> None:
        _, zenith = sky_gradient(self.env.sky_at(0.5))

        self.assertGreater(zenith[2], zenith[1])
        self.assertGreater(zenith[1], zenith[0])

    def test_the_horizon_is_paler_than_the_zenith_by_day(self) -> None:
        # Which is what haze does, and the reason the two are separate colours.
        horizon, zenith = sky_gradient(self.env.sky_at(0.5))

        self.assertGreater(sum(horizon), sum(zenith))

    def test_haze_pulls_the_horizon_toward_the_colour_of_the_light(self) -> None:
        lit = SkySettings(
            blue_horizon=(0.0, 0.0, 1.0),
            sunlight_color=(1.0, 0.0, 0.0),
            haze_horizon=1.0,
            ambient=(1.0, 1.0, 1.0),
        )

        horizon, _ = sky_gradient(lit)

        self.assertEqual(horizon, (1.0, 0.0, 0.0))

    def test_no_haze_leaves_the_horizon_the_colour_the_region_wrote(self) -> None:
        clear = SkySettings(
            blue_horizon=(0.1, 0.2, 0.3),
            sunlight_color=(1.0, 1.0, 1.0),
            haze_horizon=0.0,
            ambient=(1.0, 1.0, 1.0),
        )

        horizon, _ = sky_gradient(clear)

        for index, expected in enumerate((0.1, 0.2, 0.3)):
            self.assertAlmostEqual(horizon[index], expected, places=6)

    def test_the_lights_brightness_is_not_counted_twice(self) -> None:
        # `ambient` says how bright; `sunlight_color` says what colour. A dim
        # white light and a bright white light haze the horizon identically.
        dim = SkySettings(sunlight_color=(0.2, 0.2, 0.2), haze_horizon=1.0)
        bright = SkySettings(sunlight_color=(1.0, 1.0, 1.0), haze_horizon=1.0)

        self.assertEqual(sky_gradient(dim)[0], sky_gradient(bright)[0])

    def test_a_black_sun_does_not_divide_by_zero(self) -> None:
        sky = SkySettings(sunlight_color=(0.0, 0.0, 0.0), haze_horizon=1.0)

        horizon, _ = sky_gradient(sky)

        self.assertEqual(horizon, (1.0, 1.0, 1.0))

    def test_colours_stay_inside_the_range_a_shader_can_use(self) -> None:
        # `ambient` reaches 1.05 in the default cycle, and blue_horizon can be
        # over 1 in a hand-edited one.
        loud = SkySettings(
            blue_horizon=(4.0, 4.0, 4.0),
            blue_density=(9.0, 9.0, 9.0),
            ambient=(3.0, 3.0, 3.0),
        )

        horizon, zenith = sky_gradient(loud)

        self.assertEqual(horizon, (1.0, 1.0, 1.0))
        self.assertEqual(zenith, (1.0, 1.0, 1.0))


class DaylightTests(unittest.TestCase):
    """How brightly to light everything that is not sky."""

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_midnight_is_dimmer_than_midday(self) -> None:
        # Without this the sky went black and the ground under it stayed in
        # full sun, which is a stranger sight than either alone.
        self.assertLess(
            daylight_scale(self.env.sky_at(0.0)), daylight_scale(self.env.sky_at(0.5)) / 2
        )

    def test_a_fully_lit_sky_lights_the_world_fully(self) -> None:
        self.assertEqual(daylight_scale(SkySettings(ambient=(1.0, 1.0, 1.0))), 1.0)

    def test_a_region_with_no_ambient_at_all_still_lights_the_world(self) -> None:
        # A region is allowed to publish nothing here, and a viewer that goes
        # absolutely black at midnight cannot be used at midnight.
        self.assertEqual(daylight_scale(SkySettings(ambient=(0.0, 0.0, 0.0))), NIGHT_LIGHT_FLOOR)
        self.assertGreater(NIGHT_LIGHT_FLOOR, 0.0)

    def test_it_never_leaves_the_range_a_multiplier_can_use(self) -> None:
        for ambient in ((-3.0, -3.0, -3.0), (0.0, 0.0, 0.0), (9.0, 9.0, 9.0)):
            scale = daylight_scale(SkySettings(ambient=ambient))
            self.assertGreaterEqual(scale, 0.0)
            self.assertLessEqual(scale, 1.0)


class LightHueTests(unittest.TestCase):
    """Which colour lights what."""

    def test_the_sky_light_comes_first_and_the_sun_light_second(self) -> None:
        # Two terms, two parameters, and they are not interchangeable: the
        # ambient one lights a face turned away from the sun and the sunlight
        # one does not reach it at all. Swapping them is invisible on a lit
        # face and wrong everywhere else.
        sky = SkySettings(ambient=(1.0, 0.0, 0.0), sunlight_color=(0.0, 0.0, 1.0))

        ambient, diffuse = light_hues(sky)

        self.assertEqual(ambient, (1.0, 0.0, 0.0))
        self.assertEqual(diffuse, (0.0, 0.0, 1.0))

    def test_dawn_is_warm_and_midnight_is_cold(self) -> None:
        env = _live_environment()

        dawn, _ = light_hues(env.sky_at(0.125))
        midnight, _ = light_hues(env.sky_at(0.0))

        self.assertGreater(dawn[0], dawn[2])
        self.assertGreater(midnight[2], midnight[0])

    def test_brightness_is_divided_out_of_both(self) -> None:
        # `sunlight_color` reaches 2.8 at the sunset keyframe, so its own
        # magnitude cannot serve as a level -- that is `daylight_scale`'s job,
        # and using both would count the day twice.
        sky = SkySettings(ambient=(3.0, 3.0, 3.0), sunlight_color=(0.02, 0.02, 0.02))

        ambient, diffuse = light_hues(sky)

        self.assertEqual(ambient, (1.0, 1.0, 1.0))
        self.assertEqual(diffuse, (1.0, 1.0, 1.0))


class WaterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_sea_is_mostly_its_own_fog_colour(self) -> None:
        water = self.env.water_at(0.5)

        tint = water_tint(water, DEFAULT_SKY_HORIZON_COLOR)

        for index in range(3):
            self.assertLess(
                abs(tint[index] - water.fog_color[index]),
                abs(tint[index] - DEFAULT_SKY_HORIZON_COLOR[index]),
            )

    def test_a_dark_sky_darkens_the_sea(self) -> None:
        # The sea reflects. Without this the water stayed a bright daylight
        # blue at midnight while everything above it went black.
        water = self.env.water_at(0.5)
        night_horizon, _ = sky_gradient(self.env.sky_at(0.0))
        day_horizon, _ = sky_gradient(self.env.sky_at(0.5))

        self.assertLess(
            sum(water_tint(water, night_horizon)), sum(water_tint(water, day_horizon))
        )

    def test_it_stays_inside_the_range_a_shader_can_use(self) -> None:
        tint = water_tint(WaterSettings(fog_color=(5.0, -1.0, 0.5)), (1.0, 1.0, 1.0))

        for component in tint:
            self.assertGreaterEqual(component, 0.0)
            self.assertLessEqual(component, 1.0)


class WaterSurfaceTests(unittest.TestCase):
    """The four water fields that had been parsed and never used.

    `water_tint` above is the sea a renderer draws when it cannot measure an
    angle: fog with one fixed share of sky in it, for both ends of a range
    that runs from nearly none to nearly all. These are what a renderer that
    *can* measure the angle needs instead.
    """

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_fog_colour_is_the_sea_without_any_sky_in_it(self) -> None:
        water = self.env.water_at(0.5)

        self.assertEqual(water_fog(water), water.fog_color)
        # And it is not `water_tint`, which is the same colour with the sky
        # already mixed in. Confusing the two draws the sky into the sea twice.
        self.assertNotEqual(
            water_fog(water), water_tint(water, DEFAULT_SKY_HORIZON_COLOR)
        )

    def test_the_fog_colour_stays_inside_the_range_a_shader_can_use(self) -> None:
        for component in water_fog(WaterSettings(fog_color=(5.0, -1.0, 0.5))):
            self.assertGreaterEqual(component, 0.0)
            self.assertLessEqual(component, 1.0)

    def test_the_fresnel_pair_is_the_documents_own(self) -> None:
        water = self.env.water_at(0.5)

        offset, scale = water_fresnel(water)

        self.assertAlmostEqual(offset, water.fresnel_offset, places=6)
        self.assertAlmostEqual(scale, water.fresnel_scale, places=6)

    def test_a_document_cannot_ask_for_more_reflection_than_there_is(self) -> None:
        offset, scale = water_fresnel(
            WaterSettings(fresnel_offset=4.0, fresnel_scale=-2.0)
        )

        self.assertEqual((offset, scale), (1.0, 0.0))

    def test_the_wave_directions_come_back_as_unit_vectors(self) -> None:
        """Because a direction and a speed are two things, not one.

        The document packs both into one vector: which way the wave runs is
        the direction and how fast it runs is the length. A shader handed the
        raw vector would tie the two together -- a faster wave would also be a
        shorter one -- so they are separated here.
        """
        water = self.env.water_at(0.5)

        first_x, first_y, second_x, second_y = water_waves(water)

        self.assertAlmostEqual(math.hypot(first_x, first_y), 1.0, places=6)
        self.assertAlmostEqual(math.hypot(second_x, second_y), 1.0, places=6)
        # Still pointing where the document pointed them.
        self.assertAlmostEqual(
            first_x * water.wave1_direction[1],
            first_y * water.wave1_direction[0],
            places=6,
        )
        self.assertGreater(first_x * water.wave1_direction[0], 0.0)

    def test_the_two_waves_do_not_run_the_same_way(self) -> None:
        # They are two waves in the document because a sea made of one is a
        # corrugated roof. If the parse or the reading collapsed them the sea
        # would look like one.
        first_x, first_y, second_x, second_y = water_waves(self.env.water_at(0.5))

        self.assertLess(first_x * second_x + first_y * second_y, 0.99)

    def test_a_wave_that_stands_still_still_has_a_direction(self) -> None:
        # A zero vector has no direction to normalise, and a NaN one would
        # take the whole surface with it. Falling back leaves a sea with one
        # wave on it, which is better than a sea with none.
        waves = water_waves(WaterSettings(wave1_direction=(0.0, 0.0)))

        self.assertEqual(waves[:2], (1.0, 0.0))
        for component in waves:
            self.assertEqual(component, component)

    def test_a_longer_direction_vector_is_a_faster_wave(self) -> None:
        """Faster across the sea, which is not the same as more often.

        A phase rate alone cannot say this any more: a wave that is four times
        as fast is also four times as long, and takes just as long to cross
        its own length. What the direction's length sets is how fast the
        crests travel, which is the phase rate over the wave number.
        """
        slow = WaterSettings(wave1_direction=(0.5, 0.0))
        fast = WaterSettings(wave1_direction=(2.0, 0.0))

        crawl = water_wave_speed(slow)[0] / water_wave_number(slow)[0]
        race = water_wave_speed(fast)[0] / water_wave_number(fast)[0]

        self.assertAlmostEqual(race, crawl * 4.0, places=5)

    def test_a_wave_that_stands_still_does_not_move(self) -> None:
        speed = water_wave_speed(WaterSettings(wave1_direction=(0.0, 0.0)))

        self.assertEqual(speed[0], 0.0)

    def test_normal_scale_decides_how_fine_the_ripples_are(self) -> None:
        """Larger scale, shorter waves.

        `normal_scale` is how many times the normal map repeats; with no map
        to repeat it is read as how fine the ripples are, and the direction of
        that reading is the part worth pinning down.
        """
        coarse = water_wave_number(WaterSettings(normal_scale=(1.0, 1.0, 1.0)))
        fine = water_wave_number(WaterSettings(normal_scale=(4.0, 4.0, 4.0)))

        # Both waves, because `normal_scale` sets the scale of the sea and not
        # of one wave in it.
        for wave in (0, 1):
            self.assertGreater(fine[wave], coarse[wave])
            self.assertAlmostEqual(fine[wave], coarse[wave] * 4.0, places=5)

    def test_a_zero_normal_scale_does_not_divide_by_zero(self) -> None:
        numbers = water_wave_number(WaterSettings(normal_scale=(0.0, 0.0, 0.0)))

        for number in numbers:
            self.assertGreater(number, 0.0)
            self.assertLess(number, float("inf"))

    def test_the_two_waves_are_not_the_same_length(self) -> None:
        """Which is the whole reason the sea does not read as woven mesh.

        Two sines of one length crossing at an angle are a perfect diamond
        lattice. The document's two directions are 1.13 and 1.61 long and that
        length is a speed, so deep-water dispersion says the second wave is
        about twice the first -- and nothing about the pair repeats.
        """
        first, second = water_wave_number(self.env.water_at(0.5))

        self.assertGreater(first / second, 1.5)

    def test_the_slower_wave_is_the_shorter_one(self) -> None:
        # Dispersion's direction, and the one thing about it a captured
        # document cannot contradict: it is a relation, not a value.
        water = WaterSettings(wave1_direction=(0.5, 0.0), wave2_direction=(0.0, 2.0))

        first, second = water_wave_number(water)

        self.assertGreater(first, second)

    def test_two_equal_speeds_are_two_equal_lengths(self) -> None:
        water = WaterSettings(wave1_direction=(0.0, 1.5), wave2_direction=(1.5, 0.0))

        first, second = water_wave_number(water)

        self.assertAlmostEqual(first, second, places=6)

    def test_a_nearly_still_wave_cannot_stretch_the_other_across_the_region(
        self,
    ) -> None:
        """The clamp, and why there is one.

        Nothing bounds the ratio: a document whose second wave barely moves
        asks for a first one hundreds of metres long, which is not a wave any
        more but a tilt in the whole sea.
        """
        stalled = WaterSettings(wave1_direction=(3.0, 0.0), wave2_direction=(0.001, 0.0))
        # And the other way round, which is a different branch of the same
        # clamp and would otherwise be a wave hundreds of metres long in the
        # other direction.
        reversed_ = WaterSettings(wave1_direction=(0.001, 0.0), wave2_direction=(3.0, 0.0))

        first, second = water_wave_number(stalled)
        back, forth = water_wave_number(reversed_)

        self.assertAlmostEqual(second / first, WATER_WAVE_LENGTH_SPREAD**2, places=5)
        self.assertAlmostEqual(back / forth, WATER_WAVE_LENGTH_SPREAD**2, places=5)

    def test_scale_above_is_how_far_the_surface_leans(self) -> None:
        water = self.env.water_at(0.5)

        self.assertGreater(water_wave_slope(water), 0.0)
        self.assertGreater(
            water_wave_slope(WaterSettings(scale_above=0.2)),
            water_wave_slope(water),
        )

    def test_a_negative_lean_is_no_lean(self) -> None:
        self.assertEqual(water_wave_slope(WaterSettings(scale_above=-1.0)), 0.0)


class UnderwaterTests(unittest.TestCase):
    """The two numbers that say how far a swimmer can see, and one more.

    `water_fog_density` and `underwater_fog_mod` had been parsed and never
    used, and so had `scale_below`. A camera under the water plane got a clear
    afternoon: the sky gradient, the sun, the clouds and every prim in the
    region at full brightness.
    """

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_a_denser_sea_is_a_shorter_one(self) -> None:
        clear = underwater_reach(WaterSettings(fog_density=8.0))
        murky = underwater_reach(WaterSettings(fog_density=32.0))

        self.assertAlmostEqual(clear, murky * 4.0, places=4)

    def test_the_modifier_counts_as_much_as_the_density(self) -> None:
        """Both, not one. They multiply, and reading only the density puts
        the default cycle's visibility at seven metres rather than thirty."""
        halved_density = underwater_reach(
            WaterSettings(fog_density=8.0, underwater_fog_mod=0.5)
        )
        halved_modifier = underwater_reach(
            WaterSettings(fog_density=16.0, underwater_fog_mod=0.25)
        )

        self.assertAlmostEqual(halved_density, halved_modifier, places=4)

    def test_the_default_cycle_is_a_clear_sea(self) -> None:
        # Thirty metres. Not asserted to the metre -- what matters is that it
        # is a sea somebody can see across a room in and not across a region.
        reach = underwater_reach(self.env.water_at(0.5))

        self.assertGreater(reach, 10.0)
        self.assertLess(reach, 100.0)

    def test_a_sea_with_no_fog_in_it_does_not_divide_by_zero(self) -> None:
        reach = underwater_reach(WaterSettings(fog_density=0.0))

        self.assertGreater(reach, 1000.0)
        self.assertLess(reach, float("inf"))

    def test_a_negative_density_is_no_density(self) -> None:
        self.assertEqual(
            underwater_reach(WaterSettings(fog_density=-16.0)),
            underwater_reach(WaterSettings(fog_density=0.0)),
        )

    def test_the_surface_leans_further_from_underneath(self) -> None:
        # `scale_below` is 0.2 against `scale_above`'s 0.03 in the default
        # cycle, and the larger number is the right way round: from below a
        # surface is a lens rather than a mirror.
        water = self.env.water_at(0.5)

        self.assertGreater(water_wave_slope_below(water), water_wave_slope(water))

    def test_a_negative_lean_below_is_no_lean(self) -> None:
        self.assertEqual(water_wave_slope_below(WaterSettings(scale_below=-1.0)), 0.0)


class SunAndMoonSizeTests(unittest.TestCase):
    """`sun_scale` and `moon_scale`, which had not even been parsed.

    Both are 1.0 in every keyframe of OpenSim's default cycle, so the document
    says nothing about how they scale -- only that 1.0 is the unchanged size,
    which is what makes the direction readable at all. What it replaces is two
    pairs of magic numbers in the sky shader that no region could reach.
    """

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_default_scale_draws_the_size_this_viewer_already_drew(self) -> None:
        # The moon's two thresholds were 0.9990 and 0.9994 and it is a
        # regression if they move: this replaces a constant with a parameter,
        # it does not restyle the sky.
        low, high = moon_disc(self.env.sky_at(0.0))

        self.assertAlmostEqual(low, 0.9990, places=5)
        self.assertAlmostEqual(high, 0.9994, places=5)

    def test_the_sun_keeps_the_angle_its_old_falloff_half_faded_at(self) -> None:
        """The sun's own regression, which is not the moon's.

        Its falloff was a 900th power of the alignment and is a smoothstep
        now, so the two curves cannot be compared everywhere -- but the angle
        at which the old one had fallen to half is a number, and the middle of
        the new one's edge is set to it. Pinned to a ten-thousandth of a radian,
        which is tight enough to tell the sun's size from the moon's: the two
        are one per cent apart and a test that let them swap would say nothing.
        """
        low, high = sun_disc(SkySettings())
        edge_middle = (math.acos(low) + math.acos(high)) / 2.0

        self.assertAlmostEqual(edge_middle, math.acos(0.5 ** (1.0 / 900.0)), delta=1e-4)

    def test_the_edges_are_the_right_way_round_for_a_smoothstep(self) -> None:
        low, high = sun_disc(SkySettings())

        self.assertLess(low, high)

    def test_a_bigger_scale_is_a_bigger_disc(self) -> None:
        """Which is the one thing the document's own value cannot show.

        Asserted as an angle rather than as a cosine, because the cosine runs
        the other way and a test that compared the two numbers directly would
        pass just as happily on a sun that shrank.
        """
        small = math.acos(sun_disc(SkySettings(sun_scale=1.0))[0])
        large = math.acos(sun_disc(SkySettings(sun_scale=3.0))[0])

        self.assertAlmostEqual(large, small * 3.0, places=5)

    def test_the_sun_and_the_moon_scale_separately(self) -> None:
        sky = SkySettings(sun_scale=4.0, moon_scale=1.0)

        self.assertGreater(math.acos(sun_disc(sky)[0]), math.acos(moon_disc(sky)[0]))

    def test_a_scale_of_nothing_does_not_collapse_the_edges(self) -> None:
        # Two equal edges make a smoothstep a step, which draws an aliased dot
        # rather than no sun -- a worse answer than either.
        low, high = sun_disc(SkySettings(sun_scale=0.0))

        self.assertLess(low, high)

    def test_a_negative_scale_is_no_scale(self) -> None:
        self.assertEqual(
            sun_disc(SkySettings(sun_scale=-2.0)), sun_disc(SkySettings(sun_scale=0.0))
        )


class CloudShadowTests(unittest.TestCase):
    """`cloud_shadow`, parsed since the sixth pass and never spent."""

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_default_cycle_keeps_most_of_the_sun(self) -> None:
        # 0.27 of it is in shade, so about three quarters is left.
        kept = cloud_shadow_scale(self.env.sky_at(0.5))

        self.assertAlmostEqual(kept, 0.73, places=2)

    def test_more_shadow_is_less_light(self) -> None:
        self.assertLess(
            cloud_shadow_scale(SkySettings(cloud_shadow=0.8)),
            cloud_shadow_scale(SkySettings(cloud_shadow=0.1)),
        )

    def test_a_clear_sky_takes_nothing(self) -> None:
        self.assertEqual(cloud_shadow_scale(SkySettings(cloud_shadow=0.0)), 1.0)

    def test_a_document_cannot_ask_for_more_shade_than_there_is_sun(self) -> None:
        self.assertEqual(cloud_shadow_scale(SkySettings(cloud_shadow=4.0)), 0.0)
        self.assertEqual(cloud_shadow_scale(SkySettings(cloud_shadow=-4.0)), 1.0)


if __name__ == "__main__":
    unittest.main()


class NightSkyTests(unittest.TestCase):
    """The moon and the stars, both of which were in the document unread."""

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_moon_turns_the_same_vector_as_the_sun(self) -> None:
        self.assertEqual(MOON_REFERENCE_DIRECTION, SUN_REFERENCE_DIRECTION)

    def test_the_moon_is_opposite_the_sun_at_every_keyframe(self) -> None:
        """Which is what says the reference vector is right.

        Nothing names the vector a `moon_rotation` turns, and picking one by
        eye at a single keyframe would agree with three of them. All eight at
        once is the check: at midnight the moon is straight up and the sun
        straight down, at noon the reverse, and at every keyframe between them
        their elevations are exact negatives.
        """
        for fraction, sky in self.env.sky_track:
            with self.subTest(fraction=fraction):
                self.assertAlmostEqual(
                    _elevation_degrees(moon_direction(sky)),
                    -_elevation_degrees(sun_direction(sky)),
                    delta=0.01,
                )

    def test_the_moon_is_up_at_midnight_and_down_at_noon(self) -> None:
        midnight = dict(self.env.sky_track)[0.0]
        noon = dict(self.env.sky_track)[0.5]

        self.assertAlmostEqual(_elevation_degrees(moon_direction(midnight)), 90.0, delta=0.01)
        self.assertAlmostEqual(_elevation_degrees(moon_direction(noon)), -90.0, delta=0.01)

    def test_the_stars_are_out_at_night_and_gone_by_dawn(self) -> None:
        # Every keyframe, because the interesting claim is that the document
        # is emphatic about this: exactly 500 twice and exactly 0 six times,
        # with nothing in between to interpolate a guess from.
        lit = {
            fraction: star_level(sky) for fraction, sky in self.env.sky_track
        }

        # Rounded: the keyframe keys come off the wire as 32-bit floats, so
        # 0.95 arrives as 0.949999988.
        self.assertEqual(
            [round(fraction, 3) for fraction, level in lit.items() if level > 0.0],
            [0.0, 0.05, 0.95],
        )
        for level in lit.values():
            self.assertIn(round(level, 3), (0.0, 1.0))

    def test_the_star_scale_is_what_the_document_writes(self) -> None:
        # The constant is a normalisation and nothing more; this is the only
        # evidence for its value, so it is worth saying out loud.
        self.assertEqual(STAR_BRIGHTNESS_FULL, 500.0)
        self.assertEqual(
            {
                round(sky.star_brightness, 1)
                for _fraction, sky in self.env.sky_track
                if sky.star_brightness
            },
            {500.0},
        )

    def test_a_brighter_star_field_than_the_scale_is_still_all_of_them(self) -> None:
        self.assertEqual(star_level(SkySettings(star_brightness=5000.0)), 1.0)

    def test_half_the_scale_is_half_the_stars(self) -> None:
        # The document only ever says 0 or 500, so every other test here would
        # pass just as well if the level were not divided by the scale at all
        # -- the clamp alone turns 500 into 1. This is the one that says the
        # constant is doing arithmetic rather than sitting there.
        self.assertAlmostEqual(
            star_level(SkySettings(star_brightness=STAR_BRIGHTNESS_FULL / 2.0)),
            0.5,
            places=5,
        )

    def test_the_moon_is_up_in_the_daytime_too(self) -> None:
        """Not gated on night, deliberately.

        The default cycle holds `moon_brightness` at 0.5 through the whole
        day, and a daytime moon is a real thing. What hides it is the sky
        being brighter, which the gradient already does.
        """
        for _fraction, sky in self.env.sky_track:
            self.assertAlmostEqual(moon_level(sky), 0.5, places=4)


class CloudTests(unittest.TestCase):
    """The cloud layer's parameters, all of which were parsed and unread."""

    def setUp(self) -> None:
        self.env = _live_environment()

    def test_the_coverage_is_the_third_component_of_each_density(self) -> None:
        # The other two are an offset into a cloud texture nothing here has
        # fetched. Reading the wrong component of the triple is the easy
        # mistake and gives a plausible number.
        for _fraction, sky in self.env.sky_track:
            with self.subTest(cloud=sky.cloud_pos_density1):
                coarse, fine = cloud_cover(sky)
                self.assertAlmostEqual(coarse, sky.cloud_pos_density1[2], places=5)
                self.assertAlmostEqual(fine, sky.cloud_pos_density2[2], places=5)

    def test_the_default_cycle_is_a_cloudy_one(self) -> None:
        # Worth pinning: the coarse density never drops below 0.88 anywhere in
        # the default cycle, which is why taking it literally as "this much of
        # the sky is cloud" would give permanent overcast.
        coarse = [cloud_cover(sky)[0] for _fraction, sky in self.env.sky_track]

        # 32-bit floats: the document's 0.88 arrives as 0.8799999952.
        self.assertGreaterEqual(round(min(coarse), 3), 0.88)
        self.assertLessEqual(max(coarse), 1.0)

    def test_clouds_are_lit_rather_than_drawn_in_their_own_colour(self) -> None:
        """`cloud_color` is an albedo, and using it raw is the bug.

        At noon it is 0.41 grey against a sky this same module puts at about
        0.5 blue -- cloud darker than the sky behind it, which reads as a
        storm. Lit by the two lights the frame already has, it comes out
        brighter than the sky, which is what a cloud is.
        """
        noon = dict(self.env.sky_track)[0.5]

        drawn = cloud_hue(noon)
        horizon, zenith = sky_gradient(noon)

        self.assertGreater(sum(drawn), sum(noon.cloud_color))
        self.assertGreater(sum(drawn), sum(zenith), "cloud darker than the sky")

    def test_the_clouds_take_the_colour_of_the_hour(self) -> None:
        # Bright and slightly warm at dusk, when `sunlight_color` reaches
        # 2.84; dark and blue at midnight. Neither is chosen here -- both fall
        # out of the region's own numbers.
        dusk = cloud_hue(dict(self.env.sky_track)[0.875])
        midnight = cloud_hue(dict(self.env.sky_track)[0.0])

        self.assertGreater(sum(dusk), sum(midnight) * 3.0)
        self.assertGreater(dusk[0], dusk[2], "dusk cloud is not warm")
        self.assertGreater(midnight[2], midnight[0], "midnight cloud is not cold")

    def test_the_cell_size_is_the_scale_in_metres(self) -> None:
        noon = dict(self.env.sky_track)[0.5]

        self.assertAlmostEqual(
            cloud_size(noon), noon.cloud_scale * CLOUD_SCALE_METRES, places=3
        )

    def test_a_zero_scale_does_not_collapse_the_layer(self) -> None:
        # A region that writes 0 would otherwise divide the sky into cells of
        # no width, which in the shader is every pixel sampling one hash.
        self.assertGreater(cloud_size(SkySettings(cloud_scale=0.0)), 0.0)
