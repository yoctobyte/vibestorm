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
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

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


class AssetDataReadyHandlerTests(unittest.TestCase):
    def test_uses_five_field_asset_map_entry(self) -> None:
        from vibestorm.bus.events import AssetDataReady
        from vibestorm.viewer3d.app import _make_asset_data_ready_handler

        asset_id = UUID("11111111-1111-4111-8111-111111111111")

        class FakeHud:
            _inspector_item_asset_map = {
                "Script [lsltext]": (
                    asset_id,
                    10,
                    "Script",
                    UUID("22222222-2222-4222-8222-222222222222"),
                    UUID("33333333-3333-4333-8333-333333333333"),
                )
            }

            def __init__(self) -> None:
                self.calls = []

            def show_asset_data(self, asset_id, asset_type, data, *, item_name=""):  # type: ignore[no-untyped-def]
                self.calls.append((asset_id, asset_type, data, item_name))

        hud = FakeHud()
        handler = _make_asset_data_ready_handler(hud)

        handler(
            AssetDataReady(
                region_handle=1,
                asset_id=asset_id,
                asset_type=10,
                data=b"default {}",
            )
        )

        self.assertEqual(hud.calls, [(asset_id, 10, b"default {}", "Script")])

    def test_writes_pending_asset_saves(self) -> None:
        from vibestorm.bus.events import AssetDataReady
        from vibestorm.viewer3d.app import PendingAssetSave, _make_asset_data_ready_handler
        from vibestorm.viewer3d.hud import ObjectAssetSelection

        asset_id = UUID("11111111-1111-4111-8111-111111111111")

        class FakeHud:
            _inspector_item_asset_map = {}

            def show_asset_data(self, asset_id, asset_type, data, *, item_name=""):  # type: ignore[no-untyped-def]
                pass

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scripts" / "Main.lsl"
            pending = {
                asset_id: [
                    PendingAssetSave(
                        selection=ObjectAssetSelection(
                            item_key="Main [lsltext]",
                            asset_id=asset_id,
                            asset_type=10,
                            item_name="Main",
                        ),
                        target_path=target,
                    )
                ]
            }
            handler = _make_asset_data_ready_handler(FakeHud(), pending)

            handler(
                AssetDataReady(
                    region_handle=1,
                    asset_id=asset_id,
                    asset_type=10,
                    data=b"default { state_entry() {} }",
                )
            )

            self.assertEqual(target.read_bytes(), b"default { state_entry() {} }")
            self.assertEqual(pending, {})


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


class PerspectiveSkyBackgroundTests(unittest.TestCase):
    """The 3D renderer fills the world surface with a sky colour and
    leaves the map tile to the GL ground quad — earlier versions blitted
    the tile fullscreen, which hid the actual 3D ground behind a
    look-alike."""

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

    def test_world_surface_is_sky_when_no_tile(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import SKY_COLOR, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D())
        surface = self.pygame.Surface((64, 64))

        renderer.render(surface, Scene())

        corner = surface.get_at((1, 1))
        self.assertEqual((corner.r, corner.g, corner.b), SKY_COLOR)

    def test_world_surface_stays_sky_even_when_map_tile_path_set(self) -> None:
        # The map tile lives on the GL ground from step 6b; the world
        # surface must NOT blit it as a 2D background anymore.
        import tempfile
        from pathlib import Path

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import SKY_COLOR, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile = self.pygame.Surface((32, 32))
        tile.fill((30, 200, 90))
        with tempfile.TemporaryDirectory() as tmp:
            tile_path = Path(tmp) / "region.png"
            self.pygame.image.save(tile, str(tile_path))

            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(Camera3D())
            surface = self.pygame.Surface((64, 64))

            renderer.render(surface, scene)

            corner = surface.get_at((1, 1))
            self.assertEqual((corner.r, corner.g, corner.b), SKY_COLOR)


if __name__ == "__main__":
    unittest.main()
