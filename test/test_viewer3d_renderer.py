"""Tests for the viewer3d renderer abstraction.

The TopDownRenderer wraps the existing render_scene function. These
tests stay light: they verify the protocol contract, not pixel output.
The pygame-dependent draw path is covered indirectly via the live
viewer.
"""

import unittest
from unittest.mock import MagicMock, patch


class TopDownRendererTests(unittest.TestCase):
    def test_update_is_no_op(self) -> None:
        from vibestorm.viewer3d.camera import Camera
        from vibestorm.viewer3d.renderer import TopDownRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600))
        renderer = TopDownRenderer(camera)
        scene = Scene()

        # Update should not raise and not mutate scene.
        renderer.update(0.016, scene)
        self.assertEqual(scene.object_entities, {})

    def test_render_delegates_to_render_scene(self) -> None:
        from vibestorm.viewer3d.camera import Camera
        from vibestorm.viewer3d.renderer import TopDownRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600))
        renderer = TopDownRenderer(camera)
        scene = Scene()
        fake_surface = MagicMock(name="surface")

        with patch("vibestorm.viewer3d.renderer.render_scene") as mock_render:
            renderer.render(fake_surface, scene)

        mock_render.assert_called_once_with(fake_surface, camera, scene)

    def test_clear_caches_clears_tile_cache(self) -> None:
        from vibestorm.viewer3d.camera import Camera
        from vibestorm.viewer3d.renderer import TopDownRenderer

        camera = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=(800, 600))
        renderer = TopDownRenderer(camera)

        with patch("vibestorm.viewer3d.renderer.clear_tile_cache") as mock_clear:
            renderer.clear_caches()

        mock_clear.assert_called_once_with()

    def test_renderer_holds_camera_reference(self) -> None:
        # The camera passed to the constructor is used live (not copied), so
        # external mutations are visible to subsequent renders. This matters
        # because the app pans/zooms the camera between frames.
        from vibestorm.viewer3d.camera import Camera
        from vibestorm.viewer3d.renderer import TopDownRenderer

        camera = Camera(world_center=(0.0, 0.0), zoom=1.0, screen_size=(800, 600))
        renderer = TopDownRenderer(camera)
        camera.zoom = 4.0

        self.assertIs(renderer.camera, camera)
        self.assertEqual(renderer.camera.zoom, 4.0)


if __name__ == "__main__":
    unittest.main()
