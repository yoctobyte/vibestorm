"""The region's own weather reaching the frame.

`Scene` derives one sky, one sun and one sea colour per refresh from the day
cycle the `ExtEnvironment` capability served, indexed by the simulator's clock.
Before this, all three were constants and every region looked like the same
afternoon at the same hour.

The clock is worth a note. `SimulatorViewerTimeMessage.UsecSinceStart` is not
an uptime -- it is a Unix timestamp in microseconds, checked against the
machine's own clock on 2026-09-06 -- and it is the only time either side
agrees on, so it is what indexes the cycle.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.caps.llsd import parse_xml_value
from vibestorm.viewer3d.atmosphere import (
    DEFAULT_MOON_DISC,
    DEFAULT_MOON_FACE_AXES,
    DEFAULT_SKY_HORIZON_COLOR,
    DEFAULT_SKY_ZENITH_COLOR,
    DEFAULT_SUN_DISC,
    DEFAULT_UNDERWATER_REACH,
    DEFAULT_WATER_FOG,
    DEFAULT_WATER_FRESNEL,
    DEFAULT_WATER_RIPPLE,
    DEFAULT_WATER_RIPPLE_BELOW,
    DEFAULT_WATER_TINT,
    DEFAULT_WATER_WAVE_SPEED,
    DEFAULT_WATER_WAVES,
)
from vibestorm.viewer3d.perspective import DEFAULT_SUN_DIRECTION, lighting_direction
from vibestorm.viewer3d.scene import Scene
from vibestorm.world.environment import (
    RegionEnvironment,
    WaterSettings,
    parse_environment_document,
)
from vibestorm.world.models import SimulatorTimeSnapshot, WorldView

FIXTURE = Path("test/fixtures/environment/ext-environment-opensim.xml")


def _world_view(*, environment=True, clock: int | None = None, sun=None) -> WorldView:
    view = WorldView()
    if environment:
        view.environment = parse_environment_document(parse_xml_value(FIXTURE.read_bytes()))
    if clock is not None:
        view.latest_time = SimulatorTimeSnapshot(
            usec_since_start=clock,
            sec_per_day=14400,
            sec_per_year=31536000,
            sun_phase=1.0,
            sun_direction=sun,
        )
    return view


def _at(day_fraction: float) -> int:
    """A simulator clock reading that lands at this point in the region's day.

    The cycle's own offset is 57600 seconds, four whole days of 14400, so it
    cancels -- but going through the arithmetic rather than ignoring it is what
    keeps this honest if the fixture is ever recaptured from a region with a
    real offset.
    """
    length, offset = 14400, 57600
    return int(((day_fraction * length) - offset) % length) * 1_000_000


class EnvironmentRefreshTests(unittest.TestCase):
    def test_a_world_view_with_no_environment_keeps_the_old_colours(self) -> None:
        # Every region looked like this, and a region whose capability fails
        # still should rather than going black.
        scene = Scene()

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.sky_horizon_color, DEFAULT_SKY_HORIZON_COLOR)
        self.assertEqual(scene.sky_zenith_color, DEFAULT_SKY_ZENITH_COLOR)
        self.assertEqual(scene.water_tint, DEFAULT_WATER_TINT)
        self.assertIsNone(scene.day_fraction)

    def test_the_day_cycle_replaces_them(self) -> None:
        scene = Scene()

        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))

        self.assertNotEqual(scene.sky_horizon_color, DEFAULT_SKY_HORIZON_COLOR)
        self.assertNotEqual(scene.water_tint, DEFAULT_WATER_TINT)

    def test_the_clock_picks_the_hour(self) -> None:
        noon, midnight = Scene(), Scene()

        noon.refresh_from_world_view(_world_view(clock=_at(0.5)))
        midnight.refresh_from_world_view(_world_view(clock=_at(0.0)))

        self.assertAlmostEqual(noon.day_fraction, 0.5, places=3)
        self.assertAlmostEqual(midnight.day_fraction, 0.0, places=3)
        self.assertLess(sum(midnight.sky_zenith_color), sum(noon.sky_zenith_color))

    def test_an_environment_with_no_clock_yet_is_drawn_at_midday(self) -> None:
        # Fraction zero is midnight. A viewer that blacks out for the second
        # before the first time message is worse than one briefly too bright.
        scene = Scene()

        scene.refresh_from_world_view(_world_view(clock=None))

        self.assertEqual(scene.day_fraction, 0.5)

    def test_the_sun_comes_from_the_day_cycle(self) -> None:
        noon, midnight = Scene(), Scene()

        noon.refresh_from_world_view(_world_view(clock=_at(0.5)))
        midnight.refresh_from_world_view(_world_view(clock=_at(0.0)))

        self.assertGreater(noon.environment_sun_direction[2], 0.99)
        self.assertLess(midnight.environment_sun_direction[2], -0.99)

    def test_losing_the_environment_puts_the_old_colours_back(self) -> None:
        # A teleport into a region whose fetch has not landed must not keep
        # drawing the last region's weather.
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.sky_horizon_color, DEFAULT_SKY_HORIZON_COLOR)
        self.assertIsNone(scene.environment_sun_direction)

    def test_the_night_sky_reaches_the_scene(self) -> None:
        midnight = Scene()
        midnight.refresh_from_world_view(_world_view(clock=_at(0.0)))
        noon = Scene()
        noon.refresh_from_world_view(_world_view(clock=_at(0.5)))

        self.assertAlmostEqual(midnight.star_level, 1.0, places=3)
        self.assertEqual(noon.star_level, 0.0)
        # And the moon is the half of the sky the sun is not in.
        self.assertGreater(midnight.moon_direction[2], 0.99)
        self.assertLess(noon.moon_direction[2], -0.99)

    def test_losing_the_environment_takes_the_night_sky_with_it(self) -> None:
        # Same reason as the colours: a teleport must not leave the previous
        # region's stars hanging in an unfetched sky.
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.0)))

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.star_level, 0.0)
        self.assertEqual(scene.moon_level, 0.0)
        self.assertIsNone(scene.moon_direction)


    def test_the_cloud_layer_reaches_the_scene(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))

        coarse, fine, variance = scene.cloud_cover
        self.assertAlmostEqual(coarse, 1.0, places=3)
        self.assertAlmostEqual(fine, 0.125, places=3)
        self.assertEqual(variance, 0.0)
        self.assertGreater(scene.cloud_scale_drift[0], 0.0)

    def test_losing_the_environment_clears_the_clouds(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.cloud_cover, (0.0, 0.0, 0.0))

    def test_the_clouds_drift(self) -> None:
        """And the drift is accumulated, not read off the region's clock.

        The clock would give the right answer and arrives every few seconds,
        so a layer driven by it sits still and then jumps. This is the seam
        that has to be per frame.
        """
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))
        started = scene.cloud_scale_drift[1:]
        cell = scene.cloud_scale_drift[0]

        for _frame in range(120):
            scene.advance_clouds(1.0 / 60.0)

        self.assertGreater(scene.cloud_scale_drift[1], started[0])
        # The cell size shares the triple with the drift, so an advance that
        # rebuilt it carelessly would resize the clouds every frame.
        self.assertAlmostEqual(scene.cloud_scale_drift[0], cell, places=6)

    def test_drifting_without_a_region_is_not_an_error(self) -> None:
        # The 2D renderer and the seconds before the fetch lands both hit this.
        scene = Scene()

        scene.advance_clouds(0.016)

        self.assertEqual(scene.cloud_scale_drift[1:], (0.0, 0.0))

    def test_time_never_runs_backwards_for_the_clouds(self) -> None:
        # A negative frame delta is what a clock adjustment looks like, and
        # clouds that reverse on one are worse than clouds that pause.
        scene = Scene()
        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))
        for _frame in range(10):
            scene.advance_clouds(1.0 / 60.0)
        forward = scene.cloud_scale_drift[1]

        scene.advance_clouds(-5.0)

        self.assertAlmostEqual(scene.cloud_scale_drift[1], forward, places=9)

class LightingDirectionTests(unittest.TestCase):
    """Which of the four sun sources wins."""

    def test_the_simulators_zero_vector_does_not_win(self) -> None:
        # The bug this whole path exists to fix. OpenSim sends (0, 0, 0) in
        # every SimulatorViewerTimeMessage; it is not a missing answer, it is a
        # well-formed direction of length nothing, and it used to fall through
        # to a fixed fallback -- so the sun never moved in any session.
        scene = Scene()
        scene.refresh_from_world_view(
            _world_view(clock=_at(0.5), sun=(0.0, 0.0, 0.0))
        )

        direction = lighting_direction(scene)

        self.assertEqual(scene.sun_direction, (0.0, 0.0, 0.0))
        self.assertGreater(direction[2], 0.99)

    def test_a_simulator_that_does_send_a_sun_is_believed(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(
            _world_view(clock=_at(0.5), sun=(1.0, 0.0, 0.0))
        )

        self.assertEqual(lighting_direction(scene), (1.0, 0.0, 0.0))

    def test_the_sun_moves_across_the_day(self) -> None:
        heights = []
        for step in range(0, 20):
            scene = Scene()
            scene.refresh_from_world_view(
                _world_view(clock=_at(step / 20.0), sun=(0.0, 0.0, 0.0))
            )
            heights.append(lighting_direction(scene)[2])

        self.assertGreater(max(heights), 0.9)
        self.assertLess(min(heights), -0.9)

    def test_a_scene_with_nothing_at_all_still_has_a_light(self) -> None:
        direction = lighting_direction(Scene())

        # The fixed fallback, normalised -- the constant itself is not a unit
        # vector.
        length = math.sqrt(sum(c * c for c in DEFAULT_SUN_DIRECTION))
        for index in range(3):
            self.assertAlmostEqual(
                direction[index], DEFAULT_SUN_DIRECTION[index] / length, places=6
            )


class SunAndMoonRefreshTests(unittest.TestCase):
    """How big the region wants its sun and moon, and how much shade it has."""

    def _sky_cycle(self, **fields):
        from vibestorm.world.environment import SkySettings

        return RegionEnvironment(
            day_length=14400.0,
            sky_track=((0.0, SkySettings(**fields)),),
        )

    def test_a_world_view_with_no_environment_keeps_the_defaults(self) -> None:
        scene = Scene()
        loaded = WorldView()
        loaded.environment = self._sky_cycle(
            sun_scale=3.0, moon_scale=0.25, cloud_shadow=0.9
        )
        scene.refresh_from_world_view(loaded)

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.sun_disc, DEFAULT_SUN_DISC)
        self.assertEqual(scene.moon_disc, DEFAULT_MOON_DISC)
        self.assertEqual(scene.cloud_shadow, 1.0)

    def test_the_regions_own_sizes_reach_the_frame(self) -> None:
        scene = Scene()
        view = WorldView()
        view.environment = self._sky_cycle(
            sun_scale=3.0, moon_scale=0.25, cloud_shadow=0.9
        )

        scene.refresh_from_world_view(view)

        # Bigger sun, smaller moon, and the disc is measured as an angle
        # because the cosine runs the other way.
        self.assertGreater(
            math.acos(scene.sun_disc[0]), math.acos(DEFAULT_SUN_DISC[0])
        )
        self.assertLess(
            math.acos(scene.moon_disc[0]), math.acos(DEFAULT_MOON_DISC[0])
        )
        self.assertAlmostEqual(scene.cloud_shadow, 0.1, places=5)

    def test_the_moons_own_texture_reaches_the_frame(self) -> None:
        moon = UUID(int=0xB0B)
        scene = Scene()
        view = WorldView()
        view.environment = self._sky_cycle(moon_id=str(moon))

        scene.refresh_from_world_view(view)

        self.assertEqual(scene.moon_texture_id, moon)

    def test_the_way_the_moon_hangs_reaches_the_frame(self) -> None:
        """`moon_rotation` says more than where the moon is.

        It is a quaternion, and the two axes perpendicular to
        `MOON_REFERENCE_DIRECTION` are the two across the moon's face. A cycle
        that rolls its moon a quarter turn about its own direction puts the
        face's up where its across was.
        """
        quarter = math.pi / 4.0
        scene = Scene()
        view = WorldView()
        view.environment = self._sky_cycle(
            moon_rotation=(math.sin(quarter), 0.0, 0.0, math.cos(quarter))
        )

        scene.refresh_from_world_view(view)

        across, up = scene.moon_face_axes
        for drawn, expected in zip(across, (0.0, 0.0, 1.0), strict=True):
            self.assertAlmostEqual(drawn, expected, places=6)
        for drawn, expected in zip(up, (0.0, -1.0, 0.0), strict=True):
            self.assertAlmostEqual(drawn, expected, places=6)

    def test_a_null_moon_id_is_no_texture_rather_than_a_null_one(self) -> None:
        """Which is what the document writes when it means "draw your own".

        `sun_id` is the null UUID in all eight keyframes of the live cycle, so
        this is the shape a client meets rather than a hypothetical: a null id
        queued for fetch is a request that can never be answered.
        """
        scene = Scene()
        view = WorldView()
        view.environment = self._sky_cycle(moon_id=str(UUID(int=0)))

        scene.refresh_from_world_view(view)

        self.assertIsNone(scene.moon_texture_id)

    def test_the_cloud_field_and_its_offsets_reach_the_frame(self) -> None:
        clouds = UUID(int=0xC10D)
        scene = Scene()
        view = WorldView()
        view.environment = self._sky_cycle(
            cloud_id=str(clouds),
            cloud_pos_density1=(0.25, 0.5, 0.75),
            cloud_pos_density2=(0.125, 0.375, 0.5),
        )

        scene.refresh_from_world_view(view)

        self.assertEqual(scene.cloud_texture_id, clouds)
        # The offsets are the first two of each pair; the third is the density
        # and belongs to `cloud_cover`, which is why this checks all four.
        self.assertEqual(scene.cloud_offsets, (0.25, 0.5, 0.125, 0.375))

    def test_a_lost_environment_takes_the_moons_face_with_it(self) -> None:
        scene = Scene()
        loaded = WorldView()
        quarter = math.pi / 4.0
        loaded.environment = self._sky_cycle(
            moon_id=str(UUID(int=0xB0B)),
            cloud_id=str(UUID(int=0xC10D)),
            cloud_pos_density1=(0.25, 0.5, 0.75),
            # Rolled, so that "back to the default" is a different answer from
            # "left as this region had it".
            moon_rotation=(math.sin(quarter), 0.0, 0.0, math.cos(quarter)),
        )
        scene.refresh_from_world_view(loaded)

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertIsNone(scene.moon_texture_id)
        self.assertIsNone(scene.cloud_texture_id)
        self.assertEqual(scene.cloud_offsets, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(scene.moon_face_axes, DEFAULT_MOON_FACE_AXES)


class WaterSurfaceRefreshTests(unittest.TestCase):
    """The sea's surface, which the day cycle also describes.

    Not the same thing as its colour: which way the waves run, how fast, how
    steep, and how much sky the surface shows back at a given angle. All four
    were parsed and none reached the frame.
    """

    def _unlike_the_default(self, *, normal_map: str = ""):
        """A day cycle whose water is nothing like `WaterSettings`' defaults.

        Needed for the same reason `test_the_regions_own_surface_reaches_the
        _frame` needs it: the captured fixture *is* the defaults, so refreshing
        from it and finding the defaults afterwards proves nothing.
        """
        return RegionEnvironment(
            day_length=14400.0,
            water_track=(
                (
                    0.0,
                    WaterSettings(
                        fog_color=(0.4, 0.1, 0.05),
                        fresnel_offset=0.11,
                        fresnel_scale=0.22,
                        scale_above=0.5,
                        scale_below=0.75,
                        fog_density=32.0,
                        underwater_fog_mod=0.5,
                        normal_scale=(3.0, 3.0, 3.0),
                        wave1_direction=(0.0, 2.0),
                        wave2_direction=(-3.0, 0.0),
                        normal_map=normal_map,
                    ),
                ),
            ),
        )

    def test_a_world_view_with_no_environment_keeps_the_default_sea(self) -> None:
        scene = Scene()
        loaded = WorldView()
        loaded.environment = self._unlike_the_default(normal_map=str(UUID(int=0x5EA)))
        scene.refresh_from_world_view(loaded)

        scene.refresh_from_world_view(_world_view(environment=False))

        self.assertEqual(scene.water_fog, DEFAULT_WATER_FOG)
        self.assertEqual(scene.water_fresnel, DEFAULT_WATER_FRESNEL)
        self.assertEqual(scene.water_waves, DEFAULT_WATER_WAVES)
        self.assertEqual(scene.water_ripple, DEFAULT_WATER_RIPPLE)
        self.assertEqual(scene.water_ripple_below, DEFAULT_WATER_RIPPLE_BELOW)
        self.assertEqual(scene.water_reach, DEFAULT_UNDERWATER_REACH)
        self.assertEqual(scene.water_wave_speed, DEFAULT_WATER_WAVE_SPEED)
        self.assertIsNone(scene.water_normal_id)

    def test_the_seas_own_normal_map_reaches_the_frame(self) -> None:
        """`normal_map`, which is a fetchable asset like the moon's face.

        The default cycle names one -- a wind-ripple sheet -- and until this
        was plumbed the surface was six sines standing in for it.
        """
        surface = UUID(int=0x5EA)
        view = WorldView()
        view.environment = RegionEnvironment(
            day_length=14400.0,
            water_track=((0.0, WaterSettings(normal_map=str(surface))),),
        )
        scene = Scene()

        scene.refresh_from_world_view(view)

        self.assertEqual(scene.water_normal_id, surface)

    def test_a_null_normal_map_is_no_map_rather_than_a_null_one(self) -> None:
        scene = Scene()
        view = WorldView()
        view.environment = RegionEnvironment(
            day_length=14400.0,
            water_track=((0.0, WaterSettings(normal_map=str(UUID(int=0)))),),
        )

        scene.refresh_from_world_view(view)

        self.assertIsNone(scene.water_normal_id)

    def test_the_regions_own_surface_reaches_the_frame(self) -> None:
        """Against a sea unlike the fallback one, deliberately.

        The captured document's water frame *is* the fallback: `WaterSettings`'
        defaults were read off it in the first place. So refreshing from the
        fixture and finding the defaults on the scene proves nothing at all --
        a `Scene` that ignored the region entirely passes that. This builds a
        day cycle whose every water field differs and checks each one arrives.
        """
        view = WorldView()
        view.environment = self._unlike_the_default()
        scene = Scene()

        scene.refresh_from_world_view(view)

        self.assertEqual(scene.water_fog, (0.4, 0.1, 0.05))
        self.assertAlmostEqual(scene.water_fresnel[0], 0.11, places=6)
        self.assertAlmostEqual(scene.water_fresnel[1], 0.22, places=6)
        self.assertEqual(scene.water_waves, (0.0, 1.0, -1.0, 0.0))
        # `normal_scale` three against `WATER_WAVE_LENGTH_M`'s nine puts the
        # mean wave at three metres, and the two speeds -- two and three --
        # put the waves either side of it at two and four and a half. Asserted
        # as lengths rather than as wave numbers because a length is the thing
        # anyone can picture.
        first, second, lean = scene.water_ripple
        self.assertAlmostEqual(math.tau / first, 2.0, places=5)
        self.assertAlmostEqual(math.tau / second, 4.5, places=5)
        self.assertAlmostEqual(math.sqrt(2.0 * 4.5), 3.0, places=5)
        # `scale_above` is a lean, and this region's is much steeper.
        self.assertGreater(lean, DEFAULT_WATER_RIPPLE[2] * 2.0)
        # Each wave's crests travel at its own direction's length, and the
        # second's is half again the first's.
        self.assertAlmostEqual(
            scene.water_wave_speed[1] / second,
            (scene.water_wave_speed[0] / first) * 1.5,
            places=5,
        )
        # A sea four times as dense as the fallback's is seen a quarter as
        # far into.
        self.assertAlmostEqual(
            scene.water_reach, DEFAULT_UNDERWATER_REACH / 4.0, places=5
        )
        self.assertGreater(scene.water_ripple_below, scene.water_ripple[2])
        for index in range(7):
            self.assertNotEqual(
                (
                    scene.water_fog,
                    scene.water_fresnel,
                    scene.water_waves,
                    scene.water_ripple,
                    scene.water_wave_speed,
                    scene.water_reach,
                    scene.water_ripple_below,
                )[index],
                (
                    DEFAULT_WATER_FOG,
                    DEFAULT_WATER_FRESNEL,
                    DEFAULT_WATER_WAVES,
                    DEFAULT_WATER_RIPPLE,
                    DEFAULT_WATER_WAVE_SPEED,
                    DEFAULT_UNDERWATER_REACH,
                    DEFAULT_WATER_RIPPLE_BELOW,
                )[index],
            )

    def test_the_sea_is_not_the_colour_it_is_drawn(self) -> None:
        """`water_fog` and `water_tint` are two different colours on purpose.

        The second is the first with a fixed share of sky already in it, for
        the renderer that cannot measure an angle. Handing the shader that one
        would put the sky into the sea twice.
        """
        scene = Scene()

        scene.refresh_from_world_view(_world_view(clock=_at(0.5)))

        self.assertNotEqual(scene.water_fog, scene.water_tint)


class WaterDriftTests(unittest.TestCase):
    def test_the_waves_move_forward(self) -> None:
        scene = Scene()

        scene.advance_water(0.5)

        self.assertGreater(scene.water_phase[0], 0.0)
        self.assertGreater(scene.water_phase[1], 0.0)

    def test_the_two_waves_do_not_move_together(self) -> None:
        # They have different speeds in the document, and a sea whose two
        # waves advance in lockstep is one wave drawn twice.
        scene = Scene()

        scene.advance_water(1.0)

        self.assertNotAlmostEqual(scene.water_phase[0], scene.water_phase[1], places=3)

    def test_the_regions_own_speed_is_what_moves_them(self) -> None:
        fast, slow = Scene(), Scene()
        fast.refresh_from_world_view(_world_view(clock=_at(0.5)))
        fast.water_wave_speed = (4.0, 4.0)
        slow.water_wave_speed = (0.25, 0.25)

        fast.advance_water(0.5)
        slow.advance_water(0.5)

        self.assertGreater(fast.water_phase[0], slow.water_phase[0] * 8)

    def test_time_never_runs_backwards_for_the_waves(self) -> None:
        # A negative frame time is what a clock adjustment looks like from
        # inside the loop, and a sea that jumps backwards is worse than one
        # that stalls for a frame.
        scene = Scene()
        scene.advance_water(1.0)
        first = scene.water_phase

        scene.advance_water(-5.0)

        self.assertEqual(scene.water_phase, first)

    def test_the_phase_does_not_run_away(self) -> None:
        """Wrapped at a full turn, which the cloud drift is not.

        A phase is an angle handed straight to a sine in a shader, and shader
        floats are single precision. Left to accumulate, an hour of viewing
        puts it past ten thousand radians, where the gap between representable
        angles is wide enough to show as the waves quantising.
        """
        scene = Scene()

        for _ in range(200):
            scene.advance_water(60.0)

        for component in scene.water_phase:
            self.assertGreaterEqual(component, 0.0)
            self.assertLess(component, 2.0 * math.pi)

    def test_wrapping_does_not_move_the_wave(self) -> None:
        # The wrap has to be a whole turn or it is a jump. Two seas advanced
        # by the same total, one in a single step and one in many, have to be
        # at the same point on the sine.
        one_step, many = Scene(), Scene()

        one_step.advance_water(20.0)
        for _ in range(20):
            many.advance_water(1.0)

        for index in range(2):
            self.assertAlmostEqual(
                math.sin(one_step.water_phase[index]),
                math.sin(many.water_phase[index]),
                places=5,
            )


if __name__ == "__main__":
    unittest.main()
