"""Tests for the viewer3d PerspectiveRenderer placeholder.

Step 5a only proves the renderer-swap mechanism: picking "Render: 3D"
must replace the active ViewerRenderer with PerspectiveRenderer (and
back again), without crashing the frame loop. The placeholder draws a
fill + crosshair on the software surface — moderngl bring-up is
deferred to step 5b.

These tests avoid a real GL context. They drive the renderer with a
``pygame.Surface`` (already supported by the existing 2D path) and
exercise the contract: ``update`` is a no-op, ``render`` mutates the
surface in a recognisable way, ``clear_caches`` is safe to call.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class PerspectiveRendererPlaceholderTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
        except ImportError as exc:  # pragma: no cover - viewer extra missing
            self.skipTest(f"pygame unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        # Headless dummy driver: no real display required.
        pygame.display.set_mode((1, 1))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_update_is_a_no_op(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D())
        scene = Scene()

        renderer.update(0.016, scene)

        self.assertEqual(scene.object_entities, {})
        self.assertEqual(scene.avatar_entities, {})

    def test_render_fills_world_surface_with_sky(self) -> None:
        # The world surface is now a sky backdrop only — the map tile
        # is rendered as a 3D ground quad in render_gl, not as a
        # fullscreen 2D blit.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import SKY_COLOR, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D())
        scene = Scene()
        surface = self.pygame.Surface((320, 240))
        surface.fill((255, 255, 255))

        renderer.render(surface, scene)

        for x, y in ((0, 0), (160, 120), (319, 239)):
            px = surface.get_at((x, y))
            self.assertEqual((px.r, px.g, px.b), SKY_COLOR)

    def test_render_does_not_crash_when_camera_in_orbit_mode(self) -> None:
        # The placeholder reads camera.mode/world_center/zoom for its label.
        # Switching to orbit must not break that path.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D()
        camera.set_mode("orbit")
        renderer = PerspectiveRenderer(camera)
        surface = self.pygame.Surface((320, 240))

        renderer.render(surface, Scene())

    def test_clear_caches_is_safe(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D())
        # Must be idempotent and never raise — the app calls it on shutdown.
        renderer.clear_caches()
        renderer.clear_caches()

    def test_renderer_holds_camera_reference(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D()
        renderer = PerspectiveRenderer(camera)
        camera.set_mode("orbit")

        self.assertIs(renderer.camera, camera)
        self.assertEqual(renderer.camera.mode, "orbit")


class BuildRendererTests(unittest.TestCase):
    """The HUD's render-mode strings must drive the renderer factory.

    The factory lives in viewer3d.app so the closure inside
    ``run_viewer`` stays small. The mode strings are the same constants
    the HUD emits via ``on_render_mode_change``.
    """

    def test_2d_map_returns_top_down_renderer(self) -> None:
        from vibestorm.viewer3d.app import build_renderer
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.renderer import TopDownRenderer

        renderer = build_renderer("2d-map", Camera3D())

        self.assertIsInstance(renderer, TopDownRenderer)

    def test_3d_returns_perspective_renderer(self) -> None:
        from vibestorm.viewer3d.app import build_renderer
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = build_renderer("3d", Camera3D())

        self.assertIsInstance(renderer, PerspectiveRenderer)

    def test_unknown_mode_falls_back_to_top_down(self) -> None:
        # Future render-mode strings (e.g. "2.5d") must not crash the swap;
        # falling back to the stable 2D path is the safe default.
        from vibestorm.viewer3d.app import build_renderer
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.renderer import TopDownRenderer

        renderer = build_renderer("anything-else", Camera3D())

        self.assertIsInstance(renderer, TopDownRenderer)

    def test_factory_passes_camera_through(self) -> None:
        from vibestorm.viewer3d.app import build_renderer
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        for mode in ("2d-map", "3d"):
            with self.subTest(mode=mode):
                renderer = build_renderer(mode, camera)
                self.assertIs(renderer.camera, camera)


if __name__ == "__main__":
    unittest.main()
