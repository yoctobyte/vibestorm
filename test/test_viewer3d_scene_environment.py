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

from vibestorm.caps.llsd import parse_xml_value
from vibestorm.viewer3d.atmosphere import (
    DEFAULT_SKY_HORIZON_COLOR,
    DEFAULT_SKY_ZENITH_COLOR,
    DEFAULT_WATER_TINT,
)
from vibestorm.viewer3d.perspective import DEFAULT_SUN_DIRECTION, lighting_direction
from vibestorm.viewer3d.scene import Scene
from vibestorm.world.environment import parse_environment_document
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


if __name__ == "__main__":
    unittest.main()
