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
        from vibestorm.viewer3d.camera import Camera3D, REGION_SIZE_METERS

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


if __name__ == "__main__":
    unittest.main()
