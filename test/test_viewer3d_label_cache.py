"""Tests for the renderer's GL caches.

Three caches hold GPU memory keyed by something that grows over a session:
label textures by the text itself, object textures by asset UUID, and decoded
mesh geometry by asset UUID. All three release by **reference** — anything the
current region (or, for labels, the current frame) still refers to is kept, and
everything else is freed.

That choice is the point of these tests. A least-recently-used cap looks like
the obvious design and is wrong here: uploads happen inside the per-frame draw
loop, so the moment a region holds more textures than the cap, the cap evicts
things that are still on screen and they are re-decoded and re-uploaded every
single frame. Reference pruning cannot do that, because the live set is exactly
what the draw loop is able to ask for.

A stub context stands in for GL so the policy is testable without a GPU; the
rendering itself is covered by the GL tests elsewhere.
"""

import unittest
from uuid import UUID

from vibestorm.viewer3d.perspective import PerspectiveRenderer


class _StubTexture:
    def __init__(self) -> None:
        self.released = False
        self.filter = None
        self.repeat_x = True
        self.repeat_y = True

    def release(self) -> None:
        self.released = True


class _StubContext:
    """Just enough moderngl surface for the rasteriser and uploader."""

    LINEAR = "linear"

    def __init__(self) -> None:
        self.textures: list[_StubTexture] = []

    def texture(self, size, components, data):  # noqa: ANN001 - stub
        del size, components, data
        texture = _StubTexture()
        self.textures.append(texture)
        return texture


class _RendererTestCase(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pygame not available")
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.scene import Scene

        # ctx=None skips _setup_gl, leaving the caches empty and inert.
        self.renderer = PerspectiveRenderer(Camera3D(), ctx=None)
        self.ctx = _StubContext()
        self.scene = Scene()


class LabelCacheTests(_RendererTestCase):
    def _texture_for(self, text: str):
        return self.renderer._hover_text_texture(self.ctx, text)

    def test_identical_text_is_rasterised_once(self) -> None:
        first = self._texture_for("For Sale")
        second = self._texture_for("For Sale")

        self.assertIs(first, second)
        self.assertEqual(len(self.ctx.textures), 1)

    def test_distinct_text_gets_its_own_texture(self) -> None:
        self._texture_for("one")
        self._texture_for("two")

        self.assertEqual(len(self.ctx.textures), 2)

    def test_text_no_longer_shown_is_released(self) -> None:
        # The changing-text case: a clock prim leaves one dead texture behind
        # per tick, and only pruning frees them.
        for tick in range(20):
            self._texture_for(f"12:{tick:02d}")
        self.renderer._prune_label_textures({"12:19"})

        self.assertEqual(len(self.renderer._hover_text_textures), 1)
        self.assertEqual(len([t for t in self.ctx.textures if t.released]), 19)

    def test_visible_text_is_never_released(self) -> None:
        self._texture_for("kept")
        self._texture_for("dropped")

        self.renderer._prune_label_textures({"kept"})

        self.assertIn("kept", self.renderer._hover_text_textures)
        self.assertNotIn("dropped", self.renderer._hover_text_textures)

    def test_many_simultaneous_labels_all_survive(self) -> None:
        # The case a count cap would break: more labels on screen at once than
        # any fixed cap, each of which must stay uploaded.
        texts = {f"label {index}" for index in range(500)}
        for text in texts:
            self._texture_for(text)

        self.renderer._prune_label_textures(texts)

        self.assertEqual(len(self.renderer._hover_text_textures), 500)
        self.assertEqual([t for t in self.ctx.textures if t.released], [])

    def test_pruning_to_nothing_releases_everything(self) -> None:
        self._texture_for("gone")

        self.renderer._prune_label_textures(set())

        self.assertEqual(self.renderer._hover_text_textures, {})
        self.assertTrue(self.ctx.textures[0].released)


class ObjectTextureCacheTests(_RendererTestCase):
    """Object textures are keyed by asset UUID and pruned to the region."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile
        from pathlib import Path

        import pygame

        # One real PNG on disk, reused for every id: the cache keys on the
        # texture UUID, so distinct ids are what matters, not distinct pixels.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        surface = pygame.Surface((2, 2))
        surface.fill((10, 20, 30))
        self._png_path = Path(self._tmp.name) / "tex.png"
        pygame.image.save(surface, str(self._png_path))

    def _upload(self, index: int) -> None:
        """Upload through the real code path, not by poking the dict.

        Going through ``_upload_object_texture`` is the point: an earlier
        version of this test called the evictor directly, so removing its call
        site from the upload path still passed.
        """
        texture_id = UUID(int=index)
        self.scene.texture_paths[texture_id] = self._png_path
        self.renderer._upload_object_texture(self.ctx, self.scene, texture_id)

    def test_the_same_id_is_uploaded_once(self) -> None:
        self._upload(1)
        self._upload(1)
        self._upload(1)

        self.assertEqual(len(self.ctx.textures), 1)

    def test_a_large_region_keeps_every_visible_texture(self) -> None:
        # A count cap would evict these mid-frame and re-upload them on the
        # next one, forever. Reference pruning must leave all of them alone.
        for index in range(400):
            self._upload(index)

        self.renderer._prune_object_textures(self.scene)

        self.assertEqual(len(self.renderer._object_textures), 400)
        self.assertEqual([t for t in self.ctx.textures if t.released], [])

    def test_leaving_a_region_frees_its_textures(self) -> None:
        for index in range(5):
            self._upload(index)
        # What apply_region_changed does to the scene.
        self.scene.texture_paths.clear()

        self.renderer._prune_object_textures(self.scene)

        self.assertEqual(self.renderer._object_textures, {})
        self.assertEqual(len([t for t in self.ctx.textures if t.released]), 5)

    def test_path_bookkeeping_stays_in_lockstep(self) -> None:
        # A path entry outliving its texture would leave this dict growing
        # even though the texture cache is pruned.
        for index in range(5):
            self._upload(index)
        self.scene.texture_paths.clear()

        self.renderer._prune_object_textures(self.scene)

        self.assertEqual(self.renderer._object_texture_paths, {})

    def test_a_texture_still_referenced_is_kept(self) -> None:
        self._upload(1)
        self._upload(2)
        del self.scene.texture_paths[UUID(int=2)]

        self.renderer._prune_object_textures(self.scene)

        self.assertIn(UUID(int=1), self.renderer._object_textures)
        self.assertNotIn(UUID(int=2), self.renderer._object_textures)


class _StubBuffer:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class MeshAssetCacheTests(_RendererTestCase):
    """Decoded mesh geometry is keyed by asset UUID and pruned to the region."""

    def _install_mesh(self, index: int):
        from vibestorm.viewer3d.perspective import _mesh_asset_shape_key, _ShapeMesh

        mesh_id = UUID(int=index)
        shape_key = _mesh_asset_shape_key(mesh_id)
        mesh = _ShapeMesh(
            vbo=_StubBuffer(), ibo=_StubBuffer(), vao=_StubBuffer(), index_count=3
        )
        self.renderer._shape_meshes[shape_key] = mesh
        self.renderer._mesh_asset_paths[mesh_id] = f"/tmp/{index}.llmesh"
        self.renderer._mesh_uv_shape_keys.add(shape_key)
        self.scene.mesh_paths[mesh_id] = f"/tmp/{index}.llmesh"
        return mesh, shape_key

    def test_leaving_a_region_releases_mesh_buffers(self) -> None:
        mesh, shape_key = self._install_mesh(1)
        self.scene.mesh_paths.clear()

        self.renderer._prune_mesh_assets(self.scene)

        self.assertNotIn(shape_key, self.renderer._shape_meshes)
        self.assertTrue(mesh.vbo.released)
        self.assertTrue(mesh.ibo.released)
        self.assertTrue(mesh.vao.released)
        self.assertEqual(self.renderer._mesh_asset_paths, {})
        self.assertNotIn(shape_key, self.renderer._mesh_uv_shape_keys)

    def test_a_referenced_mesh_is_kept(self) -> None:
        _mesh, shape_key = self._install_mesh(1)

        self.renderer._prune_mesh_assets(self.scene)

        self.assertIn(shape_key, self.renderer._shape_meshes)

    def test_builtin_shape_meshes_are_never_touched(self) -> None:
        # The built-in prim meshes share _shape_meshes with mesh assets, and
        # releasing one would break every prim of that shape.
        from vibestorm.viewer3d.perspective import _ShapeMesh

        builtin = _ShapeMesh(
            vbo=_StubBuffer(), ibo=_StubBuffer(), vao=_StubBuffer(), index_count=36
        )
        self.renderer._shape_meshes["cube"] = builtin
        self._install_mesh(1)
        self.scene.mesh_paths.clear()

        self.renderer._prune_mesh_assets(self.scene)

        self.assertIn("cube", self.renderer._shape_meshes)
        self.assertFalse(builtin.vbo.released)


if __name__ == "__main__":
    unittest.main()
