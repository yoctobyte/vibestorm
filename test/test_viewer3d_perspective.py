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
import struct
import unittest
from uuid import UUID

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


class Viewer3DParserTests(unittest.TestCase):
    def test_defaults_start_in_3d_with_modest_frame_cap(self) -> None:
        from vibestorm.viewer3d.app import build_parser

        args = build_parser().parse_args(
            [
                "--login-uri", "http://example.test",
                "--first", "A",
                "--last", "B",
                "--password", "pw",
            ]
        )

        self.assertEqual(args.render_mode, "3d")
        # 60, not the 20 it shipped with. The cap was set when the renderer
        # was much slower, and it outlived the reason: the owner reported "we
        # have around 14fps, that raises to 20fps if I shrink the window" and
        # concluded the viewer was on a software rasteriser. It was not -- 20
        # was the cap, and the window size mattered because the HUD work above
        # it was what kept the loop from reaching even that.
        self.assertEqual(args.max_fps, 60.0)
        self.assertEqual(args.debug_terrain, "off")
        self.assertEqual(args.terrain_z_scale, 1.0)

    def test_debug_terrain_parser_accepts_synthetic(self) -> None:
        from vibestorm.viewer3d.app import build_parser

        args = build_parser().parse_args(
            [
                "--login-uri", "http://example.test",
                "--first", "A",
                "--last", "B",
                "--password", "pw",
                "--debug-terrain", "synthetic",
            ]
        )

        self.assertEqual(args.debug_terrain, "synthetic")


if __name__ == "__main__":
    unittest.main()


class VoidWaterTests(unittest.TestCase):
    """Water past the region edge.

    The water plane used to be exactly the region square, 0..256. From any
    height that showed the sea ending in a hard straight line with sky beyond
    it, which is the one thing a horizon must never do.
    """

    def test_water_extends_past_the_region_on_every_side(self) -> None:
        from vibestorm.viewer3d.perspective import (
            REGION_GROUND_SIZE_M,
            VOID_WATER_EXTENT_M,
            _water_vertices,
        )

        vertices = _water_vertices(20.0)
        xs = vertices[0::3]
        ys = vertices[1::3]

        self.assertEqual(min(xs), -VOID_WATER_EXTENT_M)
        self.assertEqual(min(ys), -VOID_WATER_EXTENT_M)
        self.assertEqual(max(xs), REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M)
        self.assertEqual(max(ys), REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M)

    def test_it_reaches_at_least_as_far_as_the_camera_can_see(self) -> None:
        # Anything short of the far plane puts the edge back on screen, just
        # further away.
        from vibestorm.viewer3d.camera import DEFAULT_FAR_PLANE_M
        from vibestorm.viewer3d.perspective import VOID_WATER_EXTENT_M

        self.assertGreaterEqual(VOID_WATER_EXTENT_M, DEFAULT_FAR_PLANE_M)

    def test_every_corner_sits_at_the_water_height(self) -> None:
        from vibestorm.viewer3d.perspective import _water_vertices

        self.assertEqual(set(_water_vertices(20.0)[2::3]), {20.0})


def _cube(local_id: int, position=(10.0, 10.0, 25.0), *, texture_entry=None):
    from vibestorm.viewer3d.scene import PCODE_PRIM, SceneEntity

    return SceneEntity(
        local_id=local_id,
        pcode=PCODE_PRIM,
        kind="prim",
        position=position,
        scale=(0.5, 0.5, 0.5),
        rotation=(0.0, 0.0, 0.0, 1.0),
        rotation_z_radians=0.0,
        texture_entry=texture_entry,
    )


class InstanceBlobCacheTests(unittest.TestCase):
    """One prim's model matrix and tint, packed once rather than per face.

    A cube is drawn as six meshes so its sides can carry different textures,
    and every one of those passes was rebuilding the same nineteen floats for
    every prim in the region -- six times the work for identical bytes. None of
    it depends on the face, and none of it changes while the prim sits still.

    ``Scene`` hands back the same ``SceneEntity`` for anything it did not
    rebuild, so identity is what says the packed bytes are still good. The
    failure worth guarding is the stale one: a prim that moved and kept the
    bytes that put it where it was.
    """

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # No GL context: the packing is arithmetic, and the buffers it feeds
        # are allocated lazily.
        return PerspectiveRenderer(Camera3D())

    def test_the_same_entity_packs_once(self) -> None:
        renderer = self._renderer()
        entity = _cube(1)

        first = renderer._instance_blob(entity)

        self.assertIs(renderer._instance_blob(entity), first)

    def test_a_prim_that_moved_is_repacked(self) -> None:
        renderer = self._renderer()
        renderer._instance_blob(_cube(1, (10.0, 10.0, 25.0)))

        moved = renderer._instance_blob(_cube(1, (40.0, 10.0, 25.0)))

        # The translation is the last column of the model matrix.
        self.assertEqual(struct.unpack("19f", moved)[12], 40.0)

    def test_the_pack_is_the_model_matrix_and_the_tint(self) -> None:
        from vibestorm.viewer3d.perspective import model_matrix

        renderer = self._renderer()
        entity = _cube(1, (3.0, 4.0, 5.0))

        packed = struct.unpack("19f", renderer._instance_blob(entity))

        # Through float32 and back, so compare to the precision the GPU gets.
        expected = model_matrix(entity.position, entity.scale, entity.rotation)
        r, g, b = entity.tint
        for got, want in zip(
            packed, (*expected, r / 255.0, g / 255.0, b / 255.0), strict=True
        ):
            self.assertAlmostEqual(got, want, places=6)

    def test_prims_that_left_the_region_are_forgotten(self) -> None:
        # Otherwise walking a grid keeps one packed record per prim ever seen.
        from vibestorm.viewer3d.scene import Scene

        renderer = self._renderer()
        for local_id in range(500):
            renderer._instance_blob(_cube(local_id))

        renderer._prune_instance_blobs(Scene())

        self.assertEqual(renderer._instance_blobs, {})

    def test_prims_still_in_view_are_kept(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        renderer = self._renderer()
        scene = Scene()
        for local_id in range(500):
            entity = _cube(local_id)
            scene.object_entities[local_id] = entity
            renderer._instance_blob(entity)

        renderer._prune_instance_blobs(scene)

        self.assertEqual(len(renderer._instance_blobs), 500)


class FaceTextureSplitTests(unittest.TestCase):
    """Which prims actually need drawing a face at a time.

    Six draw passes per cube exist so a ``TextureEntry`` can put a different
    texture on each side. A prim that names none wears its default all over,
    and the six passes then draw the same pixels six times.
    """

    def test_a_prim_with_no_texture_entry_does_not(self) -> None:
        from vibestorm.viewer3d.perspective import _has_face_textures

        self.assertFalse(_has_face_textures(_cube(1)))

    def test_a_uniformly_textured_prim_does_not(self) -> None:
        # The common case in-world: one texture, applied to the whole prim.
        from vibestorm.viewer3d.perspective import _has_face_textures
        from vibestorm.world.texture_entry import TextureEntry

        entry = TextureEntry(default_texture_id=UUID(int=7))

        self.assertFalse(_has_face_textures(_cube(1, texture_entry=entry)))

    def test_a_prim_with_one_face_overridden_does(self) -> None:
        from vibestorm.viewer3d.perspective import _has_face_textures
        from vibestorm.world.texture_entry import TextureEntry

        entry = TextureEntry(
            default_texture_id=UUID(int=7),
            face_texture_ids=((2, UUID(int=9)),),
        )

        self.assertTrue(_has_face_textures(_cube(1, texture_entry=entry)))


if __name__ == "__main__":
    unittest.main()
