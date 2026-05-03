import math
import unittest

from vibestorm.viewer.camera import REGION_SIZE_METERS, Camera


class CameraTransformTests(unittest.TestCase):
    def test_world_center_maps_to_screen_center(self) -> None:
        cam = Camera(world_center=(128.0, 128.0), zoom=4.0, screen_size=(1024, 768))
        sx, sy = cam.world_to_screen(128.0, 128.0)
        self.assertAlmostEqual(sx, 512.0)
        self.assertAlmostEqual(sy, 384.0)

    def test_y_axis_is_flipped(self) -> None:
        cam = Camera(world_center=(0.0, 0.0), zoom=2.0, screen_size=(800, 600))
        # World +y is north → smaller screen y (toward top of screen).
        _, sy_north = cam.world_to_screen(0.0, 50.0)
        _, sy_south = cam.world_to_screen(0.0, -50.0)
        self.assertLess(sy_north, sy_south)

    def test_round_trip_world_screen_world(self) -> None:
        cam = Camera(world_center=(50.0, 75.0), zoom=3.5, screen_size=(640, 480))
        for wx, wy in [(0.0, 0.0), (50.0, 50.0), (100.0, -25.0), (-30.0, 10.0)]:
            sx, sy = cam.world_to_screen(wx, wy)
            wx2, wy2 = cam.screen_to_world(sx, sy)
            self.assertAlmostEqual(wx, wx2, places=3)
            self.assertAlmostEqual(wy, wy2, places=3)

    def test_pan_screen_shifts_world_center_inversely(self) -> None:
        cam = Camera(world_center=(100.0, 100.0), zoom=2.0, screen_size=(800, 600))
        cam.pan_screen(20.0, 0.0)
        # +20 px right means world center moves left by 10m (20 / zoom).
        self.assertAlmostEqual(cam.world_center[0], 100.0 - 10.0)
        self.assertAlmostEqual(cam.world_center[1], 100.0)

    def test_zoom_at_screen_keeps_anchor_under_cursor(self) -> None:
        cam = Camera(world_center=(128.0, 128.0), zoom=4.0, screen_size=(1024, 768))
        anchor_screen = (256.0, 192.0)
        anchor_world_before = cam.screen_to_world(*anchor_screen)
        cam.zoom_at_screen(*anchor_screen, factor=1.5)
        anchor_screen_after = cam.world_to_screen(*anchor_world_before)
        self.assertAlmostEqual(anchor_screen_after[0], anchor_screen[0], places=2)
        self.assertAlmostEqual(anchor_screen_after[1], anchor_screen[1], places=2)
        self.assertAlmostEqual(cam.zoom, 6.0)

    def test_zoom_at_rejects_non_positive_factor(self) -> None:
        cam = Camera(world_center=(0.0, 0.0), zoom=1.0, screen_size=(100, 100))
        with self.assertRaises(ValueError):
            cam.zoom_at_screen(50, 50, 0.0)

    def test_fit_region_uses_smaller_dimension(self) -> None:
        cam = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=(1024, 768))
        cam.fit_region(padding_px=0)
        self.assertAlmostEqual(cam.zoom, 768 / REGION_SIZE_METERS)

    def test_fit_region_with_padding(self) -> None:
        cam = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 800))
        cam.fit_region(padding_px=50)
        self.assertAlmostEqual(cam.zoom, (800 - 100) / REGION_SIZE_METERS)

    def test_center_on(self) -> None:
        cam = Camera(world_center=(0.0, 0.0), zoom=1.0, screen_size=(100, 100))
        cam.center_on(42.5, -3.25)
        self.assertEqual(cam.world_center, (42.5, -3.25))

    def test_set_screen_size_clamps_to_minimum_one(self) -> None:
        cam = Camera(world_center=(0.0, 0.0), zoom=1.0, screen_size=(100, 100))
        cam.set_screen_size((0, -10))
        self.assertEqual(cam.screen_size, (1, 1))

    def test_pan_does_not_change_zoom(self) -> None:
        cam = Camera(world_center=(0.0, 0.0), zoom=4.0, screen_size=(100, 100))
        cam.pan_screen(50, -25)
        self.assertEqual(cam.zoom, 4.0)


if __name__ == "__main__":
    unittest.main()
