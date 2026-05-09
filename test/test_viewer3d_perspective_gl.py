"""Tests for PerspectiveRenderer's native GL pass (step 6 v0).

These exercise the full pipeline — shader compile, instance buffer
upload, depth test, perspective projection — by drawing a single cube
into a custom RGBA+depth framebuffer via a standalone GL context, then
reading pixels back. Tests skip cleanly when no GL is available
(headless CI without a GPU, no glcontext.x11/EGL).
"""

import math
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
            self.assertIsNotNone(renderer._instance_vbo)
            # Step 7b ships the full primitive library — every shape
            # has its own VBO/IBO/VAO bound against the shared
            # instance buffer.
            for key in ("cube", "sphere", "cylinder", "torus", "prism"):
                mesh = renderer._shape_meshes.get(key)
                self.assertIsNotNone(mesh, f"missing GL mesh for {key!r}")
                self.assertGreater(mesh.index_count, 0)
        finally:
            renderer.clear_caches()

    def test_render_gl_with_no_entities_is_a_no_op(self) -> None:
        # Camera positioned outside the region square (off the SW corner)
        # looking horizontally — ground/water planes (which are bounded
        # to the 256x256 region) don't intersect any FOV ray, so an
        # empty scene paints nothing.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(target=(-100.0, -100.0, 5.0), distance=5.0, yaw=0.0, pitch=0.0)
        camera.set_mode("orbit")

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(Scene(), aspect=1.0)
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
        self.assertEqual(renderer._shape_meshes, {})
        self.assertEqual(renderer._instance_capacity, 0)
        self.assertIsNone(renderer._ground_program)
        self.assertIsNone(renderer._ground_vao)
        self.assertIsNone(renderer._ground_texture)
        self.assertIsNone(renderer._water_program)
        self.assertIsNone(renderer._water_vao)


def _write_solid_tile(color: tuple[int, int, int], size: int = 4) -> Path:
    """Save a small solid-colour PNG and return its path. Uses pygame so
    the loader path in PerspectiveRenderer is exercised end-to-end."""
    import pygame

    surface = pygame.Surface((size, size))
    surface.fill(color)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    pygame.image.save(surface, path)
    return Path(path)


class PerspectiveRendererGroundTests(_GLTestBase):
    """Region floor (textured quad at Z=0) rendering."""

    def _ground_test_camera(self):
        """Eye below water (Z<20) looking nearly straight down at the
        ground centre — keeps the ground in view while keeping the
        water plane behind/above the camera, so the test reads the
        ground colour without water tinting it.
        """
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=15.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")
        return camera

    def test_ground_renders_textured_quad_when_map_tile_path_set(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile_path = _write_solid_tile((0, 200, 0))  # bright green
        try:
            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(self._ground_test_camera(), ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)

                r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
                self.assertGreater(g, 150, f"center should sample green tile; got {(r, g, b)}")
                self.assertLess(r, 60)
                self.assertLess(b, 60)

                self.assertIsNotNone(renderer._ground_texture)
                self.assertEqual(renderer._ground_texture_path, tile_path)
            finally:
                renderer.clear_caches()
        finally:
            tile_path.unlink(missing_ok=True)

    def test_ground_skipped_when_map_tile_path_is_none(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(self._ground_test_camera(), ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(Scene(), aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertEqual((r, g, b), (0, 0, 0))
            self.assertIsNone(renderer._ground_texture)
        finally:
            renderer.clear_caches()

    def test_terrain_uses_fallback_texture_when_map_tile_path_is_none(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        scene = Scene()
        scene.terrain_heightmap = RegionHeightmap(
            width=2,
            height=2,
            samples=[0.0, 0.0, 0.0, 0.0],
            revision=1,
        )

        renderer = PerspectiveRenderer(self._ground_test_camera(), ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertGreater(g, 70, f"fallback terrain should be greenish; got {(r, g, b)}")
            self.assertIsNotNone(renderer._ground_texture)
            self.assertIsNone(renderer._ground_texture_path)
            self.assertIsNotNone(renderer._terrain_vao)
        finally:
            renderer.clear_caches()

    def test_terrain_uses_map_tile_texture_when_heightmap_exists(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        tile_path = _write_solid_tile((20, 40, 220))
        try:
            scene = Scene()
            scene.map_tile_path = tile_path
            scene.water_height = -10.0
            scene.terrain_heightmap = RegionHeightmap(
                width=2,
                height=2,
                samples=[0.0, 0.0, 0.0, 0.0],
                revision=1,
            )

            renderer = PerspectiveRenderer(self._ground_test_camera(), ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)

                r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
                self.assertGreater(b, 150, f"terrain should sample blue tile; got {(r, g, b)}")
                self.assertLess(r, 80)
            finally:
                renderer.clear_caches()
        finally:
            tile_path.unlink(missing_ok=True)

    def test_ground_re_uploads_when_path_changes(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile_a = _write_solid_tile((200, 0, 0))
        tile_b = _write_solid_tile((0, 0, 200))
        try:
            renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
            try:
                scene = Scene()
                scene.map_tile_path = tile_a
                renderer.render_gl(scene, aspect=1.0)
                first_path = renderer._ground_texture_path

                scene.map_tile_path = tile_b
                renderer.render_gl(scene, aspect=1.0)
                second_path = renderer._ground_texture_path

                self.assertEqual(first_path, tile_a)
                self.assertEqual(second_path, tile_b)
            finally:
                renderer.clear_caches()
        finally:
            tile_a.unlink(missing_ok=True)
            tile_b.unlink(missing_ok=True)


class PerspectiveRendererShapeDispatchTests(_GLTestBase):
    """Step 7b: per-shape dispatch in render_gl."""

    @staticmethod
    def _entity(local_id: int, shape, tint=(40, 200, 60)):
        from vibestorm.viewer3d.scene import SceneEntity

        return SceneEntity(
            local_id=local_id,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=shape,
            default_texture_id=None,
            name=None,
            tint=tint,
        )

    def _render_shape_at_origin(self, shape):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(0.0, 0.0, 0.0),
            distance=5.0,
            yaw=0.0,
            pitch=0.0,
        )
        camera.set_mode("orbit")

        scene = Scene()
        scene.object_entities[1] = self._entity(1, shape)

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
        finally:
            renderer.clear_caches()

    def test_sphere_shape_renders_tinted_pixels(self) -> None:
        r, g, b, _ = self._render_shape_at_origin("sphere")
        self.assertGreater(g, 150, f"sphere center should be green-tinted; got {(r, g, b)}")
        self.assertLess(r, 80)
        self.assertLess(b, 100)

    def test_cylinder_shape_renders_tinted_pixels(self) -> None:
        r, g, b, _ = self._render_shape_at_origin("cylinder")
        self.assertGreater(g, 150, f"cylinder center should be green-tinted; got {(r, g, b)}")

    def test_prism_shape_renders_tinted_pixels(self) -> None:
        r, g, b, _ = self._render_shape_at_origin("prism")
        self.assertGreater(g, 150, f"prism center should be green-tinted; got {(r, g, b)}")

    def test_unknown_shape_falls_back_to_cube(self) -> None:
        # An unknown shape string must fall back to the default cube
        # mesh, not raise — defensive against ObjectUpdate path/profile
        # combinations the classifier hasn't categorised yet.
        r, g, b, _ = self._render_shape_at_origin("not-a-real-shape")
        self.assertGreater(g, 150)

    def test_shape_none_falls_back_to_cube(self) -> None:
        # Avatars currently leave shape=None; they must still render.
        r, g, b, _ = self._render_shape_at_origin(None)
        self.assertGreater(g, 150)

    def test_default_texture_path_overrides_entity_tint(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        texture_id = UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
        texture_path = _write_solid_tile((20, 30, 230))
        try:
            camera = Camera3D(
                target=(0.0, 0.0, 0.0),
                distance=5.0,
                yaw=0.0,
                pitch=0.0,
            )
            camera.set_mode("orbit")
            scene = Scene()
            scene.texture_paths[texture_id] = texture_path
            scene.object_entities[1] = self._entity(1, None, tint=(255, 255, 255))
            entity = scene.object_entities[1]
            scene.object_entities[1] = entity.__class__(
                local_id=entity.local_id,
                pcode=entity.pcode,
                kind=entity.kind,
                position=entity.position,
                scale=entity.scale,
                rotation=entity.rotation,
                rotation_z_radians=entity.rotation_z_radians,
                name=entity.name,
                default_texture_id=texture_id,
                shape=entity.shape,
                tint=entity.tint,
            )

            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)
                r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
                self.assertGreater(b, 140, f"cube should sample blue texture; got {(r, g, b)}")
                self.assertLess(r, 80)
            finally:
                renderer.clear_caches()
        finally:
            texture_path.unlink(missing_ok=True)


class PerspectiveRendererWaterTests(_GLTestBase):
    """Step: water plane at SL's default sea level (Z=20)."""

    def test_water_plane_renders_translucent_blue_when_camera_looks_down(self) -> None:
        # Camera high above the region centre, pitched almost straight
        # down. Without a map_tile_path the ground stays untextured, so
        # only water draws — center pixel reads water alpha-blended over
        # the cleared (black) framebuffer.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import (
            WATER_TINT_RGB,
            PerspectiveRenderer,
        )
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=200.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            scene = Scene()
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            # Water alpha-blended over black: each channel ≈ tint * alpha * 255.
            wr, wg, wb = WATER_TINT_RGB
            wa = scene.water_alpha
            expected = (round(wr * wa * 255), round(wg * wa * 255), round(wb * wa * 255))
            self.assertAlmostEqual(r, expected[0], delta=12)
            self.assertAlmostEqual(g, expected[1], delta=12)
            self.assertAlmostEqual(b, expected[2], delta=12)

            self.assertIsNotNone(renderer._water_program)
            self.assertIsNotNone(renderer._water_vao)
        finally:
            renderer.clear_caches()

    def test_water_plane_respects_scene_alpha(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import WATER_TINT_RGB, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=200.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")
        scene = Scene(water_alpha=0.9)

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            wr, wg, wb = WATER_TINT_RGB
            expected = (round(wr * 0.9 * 255), round(wg * 0.9 * 255), round(wb * 0.9 * 255))
            self.assertAlmostEqual(r, expected[0], delta=12)
            self.assertAlmostEqual(g, expected[1], delta=12)
            self.assertAlmostEqual(b, expected[2], delta=12)
        finally:
            renderer.clear_caches()

    def test_water_plane_can_be_hidden(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=200.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")
        scene = Scene(render_water=False)

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertEqual((r, g, b), (0, 0, 0))
        finally:
            renderer.clear_caches()

    def test_water_tints_submerged_ground_when_visible(self) -> None:
        # Camera above water looking down at green ground. Water (Z=20)
        # sits between camera and ground (Z=0); alpha blend pulls the
        # ground green toward water blue.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile_path = _write_solid_tile((0, 200, 0))
        try:
            camera = Camera3D(
                target=(128.0, 128.0, 0.0),
                distance=100.0,
                yaw=0.0,
                pitch=math.pi / 2 - 0.1,
            )
            camera.set_mode("orbit")

            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)

                r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
                # Green ground tinted by translucent blue water:
                # green should still dominate but blue gains and red
                # picks up a small contribution from the water tint.
                self.assertGreater(g, 80, f"submerged green should still show; got {(r, g, b)}")
                self.assertLess(g, 200, f"green should be muted by water; got {(r, g, b)}")
                self.assertGreater(b, 30, f"water tint should add blue; got {(r, g, b)}")
            finally:
                renderer.clear_caches()
        finally:
            tile_path.unlink(missing_ok=True)

    def test_water_plane_uses_scene_water_height(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=100.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")

        scene = Scene()
        scene.water_height = 6.5

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            renderer.render_gl(scene, aspect=1.0)

            self.assertAlmostEqual(renderer._water_height, 6.5)
        finally:
            renderer.clear_caches()


class TerrainMeshTests(unittest.TestCase):
    def test_terrain_mesh_from_heightmap_builds_vertices_and_indices(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        vertices, indices = terrain_mesh_from_heightmap(
            (1.0, 2.0, 3.0, 4.0), width=2, height=2, size_m=10.0
        )

        self.assertEqual(len(vertices), 4 * 5)
        self.assertEqual(vertices[:5], (0.0, 0.0, 1.0, 0.0, 1.0))
        self.assertEqual(vertices[-5:], (10.0, 10.0, 4.0, 1.0, 0.0))
        self.assertEqual(indices, (0, 1, 3, 0, 3, 2))

    def test_terrain_mesh_applies_z_scale(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        vertices, _indices = terrain_mesh_from_heightmap(
            (1.0, 2.0, 3.0, 4.0), width=2, height=2, size_m=10.0, z_scale=10.0
        )

        self.assertEqual(vertices[2], 10.0)
        self.assertEqual(vertices[-3], 40.0)

    def test_terrain_mesh_validates_sample_count(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        with self.assertRaises(ValueError):
            terrain_mesh_from_heightmap((1.0, 2.0, 3.0), width=2, height=2)

    def test_terrain_line_indices_build_grid_edges(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_line_indices

        indices = terrain_line_indices(3, 2)

        self.assertEqual(
            indices,
            (
                0, 1, 1, 2,
                3, 4, 4, 5,
                0, 3, 1, 4, 2, 5,
            ),
        )


class LightingDirectionTests(unittest.TestCase):
    def test_lighting_direction_uses_normalized_scene_sun_direction(self) -> None:
        from vibestorm.viewer3d.perspective import lighting_direction
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.sun_direction = (10.0, 0.0, 0.0)

        self.assertEqual(lighting_direction(scene), (1.0, 0.0, 0.0))

    def test_lighting_direction_falls_back_when_direction_is_zero(self) -> None:
        from vibestorm.viewer3d.perspective import DEFAULT_SUN_DIRECTION, lighting_direction
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.sun_direction = (0.0, 0.0, 0.0)

        actual = lighting_direction(scene)
        length = math.sqrt(sum(component * component for component in actual))
        self.assertAlmostEqual(length, 1.0)
        self.assertGreater(actual[2], 0.0)
        self.assertNotEqual(actual, DEFAULT_SUN_DIRECTION)


class GeneratedTextureUVTests(unittest.TestCase):
    def test_x_facing_face_uses_yz_plane(self) -> None:
        from vibestorm.viewer3d.perspective import generated_texture_uv

        self.assertEqual(
            generated_texture_uv((0.5, -0.25, 0.25), (1.0, 0.0, 0.0)),
            (0.75, 0.75),
        )

    def test_y_facing_face_uses_xz_plane(self) -> None:
        from vibestorm.viewer3d.perspective import generated_texture_uv

        self.assertEqual(
            generated_texture_uv((0.25, 0.5, -0.25), (0.0, 1.0, 0.0)),
            (0.75, 0.25),
        )

    def test_z_facing_face_uses_xy_plane(self) -> None:
        from vibestorm.viewer3d.perspective import generated_texture_uv

        self.assertEqual(
            generated_texture_uv((-0.25, 0.25, 0.5), (0.0, 0.0, 1.0)),
            (0.25, 0.75),
        )


class PerspectiveRendererTerrainTests(_GLTestBase):
    def test_upload_terrain_mesh_tracks_heightmap_revision(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        scene = Scene()
        scene.terrain_heightmap = RegionHeightmap(
            width=2,
            height=2,
            samples=[0.0, 1.0, 2.0, 3.0],
            revision=7,
        )

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            renderer._upload_terrain_mesh(self.ctx, scene)
            self.assertIsNotNone(renderer._terrain_vao)
            self.assertIsNotNone(renderer._terrain_fill_vao)
            self.assertIsNotNone(renderer._terrain_line_vao)
            self.assertEqual(renderer._terrain_line_index_count, 8)
            self.assertEqual(renderer._terrain_revision, 7)
            self.assertEqual(renderer._terrain_z_scale, 1.0)
            self.assertEqual(renderer._terrain_height_range, (0.0, 3.0))

            scene.terrain_heightmap.revision = 8
            scene.terrain_z_scale = 3.0
            renderer._upload_terrain_mesh(self.ctx, scene)
            self.assertEqual(renderer._terrain_revision, 8)
            self.assertEqual(renderer._terrain_z_scale, 3.0)
            self.assertEqual(renderer._terrain_height_range, (0.0, 9.0))
        finally:
            renderer.clear_caches()

    def test_upload_terrain_mesh_releases_when_scene_has_no_heightmap(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        scene = Scene()
        scene.terrain_heightmap = RegionHeightmap(
            width=2,
            height=2,
            samples=[0.0, 0.0, 0.0, 0.0],
            revision=1,
        )

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            renderer._upload_terrain_mesh(self.ctx, scene)
            self.assertIsNotNone(renderer._terrain_vao)
            self.assertIsNotNone(renderer._terrain_fill_vao)
            self.assertIsNotNone(renderer._terrain_line_vao)

            scene.terrain_heightmap = None
            renderer._upload_terrain_mesh(self.ctx, scene)
            self.assertIsNone(renderer._terrain_vao)
            self.assertIsNone(renderer._terrain_fill_vao)
            self.assertIsNone(renderer._terrain_line_vao)
            self.assertIsNone(renderer._terrain_revision)
        finally:
            renderer.clear_caches()

    def test_render_terrain_flag_skips_heightmap_mesh(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        scene = Scene(render_terrain=False)
        scene.terrain_heightmap = RegionHeightmap(
            width=2,
            height=2,
            samples=[0.0, 1.0, 2.0, 3.0],
            revision=1,
        )

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            renderer._upload_terrain_mesh(self.ctx, scene)
            renderer.render_gl(scene, aspect=1.0)

            self.assertIsNone(renderer._terrain_vao)
            self.assertIsNone(renderer._terrain_fill_vao)
            self.assertIsNone(renderer._terrain_line_vao)
        finally:
            renderer.clear_caches()

    def test_synthetic_style_terrain_fill_is_visible_without_texture(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=15.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")

        scene = Scene()
        scene.water_height = -10.0
        scene.terrain_heightmap = RegionHeightmap(
            width=2,
            height=2,
            samples=[0.0, 0.0, 0.0, 0.0],
            revision=1,
        )

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertGreater(g, 70, f"solid terrain fill should be visible; got {(r, g, b)}")
            self.assertGreater(r, 25)
            self.assertLess(b, 90)
        finally:
            renderer.clear_caches()


class GroupEntitiesByShapeTests(unittest.TestCase):
    """Pure-Python tests for the shape bucketing logic."""

    @staticmethod
    def _make_scene_with(shapes):
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        for i, shape in enumerate(shapes):
            scene.object_entities[i] = SceneEntity(
                local_id=i,
                pcode=9,
                kind="prim",
                position=(0.0, 0.0, 0.0),
                scale=(1.0, 1.0, 1.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape=shape,
                default_texture_id=None,
                name=None,
                tint=(255, 255, 255),
            )
        return scene

    def _grouper(self):
        # _group_entities_by_shape needs ``self._shape_meshes`` populated
        # so the alias/fallback resolution can verify membership. Use a
        # no-ctx renderer and seed the dict manually.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=None)
        for key in ("cube", "sphere", "cylinder", "torus", "prism"):
            renderer._shape_meshes[key] = object()  # sentinel — not touched
        return renderer

    def test_aliases_ring_to_torus_and_tube_to_cube(self) -> None:
        renderer = self._grouper()
        scene = self._make_scene_with(["ring", "tube"])

        groups = renderer._group_entities_by_shape(scene)

        self.assertIn("torus", groups)
        self.assertIn("cube", groups)
        self.assertEqual(len(groups["torus"]), 1)
        self.assertEqual(len(groups["cube"]), 1)

    def test_none_shape_falls_back_to_cube(self) -> None:
        renderer = self._grouper()
        scene = self._make_scene_with([None, None, "sphere"])

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(len(groups["cube"]), 2)
        self.assertEqual(len(groups["sphere"]), 1)

    def test_unknown_shape_falls_back_to_cube(self) -> None:
        renderer = self._grouper()
        scene = self._make_scene_with(["fictional"])

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(list(groups.keys()), ["cube"])

    def test_avatars_join_object_groups(self) -> None:
        # Avatars are stored in scene.avatar_entities but should
        # bucket alongside object prims of the same shape.
        from vibestorm.viewer3d.scene import SceneEntity

        renderer = self._grouper()
        scene = self._make_scene_with(["sphere"])
        scene.avatar_entities[100] = SceneEntity(
            local_id=100,
            pcode=47,
            kind="avatar",
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=None,
            default_texture_id=None,
            name=None,
            tint=(255, 200, 80),
        )

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(len(groups["sphere"]), 1)
        self.assertEqual(len(groups["cube"]), 1)

    def test_texture_id_for_entity_face_uses_texture_entry_override(self) -> None:
        from vibestorm.world.texture_entry import TextureEntry

        default_id = UUID("11111111-1111-1111-1111-111111111111")
        face_id = UUID("22222222-2222-2222-2222-222222222222")
        renderer = self._grouper()
        scene = self._make_scene_with([None])
        entity = scene.object_entities[0]
        scene.object_entities[0] = entity.__class__(
            local_id=entity.local_id,
            pcode=entity.pcode,
            kind=entity.kind,
            position=entity.position,
            scale=entity.scale,
            rotation=entity.rotation,
            rotation_z_radians=entity.rotation_z_radians,
            name=entity.name,
            default_texture_id=default_id,
            texture_entry=TextureEntry(
                default_texture_id=default_id,
                face_texture_ids=((4, face_id),),
            ),
            shape=entity.shape,
            tint=entity.tint,
        )
        scene.texture_paths[default_id] = Path("/tmp/default.png")
        scene.texture_paths[face_id] = Path("/tmp/face.png")

        self.assertEqual(
            renderer._texture_id_for_entity_face(scene, scene.object_entities[0], 4),
            face_id,
        )
        self.assertEqual(
            renderer._texture_id_for_entity_face(scene, scene.object_entities[0], 3),
            default_id,
        )


if __name__ == "__main__":
    unittest.main()
