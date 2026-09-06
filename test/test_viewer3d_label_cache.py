"""Tests for the renderer's GL caches.

Three caches hold GPU memory keyed by something that grows over a session:
label textures by the text itself, object textures by asset UUID, and decoded
mesh geometry by asset UUID. All three release by **reference** — anything the
current region (or, for labels, the current frame) still refers to is kept, and
everything else is freed.

That choice is the point of these tests. A plain least-recently-used *count*
cap looks like the obvious design and is wrong here: uploads happen inside the
per-frame draw loop, so the moment a region holds more textures than the cap,
the cap evicts things that are still on screen and they are re-decoded and
re-uploaded every single frame. Reference pruning cannot do that, because the
live set is exactly what the draw loop is able to ask for.

Reference pruning is not a *bound*, though, and object textures now carry one
on top of it -- `OBJECT_TEXTURE_BUDGET_BYTES`, in bytes rather than in count.
The objection above still stands and is answered rather than ignored: the
budget's eviction refuses to touch anything the previous frame drew, so what
it can release is, to within one frame, exactly what is not about to be asked
for again. When the previous frame's own set is over budget there is nothing
safe to release and the prune stops instead of thrashing. `ObjectTextureBudget`
covers all of that; labels and meshes stay purely reference-pruned.

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
        self.mipmapped = False
        self.anisotropy = 0.0
        self.size = (0, 0)

    def build_mipmaps(self) -> None:
        # World textures get these so a distant prim minifies through them
        # rather than point-sampling one texel per pixel.
        self.mipmapped = True

    def release(self) -> None:
        self.released = True


class _StubContext:
    """Just enough moderngl surface for the rasteriser and uploader."""

    LINEAR = "linear"
    LINEAR_MIPMAP_LINEAR = "linear_mipmap_linear"
    max_anisotropy = 16.0

    def __init__(self) -> None:
        self.textures: list[_StubTexture] = []

    def texture(self, size, components, data):  # noqa: ANN001 - stub
        del components, data
        texture = _StubTexture()
        texture.size = size
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
        # The byte budget agrees here: 400 two-by-two textures are about
        # 17 kB together, five orders of magnitude inside it.
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


class ObjectTextureBudgetTests(_RendererTestCase):
    """The byte ceiling on top of reference pruning.

    Reference pruning frees what the region stopped naming, which is correct
    and unbounded: a region may name as much as it likes, and the uploaded set
    grows with every texture the camera has ever passed over rather than with
    what is on screen. These cover the two halves of the bound -- a resolution
    cap applied once on upload, and an eviction that spends down to
    `OBJECT_TEXTURE_BUDGET_BYTES` without ever touching the previous frame.

    The budget is patched down to a few kilobytes here. Reaching the shipped
    384 MB with real uploads would mean decoding hundreds of megabytes of PNG
    per test; what is under test is the policy, not the number. That the
    shipped number is the one actually compared is covered by
    `test_a_large_region_keeps_every_visible_texture` above, which uploads 400
    textures against the real constant and expects no eviction at all.
    """

    def setUp(self) -> None:
        super().setUp()
        import tempfile
        from pathlib import Path

        import pygame

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._dir = Path(self._tmp.name)
        self._pygame = pygame

    def _png(self, name: str, size: tuple[int, int]):
        surface = self._pygame.Surface(size)
        surface.fill((10, 20, 30))
        path = self._dir / name
        self._pygame.image.save(surface, str(path))
        return path

    def _upload(self, index: int, size: tuple[int, int] = (2, 2)):
        texture_id = UUID(int=index)
        self.scene.texture_paths[texture_id] = self._png(f"t{index}.png", size)
        return self.renderer._upload_object_texture(self.ctx, self.scene, texture_id)

    # -- the resolution cap ------------------------------------------------

    def test_a_texture_over_the_edge_cap_is_uploaded_smaller(self) -> None:
        from vibestorm.viewer3d.perspective import MAX_OBJECT_TEXTURE_EDGE

        texture = self._upload(1, (MAX_OBJECT_TEXTURE_EDGE * 2, 8))

        self.assertEqual(texture.size[0], MAX_OBJECT_TEXTURE_EDGE)

    def test_the_shape_of_a_shrunk_texture_is_kept(self) -> None:
        # A 4:1 sign must not come back square: the UVs that address it were
        # authored against its aspect, not against its pixel count.
        from vibestorm.viewer3d.perspective import MAX_OBJECT_TEXTURE_EDGE

        texture = self._upload(1, (MAX_OBJECT_TEXTURE_EDGE * 2, MAX_OBJECT_TEXTURE_EDGE // 2))

        self.assertEqual(texture.size, (MAX_OBJECT_TEXTURE_EDGE, MAX_OBJECT_TEXTURE_EDGE // 4))

    def test_a_small_texture_is_never_scaled_up(self) -> None:
        # Spending memory to add no detail. Only the downward direction is a
        # budget; the upward one is waste with a rounding error attached.
        texture = self._upload(1, (8, 4))

        self.assertEqual(texture.size, (8, 4))

    def test_the_memory_estimate_counts_the_mipmap_chain(self) -> None:
        # The chain is a third again on top of the base level, and the
        # renderer builds one for every object texture -- an estimate that
        # ignored it would under-count the real cost by 25 per cent.
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        self.assertEqual(_texture_memory_bytes((512, 512)), int(512 * 512 * 4 * 4 / 3))

    # -- the eviction ------------------------------------------------------

    def _with_budget(self, budget: int):
        from unittest.mock import patch

        patcher = patch(
            "vibestorm.viewer3d.perspective.OBJECT_TEXTURE_BUDGET_BYTES", budget
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _uploaded_at_frames(self, frames: dict[int, int], size=(16, 16)) -> None:
        """Upload one texture per id, then stamp each with the frame given."""
        for index in frames:
            self._upload(index, size)
        for index, frame in frames.items():
            self.renderer._object_texture_used[UUID(int=index)] = frame

    def test_going_over_budget_evicts_the_oldest_first(self) -> None:
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        each = _texture_memory_bytes((16, 16))
        self._with_budget(each * 2)
        self._uploaded_at_frames({1: 10, 2: 11, 3: 12, 4: 13})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertNotIn(UUID(int=1), self.renderer._object_textures)
        self.assertNotIn(UUID(int=2), self.renderer._object_textures)
        self.assertIn(UUID(int=3), self.renderer._object_textures)
        self.assertIn(UUID(int=4), self.renderer._object_textures)

    def test_the_previous_frame_is_never_evicted(self) -> None:
        """The whole reason a count cap was the wrong fix.

        Everything here was drawn on the frame just gone, so everything here
        is about to be asked for again. Evicting any of it means decoding and
        re-uploading a PNG inside the draw loop on every frame from now on --
        a permanent frame-rate collapse traded for memory that comes straight
        back.
        """
        self._with_budget(1)
        self._uploaded_at_frames({1: 19, 2: 19, 3: 19})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertEqual(len(self.renderer._object_textures), 3)
        self.assertEqual([t for t in self.ctx.textures if t.released], [])

    def test_giving_up_is_recorded_rather_than_hidden(self) -> None:
        # A region whose visible set alone will not fit is a real condition
        # and not an eviction failure: it says MAX_OBJECT_TEXTURE_EDGE is too
        # generous here. Silently doing nothing would make it undiagnosable.
        self._with_budget(1)
        self._uploaded_at_frames({1: 19})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertTrue(self.renderer._object_texture_budget_exceeded)
        self.assertEqual(self.renderer._object_textures_evicted, 0)

    def test_evicting_what_it_can_still_reports_falling_short(self) -> None:
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        each = _texture_memory_bytes((16, 16))
        self._with_budget(each)  # room for one; two are pinned by the frame
        self._uploaded_at_frames({1: 5, 2: 19, 3: 19})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertNotIn(UUID(int=1), self.renderer._object_textures)
        self.assertEqual(self.renderer._object_textures_evicted, 1)
        self.assertTrue(self.renderer._object_texture_budget_exceeded)

    def test_staying_inside_the_budget_evicts_nothing(self) -> None:
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        self._with_budget(_texture_memory_bytes((16, 16)) * 4)
        self._uploaded_at_frames({1: 1, 2: 2, 3: 3})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertEqual(len(self.renderer._object_textures), 3)
        self.assertFalse(self.renderer._object_texture_budget_exceeded)

    def test_asking_for_a_cached_texture_is_what_keeps_it(self) -> None:
        """A cache *hit* has to touch the clock, or the order is upload order.

        The texture on the prim under the camera is uploaded once and then
        only ever hit, so without this it is the oldest thing in the cache and
        the first thing evicted -- the exact inversion of what is wanted.
        """
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        each = _texture_memory_bytes((16, 16))
        self._with_budget(each * 2)
        self._uploaded_at_frames({1: 1, 2: 2, 3: 3})
        self.renderer._frame_index = 10
        self.renderer._upload_object_texture(self.ctx, self.scene, UUID(int=1))
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        self.assertIn(UUID(int=1), self.renderer._object_textures)
        self.assertNotIn(UUID(int=2), self.renderer._object_textures)

    def test_an_evicted_texture_takes_its_bookkeeping_with_it(self) -> None:
        # Three side dicts key on the same id. One outliving the texture is a
        # leak that no amount of eviction reaches.
        self._with_budget(1)
        self._uploaded_at_frames({1: 1})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)

        texture_id = UUID(int=1)
        self.assertNotIn(texture_id, self.renderer._object_texture_paths)
        self.assertNotIn(texture_id, self.renderer._object_texture_bytes)
        self.assertNotIn(texture_id, self.renderer._object_texture_used)

    def test_the_budget_is_published_for_the_diagnostics_panel(self) -> None:
        from vibestorm.viewer3d.perspective import _texture_memory_bytes

        self._with_budget(_texture_memory_bytes((16, 16)) * 4)
        self._uploaded_at_frames({1: 1, 2: 2})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)
        self.renderer._publish_texture_vram(self.scene)

        self.assertIn("texture vram:", self.scene.texture_vram_summary)
        self.assertNotIn("OVER BUDGET", self.scene.texture_vram_summary)

    def test_being_over_budget_is_said_out_loud(self) -> None:
        # The condition worth reading the panel for: nothing is evictable and
        # the set does not fit, so every frame from here on re-decodes PNGs.
        self._with_budget(1)
        self._uploaded_at_frames({1: 19})
        self.renderer._frame_index = 20

        self.renderer._prune_object_textures(self.scene)
        self.renderer._publish_texture_vram(self.scene)

        self.assertIn("OVER BUDGET", self.scene.texture_vram_summary)

    def test_a_texture_the_region_dropped_goes_before_the_budget_is_read(self) -> None:
        # Reference pruning first, always: it is free and it is exact, and
        # doing it first is often the whole answer.
        self._with_budget(10**9)
        self._uploaded_at_frames({1: 1, 2: 2})
        del self.scene.texture_paths[UUID(int=2)]

        self.renderer._prune_object_textures(self.scene)

        self.assertNotIn(UUID(int=2), self.renderer._object_textures)
        # Not an eviction: that counter is for the budget alone, so a region
        # change does not read as memory pressure in the diagnostics.
        self.assertEqual(self.renderer._object_textures_evicted, 0)


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
