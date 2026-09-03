"""Tests for the viewer3d GLCompositor.

The compositor only depends on a ``moderngl.Context`` — it doesn't open
a window. Tests use ``moderngl.create_standalone_context()`` plus a
custom framebuffer so output pixels can be read back and verified.

Headless CI without a GPU may be unable to create a standalone
context (no EGL, no glcontext.x11, etc.). All tests guard on that and
skip cleanly rather than failing the suite. On the developer's box
(NVIDIA + glcontext) they exercise real GL.
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
    except Exception as exc:  # glcontext failures, no GPU, etc.
        return None, f"standalone GL context unavailable: {exc}"
    return ctx, None


class _GLTestBase(unittest.TestCase):
    """Shared setup that allocates a context + a small RGBA FBO."""

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

    def read_pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        # fbo.read returns bytes in (R, G, B, A) order, row-major from bottom.
        # We return what's at the given pygame-style (x, y) where y=0 is top.
        data = self.fbo.read(components=4)
        w, h = self.FBO_SIZE
        # Convert pygame y (top=0) to GL y (bottom=0).
        gl_y = (h - 1) - y
        offset = (gl_y * w + x) * 4
        r, g, b, a = data[offset : offset + 4]
        return (r, g, b, a)


class GLCompositorBasicTests(_GLTestBase):
    def test_construct_compiles_program(self) -> None:
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            self.assertIs(compositor.ctx, self.ctx)
        finally:
            compositor.release()

    def test_upload_draw_solid_red_surface_fills_framebuffer(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            surface = pygame.Surface(self.FBO_SIZE)
            surface.fill((220, 30, 40))

            compositor.clear((0.0, 0.0, 0.0, 1.0))
            compositor.upload_surface("world", surface)
            compositor.draw("world")

            r, g, b, a = self.read_pixel(8, 8)
            # Allow ±1 for filtering rounding even on linear sample.
            self.assertAlmostEqual(r, 220, delta=2)
            self.assertAlmostEqual(g, 30, delta=2)
            self.assertAlmostEqual(b, 40, delta=2)
            self.assertEqual(a, 255)
        finally:
            compositor.release()

    def test_upload_preserves_y_orientation(self) -> None:
        # Pygame surface top row red, bottom row blue. After upload+draw,
        # the top of the framebuffer should be red — confirming the UV
        # flip in the quad is correct.
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        w, h = self.FBO_SIZE
        surface = pygame.Surface(self.FBO_SIZE)
        surface.fill((0, 0, 255))  # bottom half blue
        for x in range(w):
            for y in range(h // 2):
                surface.set_at((x, y), (255, 0, 0))  # top half red

        compositor = GLCompositor(self.ctx)
        try:
            compositor.clear((0.0, 0.0, 0.0, 1.0))
            compositor.upload_surface("world", surface)
            compositor.draw("world")

            top = self.read_pixel(8, 2)
            bottom = self.read_pixel(8, h - 3)
            self.assertGreater(top[0], 200, f"top pixel was {top}, expected red")
            self.assertGreater(bottom[2], 200, f"bottom pixel was {bottom}, expected blue")
        finally:
            compositor.release()

    def test_alpha_blending_overlays_hud(self) -> None:
        # World = solid red, HUD = semi-transparent green. Drawing world
        # opaque then HUD with alpha=True should yield a yellowish-green
        # blend everywhere, not the green-on-black of unblended draw.
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        world = pygame.Surface(self.FBO_SIZE)
        world.fill((255, 0, 0))

        hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
        hud.fill((0, 200, 0, 128))  # 50% green

        compositor = GLCompositor(self.ctx)
        try:
            compositor.clear((0.0, 0.0, 0.0, 1.0))
            compositor.upload_surface("world", world)
            compositor.upload_surface("hud", hud)
            compositor.draw("world", alpha=False)
            compositor.draw("hud", alpha=True)

            r, g, b, a = self.read_pixel(8, 8)
            # 50% blend of (255,0,0) over (0,200,0):
            # out.rgb = src.rgb * src.a + dst.rgb * (1 - src.a)
            # r ≈ 0*0.5 + 255*0.5 = ~128
            # g ≈ 200*0.5 + 0*0.5 = ~100
            self.assertAlmostEqual(r, 128, delta=10)
            self.assertAlmostEqual(g, 100, delta=10)
            self.assertLess(b, 10)
            # Output alpha is also blended by SRC_ALPHA/ONE_MINUS_SRC_ALPHA
            # — that's fine for swapchain compositing where dest alpha is
            # ignored at present. Just confirm we wrote something.
            self.assertGreater(a, 0)
        finally:
            compositor.release()


class GLCompositorTextureCacheTests(_GLTestBase):
    def test_reupload_same_size_reuses_texture_object(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            s1 = pygame.Surface(self.FBO_SIZE)
            s1.fill((10, 20, 30))
            s2 = pygame.Surface(self.FBO_SIZE)
            s2.fill((40, 50, 60))

            compositor.upload_surface("world", s1)
            tex_before = compositor._textures["world"]
            compositor.upload_surface("world", s2)
            tex_after = compositor._textures["world"]

            self.assertIs(tex_before, tex_after)
        finally:
            compositor.release()

    def test_reupload_different_size_reallocates(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            small = pygame.Surface((8, 8))
            small.fill((0, 0, 0))
            large = pygame.Surface(self.FBO_SIZE)
            large.fill((0, 0, 0))

            compositor.upload_surface("world", small)
            self.assertEqual(compositor.texture_size("world"), (8, 8))
            compositor.upload_surface("world", large)
            self.assertEqual(compositor.texture_size("world"), self.FBO_SIZE)
        finally:
            compositor.release()


class GLCompositorAccessorTests(_GLTestBase):
    def test_has_texture_reflects_uploads(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            self.assertFalse(compositor.has_texture("world"))
            compositor.upload_surface("world", pygame.Surface((4, 4)))
            self.assertTrue(compositor.has_texture("world"))
            self.assertEqual(compositor.texture_size("world"), (4, 4))
            self.assertIsNone(compositor.texture_size("missing"))
        finally:
            compositor.release()

    def test_draw_unknown_name_raises(self) -> None:
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            with self.assertRaises(KeyError):
                compositor.draw("never-uploaded")
        finally:
            compositor.release()

    def test_release_empties_texture_table(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        compositor.upload_surface("world", pygame.Surface((4, 4)))
        compositor.upload_surface("hud", pygame.Surface((4, 4), pygame.SRCALPHA))

        compositor.release()

        self.assertEqual(compositor._textures, {})


if __name__ == "__main__":
    unittest.main()


class GLCompositorZeroCopyUploadTests(_GLTestBase):
    """Uploading straight from pygame's memory must not permute the channels.

    ``pygame.image.tobytes(surface, "RGBA")`` converted and copied the whole
    surface on the CPU every frame -- 11.2 ms at 1920x1080, twice per frame.
    The bytes are already in a layout GL can read, so the compositor hands
    them over as-is and swizzles in the shader. The colours below are
    deliberately asymmetric: a red/blue swap is exactly what this can get
    wrong, and any symmetric colour would hide it.
    """

    def _draw(self, surface, *, alpha: bool):
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            compositor.upload_surface("probe", surface)
            self.ctx.clear(0.0, 0.0, 0.0, 1.0)
            compositor.draw("probe", alpha=alpha)
            return self.read_pixel(8, 8)
        finally:
            compositor.release()

    def test_alpha_surface_keeps_its_channels(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import _is_bgra_in_memory

        surface = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
        surface.fill((200, 120, 40, 255))
        self.assertTrue(
            _is_bgra_in_memory(surface),
            "an SRCALPHA surface should take the zero-copy path on this host",
        )

        r, g, b, a = self._draw(surface, alpha=True)
        self.assertEqual((r, g, b), (200, 120, 40))
        self.assertEqual(a, 255)

    def test_every_channel_is_distinguishable(self) -> None:
        import pygame

        # Pure red and pure blue in turn: a swizzle bug turns one into the
        # other, which a single mixed colour could still coincidentally pass.
        for colour in ((255, 0, 0, 255), (0, 0, 255, 255), (0, 255, 0, 255)):
            with self.subTest(colour=colour):
                surface = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
                surface.fill(colour)
                self.assertEqual(self._draw(surface, alpha=True), colour)

    def test_converted_fallback_agrees_with_the_fast_path(self) -> None:
        import pygame

        from vibestorm.viewer3d.gl_compositor import _is_bgra_in_memory

        # A surface without an alpha mask does not qualify for the fast path,
        # and must still come out with the same colours.
        opaque = pygame.Surface(self.FBO_SIZE)
        opaque.fill((200, 120, 40))
        self.assertFalse(_is_bgra_in_memory(opaque))

        alpha_surface = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
        alpha_surface.fill((200, 120, 40, 255))

        slow = self._draw(opaque, alpha=False)
        fast = self._draw(alpha_surface, alpha=False)
        self.assertEqual(slow[:3], (200, 120, 40))
        self.assertEqual(slow[:3], fast[:3])

    def test_switching_a_name_between_layouts_updates_the_swizzle(self) -> None:
        import pygame

        # The flag is per-texture-name and must follow the last upload, not
        # stick from the first one.
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        fast = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
        fast.fill((10, 90, 210, 255))
        slow = pygame.Surface(self.FBO_SIZE)
        slow.fill((10, 90, 210))

        compositor = GLCompositor(self.ctx)
        try:
            for surface in (fast, slow, fast):
                compositor.upload_surface("probe", surface)
                self.ctx.clear(0.0, 0.0, 0.0, 1.0)
                compositor.draw("probe", alpha=False)
                self.assertEqual(self.read_pixel(8, 8)[:3], (10, 90, 210))
        finally:
            compositor.release()
