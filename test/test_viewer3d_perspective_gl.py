"""Tests for PerspectiveRenderer's native GL pass (step 6 v0).

These exercise the full pipeline — shader compile, instance buffer
upload, depth test, perspective projection — by drawing a single cube
into a custom RGBA+depth framebuffer via a standalone GL context, then
reading pixels back. Tests skip cleanly when no GL is available
(headless CI without a GPU, no glcontext.x11/EGL).
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


class _GLTestBase(unittest.TestCase):
    FBO_SIZE = (64, 64)

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
        self._depth_rb = ctx.depth_renderbuffer(self.FBO_SIZE)
        self.fbo = ctx.framebuffer(
            color_attachments=[self._color_tex],
            depth_attachment=self._depth_rb,
        )
        self.fbo.use()
        ctx.viewport = (0, 0, *self.FBO_SIZE)

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self._depth_rb.release()
        self.ctx.release()

    def _read_pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        data = self.fbo.read(components=4)
        w, h = self.FBO_SIZE
        # FBO read is bottom-up; convert from top-down screen y.
        gl_y = (h - 1) - y
        offset = (gl_y * w + x) * 4
        return tuple(data[offset : offset + 4])


class PerspectiveRendererGLTests(_GLTestBase):
    def test_setup_compiles_and_allocates_resources(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)

        try:
            self.assertIsNotNone(renderer._program)
            self.assertIsNotNone(renderer._vao)
            self.assertIsNotNone(renderer._vbo)
            self.assertIsNotNone(renderer._ibo)
            self.assertIsNotNone(renderer._instance_vbo)
        finally:
            renderer.clear_caches()

    def test_render_gl_with_no_entities_is_a_no_op(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(Scene(), aspect=1.0)
            # Empty scene — framebuffer should still be black.
            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertEqual((r, g, b), (0, 0, 0))
        finally:
            renderer.clear_caches()

    def test_render_gl_draws_a_cube_at_target(self) -> None:
        # Place a unit cube tinted red at the origin and orbit camera
        # 5 m east. The center of the framebuffer should land inside
        # the cube, so it must read red.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        camera = Camera3D(
            target=(0.0, 0.0, 0.0),
            distance=5.0,
            yaw=0.0,
            pitch=0.0,
        )
        camera.set_mode("orbit")

        scene = Scene()
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=None,
            default_texture_id=None,
            name=None,
            tint=(255, 32, 32),
        )

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            aspect = self.FBO_SIZE[0] / self.FBO_SIZE[1]
            renderer.render_gl(scene, aspect=aspect)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            # Cube is red-tinted; center pixel should be dominated by red.
            self.assertGreater(r, 200, f"center pixel was {(r, g, b)}, expected red")
            self.assertLess(g, 60)
            self.assertLess(b, 60)
        finally:
            renderer.clear_caches()

    def test_aspect_zero_or_negative_is_a_no_op(self) -> None:
        # Defensive: a degenerate viewport must not raise ValueError
        # from perspective() — the renderer should just bail out.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            renderer.render_gl(Scene(), aspect=0.0)
            renderer.render_gl(Scene(), aspect=-1.0)
        finally:
            renderer.clear_caches()


class PerspectiveRendererInstanceGrowthTests(_GLTestBase):
    def test_grows_buffer_when_entity_count_exceeds_capacity(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            initial_capacity = renderer._instance_capacity
            scene = Scene()
            for i in range(initial_capacity + 5):
                scene.object_entities[i] = SceneEntity(
                    local_id=i,
                    pcode=9,
                    kind="prim",
                    position=(0.0, 0.0, 0.0),
                    scale=(1.0, 1.0, 1.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    rotation_z_radians=0.0,
                    shape=None,
                    default_texture_id=None,
                    name=None,
                    tint=(255, 255, 255),
                )

            renderer.render_gl(scene, aspect=1.0)

            self.assertGreaterEqual(renderer._instance_capacity, initial_capacity + 5)
        finally:
            renderer.clear_caches()

    def test_clear_caches_releases_gl_resources(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        renderer.clear_caches()

        self.assertIsNone(renderer._program)
        self.assertIsNone(renderer._vao)
        self.assertEqual(renderer._instance_capacity, 0)


if __name__ == "__main__":
    unittest.main()
