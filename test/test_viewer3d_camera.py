"""Tests for the viewer3d Camera3D mode-aware camera.

Map-mode behavior must match the prior 2D Camera bit-for-bit so the
existing render path keeps working unchanged. The 3D mode fields are
state-only today (no projection math yet), so tests focus on default
state and mode switching.
"""

import math
import unittest


class Camera3DMapModeTests(unittest.TestCase):
    def test_default_mode_is_map(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()

        self.assertEqual(camera.mode, "map")

    def test_world_to_screen_inverse_of_screen_to_world(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(50.0, 60.0), zoom=2.5, screen_size=(800, 600)
        )

        wx, wy = camera.screen_to_world(*camera.world_to_screen(75.0, 90.0))

        self.assertAlmostEqual(wx, 75.0)
        self.assertAlmostEqual(wy, 90.0)

    def test_world_to_screen_centers_world_center_at_screen_center(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600)
        )

        sx, sy = camera.world_to_screen(128.0, 128.0)

        self.assertAlmostEqual(sx, 400.0)
        self.assertAlmostEqual(sy, 300.0)

    def test_world_to_screen_flips_y(self) -> None:
        # Moving north in world should move up in screen (smaller y).
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(0.0, 0.0), zoom=1.0, screen_size=(800, 600)
        )

        _, sy_north = camera.world_to_screen(0.0, 50.0)
        _, sy_centre = camera.world_to_screen(0.0, 0.0)

        self.assertLess(sy_north, sy_centre)

    def test_pan_screen_translates_world_center(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(100.0, 100.0), zoom=2.0, screen_size=(800, 600)
        )

        camera.pan_screen(20.0, 0.0)

        self.assertAlmostEqual(camera.world_center[0], 100.0 - 20.0 / 2.0)
        self.assertAlmostEqual(camera.world_center[1], 100.0)

    def test_zoom_at_screen_keeps_anchor_under_cursor(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600)
        )
        anchor_world_before = camera.screen_to_world(200.0, 250.0)

        camera.zoom_at_screen(200.0, 250.0, 1.5)

        sx_after, sy_after = camera.world_to_screen(*anchor_world_before)
        self.assertAlmostEqual(sx_after, 200.0, places=4)
        self.assertAlmostEqual(sy_after, 250.0, places=4)

    def test_fit_region_uses_smaller_screen_axis(self) -> None:
        from vibestorm.viewer3d.camera import REGION_SIZE_METERS, Camera3D

        camera = Camera3D(screen_size=(1024, 600))

        camera.fit_region(padding_px=50)

        expected_zoom = (600 - 100) / REGION_SIZE_METERS
        self.assertAlmostEqual(camera.zoom, expected_zoom)

    def test_zoom_at_screen_rejects_non_positive_factor(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()

        with self.assertRaises(ValueError):
            camera.zoom_at_screen(0.0, 0.0, 0.0)

    def test_set_screen_size_clamps_to_minimum_one(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()

        camera.set_screen_size((0, -5))

        self.assertEqual(camera.screen_size, (1, 1))


class Camera3DModeSwitchTests(unittest.TestCase):
    def test_set_mode_changes_mode_field(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()

        camera.set_mode("orbit")
        self.assertEqual(camera.mode, "orbit")

        camera.set_mode("eye")
        self.assertEqual(camera.mode, "eye")

        camera.set_mode("free")
        self.assertEqual(camera.mode, "free")

    def test_set_mode_back_to_map_preserves_2d_state(self) -> None:
        # Switching modes should leave the Map-mode pan/zoom state alone so
        # users can flip back without losing their viewport.
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            world_center=(75.0, 200.0), zoom=3.0, screen_size=(640, 480)
        )

        camera.set_mode("orbit")
        camera.set_mode("map")

        self.assertEqual(camera.world_center, (75.0, 200.0))
        self.assertEqual(camera.zoom, 3.0)

    def test_3d_mode_state_defaults(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()

        self.assertEqual(camera.yaw, 0.0)
        self.assertEqual(camera.pitch, 0.0)
        self.assertGreater(camera.distance, 0.0)
        self.assertEqual(len(camera.eye_position), 3)
        self.assertEqual(len(camera.target), 3)


class CameraBackwardsCompatTests(unittest.TestCase):
    def test_camera_alias_resolves_to_camera3d(self) -> None:
        from vibestorm.viewer3d.camera import Camera, Camera3D

        self.assertIs(Camera, Camera3D)

    def test_camera_alias_accepts_legacy_kwargs(self) -> None:
        from vibestorm.viewer3d.camera import Camera

        camera = Camera(
            world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600)
        )

        self.assertEqual(camera.mode, "map")
        sx, sy = camera.world_to_screen(128.0, 128.0)
        self.assertAlmostEqual(sx, 400.0)
        self.assertAlmostEqual(sy, 300.0)

    def test_yaw_can_be_set_to_arbitrary_radians(self) -> None:
        # 3D modes need pi-relative yaw values; sanity-check the field
        # accepts them and stays a float.
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.yaw = math.pi / 4

        self.assertAlmostEqual(camera.yaw, math.pi / 4)


class Camera3DOrbitControlTests(unittest.TestCase):
    def test_orbit_rotate_updates_yaw_and_pitch(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.orbit_rotate(10.0, -5.0, sensitivity=0.1)

        self.assertAlmostEqual(camera.yaw, -1.0)
        self.assertAlmostEqual(camera.pitch, -0.5)

    def test_orbit_zoom_changes_distance(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(distance=50.0)
        camera.orbit_zoom(1.0, factor_per_step=2.0)
        self.assertAlmostEqual(camera.distance, 25.0)
        camera.orbit_zoom(-1.0, factor_per_step=2.0)
        self.assertAlmostEqual(camera.distance, 50.0)

    def test_orbit_pan_and_lift_move_target(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(target=(10.0, 20.0, 5.0), distance=50.0)
        camera.orbit_pan(10.0, -20.0, sensitivity=0.1)
        camera.orbit_lift(3.0)

        self.assertEqual(camera.target, (9.0, 18.0, 8.0))


class Camera3DPresetTests(unittest.TestCase):
    def test_sim_overview_sets_region_orbit(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.set_sim_overview()

        self.assertEqual(camera.mode, "orbit")
        self.assertEqual(camera.target, (128.0, 128.0, 24.0))
        self.assertGreater(camera.distance, 100.0)

    def test_avatar_behind_uses_rotation_forward(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        # 90-degree yaw around Z: local +X faces world +Y.
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        camera.set_avatar_behind((10.0, 20.0, 5.0), (0.0, 0.0, s, c), distance_m=10.0)

        self.assertEqual(camera.mode, "free")
        self.assertAlmostEqual(camera.eye_position[0], 10.0, places=5)
        self.assertAlmostEqual(camera.eye_position[1], 10.0, places=5)
        self.assertGreater(camera.eye_position[2], 5.0)
        self.assertGreater(camera.target[1], 20.0)

    def test_avatar_eye_looks_forward_from_head_height(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.set_avatar_eye((10.0, 20.0, 5.0), None)

        self.assertEqual(camera.mode, "eye")
        self.assertEqual(camera.eye_position, (10.0, 20.0, 6.65))
        self.assertGreater(camera.target[0], camera.eye_position[0])


def _slope_west_of(edge_x: float, *, low: float = 0.0, high: float = 40.0):
    """Ground that is `high` west of `edge_x` and `low` east of it."""

    def ground(x: float, y: float) -> float | None:
        return high if x < edge_x else low

    return ground


class EyeClearOfTheGroundTests(unittest.TestCase):
    """The camera is not allowed inside the hill it is standing on.

    Found by rendering a third-person camera at the foot of a ridge and
    looking at it: the frame was a wall of flat green with the sea visible
    underneath it, which is what the inside of the terrain looks like. The
    default camera sits ten metres behind the avatar, so any slope steeper
    than about eighteen degrees put it there.
    """

    def test_an_eye_in_clear_air_is_left_where_it_was(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        eye = (10.0, 0.0, 50.0)

        self.assertEqual(
            eye_clear_of_the_ground(eye, (0.0, 0.0, 50.0), lambda x, y: 0.0), eye
        )

    def test_an_eye_in_the_ground_is_pulled_back_towards_the_target(self) -> None:
        from vibestorm.viewer3d.camera import (
            GROUND_CLEARANCE_M,
            eye_clear_of_the_ground,
        )

        target = (0.0, 0.0, 10.0)
        eye = (-10.0, 0.0, 10.0)  # ten metres west, inside the ridge
        ground = _slope_west_of(-5.0, low=0.0, high=40.0)

        held = eye_clear_of_the_ground(eye, target, ground)

        self.assertGreater(held[0], eye[0], "the eye was not pulled in at all")
        self.assertLess(held[0], target[0], "the eye was pulled past the target")
        self.assertGreaterEqual(
            held[2], ground(held[0], held[1]) + GROUND_CLEARANCE_M - 1e-6
        )

    def test_pulling_in_does_not_change_where_the_camera_looks_from(self) -> None:
        from vibestorm.viewer3d.camera import _normalize, _sub, eye_clear_of_the_ground

        target = (0.0, 0.0, 10.0)
        eye = (-8.0, -6.0, 4.0)
        ground = _slope_west_of(-2.0, low=0.0, high=40.0)

        held = eye_clear_of_the_ground(eye, target, ground)

        wanted = _normalize(_sub(eye, target))
        got = _normalize(_sub(held, target))
        for axis in range(3):
            self.assertAlmostEqual(got[axis], wanted[axis], places=5)

    def test_the_camera_stops_at_the_near_side_of_a_ridge(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        # A ridge close to the target, a wide pocket of clear air beyond it,
        # and solid ground further out with the eye buried in that. Halfway
        # along the line is in the pocket, so a bisection finds it clear and
        # converges on the far side of a ridge the camera cannot see over --
        # nine metres from the avatar with a hill in between. A march out from
        # the target has to meet the near face first.
        def ground(x: float, y: float) -> float | None:
            return 40.0 if (-2.5 <= x <= -1.0 or x <= -9.0) else 0.0

        held = eye_clear_of_the_ground((-10.0, 0.0, 10.0), (0.0, 0.0, 10.0), ground)

        self.assertGreater(held[0], -1.0, "the camera ended up behind the ridge")
        self.assertLess(held[0], -0.9, "the camera did not come out to the ridge")

    def test_an_eye_under_ground_with_no_way_back_is_lifted_instead(self) -> None:
        from vibestorm.viewer3d.camera import (
            GROUND_CLEARANCE_M,
            eye_clear_of_the_ground,
        )

        # The target is buried too, so there is nowhere on the line to retreat
        # to. Drawing the world from above the ground beats drawing it from
        # inside.
        held = eye_clear_of_the_ground(
            (5.0, 6.0, 1.0), (0.0, 0.0, 1.0), lambda x, y: 20.0
        )

        self.assertEqual(held[0], 5.0)
        self.assertEqual(held[1], 6.0)
        self.assertAlmostEqual(held[2], 20.0 + GROUND_CLEARANCE_M)

    def test_an_eye_sitting_on_its_target_is_lifted_rather_than_divided_by(
        self,
    ) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        held = eye_clear_of_the_ground(
            (3.0, 3.0, 0.0), (3.0, 3.0, 0.0), lambda x, y: 10.0
        )

        self.assertAlmostEqual(held[2], 10.5)

    def test_where_there_is_no_ground_there_is_no_constraint(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        eye = (-10.0, 0.0, -400.0)

        self.assertEqual(
            eye_clear_of_the_ground(eye, (0.0, 0.0, 10.0), lambda x, y: None), eye
        )


class CameraEyeTests(unittest.TestCase):
    """Which modes are held off the ground, and which are not."""

    def _camera(self, mode: str):
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.target = (0.0, 0.0, 10.0)
        camera.eye_position = (-10.0, 0.0, 10.0)
        camera.distance = 10.0
        camera.yaw = math.pi  # due west of the target
        camera.pitch = 0.0
        camera.set_mode(mode)
        camera.ground_height = _slope_west_of(-5.0, low=0.0, high=40.0)
        return camera

    def test_the_orbit_camera_is_held_off_the_ground(self) -> None:
        camera = self._camera("orbit")

        self.assertGreater(camera.eye()[0], camera.orbit_eye()[0])

    def test_the_avatar_behind_camera_is_held_off_the_ground(self) -> None:
        # `set_avatar_behind` leaves the camera in "free" mode, and that is the
        # camera the viewer starts in.
        camera = self._camera("free")

        self.assertGreater(camera.eye()[0], camera.eye_position[0])

    def test_the_first_person_camera_is_left_where_the_avatar_is(self) -> None:
        # The eye in "eye" mode is the avatar's own head. If that is inside a
        # hill the simulator put it there, and moving it would only make the
        # picture disagree with where the avatar is standing.
        camera = self._camera("eye")

        self.assertEqual(camera.eye(), camera.eye_position)

    def test_a_camera_that_has_not_been_told_the_ground_goes_where_it_is_put(
        self,
    ) -> None:
        camera = self._camera("free")
        camera.ground_height = None

        self.assertEqual(camera.eye(), camera.eye_position)

    def test_the_map_camera_looks_down_from_above_the_target(self) -> None:
        camera = self._camera("map")

        self.assertEqual(camera.eye(), (0.0, 0.0, 20.0))

    def test_the_view_matrix_is_drawn_from_the_eye_that_was_held_back(self) -> None:
        from vibestorm.viewer3d.camera import look_at

        camera = self._camera("free")

        self.assertEqual(
            camera.view_matrix(), look_at(camera.eye(), camera.target)
        )
        self.assertNotEqual(
            camera.view_matrix(), look_at(camera.eye_position, camera.target)
        )



if __name__ == "__main__":
    unittest.main()
