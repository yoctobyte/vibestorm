"""Tests for the viewer3d app's GL compositor wiring (step 5b-ii).

The full ``run_viewer`` coroutine needs a real pygame OpenGL window and
a live login; it is not unit-testable. The pieces that are unit
testable are the small module-level helpers ``allocate_frame_surfaces``
and ``composite_frame``, plus the ``PerspectiveRenderer`` placeholder's
new map-tile background path. These tests cover those.

The compositor tests use ``moderngl.create_standalone_context()`` and
skip if no GL is available.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as exc:
        return None, f"standalone GL context unavailable: {exc}"
    return ctx, None


class AllocateFrameSurfacesTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((1, 1))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_returns_two_surfaces_at_requested_size(self) -> None:
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        world, hud = allocate_frame_surfaces(self.pygame, (640, 480))

        self.assertEqual(world.get_size(), (640, 480))
        self.assertEqual(hud.get_size(), (640, 480))

    def test_hud_surface_has_per_pixel_alpha(self) -> None:
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        _, hud = allocate_frame_surfaces(self.pygame, (320, 200))
        flags = hud.get_flags()

        self.assertTrue(flags & self.pygame.SRCALPHA, f"hud flags={flags:#x}")

    def test_world_surface_is_opaque_format(self) -> None:
        # The world surface is opaque — the compositor draws it without
        # blending. SRCALPHA is the bug we want to avoid here.
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        world, _ = allocate_frame_surfaces(self.pygame, (320, 200))
        flags = world.get_flags()

        self.assertFalse(flags & self.pygame.SRCALPHA, f"world flags={flags:#x}")


class CompositeFrameTests(unittest.TestCase):
    FBO_SIZE = (16, 16)

    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        ctx, err = _try_create_context()
        if ctx is None:
            self.skipTest(err)
        self.ctx = ctx
        self._color_tex = ctx.texture(self.FBO_SIZE, components=4)
        self.fbo = ctx.framebuffer(color_attachments=[self._color_tex])
        self.fbo.use()
        ctx.viewport = (0, 0, *self.FBO_SIZE)

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self.ctx.release()

    def _read_center_pixel(self) -> tuple[int, int, int, int]:
        data = self.fbo.read(components=4)
        w, h = self.FBO_SIZE
        x = w // 2
        y = h // 2
        offset = (y * w + x) * 4
        return tuple(data[offset : offset + 4])

    def test_draws_world_then_hud(self) -> None:
        # World = solid red, HUD = transparent middle, opaque green
        # band. composite_frame should leave the center as pure red
        # (HUD's transparent center doesn't change anything).
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            world.fill((255, 0, 0))

            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
            hud.fill((0, 0, 0, 0))  # fully transparent everywhere

            composite_frame(compositor, world, hud)

            r, g, b, _a = self._read_center_pixel()
            self.assertGreater(r, 240)
            self.assertLess(g, 10)
            self.assertLess(b, 10)
        finally:
            compositor.release()

    def test_hud_alpha_overlays_world(self) -> None:
        # HUD = opaque blue everywhere. Center pixel after composite
        # should be blue, not red.
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            world.fill((255, 0, 0))
            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
            hud.fill((0, 0, 220, 255))

            composite_frame(compositor, world, hud)

            r, g, b, _a = self._read_center_pixel()
            self.assertLess(r, 20)
            self.assertLess(g, 20)
            self.assertGreater(b, 200)
        finally:
            compositor.release()

    def test_uploads_under_world_and_hud_names(self) -> None:
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)

            composite_frame(compositor, world, hud)

            self.assertTrue(compositor.has_texture("world"))
            self.assertTrue(compositor.has_texture("hud"))
        finally:
            compositor.release()


class PerspectiveTileBackgroundTests(unittest.TestCase):
    """The placeholder reads scene.map_tile_path and blits the PNG
    fullscreen when one is available. Without a tile, it falls back to
    PLACEHOLDER_BG. This is what the compositor uploads as the textured
    quad in step 5b-ii."""

    def setUp(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((1, 1))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_falls_back_to_placeholder_bg_without_tile(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PLACEHOLDER_BG, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D())
        surface = self.pygame.Surface((64, 64))

        renderer.render(surface, Scene())

        corner = surface.get_at((1, 1))
        self.assertEqual((corner.r, corner.g, corner.b), PLACEHOLDER_BG)

    def test_blits_map_tile_when_path_set(self) -> None:
        # Write a small PNG with a distinctive corner colour, point Scene
        # at it, then verify the rendered surface's corner matches.
        import tempfile
        from pathlib import Path

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PLACEHOLDER_BG, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile = self.pygame.Surface((32, 32))
        tile.fill((30, 200, 90))  # distinctive green
        with tempfile.TemporaryDirectory() as tmp:
            tile_path = Path(tmp) / "region.png"
            self.pygame.image.save(tile, str(tile_path))

            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(Camera3D())
            surface = self.pygame.Surface((64, 64))

            renderer.render(surface, scene)

            corner = surface.get_at((1, 1))
            # Tile corner should now be visible (some shade of green),
            # not the dark PLACEHOLDER_BG.
            self.assertNotEqual((corner.r, corner.g, corner.b), PLACEHOLDER_BG)
            self.assertGreater(corner.g, 100)

    def test_clear_caches_drops_loaded_tile(self) -> None:
        import tempfile
        from pathlib import Path

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile = self.pygame.Surface((8, 8))
        tile.fill((100, 100, 100))
        with tempfile.TemporaryDirectory() as tmp:
            tile_path = Path(tmp) / "region.png"
            self.pygame.image.save(tile, str(tile_path))

            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(Camera3D())
            renderer.render(self.pygame.Surface((32, 32)), scene)
            self.assertIn(tile_path, renderer._tile_cache)

            renderer.clear_caches()
            self.assertEqual(renderer._tile_cache, {})


if __name__ == "__main__":
    unittest.main()
