"""Tests for PerspectiveRenderer's native GL pass (step 6 v0).

These exercise the full pipeline — shader compile, instance buffer
upload, depth test, perspective projection — by drawing a single cube
into a custom RGBA+depth framebuffer via a standalone GL context, then
reading pixels back. Tests skip cleanly when no GL is available
(headless CI without a GPU, no glcontext.x11/EGL).
"""

import math
import os
import statistics
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
            # Avatars are not in _shape_meshes: they get one buffer per bone
            # so a limb can move without the rest of the figure.
            for bone in ("root", "arm_l", "shin_r"):
                self.assertIsNotNone(
                    renderer._avatar_bone_meshes.get(bone), f"missing bone mesh {bone!r}"
                )
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
            # Sky off: this asserts on what the *world* pass drew, and the sky
            # quad now paints every pixel the world does not cover.
            scene = Scene()
            scene.render_sky = False
            renderer.render_gl(scene, aspect=1.0)
            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            self.assertEqual((r, g, b), (0, 0, 0))
        finally:
            renderer.clear_caches()

    def test_the_sky_fills_what_the_world_does_not(self) -> None:
        # The other half of the same claim: with the sky on, that pixel is no
        # longer the clear colour, and it is not black either.
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
            self.assertNotEqual((r, g, b), (0, 0, 0))
            self.assertGreater(b, r, "sky should be bluer than it is red")
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


def _flat_sea_pixel(camera, scene) -> tuple[float, float, float, float]:
    """What the shader has to draw where the centre ray meets the sea.

    Worked out here rather than compared against a remembered number, and
    it can be worked out exactly because the caller switches the waves off:
    with a slope of zero the surface normal is exactly +Z and every term
    closes. Reflectance is Schlick's, the reflected ray goes back up at the
    angle the view came down at, and the sky along it is the same gradient
    the sky itself is drawn with.

    Returns (r, g, b, a) in 0..1, before the blend against whatever is
    behind the water.
    """
    from vibestorm.viewer3d.perspective import (
        WATER_HAZE_FAR_M,
        WATER_HAZE_NEAR_M,
    )

    eye = camera.orbit_eye()
    span = tuple(camera.target[i] - eye[i] for i in range(3))
    length = math.sqrt(sum(component * component for component in span))
    forward = tuple(component / length for component in span)
    # Where the centre ray crosses the sea, and how far that is from the
    # viewer along the ground.
    step = (scene.water_height - eye[2]) / forward[2]
    hit = tuple(eye[i] + forward[i] * step for i in range(3))
    ground_distance = math.hypot(hit[0] - eye[0], hit[1] - eye[1])

    facing = max(0.0, min(1.0, -forward[2]))
    offset, scale = scene.water_fresnel
    mirror = max(0.0, min(1.0, offset + scale * (1.0 - facing) ** 5))
    # A flat surface reflects the ray back at its own elevation.
    sky = tuple(
        horizon + (zenith - horizon) * math.sqrt(max(0.0, min(1.0, facing)))
        for horizon, zenith in zip(
            scene.sky_horizon_color, scene.sky_zenith_color, strict=True
        )
    )
    rgb = tuple(
        fog + (reflected - fog) * mirror
        for fog, reflected in zip(scene.water_fog, sky, strict=True)
    )
    haze = _smoothstep(WATER_HAZE_NEAR_M, WATER_HAZE_FAR_M, ground_distance)
    rgb = tuple(
        channel + (horizon - channel) * haze
        for channel, horizon in zip(rgb, scene.sky_horizon_color, strict=True)
    )
    alpha = scene.water_alpha + (1.0 - scene.water_alpha) * mirror
    alpha += (1.0 - alpha) * haze
    return (*rgb, alpha)


def _smoothstep(low: float, high: float, value: float) -> float:
    """GLSL's `smoothstep`, so a test can predict what a shader drew."""
    t = max(0.0, min(1.0, (value - low) / (high - low)))
    return t * t * (3.0 - 2.0 * t)


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
            # Sky off: this asserts on what the *world* pass drew.
            scene = Scene()
            scene.render_sky = False
            renderer.render_gl(scene, aspect=1.0)

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

    def _looking_down_camera(self):
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(128.0, 128.0, 0.0),
            distance=200.0,
            yaw=0.0,
            pitch=math.pi / 2 - 0.1,
        )
        camera.set_mode("orbit")
        return camera

    def test_water_plane_renders_translucent_blue_when_camera_looks_down(self) -> None:
        # Camera high above the region centre, pitched almost straight
        # down. Without a map_tile_path the ground stays untextured, so
        # only water draws — center pixel reads water alpha-blended over
        # the cleared (black) framebuffer.
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = self._looking_down_camera()
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            scene = Scene()
            # Sky off: the arithmetic below is water over the *clear* colour,
            # and the sky quad would be behind the water instead.
            scene.render_sky = False
            # Waves off, so the normal is +Z everywhere and the expected
            # colour is a closed expression rather than a sampled one. What
            # the waves do is asserted on its own further down.
            scene.water_ripple = (scene.water_ripple[0], 0.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            wr, wg, wb, wa = _flat_sea_pixel(camera, scene)
            expected = (round(wr * wa * 255), round(wg * wa * 255), round(wb * wa * 255))
            self.assertAlmostEqual(r, expected[0], delta=6)
            self.assertAlmostEqual(g, expected[1], delta=6)
            self.assertAlmostEqual(b, expected[2], delta=6)

            self.assertIsNotNone(renderer._water_program)
            self.assertIsNotNone(renderer._water_vao)
        finally:
            renderer.clear_caches()

    def test_water_plane_respects_scene_alpha(self) -> None:
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = self._looking_down_camera()
        scene = Scene(water_alpha=0.9)
        scene.render_sky = False  # water over the clear colour, not over sky
        scene.water_ripple = (scene.water_ripple[0], 0.0)

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)

            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            wr, wg, wb, wa = _flat_sea_pixel(camera, scene)
            expected = (round(wr * wa * 255), round(wg * wa * 255), round(wb * wa * 255))
            self.assertAlmostEqual(r, expected[0], delta=6)
            self.assertAlmostEqual(g, expected[1], delta=6)
            self.assertAlmostEqual(b, expected[2], delta=6)
        finally:
            renderer.clear_caches()

    def test_a_more_opaque_sea_hides_more_of_what_is_under_it(self) -> None:
        """The slider still does what it says with a Fresnel term above it.

        Worth asserting separately from the arithmetic: reflectance now feeds
        the alpha as well as the colour, and a mistake there could leave the
        surface fully opaque at every setting -- which the two tests above
        would not notice, because they compute the same alpha the shader
        would.
        """
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = self._looking_down_camera()
        tile_path = _write_solid_tile((0, 220, 0))
        try:
            seen = []
            for alpha in (0.2, 0.95):
                scene = Scene(water_alpha=alpha)
                scene.render_sky = False
                scene.map_tile_path = tile_path
                renderer = PerspectiveRenderer(camera, ctx=self.ctx)
                try:
                    self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                    renderer.render_gl(scene, aspect=1.0)
                    _, green, _, _ = self._read_pixel(
                        self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2
                    )
                    seen.append(green)
                finally:
                    renderer.clear_caches()
            self.assertGreater(
                seen[0] - seen[1],
                40,
                f"a nearly transparent sea should show much more ground; got {seen}",
            )
        finally:
            tile_path.unlink(missing_ok=True)

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
        scene.render_sky = False  # asserts on what the world pass drew

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

    def _multi_frame(self, texts, eye=(6.0, 0.0, 1.2)):
        """Render one frame per text with a single renderer, returning both.

        The cache pruning is a *cross-frame* mechanism: frame N uploads, frame
        N+1 decides what is still live. Every other test in this class renders
        exactly one frame and tears the renderer down afterwards, so none of
        them can reach the case where pruning releases a texture that a later
        frame still needs.
        """
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 1.2), eye_position=eye)
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        frames = []
        identities = []
        try:
            for text in texts:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(self._scene(text=text), aspect=1.0)
                data = self.fbo.read(components=3)
                frames.append([tuple(data[i : i + 3]) for i in range(0, len(data), 3)])
                # Identity, not just presence. Pruning runs before the draw
                # loop and the draw loop re-rasterises whatever is missing, so
                # a prune that wrongly releases live labels still *renders*
                # correctly — it just re-uploads every frame. Only the identity
                # of the cached texture reveals that.
                entry = renderer._hover_text_textures.get(text)
                identities.append(id(entry[0]) if entry is not None else None)
            # Read the cache size here, not in the caller: clear_caches() in
            # the finally below runs before this function returns, so the
            # caller would only ever see an emptied cache.
            cached = len(renderer._hover_text_textures)
            return frames, cached, identities
        finally:
            renderer.clear_caches()

    def test_unchanged_text_still_draws_on_later_frames(self) -> None:
        # The regression a bad prune would cause: the label is released after
        # the first frame and the second renders empty.
        frames, _cached, identities = self._multi_frame(["HELLO"] * 3)

        for index, pixels in enumerate(frames):
            self.assertGreater(
                self._magenta_count(pixels), 0, f"frame {index} lost its label"
            )
        # And it is the *same* texture each frame, not a fresh upload per
        # frame that merely looks identical on screen.
        self.assertEqual(len(set(identities)), 1, "label was re-uploaded each frame")

    def test_changing_text_draws_every_frame_and_keeps_one_texture(self) -> None:
        # The clock-prim case that motivated pruning at all: text changes each
        # frame, so each frame must draw, and stale textures must not pile up.
        frames, cached, _identities = self._multi_frame(["12:00", "12:01", "12:02"])

        for index, pixels in enumerate(frames):
            self.assertGreater(
                self._magenta_count(pixels), 0, f"frame {index} did not draw"
            )
        self.assertEqual(cached, 1)

    def test_text_that_goes_away_is_released_and_can_come_back(self) -> None:
        frames, cached, _identities = self._multi_frame(["HELLO", None, "HELLO"])

        self.assertGreater(self._magenta_count(frames[0]), 0)
        self.assertEqual(self._magenta_count(frames[1]), 0)
        # Re-rasterised after having been pruned — the round trip works.
        self.assertGreater(self._magenta_count(frames[2]), 0)
        self.assertEqual(cached, 1)


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


class AvatarPoseGLTests(_GLTestBase):
    """The gait has to survive the trip from a pose dict to the framebuffer.

    Between the two are nine per-bone buffers, nine composed matrices and one
    instance upload per bone across every avatar in the region. A mistake
    anywhere in that -- a bone drawn at rest, an instance matrix landing on
    the wrong avatar -- still renders an avatar-shaped avatar, which is why
    these look at pixels rather than at matrices.
    """

    FBO_SIZE = (160, 160)

    def _render(self, avatars, poses) -> bytes:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.render_sky = False
        for local_id, position in avatars:
            scene.avatar_entities[local_id] = SceneEntity(
                local_id=local_id,
                pcode=47,
                kind="avatar",
                position=position,
                scale=(0.45, 0.60, 1.90),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape=None,
                tint=(255, 200, 80),
            )
        scene.avatar_poses = poses

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(0.6, -4.5, 0.3))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self.fbo.read(components=3)
        finally:
            renderer.clear_caches()

    def _drawn_columns(self, data: bytes, rows: range) -> set[int]:
        width, _height = self.FBO_SIZE
        columns = set()
        for y in rows:
            for x in range(width):
                offset = (y * width + x) * 3
                if any(data[offset : offset + 3]):
                    columns.add(x)
        return columns

    @staticmethod
    def _mid_stride():
        import math

        from vibestorm.viewer3d.avatar_pose import AvatarMotion, pose_for_motion

        return pose_for_motion(
            AvatarMotion(position=(0.0, 0.0, 0.0), speed_mps=3.2, gait_phase=math.pi / 2.0)
        )

    def test_a_walking_avatar_stands_with_its_feet_apart(self) -> None:
        # The bottom quarter of the figure is legs and shoes. Standing, they
        # are together; mid-stride they are not, and by a lot -- measured 8
        # pixels against 37 at this size.
        standing = self._render([(1, (0.0, 0.0, 0.0))], {})
        walking = self._render([(1, (0.0, 0.0, 0.0))], {1: self._mid_stride()})

        feet = range(50, 65)
        together = self._drawn_columns(standing, feet)
        apart = self._drawn_columns(walking, feet)

        self.assertTrue(together, "the standing figure did not draw")
        self.assertGreater(
            max(apart) - min(apart),
            2 * (max(together) - min(together)),
            "the walking figure's feet are no further apart than a standing one's",
        )

    def test_one_avatar_s_pose_does_not_reach_another(self) -> None:
        # Every bone is drawn as one instanced pass over *all* avatars, with
        # each avatar's pose folded into its own instance matrix. Getting the
        # ordering wrong there would put one person's stride on somebody else
        # -- and with one avatar on screen, nothing would ever show it.
        # The camera looks along +Y, so +X is screen right: one avatar to each
        # side, and the still one keeps the right half to itself.
        alone = self._render([(1, (1.6, 0.0, 0.0))], {})
        beside_a_walker = self._render(
            [(1, (1.6, 0.0, 0.0)), (2, (-1.6, 0.0, 0.0))],
            {2: self._mid_stride()},
        )

        width, height = self.FBO_SIZE
        differing = sum(
            1
            for y in range(height)
            for x in range(width // 2, width)
            if alone[(y * width + x) * 3 : (y * width + x) * 3 + 3]
            != beside_a_walker[(y * width + x) * 3 : (y * width + x) * 3 + 3]
        )

        drawn = sum(
            1
            for y in range(height)
            for x in range(width // 2, width)
            if any(alone[(y * width + x) * 3 : (y * width + x) * 3 + 3])
        )

        self.assertGreater(drawn, 200, "the still avatar is not in the half being compared")
        self.assertEqual(differing, 0, "the walker's pose moved the avatar standing still")


class NormalTransformGLTests(_GLTestBase):
    """A normal does not transform like a position under a non-uniform scale.

    Multiplying it by the model matrix is the common shortcut, and it is
    exactly right for a uniform scale and for every axis-aligned face of a box
    -- which is most of what a test suite reaches for, and why this went
    unnoticed. It is wrong for any normal that is not along an axis, and SL
    prims are stretched constantly.

    The discriminator is a sphere squashed flat: a disc, whose top surface
    faces almost straight up everywhere. Correctly transformed, its normals
    stay near +Z, so a ring of samples around the disc shades evenly whatever
    the sun is doing. Multiplied by the model matrix, the flattened axis
    shrinks the normal's Z and the wide axes exaggerate its X and Y, tipping
    every normal outwards -- and the same ring runs from bright on the sunlit
    side to dark on the other. Measured: a spread of 6 out of 255 against 67.
    """

    FBO_SIZE = (128, 128)

    def _disc_ring(self, radius: int) -> list[int]:
        import math

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.render_sky = False
        # Low and to one side, so a normal tipped outwards is unmistakable.
        scene.sun_direction = (1.0, 0.0, 0.35)
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(4.0, 4.0, 0.2),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="sphere",
            tint=(220, 220, 220),
        )

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(0.2, 0.2, 7.0))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            width, _height = self.FBO_SIZE
            centre = 64
            samples = []
            for step in range(12):
                angle = step * math.pi / 6.0
                x = int(centre + radius * math.cos(angle))
                y = int(centre + radius * math.sin(angle))
                samples.append(data[(y * width + x) * 3])
            return samples
        finally:
            renderer.clear_caches()

    def test_a_flattened_sphere_shades_evenly_across_its_face(self) -> None:
        for radius in (12, 20):
            with self.subTest(radius=radius):
                ring = self._disc_ring(radius)
                self.assertTrue(all(value > 0 for value in ring), f"disc not drawn: {ring}")
                self.assertLess(
                    max(ring) - min(ring),
                    12,
                    "the flattened sphere is lit from the side, so normals "
                    f"went through the model matrix rather than its inverse "
                    f"transpose: {ring}",
                )


class AvatarPaletteGLTests(_GLTestBase):
    """The avatar's palette has to survive the whole trip to the screen.

    Everything between the part table and the pixel can fail quietly. If the
    mesh's UVs are not uploaded the shader falls back to its generated
    coordinates, which sweep the full 0..1 range and scatter every palette
    entry over the whole figure; if the palette texture is not bound the
    figure comes out in one flat instance tint, which is exactly what the old
    placeholder looked like. Neither shows up as an error, and only the second
    looks obviously wrong.

    So this checks *where* each colour lands: hair and skin at the top,
    shirt across the chest, trousers down the shin, shoes at the ground.
    Comparing hue rather than value, because the sun scales a colour and
    leaves the ratio between its channels alone.
    """

    FBO_SIZE = (128, 128)

    #: (x, y) in the framebuffer's own bottom-up rows, and the palette entry
    #: that body part is made of. Taken from the middle of each region, with
    #: several rows of the same colour above and below.
    LANDMARKS = (
        ((64, 84), "skin"),
        ((64, 70), "shirt"),
        ((60, 48), "trousers"),
        ((60, 28), "shoes"),
    )

    @staticmethod
    def _hue(pixel) -> tuple[float, ...]:
        total = sum(pixel) or 1
        return tuple(channel / total for channel in pixel)

    def _render_avatar(self) -> bytes:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.render_sky = False
        scene.avatar_entities[1] = SceneEntity(
            local_id=1,
            pcode=47,
            kind="avatar",
            position=(0.0, 0.0, 0.0),
            scale=(0.45, 0.60, 1.90),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=None,
            tint=(255, 200, 80),
        )

        camera = Camera3D(target=(0.0, 0.0, 0.15), eye_position=(3.2, 0.0, 0.35))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self.fbo.read(components=3)
        finally:
            renderer.clear_caches()

    def _pixel(self, data: bytes, x: int, y: int) -> tuple[int, int, int]:
        width, _height = self.FBO_SIZE
        offset = (y * width + x) * 3
        return tuple(data[offset : offset + 3])

    def test_each_body_part_is_painted_from_its_own_palette_entry(self) -> None:
        from vibestorm.viewer3d.avatar_mesh import PALETTE

        colours = dict(PALETTE)
        data = self._render_avatar()

        for (x, y), region in self.LANDMARKS:
            pixel = self._pixel(data, x, y)
            self.assertTrue(any(pixel), f"nothing drawn at the {region} landmark")
            drift = max(
                abs(a - b)
                for a, b in zip(self._hue(pixel), self._hue(colours[region]))
            )
            self.assertLess(
                drift,
                0.02,
                f"the {region} landmark came out {pixel}, not a shade of "
                f"{colours[region]}",
            )

    def test_the_instance_tint_does_not_paint_the_avatar(self) -> None:
        # The entity's tint is the 2D map's marker colour and is the same for
        # every avatar in the region. Reaching the 3D figure would overwrite
        # the palette with one flat colour. Skin is the near miss that a
        # simple "reddish pixel" check trips over; by hue it is well clear.
        data = self._render_avatar()
        width, height = self.FBO_SIZE
        marker = self._hue((255, 200, 80))

        tinted = 0
        for y in range(height):
            for x in range(width):
                pixel = self._pixel(data, x, y)
                if not any(pixel):
                    continue
                if max(abs(a - b) for a, b in zip(self._hue(pixel), marker)) < 0.02:
                    tinted += 1

        self.assertEqual(tinted, 0, "the marker tint is painting the avatar")


class AuthoredNormalGLTests(_GLTestBase):
    """The renderer must upload authored normals, not fall back to position.

    This is the leg the pure-mesh normal tests cannot cover: they prove
    meshes.shape_normals() is right, not that render_gl passes it through.
    Removing the pass-through left every mesh test green.

    A cube face lit by a fixed sun is the discriminator. With authored
    normals the whole face shares one normal and shades uniformly; with
    normalize(position) each of its four corners gets a different diagonal
    normal, so the face renders as a visible gradient.
    """

    FBO_SIZE = (128, 128)

    def _render_cube_face_on(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(4.0, 4.0, 4.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape="cube",
            tint=(220, 220, 220),
        )

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(6.0, 0.0, 0.0))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            width, height = self.FBO_SIZE
            samples = []
            for y in (height // 3, height // 2, 2 * height // 3):
                for x in (width // 3, width // 2, 2 * width // 3):
                    offset = (y * width + x) * 3
                    samples.append(data[offset])
            return samples
        finally:
            renderer.clear_caches()

    def _render_avatar_face_on(self, samples_at):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.avatar_entities[1] = SceneEntity(
            local_id=1,
            pcode=47,
            kind="avatar",
            position=(0.0, 0.0, 0.0),
            scale=(4.0, 4.0, 4.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            shape=None,
            tint=(220, 220, 220),
        )

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(8.0, 0.0, 0.0))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            width, _height = self.FBO_SIZE
            return [data[(y * width + x) * 3] for x, y in samples_at]
        finally:
            renderer.clear_caches()

    def test_avatar_torso_facet_shades_uniformly(self) -> None:
        # The avatar renders through _shape_meshes rather than the per-face
        # buffers, so it covers the other upload site. Its parts sit away from
        # the origin, which makes the position fallback especially wrong: it
        # gives every vertex a normal pointing away from the figure's centre,
        # so a merged mesh smears into one smooth plank.
        #
        # The band runs *down* one facet of the torso rather than across the
        # chest. The torso is an eight-sided tube, so a horizontal band crosses
        # a facet edge and a step there is correct, not a bug; a vertical band
        # stays on one facet, where every pixel shares one authored normal.
        # Keep it inside the chest -- the surrounding pixels are the same
        # colour for a dozen rows in each direction, so a small change to the
        # figure's proportions will not silently move it onto the background.
        width, height = self.FBO_SIZE
        column = width // 2 - 6
        band = [(column, y) for y in range(height // 2 + 2, height // 2 + 15, 3)]

        samples = self._render_avatar_face_on(band)

        self.assertTrue(all(value > 0 for value in samples), f"torso not drawn: {samples}")
        self.assertLessEqual(
            max(samples) - min(samples),
            2,
            f"avatar torso is a gradient, so authored normals were not used: {samples}",
        )

    def _sculpt_plane_texture(self, tmpdir):
        """A sculpt map encoding a flat plane in the XY plane at z = 0.5.

        A sculpt map stores XYZ as RGB over the 0..1 range remapped to
        -0.5..0.5, so a constant blue channel is a constant height.
        """
        import pygame

        size = 16
        surface = pygame.Surface((size, size))
        for y in range(size):
            for x in range(size):
                surface.set_at(
                    (x, y),
                    (int(255 * x / (size - 1)), int(255 * y / (size - 1)), 255),
                )
        path = Path(tmpdir) / "plane.png"
        pygame.image.save(surface, str(path))
        return path

    def test_sculpt_geometry_drives_its_own_normals(self) -> None:
        # The third upload site. A sculpt map carries only positions, so the
        # renderer must derive normals from the decoded geometry; the
        # normalize(position) fallback would only suit a sphere on the origin.
        # A flat sculpt plane must therefore shade uniformly.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        sculpt_id = UUID("55555555-6666-7777-8888-999999999999")
        with tempfile.TemporaryDirectory() as tmpdir:
            scene = Scene()
            scene.render_terrain = False
            scene.render_water = False
            scene.texture_paths[sculpt_id] = self._sculpt_plane_texture(tmpdir)
            scene.object_entities[1] = SceneEntity(
                local_id=1,
                pcode=9,
                kind="prim",
                position=(0.0, 0.0, 0.0),
                scale=(6.0, 6.0, 6.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape="cube",
                mesh_source_kind="sculpt",
                mesh_asset_id=sculpt_id,
                sculpt_type=3,
                tint=(220, 220, 220),
            )

            camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(1.0, 0.0, 7.0))
            camera.set_mode("free")
            camera.screen_size = self.FBO_SIZE

            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)
                self.assertIn(
                    f"sculpt:{sculpt_id}:3",
                    renderer._shape_meshes,
                    "sculpt asset never reached the shape meshes",
                )
                data = self.fbo.read(components=3)
                width, height = self.FBO_SIZE
                band = [
                    data[((height // 2) * width + x) * 3]
                    for x in range(width // 2 - 6, width // 2 + 7, 3)
                ]
                self.assertTrue(all(v > 0 for v in band), f"sculpt not drawn: {band}")
                self.assertLessEqual(
                    max(band) - min(band),
                    3,
                    f"flat sculpt shades as a gradient: {band}",
                )
            finally:
                renderer.clear_caches()

    def test_a_cube_face_shades_uniformly(self) -> None:
        samples = self._render_cube_face_on()

        self.assertTrue(all(value > 0 for value in samples), f"face not drawn: {samples}")
        self.assertLessEqual(
            max(samples) - min(samples),
            2,
            f"cube face is a gradient, so authored normals were not used: {samples}",
        )


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


class SeaHorizonGLTests(_GLTestBase):
    """Where the sea meets the sky.

    It used to meet it in a line. The water plane is the sky's own horizon
    colour with a dark tint blended over it, so the two simply abut, and from
    a camera three metres above the surface the step across that line measured
    **sixty-seven levels** -- a wall at the edge of the world rather than a
    distance. Real air between the viewer and the horizon is what fixes it.

    A tall narrow buffer, and the assertions are on a single column of it:
    what is being measured is how the colour changes going *up*, which is one
    dimension, and 320 rows of it resolve a degree of elevation to about five.
    """

    COLUMN_SIZE = (16, 320)
    EYE = (73.0, 73.0, 34.0)
    WATER_HEIGHT = 17.0

    def _column(self, scene, *, target=None):
        """Render one tall frame and return its centre column, bottom-up."""
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        size = self.COLUMN_SIZE
        texture = self.ctx.texture(size, components=4)
        depth = self.ctx.depth_renderbuffer(size)
        fbo = self.ctx.framebuffer(color_attachments=[texture], depth_attachment=depth)
        camera = Camera3D(
            mode="eye",
            eye_position=self.EYE,
            # Level and due north-east unless asked otherwise: the horizon has
            # to be in the frame for any of this to mean anything.
            target=target or (128.0, 128.0, self.EYE[2]),
        )
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            fbo.use()
            self.ctx.viewport = (0, 0, *size)
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=size[0] / size[1])
            data = fbo.read(components=4)
        finally:
            renderer.clear_caches()
            fbo.release()
            depth.release()
            texture.release()
            self.fbo.use()
            self.ctx.viewport = (0, 0, *self.FBO_SIZE)
        width, height = size
        middle = width // 2
        return [
            tuple(data[(y * width + middle) * 4 : (y * width + middle) * 4 + 4])
            for y in range(height)
        ]

    def _sea_scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.render_terrain = False
        scene.render_clouds = False
        scene.water_height = self.WATER_HEIGHT
        return scene

    def _still_sea_scene(self):
        """The same sea with its surface taken away: no waves, no reflection.

        Every test in this class is about the *air* between the viewer and the
        horizon, and a reflecting surface is the one thing that would hide it:
        reflectance rises towards a grazing angle, which is exactly the
        direction the haze rises in too, so a column of pixels going up towards
        the horizon changes for two reasons at once and neither can be read off
        it. What the surface itself does is asserted in `SeaSurfaceGLTests`.
        """
        scene = self._sea_scene()
        scene.water_fresnel = (0.0, 0.0)
        scene.water_ripple = (scene.water_ripple[0], 0.0)
        return scene

    def _worst_step(self, column, low_row: int, high_row: int) -> int:
        return max(
            max(abs(column[y][i] - column[y - 1][i]) for i in range(3))
            for y in range(low_row + 1, high_row)
        )

    def test_the_sea_does_not_meet_the_sky_in_a_wall(self) -> None:
        # The whole point. Sixty-seven before; anything in this range is a
        # gradient rather than an edge.
        column = self._column(self._sea_scene())

        self.assertLess(self._worst_step(column, 120, 175), 30)

    def test_the_sea_close_by_is_still_the_sea(self) -> None:
        """The haze must not eat the water at the viewer's feet.

        The first version measured the distance in the *vertex* shader. The
        plane is two triangles more than two kilometres across, so every
        fragment got the average of three corners a kilometre off and the
        whole sea came out sky-coloured -- a horizon with no wall in it
        because there was no sea either.
        """
        scene = self._sea_scene()

        column = self._column(scene, target=(128.0, 128.0, -40.0))

        near = column[len(column) // 2]
        self.assertLess(
            sum(near[:3]),
            sum(round(c * 255) for c in scene.sky_horizon_color) - 60,
            f"the sea underfoot is the colour of the sky: {near}",
        )

    def test_the_far_sea_takes_the_colour_of_this_region_s_sky(self) -> None:
        """Not a constant: the haze has to be the sky this region is drawing.

        Asserted as a *change* down the column rather than as one pixel's
        colour, because a single pixel near the horizon could be the sky
        itself and a test that read one would pass without any sea in it.
        Row 130 is a good way below the horizon and row 152 is close to it;
        the second has to be much nearer the sky's colour than the first.
        """
        scene = self._still_sea_scene()
        # Opaque, so the only red in the frame is red the *haze* put there.
        # A translucent sea over a red sky is red through it as well, and at
        # this water's true fog colour -- which is very dark -- 28 per cent of
        # a bright sky is enough of it to swamp the thing being measured.
        scene.water_alpha = 1.0
        scene.sky_horizon_color = (0.85, 0.35, 0.20)
        scene.sky_zenith_color = (0.85, 0.35, 0.20)

        column = self._column(scene)

        near, far = column[130], column[152]
        # The sky here is strongly red; the water tint is blue-green.
        self.assertLess(near[0], near[2], f"the near sea is already the sky: {near}")
        self.assertGreater(far[0], far[2], f"the far sea is not the sky: {far}")

    def test_the_far_sea_stops_caring_about_the_water_opacity(self) -> None:
        """Opacity is a near-water setting, and at the horizon it is a bug.

        The slider exists so a viewer can see what is under the surface. At
        the horizon there is nothing under the surface but sky, and leaving
        the sea partly transparent there lets that sky through at a different
        brightness from the sky beside it -- which is the wall coming back by
        another route. So the alpha rises with the haze, and by the horizon
        the slider has stopped mattering.
        """
        opaque = self._still_sea_scene()
        opaque.water_alpha = 1.0
        clear = self._still_sea_scene()
        clear.water_alpha = 0.2

        column_opaque = self._column(opaque)
        column_clear = self._column(clear)

        def gap(row: int) -> int:
            return max(
                abs(a - b)
                for a, b in zip(
                    column_opaque[row][:3], column_clear[row][:3], strict=True
                )
            )

        # Row 145 is well out to sea and row 153 is nearly at the horizon.
        self.assertGreater(gap(145), 40, "the opacity slider does nothing at all")
        self.assertLess(gap(153), 10, "the sea is as translucent at the horizon")

    def test_the_haze_is_measured_from_the_viewer(self) -> None:
        """From the viewer, not from the world origin.

        The two are the same thing in a region whose corner is at (0, 0) and
        whose camera is near it, which is every other test here -- so a shader
        handed a zero eye position passes all of them. A viewer standing well
        away from the origin is where it shows: the sea at their feet is a
        kilometre from (0, 0) and would be drawn as fully hazed, which is to
        say as sky.
        """
        scene = self._still_sea_scene()
        self.EYE = (900.0, 900.0, 34.0)

        column = self._column(scene, target=(900.0, 910.0, -40.0))

        underfoot = column[len(column) // 2]
        to_water = sum(
            abs(underfoot[i] - round(scene.water_fog[i] * 255)) for i in range(3)
        )
        to_sky = sum(
            abs(underfoot[i] - round(scene.sky_horizon_color[i] * 255)) for i in range(3)
        )
        self.assertLess(
            to_water, to_sky, f"the sea underfoot is drawn as horizon: {underfoot}"
        )

    def test_the_sea_inside_the_region_is_not_hazed(self) -> None:
        """`WATER_HAZE_NEAR_M` is about a region across, on purpose.

        A viewer standing in a region should see that region's water as
        water. Starting the haze at their feet instead tints everything, most
        of it too slightly to notice in a screenshot -- so the tint and the
        sky are pushed to opposite extremes here, which turns a few levels of
        drift into fifty.
        """
        scene = self._still_sea_scene()
        scene.water_fog = (0.0, 0.0, 0.0)
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)

        column = self._column(scene)

        # Row 60 is sea about 47 metres off and row 140 about 242 -- both
        # inside the near distance, so the only difference between them
        # should be the ripple.
        close, far = column[60], column[140]
        self.assertLess(
            max(abs(a - b) for a, b in zip(close[:3], far[:3], strict=True)),
            18,
            f"the near sea is already hazing: {close} against {far}",
        )

class SeaSurfaceGLTests(_GLTestBase):
    """The surface of the sea, rather than the air over it.

    Four things the region's document says about water had been parsed and
    never used: `wave1_direction`, `wave2_direction`, `fresnel_offset` and
    `fresnel_scale`. The sea was a flat sheet of one colour with a fixed share
    of sky already mixed into it -- `WATER_SKY_REFLECTANCE`, whose own comment
    said one mixture was standing in for both ends of an angle it could not
    measure. It can measure it now.

    `normal_map` is the fifth and is still not used: it names a texture asset
    nobody here has fetched. Two sines stand in for it, which is why the
    wavelength and the steepness are constants in `atmosphere` rather than
    numbers off the wire -- and the tests below are careful to assert what the
    document *does* decide (which way, how fast, how much sky) rather than the
    shape, which it does not.

    Every scene here is opaque and has the sky quad off, so what is read is
    the water pass and nothing behind it.
    """

    FBO_SIZE = (96, 96)
    WATER_HEIGHT = 20.0
    #: Nearly straight down, and nearly is deliberate: a camera looking exactly
    #: along -Z has no up vector to speak of, the view matrix comes out
    #: degenerate, and nothing is drawn at all. Which is a thing to know, since
    #: a test that framed it that way would read black and pass whatever the
    #: shader did.
    STEEP = ((128.0, 128.0, 80.0), (133.0, 128.0, 0.0))
    #: Four metres over the water, looking a hundred and twenty metres out --
    #: well inside `WATER_HAZE_NEAR_M`, so nothing here is the haze.
    GRAZING = ((128.0, 128.0, 24.0), (248.0, 128.0, 20.0))

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.render_terrain = False
        scene.render_sky = False
        scene.render_clouds = False
        scene.water_height = self.WATER_HEIGHT
        scene.water_alpha = 1.0
        scene.water_fog = (0.0, 0.0, 0.0)
        return scene

    def _frame(self, scene, eye, target):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(mode="eye", eye_position=eye, target=target)
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self.fbo.read(components=4)
        finally:
            renderer.clear_caches()

    def _middle(self, frame) -> tuple[int, int, int, int]:
        width, height = self.FBO_SIZE
        offset = ((height // 2) * width + width // 2) * 4
        return tuple(frame[offset : offset + 4])

    def _worst_difference(self, first, second) -> int:
        width, height = self.FBO_SIZE
        return max(
            max(abs(first[i + c] - second[i + c]) for c in range(3))
            for i in range(0, width * height * 4, 4)
        )

    def _differing_pixels(self, first, second, *, threshold: int = 6) -> int:
        width, height = self.FBO_SIZE
        return sum(
            1
            for i in range(0, width * height * 4, 4)
            if max(abs(first[i + c] - second[i + c]) for c in range(3)) > threshold
        )

    # -- what the angle decides -------------------------------------------

    def test_the_sea_shows_back_more_sky_the_flatter_it_is_looked_at(self) -> None:
        """A pond is a window from above and a mirror from the side.

        Both cameras look at water well inside `WATER_HAZE_NEAR_M`, so the
        difference between them cannot be the haze: it is the angle, which is
        the whole reason `fresnel_offset` and `fresnel_scale` are on the wire.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)
        scene.water_ripple = (scene.water_ripple[0], 0.0)

        straight_down = self._middle(self._frame(scene, *self.STEEP))
        along_it = self._middle(self._frame(scene, *self.GRAZING))

        self.assertGreater(
            along_it[0] - straight_down[0],
            60,
            f"the angle changed nothing: {straight_down} against {along_it}",
        )

    def test_the_sea_shows_back_the_sky_that_is_above_it(self) -> None:
        """Not one colour of sky: the one the reflected ray points at.

        Looking straight down, a flat surface sends the eye back where it came
        from, which is up -- so what comes back is the zenith. Looking along
        the water it sends it out at the horizon. Getting this wrong is not
        subtle: it draws the sea as a second sky with the gradient upside
        down.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 0.0, 0.0)
        scene.sky_zenith_color = (0.0, 0.0, 1.0)
        scene.water_ripple = (scene.water_ripple[0], 0.0)

        straight_down = self._middle(self._frame(scene, *self.STEEP))
        along_it = self._middle(self._frame(scene, *self.GRAZING))

        self.assertGreater(
            straight_down[2], straight_down[0] + 40, f"not the zenith: {straight_down}"
        )
        self.assertGreater(
            along_it[0], along_it[2] + 40, f"not the horizon: {along_it}"
        )

    def test_a_sea_that_shows_back_more_sky_hides_more_of_what_is_under_it(
        self,
    ) -> None:
        """Reflected light does not come from below the surface.

        The two scenes differ in one number and are otherwise identical, and
        the sky is set to the sea's own colour so that the *colour* term
        cannot move: whatever changes in the frame changed through the alpha.
        """
        tile_path = _write_solid_tile((0, 220, 0))
        try:
            seen = []
            for fresnel in ((0.0, 0.0), (0.9, 0.0)):
                scene = self._scene()
                scene.render_terrain = True
                scene.map_tile_path = tile_path
                scene.water_alpha = 0.35
                scene.sky_horizon_color = scene.water_fog
                scene.sky_zenith_color = scene.water_fog
                scene.water_fresnel = fresnel
                scene.water_ripple = (scene.water_ripple[0], 0.0)
                seen.append(self._middle(self._frame(scene, *self.STEEP))[1])
            self.assertGreater(
                seen[0] - seen[1],
                60,
                f"the ground shows through a mirror just as well: {seen}",
            )
        finally:
            tile_path.unlink(missing_ok=True)

    # -- what the waves do -------------------------------------------------

    def test_the_waves_are_there(self) -> None:
        """A rippled sea is not the flat one, from the same camera.

        Read as a count of pixels rather than as one pixel: a wave crest can
        fall anywhere, including on whichever pixel a test picked.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)
        eye, target = (128.0, 128.0, 26.0), (168.0, 128.0, 20.0)

        rippled = self._frame(scene, eye, target)
        scene.water_ripple = (scene.water_ripple[0], 0.0)
        flat = self._frame(scene, eye, target)

        self.assertGreater(
            self._differing_pixels(rippled, flat),
            400,
            "the sea is as flat with waves on it as without",
        )

    def test_the_waves_move(self) -> None:
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)
        eye, target = (128.0, 128.0, 26.0), (168.0, 128.0, 20.0)

        first = self._frame(scene, eye, target)
        scene.water_phase = (1.4, 2.3)
        later = self._frame(scene, eye, target)

        self.assertGreater(
            self._differing_pixels(first, later),
            400,
            "the sea is frozen: the phase is not reaching the surface",
        )

    def test_the_waves_run_the_way_the_region_says(self) -> None:
        """The two directions off the wire, not a fixed pattern.

        Asserted as two seas differing rather than as a measured heading: what
        the document decides is *which way*, and a renderer that ignored it
        would draw the same water for both of these.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)
        eye, target = self.GRAZING

        scene.water_waves = (1.0, 0.0, 1.0, 0.0)
        eastward = self._frame(scene, eye, target)
        scene.water_waves = (0.0, 1.0, 0.0, 1.0)
        northward = self._frame(scene, eye, target)

        self.assertGreater(
            self._differing_pixels(eastward, northward),
            400,
            "waves running east and waves running north draw the same sea",
        )

    def _line_variation(self, frame) -> tuple[int, int]:
        """How much the frame changes along each screen axis.

        Returned as (worst change across a row, worst change down a column).
        A surface whose waves all run one way is *constant* along one of the
        two once the camera is looking straight down at it, which is the
        measurement `test_the_sea_is_not_a_corrugated_roof` is built on.
        """
        width, height = self.FBO_SIZE

        def green(x: int, y: int) -> int:
            return frame[(y * width + x) * 4 + 1]

        # The outermost ring is left out: the water plane does not reach the
        # corners of a downward frame and the clear colour there would read as
        # variation in every direction.
        rows = max(
            max(green(x, y) for x in range(8, width - 8))
            - min(green(x, y) for x in range(8, width - 8))
            for y in range(8, height - 8)
        )
        columns = max(
            max(green(x, y) for y in range(8, height - 8))
            - min(green(x, y) for y in range(8, height - 8))
            for x in range(8, width - 8)
        )
        return rows, columns

    def test_the_sea_is_not_a_corrugated_roof(self) -> None:
        """Two sines make a grid, and a grid does not read as water.

        The document gives two wave directions; a surface built from those two
        alone is periodic in both, which draws as regular diamonds -- corrugated
        iron rather than a sea. Each is drawn with a second, finer wave turned
        off its heading, and this is what says so.

        Handed one heading for both documented waves, a sea built only from
        them varies along that heading and *not at all* across it. So the
        frame is measured along both screen axes and the smaller of the two
        has to be substantial: a sea that is flat along either axis is the
        cross-hatch coming back.

        The reflected direction is what carries this rather than the amount
        reflected. Straight down, Schlick's fifth power is almost flat -- a
        ripple barely changes how much sky comes back -- but it changes
        sharply *which* sky, so the horizon and the zenith are set to opposite
        colours and the surface is made a full mirror.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 0.0, 0.0)
        scene.sky_zenith_color = (0.0, 1.0, 0.0)
        scene.water_fresnel = (1.0, 0.0)
        # A steep sea: near the vertical the reflected ray swings with the
        # slope, and the document's own 0.18 is too gentle to swing it far.
        scene.water_ripple = (scene.water_ripple[0], 1.0)
        scene.water_waves = (1.0, 0.0, 1.0, 0.0)

        frame = self._frame(scene, *self.STEEP)

        across, down = self._line_variation(frame)
        self.assertGreater(
            min(across, down),
            30,
            f"the sea is flat along one axis: rows {across}, columns {down}",
        )

    def test_ripples_too_small_to_draw_are_not_drawn(self) -> None:
        """The one artefact that reads as a broken renderer.

        Waves are about four metres long and the plane runs for two
        kilometres, so most of it is being asked for a ripple narrower than a
        pixel. Sampled once per pixel that is not water, it is moire: a coarse
        pattern that crawls when the camera moves.

        Forced here rather than waited for -- the wavenumber is pushed up
        until every ripple in the frame is far below a pixel, which is what
        the far half of any real frame already looks like. The answer has to
        be a flat sea, and it has to be *the* flat sea: identical to the same
        scene with the waves switched off.
        """
        scene = self._scene()
        scene.sky_horizon_color = (1.0, 1.0, 1.0)
        scene.sky_zenith_color = (1.0, 1.0, 1.0)
        eye, target = (128.0, 128.0, 26.0), (168.0, 128.0, 20.0)

        number, slope = scene.water_ripple
        scene.water_ripple = (number * 400.0, slope)
        far_too_fine = self._frame(scene, eye, target)
        scene.water_ripple = (number, 0.0)
        flat = self._frame(scene, eye, target)

        self.assertLess(
            self._worst_difference(far_too_fine, flat),
            8,
            "sub-pixel ripples are being drawn, which is moire",
        )


class LargeRegionTextureGLTests(_GLTestBase):
    """A region holding more distinct textures than any fixed cache cap.

    This is the scenario that made the LRU caps (4e8de78, 782c9f4) the wrong
    fix and got them replaced by reference pruning in de96035: object textures
    are uploaded from inside the per-frame draw loop, so a capacity bound
    evicts textures that are still on screen and re-uploads them every frame,
    forever. Nothing in the test region comes close to that size, so the case
    is constructed here rather than observed in-world.

    `OBJECT_TEXTURE_BUDGET_BYTES` later put a ceiling back on top of the
    reference pruning, and the last two tests here are what says it did not
    reintroduce the thrash: they patch the budget below the visible set and
    render repeatedly, through the real draw loop rather than by calling the
    evictor.
    """

    #: Comfortably above the 256 cap those reverted commits used.
    TEXTURE_COUNT = 300

    def _scene_with_many_textures(self, tmpdir):
        import pygame

        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        for index in range(self.TEXTURE_COUNT):
            # Distinct files as well as distinct ids: sharing one path would
            # let a wrong implementation look right via the path check.
            surface = pygame.Surface((2, 2))
            surface.fill((index % 256, 30, 200))
            path = Path(tmpdir) / f"tex{index}.png"
            pygame.image.save(surface, str(path))

            texture_id = UUID(int=index + 1)
            scene.texture_paths[texture_id] = path
            scene.object_entities[index + 1] = SceneEntity(
                local_id=index + 1,
                pcode=9,
                kind="prim",
                # Spread them out so they are genuinely separate draws rather
                # than 300 prims occupying one pixel.
                position=(float(index % 20) - 10.0, float(index // 20) - 7.0, 0.0),
                scale=(0.5, 0.5, 0.5),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape="cube",
                default_texture_id=texture_id,
                name=None,
                tint=(255, 255, 255),
            )
        return scene

    def test_every_texture_survives_a_second_frame(self) -> None:
        import tempfile

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=30.0, yaw=0.0, pitch=0.5)
        camera.set_mode("orbit")
        camera.screen_size = self.FBO_SIZE

        with tempfile.TemporaryDirectory() as tmpdir:
            scene = self._scene_with_many_textures(tmpdir)
            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                renderer.render_gl(scene, aspect=1.0)
                after_first = dict(renderer._object_textures)
                renderer.render_gl(scene, aspect=1.0)
                after_second = dict(renderer._object_textures)
            finally:
                renderer.clear_caches()

        self.assertEqual(len(after_first), self.TEXTURE_COUNT)
        self.assertEqual(len(after_second), self.TEXTURE_COUNT)
        # Identity across frames is the real assertion: a capacity cap would
        # keep the count at its limit and silently re-upload, which a count
        # check alone would not distinguish from correct behaviour.
        for texture_id, texture in after_first.items():
            self.assertIs(
                after_second.get(texture_id),
                texture,
                f"texture {texture_id} was re-uploaded on the second frame",
            )

    def _budgeted_renderer(self, budget: int):
        from unittest.mock import patch

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        patcher = patch(
            "vibestorm.viewer3d.perspective.OBJECT_TEXTURE_BUDGET_BYTES", budget
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=30.0, yaw=0.0, pitch=0.5)
        camera.set_mode("orbit")
        camera.screen_size = self.FBO_SIZE
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def test_a_visible_set_over_budget_is_kept_rather_than_thrashed(self) -> None:
        """Every texture here is drawn every frame, and the budget is tiny.

        There is nothing safe to release, so the right answer is to hold the
        set and say so. Releasing any of it would decode and re-upload a PNG
        inside the draw loop on every frame from here on -- the collapse the
        reverted caps caused, arriving through a different door.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            scene = self._scene_with_many_textures(tmpdir)
            renderer = self._budgeted_renderer(1)
            try:
                renderer.render_gl(scene, aspect=1.0)
                first = dict(renderer._object_textures)
                renderer.render_gl(scene, aspect=1.0)
                renderer.render_gl(scene, aspect=1.0)
                second = dict(renderer._object_textures)
                exceeded = renderer._object_texture_budget_exceeded
            finally:
                renderer.clear_caches()

        self.assertEqual(len(second), self.TEXTURE_COUNT)
        for texture_id, texture in first.items():
            self.assertIs(
                second.get(texture_id),
                texture,
                f"texture {texture_id} was re-uploaded under budget pressure",
            )
        self.assertTrue(exceeded, "falling short of the budget was not recorded")

    def test_textures_that_stopped_being_drawn_are_evicted(self) -> None:
        """The case the budget exists for.

        The region still *references* all 300 -- `texture_paths` is untouched,
        so reference pruning frees nothing -- but only half are still on a
        prim. Walking away from a building leaves exactly this: the camera
        will never ask for those textures again, and nothing before this
        released them.
        """
        import tempfile

        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        keep = self.TEXTURE_COUNT // 2
        # Room for the half still drawn, and not a texture more.
        budget = _texture_memory_bytes((2, 2)) * keep

        with tempfile.TemporaryDirectory() as tmpdir:
            scene = self._scene_with_many_textures(tmpdir)
            renderer = self._budgeted_renderer(budget)
            try:
                renderer.render_gl(scene, aspect=1.0)
                self.assertEqual(len(renderer._object_textures), self.TEXTURE_COUNT)

                for local_id in list(scene.object_entities)[keep:]:
                    del scene.object_entities[local_id]
                renderer.render_gl(scene, aspect=1.0)
                renderer.render_gl(scene, aspect=1.0)

                still_drawn = {
                    entity.default_texture_id
                    for entity in scene.object_entities.values()
                }
                held = set(renderer._object_textures)
                evicted = renderer._object_textures_evicted
                exceeded = renderer._object_texture_budget_exceeded
            finally:
                renderer.clear_caches()

        self.assertLessEqual(len(held), keep)
        self.assertGreater(evicted, 0)
        self.assertFalse(exceeded)
        self.assertEqual(
            still_drawn - held, set(), "a texture still on a prim was evicted"
        )

    def test_the_budget_reaches_the_scene_through_a_real_frame(self) -> None:
        # The plumbing, not the policy: a budget computed perfectly and never
        # published tells nobody anything, and every unit test still passes.
        #
        # Two frames, because the prune runs at the top of one: the first
        # frame's prune sees an empty cache and cannot know the frame is about
        # to fill it. The verdict is always the previous frame's, which is the
        # most a per-frame budget can honestly say.
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            scene = self._scene_with_many_textures(tmpdir)
            renderer = self._budgeted_renderer(1)
            try:
                renderer.render_gl(scene, aspect=1.0)
                renderer.render_gl(scene, aspect=1.0)
                summary = scene.texture_vram_summary
            finally:
                renderer.clear_caches()

        self.assertIn("texture vram:", summary)
        self.assertIn("OVER BUDGET", summary)

    def test_leaving_the_region_releases_all_of_them(self) -> None:
        import tempfile

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=30.0, yaw=0.0, pitch=0.5)
        camera.set_mode("orbit")
        camera.screen_size = self.FBO_SIZE

        with tempfile.TemporaryDirectory() as tmpdir:
            scene = self._scene_with_many_textures(tmpdir)
            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                renderer.render_gl(scene, aspect=1.0)
                self.assertEqual(len(renderer._object_textures), self.TEXTURE_COUNT)

                # What apply_region_changed does to the scene.
                scene.texture_paths.clear()
                scene.object_entities.clear()
                renderer.render_gl(scene, aspect=1.0)

                self.assertEqual(renderer._object_textures, {})
                self.assertEqual(renderer._object_texture_paths, {})
            finally:
                renderer.clear_caches()


if __name__ == "__main__":
    unittest.main()


class TerrainTextureUploadTests(_GLTestBase):
    """The region's four ground textures go up together or not at all.

    The shader blends between adjacent elevation bands, so a missing texture
    does not leave a gap in one corner of the region: it puts a black stripe
    across every elevation that names it. Falling back to the flat fill until
    the set is complete looks like loading; a black stripe looks like a bug.
    """

    def _png(self, directory: Path, name: str, color: tuple[int, int, int]) -> Path:
        import pygame

        surface = pygame.Surface((4, 4))
        surface.fill(color)
        path = directory / name
        pygame.image.save(surface, str(path))
        return path

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        return PerspectiveRenderer(Camera3D(), ctx=self.ctx)

    def _scene(self, paths):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.terrain_texture_paths = paths
        scene.terrain_start_height = (10.0, 10.0, 10.0, 10.0)
        scene.terrain_height_range = (60.0, 60.0, 60.0, 60.0)
        return scene

    def test_the_shader_compiles(self) -> None:
        renderer = self._renderer()
        try:
            self.assertIsNotNone(renderer._terrain_texture_program)
        finally:
            renderer.clear_caches()

    def test_a_full_set_uploads(self) -> None:
        renderer = self._renderer()
        try:
            with tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                paths = tuple(
                    self._png(directory, f"t{index}.png", (index * 40, 100, 60))
                    for index in range(4)
                )
                self.assertTrue(
                    renderer._upload_terrain_textures(self.ctx, self._scene(paths))
                )
                self.assertEqual(len(renderer._terrain_textures), 4)
        finally:
            renderer.clear_caches()

    def test_a_partial_set_uploads_nothing(self) -> None:
        renderer = self._renderer()
        try:
            with tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                paths = (
                    self._png(directory, "t0.png", (200, 100, 60)),
                    self._png(directory, "t1.png", (100, 200, 60)),
                    None,
                    None,
                )
                self.assertFalse(
                    renderer._upload_terrain_textures(self.ctx, self._scene(paths))
                )
                self.assertEqual(renderer._terrain_textures, [])
        finally:
            renderer.clear_caches()

    def test_an_unchanged_set_is_not_re_uploaded(self) -> None:
        # This runs every frame. Re-decoding four PNGs at 60 Hz would cost more
        # than the map tile it replaced.
        renderer = self._renderer()
        try:
            with tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                paths = tuple(
                    self._png(directory, f"t{index}.png", (index * 40, 100, 60))
                    for index in range(4)
                )
                scene = self._scene(paths)
                self.assertTrue(renderer._upload_terrain_textures(self.ctx, scene))
                first = list(renderer._terrain_textures)
                self.assertTrue(renderer._upload_terrain_textures(self.ctx, scene))
                self.assertEqual(renderer._terrain_textures, first)
        finally:
            renderer.clear_caches()

    def test_the_textures_repeat(self) -> None:
        # They tile across the region; clamping would stretch one copy over
        # 256 m, which is the map-tile problem again in a different costume.
        renderer = self._renderer()
        try:
            with tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                paths = tuple(
                    self._png(directory, f"t{index}.png", (index * 40, 100, 60))
                    for index in range(4)
                )
                renderer._upload_terrain_textures(self.ctx, self._scene(paths))
                for texture in renderer._terrain_textures:
                    self.assertTrue(texture.repeat_x)
                    self.assertTrue(texture.repeat_y)
        finally:
            renderer.clear_caches()


class SkyGLTests(_GLTestBase):
    """The sky gradient and the sun.

    The sky was one flat fill the compositor cleared to, which read as a blue
    wall rather than as air. It is now a gradient with the sun drawn into it,
    both from the view ray, so there is nothing to see unless the shader is
    actually running.
    """

    def _camera(self, pitch: float = 0.0, yaw: float = 0.0):
        from vibestorm.viewer3d.camera import Camera3D

        # Off the region corner, so no ground or water is in shot.
        camera = Camera3D(target=(-400.0, -400.0, 60.0), distance=5.0, yaw=yaw, pitch=pitch)
        camera.set_mode("orbit")
        return camera

    def _render(self, camera, scene=None):
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene if scene is not None else Scene(), aspect=1.0)
            return self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
        finally:
            renderer.clear_caches()

    def test_the_sky_is_bluer_higher_up(self) -> None:
        # The whole point of a gradient. Looking up must not give the same
        # pixel as looking at the horizon.
        horizon = self._render(self._camera(pitch=0.0))
        overhead = self._render(self._camera(pitch=-math.pi / 2 + 0.2))

        self.assertNotEqual(horizon[:3], overhead[:3])
        self.assertGreater(
            horizon[0], overhead[0], "the horizon should be the paler end"
        )

    def test_the_sun_brightens_the_pixel_it_is_in(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        camera = self._camera(pitch=0.0, yaw=0.0)
        # The direction the camera looks, so the sun lands dead centre. An
        # orbit camera at yaw 0 and pitch 0 looks along -X.
        toward = Scene()
        toward.sun_direction = (-1.0, 0.0, 0.0)
        away = Scene()
        away.sun_direction = (1.0, 0.0, 0.0)

        lit = self._render(camera, toward)
        unlit = self._render(camera, away)

        self.assertGreater(
            sum(lit[:3]), sum(unlit[:3]), "the sun should brighten what it is in"
        )

    def test_turning_the_sky_off_leaves_the_clear_colour(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.render_sky = False
        self.assertEqual(self._render(self._camera(), scene)[:3], (0, 0, 0))

    def test_the_sky_never_occludes_the_world(self) -> None:
        # It draws first with the depth test off, which GL also takes as "do
        # not write depth". If it wrote depth at the far plane, distant
        # geometry would vanish behind an empty sky.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        camera = Camera3D(
            target=(128.0, 128.0, 0.0), distance=200.0, yaw=0.0, pitch=math.pi / 2 - 0.1
        )
        camera.set_mode("orbit")
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(Scene(), aspect=1.0)
            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            # Water is under this camera; if the sky had occluded it the pixel
            # would be the sky gradient, which is markedly less blue-dominant.
            self.assertGreater(b, max(r, g), "water should still be drawn")
        finally:
            renderer.clear_caches()


class UniformTexturePathGLTests(_GLTestBase):
    """A prim whose faces are all the same is drawn once, not six times.

    Cubes, cylinders and prisms are split into one mesh per SL face so a
    ``TextureEntry`` can put a different texture on each side. Almost no prim
    does: the usual thing in-world is one texture over the whole box, and the
    six passes then draw the same pixels six times over -- which was most of a
    frame in any region of size.

    The shortcut is only safe if it is invisible, so this renders the same
    uniformly textured prim both ways and compares the framebuffers pixel for
    pixel. ``PrimFaceMapGLTests`` covers the other half: a prim that *does*
    name a per-face texture still gets it, on the right face.
    """

    TILE = UUID("cccccccc-0000-0000-0000-000000000001")

    def _scene(self, shape: str):
        from vibestorm.viewer3d.scene import Scene, SceneEntity
        from vibestorm.world.texture_entry import TextureEntry

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.render_sky = False
        scene.texture_paths[self.TILE] = _write_solid_tile((40, 200, 90))
        scene.object_entities[1] = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
            # Turned off every axis, so the shortcut cannot pass by drawing a
            # symmetric silhouette that happens to match.
            rotation=(0.2, 0.3, 0.1, 0.927),
            rotation_z_radians=0.0,
            shape=shape,
            default_texture_id=self.TILE,
            texture_entry=TextureEntry(default_texture_id=self.TILE),
        )
        return scene

    def _frame(self, scene) -> bytes:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(target=(0.0, 0.0, 0.0), eye_position=(5.0, 4.0, 3.0))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self.fbo.read(components=4)
        finally:
            renderer.clear_caches()

    def _frame_forcing_the_face_path(self, scene) -> bytes:
        from vibestorm.viewer3d import perspective

        original = perspective._has_face_textures
        perspective._has_face_textures = lambda entity: True
        try:
            return self._frame(scene)
        finally:
            perspective._has_face_textures = original

    def test_a_uniform_cube_draws_the_same_either_way(self) -> None:
        scene = self._scene("cube")

        self.assertEqual(self._frame(scene), self._frame_forcing_the_face_path(scene))

    def test_a_uniform_cylinder_draws_the_same_either_way(self) -> None:
        scene = self._scene("cylinder")

        self.assertEqual(self._frame(scene), self._frame_forcing_the_face_path(scene))

    def test_a_uniform_prism_draws_the_same_either_way(self) -> None:
        scene = self._scene("prism")

        self.assertEqual(self._frame(scene), self._frame_forcing_the_face_path(scene))

    def test_the_prim_is_actually_on_screen(self) -> None:
        # Two identical black frames would satisfy the comparisons above.
        scene = self._scene("cube")

        self._frame(scene)
        pixel = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)

        self.assertGreater(pixel[1], max(pixel[0], pixel[2]), f"expected the tile, got {pixel}")


class MipmapGLTests(_GLTestBase):
    """Distant textures must not crawl.

    Without mipmaps a texture is point-sampled however small it is on screen,
    so a tiled wall down the street or a ground texture towards the horizon
    picks a near-random texel per pixel and shimmers as the camera moves. The
    terrain textures here were *built* with mipmaps and never sampled one:
    ``filter`` is ``(minification, magnification)``, and the minification half
    was ``LINEAR``, which does not consult a mipmap at all.

    The check is comparative, so it says nothing about how any particular GPU
    filters. It renders the same receding textured slab both ways and compares
    how much neighbouring pixels disagree: far away that must drop, and close
    up it must not, because blurring what is right in front of the camera
    would be its own bug.
    """

    FBO_SIZE = (256, 192)
    TILE = UUID("aaaaaaaa-0000-0000-0000-000000000009")

    def _checkerboard(self, size: int = 64, cell: int = 4) -> Path:
        import pygame

        surface = pygame.Surface((size, size))
        for y in range(size):
            for x in range(size):
                shade = 255 if ((x // cell + y // cell) % 2) else 0
                surface.set_at((x, y), (shade, shade, shade))
        path = Path(tempfile.mkdtemp()) / "checker.png"
        pygame.image.save(surface, str(path))
        return path

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.render_terrain = False
        scene.render_water = False
        scene.render_sky = False
        scene.texture_paths[self.TILE] = self._checkerboard()
        # A long slab running away from the camera, so one frame holds every
        # distance from underfoot to the vanishing point.
        scene.object_entities[1] = SceneEntity(
            local_id=1, pcode=9, kind="prim",
            position=(0.0, 200.0, 0.0), scale=(60.0, 400.0, 0.1),
            rotation=(0.0, 0.0, 0.0, 1.0), rotation_z_radians=0.0,
            shape="cube", default_texture_id=self.TILE,
        )
        return scene

    def _frame(self, *, filtering: str = "shipped") -> bytes:
        """One frame of the slab, filtered the shipped way or a poorer one.

        ``"none"`` is what was here before -- mipmaps unused, however far
        away. ``"isotropic"`` samples them but takes one sample per pixel, so
        a grazing surface drops to a level coarse enough for its squashed axis
        and loses everything across the other one.
        """
        from vibestorm.viewer3d import perspective
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(target=(0.0, 400.0, 0.4), eye_position=(0.0, 0.0, 1.2))
        camera.set_mode("free")
        camera.screen_size = self.FBO_SIZE

        original = perspective._minify_through_mipmaps
        if filtering == "none":
            perspective._minify_through_mipmaps = lambda ctx, texture: setattr(
                texture, "filter", (ctx.LINEAR, ctx.LINEAR)
            )
        elif filtering == "isotropic":

            def isotropic(ctx, texture) -> None:
                texture.build_mipmaps()
                texture.filter = (ctx.LINEAR_MIPMAP_LINEAR, ctx.LINEAR)

            perspective._minify_through_mipmaps = isotropic
        elif filtering != "shipped":
            raise ValueError(filtering)
        renderer = perspective.PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(self._scene(), aspect=self.FBO_SIZE[0] / self.FBO_SIZE[1])
            return self.fbo.read(components=4)
        finally:
            perspective._minify_through_mipmaps = original
            renderer.clear_caches()

    def _row_spread(self, data: bytes, rows: range) -> float:
        """How much neighbouring pixels disagree, averaged over some rows."""
        width, height = self.FBO_SIZE
        spreads = []
        for row in rows:
            gl_y = (height - 1) - row
            values = [data[(gl_y * width + x) * 4] for x in range(width)]
            spreads.append(statistics.pstdev(values))
        return statistics.mean(spreads)

    #: Rows near the vanishing line, where one screen pixel covers many texels.
    FAR = range(96, 104, 2)
    #: Rows well below it, a few metres in front of the camera.
    NEAR = range(112, 124, 2)

    def test_the_far_end_stops_crawling(self) -> None:
        without = self._row_spread(self._frame(filtering="none"), self.FAR)
        with_mipmaps = self._row_spread(self._frame(filtering="isotropic"), self.FAR)

        self.assertLess(
            with_mipmaps,
            without * 0.85,
            f"distant pixels should disagree less: {without:.1f} -> {with_mipmaps:.1f}",
        )

    def test_the_near_end_is_left_sharp(self) -> None:
        # Mipmaps that blurred what is right in front of the camera would trade
        # one artefact for a worse one.
        without = self._row_spread(self._frame(filtering="none"), self.NEAR)
        with_mipmaps = self._row_spread(self._frame(filtering="shipped"), self.NEAR)

        self.assertGreater(
            with_mipmaps,
            without * 0.95,
            f"close-up pixels should be as sharp: {without:.1f} -> {with_mipmaps:.1f}",
        )

    def test_anisotropy_gets_back_the_detail_mipmaps_throw_away(self) -> None:
        """The other half of the trade, and why mipmaps alone are not enough.

        A surface seen along itself is squashed hard in one direction and
        barely at all in the other. One sample per pixel forces a level coarse
        enough for the squashed axis, which throws away everything across the
        other -- ground that goes to mush a few metres out. Several samples
        along the squashed direction is exactly what anisotropic filtering is.

        Detail is measured as neighbouring pixels disagreeing, the same
        quantity as the aliasing above, and the two are only telling apart
        because the minification filter is a mipmap filter in *both* frames
        here: within that family a pixel cannot be a random texel, so what is
        left is detail.
        """
        isotropic = self._row_spread(self._frame(filtering="isotropic"), self.FAR)
        shipped = self._row_spread(self._frame(filtering="shipped"), self.FAR)

        self.assertGreater(
            shipped,
            isotropic * 1.05,
            f"the far ground should keep more detail: {isotropic:.1f} -> {shipped:.1f}",
        )

    def test_a_world_texture_asks_for_anisotropy(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import MAX_ANISOTROPY, PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            texture = renderer._upload_object_texture(self.ctx, self._scene(), self.TILE)

            self.assertIsNotNone(texture)
            self.assertEqual(
                texture.anisotropy, min(MAX_ANISOTROPY, self.ctx.max_anisotropy)
            )
        finally:
            renderer.clear_caches()

    def test_an_object_texture_minifies_through_its_mipmaps(self) -> None:
        # The specific bug: mipmaps built, and a minification filter that never
        # looks at one.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=self.ctx)
        try:
            scene = self._scene()
            texture = renderer._upload_object_texture(self.ctx, scene, self.TILE)

            self.assertIsNotNone(texture)
            minification, magnification = texture.filter
            self.assertEqual(minification, self.ctx.LINEAR_MIPMAP_LINEAR)
            self.assertEqual(magnification, self.ctx.LINEAR)
        finally:
            renderer.clear_caches()



class RegionWeatherGLTests(_GLTestBase):
    """The region's day cycle reaching actual pixels.

    Everything else about this path is arithmetic that can be checked without a
    GPU. What cannot is whether the numbers are *plumbed* -- a derivation that
    is perfect and never reaches a uniform draws exactly the sky it did before,
    and every unit test still passes.
    """

    def _scene_at(self, day_fraction: float):
        from pathlib import Path

        from vibestorm.caps.llsd import parse_xml_value
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.environment import parse_environment_document
        from vibestorm.world.models import SimulatorTimeSnapshot, WorldView

        view = WorldView()
        view.environment = parse_environment_document(
            parse_xml_value(
                Path("test/fixtures/environment/ext-environment-opensim.xml").read_bytes()
            )
        )
        length, offset = 14400, 57600
        view.latest_time = SimulatorTimeSnapshot(
            usec_since_start=int(((day_fraction * length) - offset) % length) * 1_000_000,
            sec_per_day=length,
            sec_per_year=31536000,
            sun_phase=0.0,
            sun_direction=(0.0, 0.0, 0.0),
        )
        scene = Scene()
        scene.refresh_from_world_view(view)
        return scene

    #: Where the sky camera looks: high, and west, away from the morning sun.
    #:
    #: Not *straight* up, which is the obvious choice and a trap -- `look_at`
    #: crosses the view direction with the world up, and a view along the up
    #: axis makes that cross product zero. The matrix collapses and the frame
    #: quietly shows the horizon instead, which is a plausible sky and the
    #: wrong one to be measuring.
    SKY_EYE = (128.0, 128.0, 30.0)
    SKY_TARGET = (110.6, 128.0, 128.5)

    def _sky_pixel(self, scene) -> tuple[int, int, int, int]:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(
            mode="eye", eye_position=self.SKY_EYE, target=self.SKY_TARGET
        )
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            return self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
        finally:
            renderer.clear_caches()

    def _expected_sky(self, scene) -> tuple[float, float, float]:
        """What the shader's gradient makes of the two colours in this view.

        `mix(horizon, zenith, sqrt(dir.z))`, spelled out here rather than
        borrowed, so the assertion is about the plumbing and not about the two
        sides agreeing with each other.
        """
        direction = [b - a for a, b in zip(self.SKY_EYE, self.SKY_TARGET, strict=True)]
        length = math.sqrt(sum(c * c for c in direction))
        blend = math.sqrt(max(0.0, min(1.0, direction[2] / length)))
        return tuple(
            h + (z - h) * blend
            for h, z in zip(scene.sky_horizon_color, scene.sky_zenith_color, strict=True)
        )

    def test_the_night_sky_is_darker_than_the_day_sky(self) -> None:
        day = self._sky_pixel(self._scene_at(0.5))
        night = self._sky_pixel(self._scene_at(0.0))

        self.assertGreater(sum(day[:3]), 60, "midday should not be nearly black")
        self.assertLess(sum(night[:3]), sum(day[:3]) / 2)

    def test_the_drawn_sky_is_the_colour_the_scene_derived(self) -> None:
        # At 0.3 rather than at noon: at noon the sun is overhead and its glow
        # is what a high camera would be measuring instead of the gradient.
        from vibestorm.viewer3d.atmosphere import (
            DEFAULT_SKY_HORIZON_COLOR,
            DEFAULT_SKY_ZENITH_COLOR,
        )
        from vibestorm.viewer3d.scene import Scene

        scene = self._scene_at(0.3)
        r, g, b, _ = self._sky_pixel(scene)

        for index, drawn in enumerate((r, g, b)):
            self.assertAlmostEqual(
                drawn, round(self._expected_sky(scene)[index] * 255), delta=6
            )

        # And it is not the sky this viewer drew before the region was asked.
        before = Scene()
        before.sky_horizon_color = DEFAULT_SKY_HORIZON_COLOR
        before.sky_zenith_color = DEFAULT_SKY_ZENITH_COLOR
        self.assertGreater(
            sum(
                abs(round(a * 255) - round(b * 255))
                for a, b in zip(
                    self._expected_sky(scene), self._expected_sky(before), strict=True
                )
            ),
            20,
        )

    def _sky_frame(self, scene, eye, target):
        """Render one sky-only frame and hand back every pixel of it."""
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        scene.render_water = False
        scene.render_terrain = False
        camera = Camera3D(mode="eye", eye_position=eye, target=target)
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=4)
        finally:
            renderer.clear_caches()
        return [tuple(data[i : i + 4]) for i in range(0, len(data), 4)]

    #: The star tests render into their own, larger buffer.
    #:
    #: A star is about a twentieth of a degree across, and the shared 64-pixel
    #: buffer spans 60 degrees -- so a star covers a twentieth of a pixel, and
    #: whether one shows up at all comes down to how near a pixel centre it
    #: lands. The first version of this test measured the *moon's* edge and
    #: passed with the star uniform wired to zero. At 256 the field is still
    #: sub-pixel but reliably samples a few dozen of them.
    STAR_FBO_SIZE = (256, 256)

    #: Where the star camera looks, and at what. High and west, and the moon
    #: is turned off in every star frame: at midnight it sits at the zenith,
    #: comfortably inside a 60-degree frame aimed 80 degrees up, and its edge
    #: is exactly the sharp bright thing these tests count.
    STAR_EYE = (128.0, 128.0, 30.0)
    STAR_TARGET = (110.6, 128.0, 128.5)

    def _starless_moon(self, day_fraction: float):
        scene = self._scene_at(day_fraction)
        scene.moon_level = 0.0
        scene.render_water = False
        scene.render_terrain = False
        return scene

    def _star_frame(self, scene, *, target=None, viewport_offset: int = 0):
        """Render one sky into a 256-pixel buffer and return all its pixels.

        `viewport_offset` shifts the viewport inside the buffer without
        touching the camera, which is the whole trick of the invariance test
        below: the same NDC, and so the same world direction, lands on a
        different `gl_FragCoord`.
        """
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        size = self.STAR_FBO_SIZE
        texture = self.ctx.texture(size, components=4)
        depth = self.ctx.depth_renderbuffer(size)
        fbo = self.ctx.framebuffer(color_attachments=[texture], depth_attachment=depth)
        camera = Camera3D(
            mode="eye",
            eye_position=self.STAR_EYE,
            target=target or self.STAR_TARGET,
        )
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            fbo.use()
            self.ctx.viewport = (viewport_offset, viewport_offset, *size)
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = fbo.read(components=4)
        finally:
            renderer.clear_caches()
            fbo.release()
            depth.release()
            texture.release()
            self.fbo.use()
            self.ctx.viewport = (0, 0, *self.FBO_SIZE)
        return [tuple(data[i : i + 4]) for i in range(0, len(data), 4)]

    def _speckles(self, frame, threshold: int = 60) -> int:
        """Pixels much brighter than the eight around them.

        Brightness alone cannot find a star: the daytime sky is brighter than
        any star, everywhere. What a star *is* is a sharp local peak, and a
        gradient, a sun glow and a moon's interior are all smooth.
        """
        width, height = self.STAR_FBO_SIZE
        total = 0
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                here = sum(frame[y * width + x][:3])
                around = [
                    sum(frame[(y + dy) * width + (x + dx)][:3])
                    for dy in (-1, 0, 1)
                    for dx in (-1, 0, 1)
                    if (dx, dy) != (0, 0)
                ]
                if here - (sum(around) / 8.0) > threshold:
                    total += 1
        return total

    def test_stars_come_out_at_night_and_not_before(self) -> None:
        """The night sky was an empty gradient, and `star_brightness` was in
        the document all along: exactly 500 in both night keyframes and
        exactly 0 in all six daytime ones.
        """
        night = self._speckles(self._star_frame(self._starless_moon(0.0)))
        day = self._speckles(self._star_frame(self._starless_moon(0.3)))

        self.assertGreater(night, 10, "no stars in the night sky")
        self.assertEqual(day, 0, "something is drawing stars in the daytime sky")

    def test_the_night_sky_is_stars_and_mostly_not_stars(self) -> None:
        # The upper bound, and it is not fussiness: a field with no threshold
        # on the cell hash puts a star in every cell, which is a grey wash
        # rather than a night sky and looks nothing like one.
        frame = self._star_frame(self._starless_moon(0.0))

        lit = sum(1 for r, g, b, _ in frame if r + g + b > 130)

        self.assertGreater(lit, 0)
        self.assertLess(lit, len(frame) // 100, "the sky is a wall of stars")

    def test_there_are_no_stars_below_the_horizon(self) -> None:
        # Under the horizon is ground, and the terrain and the sea are turned
        # off in these frames -- so without the check the sky quad happily
        # draws stars into the earth. At 0.05 rather than at midnight: the sun
        # is straight down at midnight and its own blob would be in the frame.
        scene = self._starless_moon(0.05)

        below = self._star_frame(
            scene, target=(self.STAR_EYE[0], self.STAR_EYE[1] + 0.01, -70.0)
        )

        self.assertEqual(self._speckles(below), 0)

    def test_the_stars_do_not_move_with_the_screen(self) -> None:
        """The one that a screenshot cannot show.

        Stars sit on the celestial sphere: the same world direction has to
        give the same star whatever pixel it lands on, or they swim about as
        the camera turns. Hashing the view direction does that; hashing
        anything that includes the screen position looks identical in a still
        and is wrong the moment anyone moves.

        Shifting the *viewport* inside a larger buffer, rather than moving the
        camera, is what isolates it: every NDC -- and so every world direction
        -- is unchanged, and only `gl_FragCoord` differs.
        """
        scene = self._starless_moon(0.0)
        shift = 16
        width, height = self.STAR_FBO_SIZE

        square = self._star_frame(scene)
        shifted = self._star_frame(scene, viewport_offset=shift)

        differing = 0
        for y in range(height - shift):
            for x in range(width - shift):
                here = square[y * width + x]
                there = shifted[(y + shift) * width + (x + shift)]
                if max(abs(here[i] - there[i]) for i in range(3)) > 3:
                    differing += 1
        self.assertEqual(differing, 0, "the star field follows the screen, not the sky")

    def test_the_moon_is_drawn_where_the_day_cycle_puts_it(self) -> None:
        """Straight up at midnight, and opposite the sun at every keyframe.

        The camera is aimed down the scene's own moon direction, so what this
        asserts is that the uniform and the derivation agree -- pointing it at
        a hardcoded bearing would pass just as well with the moon nailed to
        the sky.
        """
        scene = self._scene_at(0.0)
        direction = scene.moon_direction
        self.assertIsNotNone(direction)
        eye = (128.0, 128.0, 30.0)
        target = tuple(e + d * 100.0 for e, d in zip(eye, direction, strict=True))

        lit = self._sky_frame(scene, eye, target)
        centre = lit[(self.FBO_SIZE[1] // 2) * self.FBO_SIZE[0] + self.FBO_SIZE[0] // 2]

        gradient = sum(round(c * 255) for c in self._expected_sky(scene))
        self.assertGreater(
            sum(centre[:3]),
            gradient + 100,
            f"no moon at the direction the scene derived: {centre}",
        )

    def test_no_moon_is_drawn_when_the_region_says_none(self) -> None:
        scene = self._scene_at(0.0)
        direction = scene.moon_direction
        scene.moon_level = 0.0
        eye = (128.0, 128.0, 30.0)
        target = tuple(e + d * 100.0 for e, d in zip(eye, direction, strict=True))

        dark = self._sky_frame(scene, eye, target)
        centre = dark[(self.FBO_SIZE[1] // 2) * self.FBO_SIZE[0] + self.FBO_SIZE[0] // 2]

        gradient = sum(round(c * 255) for c in self._expected_sky(scene))
        self.assertLess(sum(centre[:3]), gradient + 40)

    #: Where the cloud camera looks: 40 degrees up and due north.
    #:
    #: The layer is flat and at a fixed height, so a ray straight up crosses
    #: one cell of it and a level ray never leaves the horizon fade. Forty
    #: degrees is where the perspective is, which is where the drawing is.
    CLOUD_EYE = (128.0, 128.0, 30.0)
    CLOUD_TARGET = (128.0, 228.0, 30.0 + 100.0 * math.tan(math.radians(40.0)))

    def _cloud_frame(self, scene):
        return self._star_frame(scene, target=self.CLOUD_TARGET)

    def _cloudy(self, day_fraction: float):
        scene = self._scene_at(day_fraction)
        scene.moon_level = 0.0
        scene.star_level = 0.0
        scene.render_water = False
        scene.render_terrain = False
        return scene

    def _spread(self, frame) -> int:
        """How far apart the brightest and dimmest pixels of a frame are.

        A cloudless sky is a gradient plus a sun glow, both smooth and both
        narrow across a 40-degree view. Cloud is the thing that puts a bright
        patch next to a dark one.
        """
        sums = [r + g + b for r, g, b, _ in frame]
        return max(sums) - min(sums)

    def test_the_region_draws_clouds(self) -> None:
        with_clouds = self._cloud_frame(self._cloudy(0.5))
        clear = self._cloudy(0.5)
        clear.cloud_cover = (0.0, 0.0, 0.0)
        without = self._cloud_frame(clear)

        self.assertGreater(
            self._spread(with_clouds),
            self._spread(without) + 60,
            "the sky looks the same with and without cloud",
        )

    def test_the_cloud_toggle_gives_the_sky_back(self) -> None:
        # Not decoration: the layer costs about five milliseconds of a
        # 1280x800 frame on llvmpipe, which is what this runs on.
        scene = self._cloudy(0.5)
        cloudy = self._spread(self._cloud_frame(scene))

        scene.render_clouds = False
        clear = self._spread(self._cloud_frame(scene))

        self.assertGreater(cloudy, clear + 60)

    def test_clouds_are_brighter_than_the_sky_at_noon(self) -> None:
        """The bug this catches is using `cloud_color` raw.

        It is 0.41 grey, against a noon sky near 0.5 blue, so drawn as-is the
        clouds are darker than what is behind them -- a permanent thunderstorm
        over every region. Lit, they are the brightest thing in a daytime sky.
        """
        frame = self._cloud_frame(self._cloudy(0.5))
        scene = self._cloudy(0.5)
        scene.cloud_cover = (0.0, 0.0, 0.0)
        clear = self._cloud_frame(scene)

        brightest_cloud = max(r + g + b for r, g, b, _ in frame)
        brightest_sky = max(r + g + b for r, g, b, _ in clear)

        self.assertGreater(brightest_cloud, brightest_sky)

    def test_the_night_clouds_are_darker_than_the_day_clouds(self) -> None:
        day = max(r + g + b for r, g, b, _ in self._cloud_frame(self._cloudy(0.5)))
        night = max(r + g + b for r, g, b, _ in self._cloud_frame(self._cloudy(0.0)))

        self.assertLess(night, day / 2)

    def test_the_clouds_drift_across_the_sky(self) -> None:
        # The drift reaches the shader, which no still frame can show.
        scene = self._cloudy(0.5)
        before = self._cloud_frame(scene)

        for _frame in range(600):
            scene.advance_clouds(1.0 / 60.0)
        after = self._cloud_frame(scene)

        moved = sum(
            1
            for a, b in zip(before, after, strict=True)
            if max(abs(a[i] - b[i]) for i in range(3)) > 6
        )
        self.assertGreater(moved, len(before) // 20, "the clouds did not move")

    def test_the_clouds_do_not_move_with_the_screen(self) -> None:
        # Same trap as the stars, and the same isolation: shifting the
        # viewport changes `gl_FragCoord` and nothing else, so a layer that
        # is a function of the view ray must come out identical.
        scene = self._cloudy(0.5)
        shift = 16
        width, height = self.STAR_FBO_SIZE

        square = self._star_frame(scene, target=self.CLOUD_TARGET)
        shifted = self._star_frame(
            scene, target=self.CLOUD_TARGET, viewport_offset=shift
        )

        differing = sum(
            1
            for y in range(height - shift)
            for x in range(width - shift)
            if max(
                abs(square[y * width + x][i] - shifted[(y + shift) * width + (x + shift)][i])
                for i in range(3)
            )
            > 3
        )
        self.assertEqual(differing, 0, "the cloud layer follows the screen")

    def _cloud_target(self, elevation_degrees: float):
        """A target that puts the frame's centre at this elevation."""
        return (
            self.STAR_EYE[0],
            self.STAR_EYE[1] + 100.0,
            self.STAR_EYE[2] + 100.0 * math.tan(math.radians(elevation_degrees)),
        )

    def _horizontal_edges(self, frame, low_row: int, high_row: int, threshold: int = 18):
        """How many pixels differ sharply from the one to their left.

        A proxy for how *fine* the cloud is in a band of the frame, which is
        the only thing that separates a layer with perspective from one
        without. Rows count from the bottom of the buffer, so a low row is a
        low patch of sky.
        """
        width = self.STAR_FBO_SIZE[0]
        total = 0
        for y in range(low_row, high_row):
            for x in range(1, width):
                here = sum(frame[y * width + x][:3])
                left = sum(frame[y * width + x - 1][:3])
                if abs(here - left) > threshold:
                    total += 1
        return total

    def _band_deviation(self, frame, other, low_row: int, high_row: int) -> int:
        width = self.STAR_FBO_SIZE[0]
        return max(
            abs(sum(frame[y * width + x][:3]) - sum(other[y * width + x][:3]))
            for y in range(low_row, high_row)
            for x in range(width)
        )

    def test_the_cloud_layer_has_perspective(self) -> None:
        """A flat sheet of noise pasted on the sky is not weather.

        The layer is a plane at a fixed height and the view ray crosses it, so
        the further down the frame you look the further across the plane the
        ray lands and the finer the cloud gets -- which is the whole reason it
        reads as sky. Dropping the divide by `dir.z` still draws convincing
        cloud in the middle of the frame; what it cannot do is compress it
        towards the horizon.
        """
        frame = self._star_frame(self._cloudy(0.5), target=self._cloud_target(21.5))

        near_horizon = self._horizontal_edges(frame, 70, 110)
        high_up = self._horizontal_edges(frame, 200, 240)

        self.assertGreater(
            near_horizon,
            high_up * 5,
            "the cloud is no finer near the horizon than overhead",
        )

    def test_the_clouds_fade_out_at_the_horizon(self) -> None:
        # Where the ray's crossing distance runs away, the noise runs past
        # what the pixels can sample and turns to fizz. Fading the layer out
        # before that is both the fix and what real cloud does into the haze.
        cloudy = self._cloudy(0.5)
        clear = self._cloudy(0.5)
        clear.cloud_cover = (0.0, 0.0, 0.0)
        target = self._cloud_target(21.5)

        frame = self._star_frame(cloudy, target=target)
        empty = self._star_frame(clear, target=target)

        self.assertLess(self._band_deviation(frame, empty, 40, 55), 40)
        # And it is a fade, not a curtain: higher up the same frame is cloud.
        self.assertGreater(self._band_deviation(frame, empty, 150, 200), 100)

    def test_the_cell_size_sets_how_big_the_clouds_are(self) -> None:
        # `cloud_scale` is the region's only say in this, and a shader that
        # divides by a constant instead draws the same sky for every region.
        target = self._cloud_target(21.5)
        small = self._cloudy(0.5)
        large = self._cloudy(0.5)
        large.cloud_scale_drift = (400.0, *large.cloud_scale_drift[1:])

        fine = self._horizontal_edges(self._star_frame(small, target=target), 70, 240)
        coarse = self._horizontal_edges(self._star_frame(large, target=target), 70, 240)

        self.assertGreater(fine, coarse * 4, "the cell size did not reach the shader")

    def test_the_drawn_horizon_is_the_horizon_colour(self) -> None:
        """The other half of the gradient, and it needed its own test.

        A mutation that sent only `u_horizon` back to its constant survived
        every other test here: the sky camera looks 80 degrees up, where the
        gradient is 99 per cent zenith, so a wrong horizon moved the pixel by
        less than a level of quantisation. This one looks level.
        """
        from vibestorm.viewer3d.atmosphere import DEFAULT_SKY_HORIZON_COLOR
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        scene = self._scene_at(0.3)
        # Level and due north, away from the 45-degree morning sun in the east,
        # and with the water off so the lower half of the sky is not covered.
        scene.render_water = False
        camera = Camera3D(
            mode="eye",
            eye_position=(128.0, 128.0, 30.0),
            target=(128.0, 228.0, 30.0),
        )
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            r, g, b, _ = self._read_pixel(
                self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2
            )
        finally:
            renderer.clear_caches()

        for index, drawn in enumerate((r, g, b)):
            self.assertAlmostEqual(
                drawn, round(scene.sky_horizon_color[index] * 255), delta=6
            )
        self.assertGreater(
            sum(
                abs(round(a * 255) - round(c * 255))
                for a, c in zip(
                    scene.sky_horizon_color, DEFAULT_SKY_HORIZON_COLOR, strict=True
                )
            ),
            20,
        )

    def test_the_water_takes_the_regions_colour(self) -> None:
        """The sea drawn is the one this region's document describes.

        Which is no longer a single colour to compare against: the sea is its
        own fog with as much of *this region's* sky in it as the angle calls
        for. So the pixel is checked against that whole expression, and then
        against the same expression with the fallback sky in it -- the second
        is what a renderer ignoring the region would draw, and the two have to
        be far enough apart that passing the first means something.
        """
        from vibestorm.viewer3d.atmosphere import (
            DEFAULT_SKY_HORIZON_COLOR,
            DEFAULT_SKY_ZENITH_COLOR,
        )
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        scene = self._scene_at(0.5)
        scene.render_sky = False  # water over the clear colour, not over sky
        # Waves off: with them on the centre pixel is on some part of a ripple
        # and the prediction would have to guess which.
        scene.water_ripple = (scene.water_ripple[0], 0.0)
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
            renderer.render_gl(scene, aspect=1.0)
            r, g, b, _ = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
        finally:
            renderer.clear_caches()

        wr, wg, wb, wa = _flat_sea_pixel(camera, scene)
        expected = [round(channel * wa * 255) for channel in (wr, wg, wb)]
        for index, drawn in enumerate((r, g, b)):
            self.assertAlmostEqual(drawn, expected[index], delta=8)

        scene.sky_horizon_color = DEFAULT_SKY_HORIZON_COLOR
        scene.sky_zenith_color = DEFAULT_SKY_ZENITH_COLOR
        fr, fg, fb, fa = _flat_sea_pixel(camera, scene)
        fallback = [round(channel * fa * 255) for channel in (fr, fg, fb)]
        self.assertGreater(
            sum(abs(a - b) for a, b in zip(expected, fallback, strict=True)),
            25,
            "the region's sea is indistinguishable from the fallback one",
        )

    def test_the_sun_is_drawn_where_the_day_cycle_puts_it(self) -> None:
        # Looking east at dawn should find the sun's glow; looking west at the
        # same moment should not. Before this the sun sat in one fixed spot
        # whatever the region or the hour.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        scene = self._scene_at(0.125)

        def brightness(target: tuple[float, float, float]) -> int:
            camera = Camera3D(
                mode="eye", eye_position=(128.0, 128.0, 30.0), target=target
            )
            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)
                pixel = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            finally:
                renderer.clear_caches()
            return sum(pixel[:3])

        # The dawn sun sits at +5.4 degrees, due east.
        east = brightness((228.0, 128.0, 39.5))
        west = brightness((28.0, 128.0, 39.5))

        self.assertGreater(east, west * 1.5)

    def test_the_ground_goes_dark_at_night_too(self) -> None:
        # The sky dimmed before this and nothing under it did, so a region at
        # midnight was a black sky over a field in full sun.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import SceneEntity

        def ground(day_fraction: float) -> int:
            # A white prim rather than the terrain: an empty scene has no
            # heightmap, so nothing would draw and both readings would be the
            # clear colour, agreeing perfectly and saying nothing.
            scene = self._scene_at(day_fraction)
            scene.render_sky = False
            scene.render_water = False
            scene.object_entities[1] = SceneEntity(
                local_id=1,
                pcode=9,
                kind="prim",
                position=(128.0, 128.0, 25.0),
                scale=(4.0, 4.0, 4.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape=None,
                default_texture_id=None,
                name=None,
                tint=(255, 255, 255),
            )
            camera = Camera3D(
                target=(128.0, 128.0, 25.0),
                distance=12.0,
                yaw=0.0,
                pitch=0.3,
            )
            camera.set_mode("orbit")
            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)
                pixel = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            finally:
                renderer.clear_caches()
            return sum(pixel[:3])

        day = ground(0.5)
        night = ground(0.0)

        self.assertGreater(day, 30, "the ground should be lit at midday")
        self.assertLess(night, day / 2)
        self.assertGreater(night, 0, "and not absolutely black -- the moon is real")

    def test_the_light_takes_the_colour_of_the_hour(self) -> None:
        # A white prim lit by a dawn sky should not come back grey. The tint
        # lives in `ambient`: pink at dawn, warm at dusk, blue at midnight --
        # and a light term that stayed a scalar would render all three the same
        # shade of the prim's own colour.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer
        from vibestorm.viewer3d.scene import SceneEntity

        def lit(day_fraction: float) -> tuple[int, int, int]:
            scene = self._scene_at(day_fraction)
            scene.render_sky = False
            scene.render_water = False
            scene.object_entities[1] = SceneEntity(
                local_id=1,
                pcode=9,
                kind="prim",
                position=(128.0, 128.0, 25.0),
                scale=(4.0, 4.0, 4.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                rotation_z_radians=0.0,
                shape=None,
                default_texture_id=None,
                name=None,
                tint=(255, 255, 255),
            )
            camera = Camera3D(
                target=(128.0, 128.0, 25.0), distance=12.0, yaw=0.0, pitch=0.3
            )
            camera.set_mode("orbit")
            renderer = PerspectiveRenderer(camera, ctx=self.ctx)
            try:
                self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
                renderer.render_gl(scene, aspect=1.0)
                pixel = self._read_pixel(self.FBO_SIZE[0] // 2, self.FBO_SIZE[1] // 2)
            finally:
                renderer.clear_caches()
            return pixel[:3]

        dawn = lit(0.125)
        night = lit(0.0)

        # Dawn is warm: more red than blue on a white prim.
        self.assertGreater(dawn[0], dawn[2])
        # Night is cold: more blue than red.
        self.assertGreater(night[2], night[0])
