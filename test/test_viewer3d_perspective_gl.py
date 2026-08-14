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
            for key in ("cube", "sphere", "cylinder", "torus", "prism", "avatar"):
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
        for key in ("cube", "sphere", "cylinder", "torus", "tube", "ring", "prism", "avatar"):
            renderer._shape_meshes[key] = object()  # sentinel — not touched
        return renderer

    def test_tube_and_ring_use_their_own_meshes(self) -> None:
        # They used to alias to cube and torus. A tube is a square-section
        # sweep and a ring a triangle-section one, so borrowing the round
        # torus or a box misreports both.
        renderer = self._grouper()
        scene = self._make_scene_with(["ring", "tube"])

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(len(groups.get("ring", [])), 1)
        self.assertEqual(len(groups.get("tube", [])), 1)
        self.assertNotIn("cube", groups)
        self.assertNotIn("torus", groups)

    def test_mesh_still_stands_in_as_a_sphere(self) -> None:
        # The one remaining alias: a placeholder until authored mesh assets
        # are fetched and decoded.
        renderer = self._grouper()
        scene = self._make_scene_with(["mesh"])

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(len(groups["sphere"]), 1)

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

    def test_loaded_mesh_asset_uses_asset_mesh_bucket(self) -> None:
        renderer = self._grouper()
        mesh_id = UUID("11111111-2222-3333-4444-555555555555")
        scene = self._make_scene_with(["mesh"])
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
            default_texture_id=entity.default_texture_id,
            texture_entry=entity.texture_entry,
            shape=entity.shape,
            mesh_source_kind="mesh",
            mesh_asset_id=mesh_id,
            sculpt_type=5,
            tint=entity.tint,
        )
        renderer._shape_meshes[f"mesh:{mesh_id}"] = object()

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(list(groups.keys()), [f"mesh:{mesh_id}"])

    def test_loaded_sculpt_asset_uses_sculpt_mesh_bucket(self) -> None:
        renderer = self._grouper()
        sculpt_id = UUID("22222222-3333-4444-5555-666666666666")
        scene = self._make_scene_with(["torus"])
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
            default_texture_id=entity.default_texture_id,
            texture_entry=entity.texture_entry,
            shape=entity.shape,
            mesh_source_kind="sculpt",
            mesh_asset_id=sculpt_id,
            sculpt_type=2,
            tint=entity.tint,
        )
        renderer._shape_meshes[f"sculpt:{sculpt_id}:2"] = object()

        groups = renderer._group_entities_by_shape(scene)

        self.assertEqual(list(groups.keys()), [f"sculpt:{sculpt_id}:2"])

    def test_avatars_join_object_groups(self) -> None:
        # Avatars are stored in scene.avatar_entities and should use the
        # dedicated humanoid placeholder mesh, not the cube fallback.
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
        self.assertEqual(len(groups["avatar"]), 1)

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


class ParcelBorderGLTests(_GLTestBase):
    """Parcel property lines must actually rasterize, not just build a VAO."""

    def _scene_with_borders(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene(region_handle=0xAA)
        scene.render_objects = False
        scene.render_water = False
        scene.render_terrain = False
        # The region perimeter, the shape a single region-wide parcel
        # produces live: 64 west edges plus 64 south edges.
        segments = []
        for i in range(64):
            segments.append((0.0, i * 4.0, 0.0, i * 4.0 + 4.0))
            segments.append((i * 4.0, 0.0, i * 4.0 + 4.0, 0.0))
        scene.parcel_borders = tuple(segments)
        return scene

    def _camera(self):
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D()
        camera.screen_size = self.FBO_SIZE
        camera.set_mode("free")
        camera.eye_position = (128.0, -180.0, 260.0)
        camera.target = (128.0, 128.0, 0.0)
        return camera

    def _frame(self, renderer, scene) -> bytes:
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
        renderer.render_gl(scene, aspect=1.0)
        return self.fbo.read(components=3)

    def test_parcel_borders_rasterize_in_border_color(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(self._camera(), ctx=self.ctx)
        try:
            scene = self._scene_with_borders()

            scene.render_parcel_borders = False
            without = self._frame(renderer, scene)

            scene.render_parcel_borders = True
            with_borders = self._frame(renderer, scene)

            self.assertEqual(renderer._parcel_border_vertex_count, 256)
            changed = [
                tuple(with_borders[i : i + 3])
                for i in range(0, len(with_borders), 3)
                if with_borders[i : i + 3] != without[i : i + 3]
            ]
            self.assertTrue(changed, "parcel borders drew nothing")
            # PARCEL_BORDER_RGBA is green-dominant.
            brightest = max(changed, key=sum)
            self.assertGreater(brightest[1], brightest[0])
            self.assertGreater(brightest[1], brightest[2])
        finally:
            renderer.clear_caches()

    def test_parcel_border_vao_rebuilds_when_segments_change(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(self._camera(), ctx=self.ctx)
        try:
            scene = self._scene_with_borders()
            self._frame(renderer, scene)
            self.assertEqual(renderer._parcel_border_vertex_count, 256)

            scene.parcel_borders = ((0.0, 0.0, 0.0, 4.0),)
            self._frame(renderer, scene)
            self.assertEqual(renderer._parcel_border_vertex_count, 2)

            scene.parcel_borders = ()
            self._frame(renderer, scene)
            self.assertEqual(renderer._parcel_border_vertex_count, 0)
            self.assertIsNone(renderer._parcel_border_vao)
        finally:
            renderer.clear_caches()


class MeshNormalGLTests(_GLTestBase):
    """Decoded mesh normals must actually reach the shader.

    The shape program used to fake normals as ``normalize(in_pos)``. Meshes now
    carry an ``in_normal`` attribute, so authored normals that disagree with the
    geometry must shade differently from the decoder's computed ones.

    Note the decoder always populates ``normals`` — computing them from the
    triangles when the asset omits a ``Normal`` array — so the contrast here is
    authored-sideways vs computed-from-geometry, not present vs absent.
    """

    def _mesh_path(self, tmpdir: str, *, with_normals: bool) -> Path:
        import struct

        from test_sl_mesh import _llsd_binary, _mesh_asset, _triangle_submesh

        submesh = _triangle_submesh()
        if with_normals:
            # Author every normal as +X. The triangle lies in the XY plane, so
            # the decoder would otherwise compute +Z — a maximal disagreement.
            sideways = struct.pack("<HHH", 65535, 32767, 32767) * 3
            submesh["Normal"] = _llsd_binary(sideways)
        path = Path(tmpdir) / f"mesh_{with_normals}.llmesh"
        path.write_bytes(_mesh_asset([submesh]))
        return path

    def _render_mesh(self, tmpdir: str, *, with_normals: bool) -> bytes:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        mesh_id = UUID(int=7)
        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=3.0, yaw=0.0, pitch=0.9)
        camera.set_mode("orbit")
        camera.screen_size = self.FBO_SIZE

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.mesh_paths[mesh_id] = self._mesh_path(tmpdir, with_normals=with_normals)
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="mesh",
            mesh_source_kind="mesh",
            mesh_asset_id=mesh_id,
            tint=(255, 255, 255),
        )

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self.fbo.read(components=3)
        finally:
            renderer.clear_caches()

    def test_authored_normals_change_shading(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            faked = self._render_mesh(tmpdir, with_normals=False)
            authored = self._render_mesh(tmpdir, with_normals=True)

        lit_faked = [p for p in faked if p]
        lit_authored = [p for p in authored if p]
        self.assertTrue(lit_faked, "mesh did not render without authored normals")
        self.assertTrue(lit_authored, "mesh did not render with authored normals")
        self.assertNotEqual(
            faked,
            authored,
            "authored normals produced identical pixels - in_normal is not "
            "reaching the shader",
        )


class InterleaveVertexAttributesTests(unittest.TestCase):
    def test_falls_back_to_normalized_position(self) -> None:
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes([0.0, 0.0, 2.0])

        self.assertEqual(packed, [0.0, 0.0, 2.0, 0.0, 0.0, 1.0, 0.0, 0.0])

    def test_uses_supplied_normals(self) -> None:
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes([5.0, 0.0, 0.0], [0.0, 0.0, 3.0])

        self.assertEqual(packed, [5.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])

    def test_mismatched_normal_length_falls_back(self) -> None:
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes([0.0, 4.0, 0.0], [1.0, 2.0])

        self.assertEqual(packed, [0.0, 4.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0])

    def test_uses_supplied_uvs(self) -> None:
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes(
            [0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.25, 0.75]
        )

        self.assertEqual(packed, [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.25, 0.75])

    def test_mismatched_uv_length_falls_back_to_zero(self) -> None:
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes(
            [0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.25]
        )

        self.assertEqual(packed, [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0])

    def test_degenerate_normal_becomes_up(self) -> None:
        # A vertex at the origin has no position to derive a normal from.
        from vibestorm.viewer3d.perspective import _interleave_vertex_attributes

        packed = _interleave_vertex_attributes([0.0, 0.0, 0.0])

        self.assertEqual(packed, [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0])


class PrimFaceMapGLTests(_GLTestBase):
    """A TextureEntry override must land on the SL face it names.

    Painting a face is only half the job: the renderer used to split the cube
    by the order ``CUBE_INDICES`` happens to author its faces in, which is not
    SL's numbering, so every per-face texture on a box landed on the wrong
    side. These tests aim the camera at a known face and assert the colour, so
    a mapping that is merely self-consistent still fails.
    """

    RED = UUID("dddddddd-0000-0000-0000-000000000001")
    BLUE = UUID("dddddddd-0000-0000-0000-000000000002")

    # A camera straight above its target has a view direction parallel to the
    # (0, 0, 1) up vector, which degenerates the view matrix and renders
    # nothing. Nudge the cap cameras off-axis; the centre ray still lands on
    # the cap of a 2 m prim.
    TOP_EYE = (1.5, 0.0, 6.0)
    BOTTOM_EYE = (1.5, 0.0, -6.0)

    def _scene_with_face(self, shape: str, face_index: int):
        from vibestorm.viewer3d.scene import Scene, SceneEntity
        from vibestorm.world.texture_entry import TextureEntry

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.texture_paths[self.RED] = _write_solid_tile((255, 0, 0))
        scene.texture_paths[self.BLUE] = _write_solid_tile((0, 0, 255))
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=shape,
            default_texture_id=self.BLUE,
            texture_entry=TextureEntry(
                default_texture_id=self.BLUE,
                face_texture_ids=((face_index, self.RED),),
            ),
        )
        return scene

    def _center_pixel_from(self, scene, eye: tuple[float, float, float]):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=eye)
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
        finally:
            renderer.clear_caches()

    def _assert_red(self, pixel, message: str) -> None:
        r, g, b, _ = pixel
        self.assertGreater(r, b + 40, f"{message}; got {(r, g, b)}")

    def _assert_blue(self, pixel, message: str) -> None:
        r, g, b, _ = pixel
        self.assertGreater(b, r + 40, f"{message}; got {(r, g, b)}")

    # SL box numbering: 0=+X, 1=+Y, 2=-X, 3=-Y, 4=top, 5=bottom.
    def test_box_face_0_is_the_plus_x_side(self) -> None:
        scene = self._scene_with_face("cube", 0)
        self._assert_red(
            self._center_pixel_from(scene, (6.0, 0.0, 0.0)),
            "SL box face 0 should face +X",
        )
        self._assert_blue(
            self._center_pixel_from(scene, self.TOP_EYE),
            "SL box face 0 must not paint the top",
        )

    def test_box_face_1_is_the_plus_y_side(self) -> None:
        scene = self._scene_with_face("cube", 1)
        self._assert_red(
            self._center_pixel_from(scene, (0.0, 6.0, 0.0)),
            "SL box face 1 should face +Y",
        )

    def test_box_face_4_is_the_top(self) -> None:
        scene = self._scene_with_face("cube", 4)
        self._assert_red(
            self._center_pixel_from(scene, self.TOP_EYE),
            "SL box face 4 should be the top",
        )
        self._assert_blue(
            self._center_pixel_from(scene, self.BOTTOM_EYE),
            "SL box face 4 must not paint the bottom",
        )

    def test_box_face_5_is_the_bottom(self) -> None:
        scene = self._scene_with_face("cube", 5)
        self._assert_red(
            self._center_pixel_from(scene, self.BOTTOM_EYE),
            "SL box face 5 should be the bottom",
        )

    # SL cylinder numbering: 0=curved side, 1=top, 2=bottom.
    def test_cylinder_face_0_is_the_curved_side(self) -> None:
        scene = self._scene_with_face("cylinder", 0)
        self._assert_red(
            self._center_pixel_from(scene, (6.0, 0.0, 0.0)),
            "SL cylinder face 0 should be the side",
        )
        self._assert_blue(
            self._center_pixel_from(scene, self.TOP_EYE),
            "SL cylinder face 0 must not paint the top cap",
        )

    def test_cylinder_face_1_is_the_top_cap(self) -> None:
        scene = self._scene_with_face("cylinder", 1)
        self._assert_red(
            self._center_pixel_from(scene, self.TOP_EYE),
            "SL cylinder face 1 should be the top cap",
        )
        self._assert_blue(
            self._center_pixel_from(scene, self.BOTTOM_EYE),
            "SL cylinder face 1 must not paint the bottom cap",
        )

    def test_prism_top_cap_is_face_3(self) -> None:
        scene = self._scene_with_face("prism", 3)
        self._assert_red(
            self._center_pixel_from(scene, self.TOP_EYE),
            "SL prism face 3 should be the top cap",
        )

    def test_multi_face_prims_allocate_per_face_buffers(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D()
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.assertEqual(
                sorted(renderer._prim_face_meshes), ["cube", "cylinder", "prism"]
            )
            self.assertEqual(sorted(renderer._prim_face_meshes["cube"]), list(range(6)))
            self.assertEqual(
                sorted(renderer._prim_face_meshes["cylinder"]), [0, 1, 2]
            )
            self.assertEqual(sorted(renderer._prim_face_meshes["prism"]), list(range(5)))
        finally:
            renderer.clear_caches()

    def test_single_face_prims_use_the_face_zero_override(self) -> None:
        # Spheres and tori have one SL face, so an override on face 0 is the
        # prim's texture. Reading TextureEntry's default instead ignores it.
        for shape in ("sphere", "torus"):
            with self.subTest(shape=shape):
                scene = self._scene_with_face(shape, 0)
                self._assert_red(
                    self._center_pixel_from(scene, (6.0, 0.0, 0.0)),
                    f"{shape} face 0 override was ignored",
                )


class HoverTextGLTests(_GLTestBase):
    """Prim floating text must actually reach the framebuffer.

    The billboard is camera-facing and scaled by eye distance so it keeps a
    constant apparent size, which means "did it draw" cannot be answered by
    checking one fixed pixel — these count tinted pixels over the whole frame.

    They also need a bigger framebuffer than the rest of the file. Constant
    apparent size means moving the camera closer does not make the text
    bigger; at the shared 64x64 target a label is ~2 px tall and every glyph
    pixel is partial coverage.
    """

    FBO_SIZE = (256, 256)

    def _scene(self, *, text, color=(255, 0, 255, 255), position=(0.0, 0.0, 0.0)):
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=position,
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="cube",
            hover_text=text,
            hover_text_color=color,
            tint=(20, 20, 20),
        )
        return scene

    def _render(self, scene, eye=(6.0, 0.0, 1.2)):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 1.2), eye_position=eye)
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            pixels = [tuple(data[i : i + 3]) for i in range(0, len(data), 3)]
            return renderer, pixels
        finally:
            renderer.clear_caches()

    @staticmethod
    def _magenta_count(pixels) -> int:
        return sum(1 for r, g, b in pixels if r > 120 and b > 120 and g < 90)

    def test_hover_text_paints_in_its_own_colour(self) -> None:
        _, pixels = self._render(self._scene(text="HELLO"))

        self.assertGreater(
            self._magenta_count(pixels), 0, "hover text billboard did not draw"
        )

    def test_a_prim_without_hover_text_paints_nothing(self) -> None:
        # Guards against the billboard drawing for every prim regardless.
        _, pixels = self._render(self._scene(text=None))

        self.assertEqual(self._magenta_count(pixels), 0)

    def test_scene_flag_hides_hover_text(self) -> None:
        scene = self._scene(text="HELLO")
        scene.render_hover_text = False

        _, pixels = self._render(scene)

        self.assertEqual(self._magenta_count(pixels), 0)

    def test_fully_transparent_text_is_skipped(self) -> None:
        _, pixels = self._render(self._scene(text="HELLO", color=(255, 0, 255, 0)))

        self.assertEqual(self._magenta_count(pixels), 0)

    def test_more_text_covers_more_pixels(self) -> None:
        # A weak but honest check that the glyphs are rasterised rather than a
        # blank quad of fixed size being tinted.
        _, few = self._render(self._scene(text="I"))
        _, many = self._render(self._scene(text="WWWWWWWWWW"))

        self.assertGreater(self._magenta_count(many), self._magenta_count(few))

    def test_identical_strings_share_one_texture_upload(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import SceneEntity

        scene = self._scene(text="SHARED")
        scene.object_entities[2] = SceneEntity(
            local_id=2,
            pcode=9,
            kind="prim",
            position=(3.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="cube",
            hover_text="SHARED",
            hover_text_color=(0, 255, 0, 255),
        )

        camera = Camera3D(target=(0.0, 0.0, 1.2), eye_position=(9.0, 0.0, 1.2))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            renderer.render_gl(scene, aspect=1.0)
            self.assertEqual(list(renderer._hover_text_textures), ["SHARED"])
        finally:
            renderer.clear_caches()

    def test_clear_caches_releases_text_textures(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 1.2), eye_position=(6.0, 0.0, 1.2))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        renderer.render_gl(self._scene(text="HELLO"), aspect=1.0)
        self.assertTrue(renderer._hover_text_textures)

        renderer.clear_caches()

        self.assertEqual(renderer._hover_text_textures, {})


class AvatarNameTagGLTests(_GLTestBase):
    """Avatar name tags share the hover-text billboard pass."""

    FBO_SIZE = (256, 256)

    def _scene(self, *, name):
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.avatar_entities[7] = SceneEntity(
            local_id=7,
            pcode=47,
            kind="avatar",
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 2.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=None,
            name=name,
            tint=(10, 10, 40),
        )
        return scene

    def _render(self, scene):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 1.2), eye_position=(6.0, 0.0, 1.4))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            return [tuple(data[i : i + 3]) for i in range(0, len(data), 3)]
        finally:
            renderer.clear_caches()

    @staticmethod
    def _whitish(pixels) -> int:
        return sum(1 for r, g, b in pixels if r > 180 and g > 180 and b > 180)

    def test_named_avatar_draws_a_white_tag(self) -> None:
        self.assertGreater(
            self._whitish(self._render(self._scene(name="Vibestorm Tester"))),
            0,
            "avatar name tag did not draw",
        )

    def test_unnamed_avatar_draws_nothing(self) -> None:
        self.assertEqual(self._whitish(self._render(self._scene(name=None))), 0)

    def test_scene_flag_hides_avatar_names(self) -> None:
        scene = self._scene(name="Vibestorm Tester")
        scene.render_avatar_names = False

        self.assertEqual(self._whitish(self._render(scene)), 0)

    def test_hover_text_flag_does_not_hide_avatar_names(self) -> None:
        # The two label sources share one pass but must stay independently
        # switchable.
        scene = self._scene(name="Vibestorm Tester")
        scene.render_hover_text = False

        self.assertGreater(self._whitish(self._render(scene)), 0)


class MeshMaterialGroupGLTests(_GLTestBase):
    """Each mesh submesh must draw with its own face texture.

    ``decode_sl_mesh_asset`` maps submeshes 1:1 to prim faces via
    ``material_groups``. The renderer splits the index buffer along those
    groups so a two-submesh mesh with different per-face TextureEntry
    overrides paints two different colours, the same way cube faces do.
    """

    def _two_submesh_asset(self) -> bytes:
        import struct

        from test_sl_mesh import _llsd_binary, _llsd_map, _mesh_asset, _vec3

        def submesh(y_lo: float, y_hi: float) -> dict:
            positions = struct.pack("<HHHHHHHHH", 0, 0, 0, 65535, 0, 0, 0, 65535, 0)
            return {
                "Position": _llsd_binary(positions),
                "PositionDomain": _llsd_map(
                    {"Min": _vec3(-0.5, y_lo, 0.0), "Max": _vec3(0.5, y_hi, 0.0)}
                ),
                "TriangleList": _llsd_binary(struct.pack("<HHH", 0, 1, 2)),
            }

        # Two disjoint triangles: one in the southern half, one in the northern.
        return _mesh_asset([submesh(-0.5, 0.0), submesh(0.0, 0.5)])

    def _solid_texture(self, tmpdir: str, name: str, rgb: tuple) -> Path:
        import pygame

        path = Path(tmpdir) / name
        surface = pygame.Surface((8, 8))
        surface.fill(rgb)
        pygame.image.save(surface, str(path))
        return path

    def test_material_groups_bind_per_face_textures(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity
        from vibestorm.world.texture_entry import TextureEntry

        mesh_id = UUID(int=11)
        red_id = UUID(int=21)
        blue_id = UUID(int=22)

        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "two_faces.llmesh"
            mesh_path.write_bytes(self._two_submesh_asset())

            camera = Camera3D(target=(0.0, 0.0, 0.0), distance=4.0, yaw=0.0, pitch=1.4)
            camera.set_mode("orbit")
            camera.screen_size = self.FBO_SIZE

            scene = Scene()
            scene.render_terrain = False
            scene.render_water = False
            scene.mesh_paths[mesh_id] = mesh_path
            scene.texture_paths[red_id] = self._solid_texture(
                tmpdir, "red.png", (255, 0, 0)
            )
            scene.texture_paths[blue_id] = self._solid_texture(
                tmpdir, "blue.png", (0, 0, 255)
            )
            scene.object_entities[1] = SceneEntity(
                local_id=1,
                pcode=9,
                kind="prim",
                position=(0.0, 0.0, 0.0),
                scale=(3.0, 3.0, 3.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape="mesh",
                mesh_source_kind="mesh",
                mesh_asset_id=mesh_id,
                default_texture_id=red_id,
                texture_entry=TextureEntry(
                    default_texture_id=red_id,
                    face_texture_ids=((0, red_id), (1, blue_id)),
                ),
            )

            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                shape_key = f"mesh:{mesh_id}"
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)

                self.assertIn(
                    shape_key,
                    renderer._mesh_face_meshes,
                    "material groups did not produce per-face index buffers",
                )
                self.assertEqual(
                    sorted(renderer._mesh_face_meshes[shape_key]), [0, 1]
                )

                data = self.fbo.read(components=3)
                pixels = [
                    tuple(data[i : i + 3]) for i in range(0, len(data), 3)
                ]
                reddish = [p for p in pixels if p[0] > 80 and p[0] > p[2] + 40]
                bluish = [p for p in pixels if p[2] > 80 and p[2] > p[0] + 40]
                self.assertTrue(reddish, "face 0 did not paint with its texture")
                self.assertTrue(bluish, "face 1 did not paint with its texture")
            finally:
                renderer.clear_caches()

    def test_single_group_mesh_keeps_one_draw_call(self) -> None:
        # A one-submesh mesh gains nothing from splitting, so it must not
        # allocate per-face buffers.
        from test_sl_mesh import _mesh_asset, _triangle_submesh

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        mesh_id = UUID(int=12)
        with tempfile.TemporaryDirectory() as tmpdir:
            mesh_path = Path(tmpdir) / "one_face.llmesh"
            mesh_path.write_bytes(_mesh_asset([_triangle_submesh()]))

            camera = Camera3D(target=(0.0, 0.0, 0.0), distance=3.0, yaw=0.0, pitch=1.2)
            camera.set_mode("orbit")
            scene = Scene()
            scene.render_terrain = False
            scene.render_water = False
            scene.mesh_paths[mesh_id] = mesh_path
            scene.object_entities[1] = SceneEntity(
                local_id=1,
                pcode=9,
                kind="prim",
                position=(0.0, 0.0, 0.0),
                scale=(2.0, 2.0, 2.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape="mesh",
                mesh_source_kind="mesh",
                mesh_asset_id=mesh_id,
            )

            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                renderer.render_gl(scene, aspect=1.0)
                self.assertNotIn(f"mesh:{mesh_id}", renderer._mesh_face_meshes)
            finally:
                renderer.clear_caches()


class MeshUVGLTests(_GLTestBase):
    """Authored TexCoord0 must drive texture sampling, not position.

    The fragment shader derives UVs from position/normal for primitives. A mesh
    that carried its own TexCoord0 array should sample somewhere else entirely,
    so a texture with distinct halves reads differently with and without it.
    """

    def _mesh_asset(self, *, with_uvs: bool) -> bytes:
        import struct

        from test_sl_mesh import _llsd_binary, _mesh_asset, _triangle_submesh

        submesh = _triangle_submesh()
        if with_uvs:
            # Pin all three vertices inside the right-hand half of the texture,
            # where the position-derived mapping would never send the whole
            # triangle. Deliberately 0.75 rather than 1.0: at exactly the edge,
            # wrap-around linear filtering blends the last texel with the
            # first and the triangle comes out magenta.
            pinned = int(0.75 * 65535)
            submesh["TexCoord0"] = _llsd_binary(
                struct.pack("<HH", pinned, pinned) * 3
            )
        return _mesh_asset([submesh])

    def _split_texture(self, tmpdir: str) -> Path:
        import pygame

        path = Path(tmpdir) / "split.png"
        surface = pygame.Surface((16, 16))
        surface.fill((255, 0, 0))
        pygame.draw.rect(surface, (0, 0, 255), pygame.Rect(8, 0, 8, 16))
        pygame.image.save(surface, str(path))
        return path

    def _render(self, tmpdir: str, *, with_uvs: bool) -> bytes:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        mesh_id = UUID(int=31)
        texture_id = UUID(int=32)
        mesh_path = Path(tmpdir) / f"uv_{with_uvs}.llmesh"
        mesh_path.write_bytes(self._mesh_asset(with_uvs=with_uvs))

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=3.0, yaw=0.0, pitch=1.4)
        camera.set_mode("orbit")
        camera.screen_size = self.FBO_SIZE

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.mesh_paths[mesh_id] = mesh_path
        scene.texture_paths[texture_id] = self._split_texture(tmpdir)
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(3.0, 3.0, 3.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="mesh",
            mesh_source_kind="mesh",
            mesh_asset_id=mesh_id,
            default_texture_id=texture_id,
        )

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            self.assertEqual(
                f"mesh:{mesh_id}" in renderer._mesh_uv_shape_keys,
                with_uvs,
                "authored-UV tracking disagrees with the asset",
            )
            return self.fbo.read(components=3)
        finally:
            renderer.clear_caches()

    def test_authored_uvs_change_texture_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            generated = self._render(tmpdir, with_uvs=False)
            authored = self._render(tmpdir, with_uvs=True)

        self.assertTrue(any(generated), "mesh did not render with generated UVs")
        self.assertTrue(any(authored), "mesh did not render with authored UVs")
        self.assertNotEqual(
            generated,
            authored,
            "authored UVs produced identical pixels - in_mesh_uv is not reaching "
            "the shader",
        )

    def test_authored_uvs_sample_the_pinned_texel(self) -> None:
        # Every vertex is pinned to u=0.75, inside the blue half of the split
        # texture, so the triangle must come out blue-dominant.
        with tempfile.TemporaryDirectory() as tmpdir:
            authored = self._render(tmpdir, with_uvs=True)

        pixels = [tuple(authored[i : i + 3]) for i in range(0, len(authored), 3)]
        lit = [p for p in pixels if any(p)]
        self.assertTrue(lit, "mesh did not render")
        bluish = [p for p in lit if p[2] > p[0]]
        self.assertGreater(
            len(bluish),
            len(lit) // 2,
            "authored UVs did not sample the pinned half of the texture",
        )


if __name__ == "__main__":
    unittest.main()
