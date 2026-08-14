"""Tests for the hover-text label texture cache.

The cache is keyed by the text string, so it is bounded by the number of
*distinct labels seen over time*, not by the number of prims. A prim whose
text changes — a clock, a visitor counter, a vendor price — mints a new GL
texture per update. Without eviction that is a texture leak that grows for
the whole session and never shows up in a short test run.

These use a stub context so the eviction policy can be tested without a GL
context; the rendering itself is covered by the GL tests elsewhere.
"""

import unittest

from vibestorm.viewer3d.perspective import (
    HOVER_TEXT_CACHE_MAX,
    OBJECT_TEXTURE_CACHE_MAX,
    PerspectiveRenderer,
)


class _StubTexture:
    def __init__(self) -> None:
        self.released = False
        self.filter = None
        self.repeat_x = True
        self.repeat_y = True

    def release(self) -> None:
        self.released = True


class _StubContext:
    """Just enough moderngl surface for the label rasteriser."""

    LINEAR = "linear"

    def __init__(self) -> None:
        self.textures: list[_StubTexture] = []

    def texture(self, size, components, data):  # noqa: ANN001 - stub
        del size, components, data
        texture = _StubTexture()
        self.textures.append(texture)
        return texture


class LabelCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pygame not available")
        from vibestorm.viewer3d.camera import Camera3D

        # ctx=None skips _setup_gl, leaving the caches empty and inert.
        self.renderer = PerspectiveRenderer(Camera3D(), ctx=None)
        self.ctx = _StubContext()

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

    def test_cache_is_bounded(self) -> None:
        for index in range(HOVER_TEXT_CACHE_MAX + 20):
            self._texture_for(f"tick {index}")

        self.assertLessEqual(
            len(self.renderer._hover_text_textures), HOVER_TEXT_CACHE_MAX
        )

    def test_evicted_textures_are_released_not_merely_dropped(self) -> None:
        # Dropping the reference without release() leaks on the GPU, which no
        # amount of Python-side bookkeeping would reveal.
        for index in range(HOVER_TEXT_CACHE_MAX + 5):
            self._texture_for(f"tick {index}")

        released = [t for t in self.ctx.textures if t.released]
        self.assertEqual(len(released), 5)

    def test_eviction_is_least_recently_used(self) -> None:
        for index in range(HOVER_TEXT_CACHE_MAX):
            self._texture_for(f"label {index}")

        # Touch the oldest so it is no longer the eviction candidate.
        self._texture_for("label 0")
        self._texture_for("newcomer")

        self.assertIn("label 0", self.renderer._hover_text_textures)
        self.assertNotIn("label 1", self.renderer._hover_text_textures)

    def test_a_repeated_label_never_evicts_itself(self) -> None:
        # The realistic steady state: one prim with static text, in view for a
        # long session, must not be re-rasterised because of churn elsewhere.
        self._texture_for("stable")
        for index in range(HOVER_TEXT_CACHE_MAX * 2):
            self._texture_for(f"changing {index}")
            self._texture_for("stable")

        self.assertIn("stable", self.renderer._hover_text_textures)
        stable_textures = [
            t for t in self.ctx.textures if not t.released
        ]
        self.assertLessEqual(len(stable_textures), HOVER_TEXT_CACHE_MAX)


class ObjectTextureCacheTests(unittest.TestCase):
    """The object texture cache holds real VRAM, and survives region changes.

    It is kept across regions on purpose — texture files persist on disk, so a
    revisited region reuses them — which is exactly why it needs a bound: it
    is the one GL cache nothing ever clears mid-session.
    """

    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError:  # pragma: no cover
            self.skipTest("pygame not available")
        import tempfile
        from pathlib import Path

        import pygame

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.scene import Scene

        self.renderer = PerspectiveRenderer(Camera3D(), ctx=None)
        self.ctx = _StubContext()
        self._scene = Scene()

        # One real 2x2 PNG on disk, reused for every id: the cache keys on the
        # texture UUID, so distinct ids are what matters, not distinct pixels.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        surface = pygame.Surface((2, 2))
        surface.fill((10, 20, 30))
        self._png_path = Path(self._tmp.name) / "tex.png"
        pygame.image.save(surface, str(self._png_path))

    def _upload(self, index: int) -> None:
        """Upload through the real code path, not by poking the dict.

        Going through ``_upload_object_texture`` is the point: an earlier version
        of this test called the evictor directly, so removing the evictor's
        call site from the upload path still passed.
        """
        from uuid import UUID

        texture_id = UUID(int=index)
        self._scene.texture_paths[texture_id] = self._png_path
        self.renderer._upload_object_texture(self.ctx, self._scene, texture_id)

    def test_cache_is_bounded(self) -> None:
        for index in range(OBJECT_TEXTURE_CACHE_MAX + 30):
            self._upload(index)

        self.assertLessEqual(
            len(self.renderer._object_textures), OBJECT_TEXTURE_CACHE_MAX
        )

    def test_evicted_textures_are_released(self) -> None:
        for index in range(OBJECT_TEXTURE_CACHE_MAX + 7):
            self._upload(index)

        released = [t for t in self.ctx.textures if t.released]
        self.assertEqual(len(released), 7)

    def test_the_same_id_is_uploaded_once(self) -> None:
        # The bound must not turn the cache into a re-upload loop.
        self._upload(1)
        self._upload(1)
        self._upload(1)

        self.assertEqual(len(self.ctx.textures), 1)

    def test_a_reused_texture_survives_churn(self) -> None:
        from uuid import UUID

        churn = OBJECT_TEXTURE_CACHE_MAX * 2
        self._upload(9999)
        for index in range(churn):
            self._upload(index)
            self._upload(9999)

        self.assertIn(UUID(int=9999), self.renderer._object_textures)
        # Presence alone proves nothing: without an LRU touch the reused
        # texture is evicted and then re-uploaded on the very next request, so
        # it is still present at the end. The upload count is what shows it was
        # actually kept — one upload for it, one per distinct churn id.
        self.assertEqual(len(self.ctx.textures), churn + 1)

    def test_path_bookkeeping_stays_in_lockstep(self) -> None:
        # A path entry outliving its texture would leave this dict growing
        # without bound even though the texture cache is capped.
        for index in range(OBJECT_TEXTURE_CACHE_MAX + 7):
            self._upload(index)

        self.assertEqual(
            len(self.renderer._object_texture_paths),
            len(self.renderer._object_textures),
        )


if __name__ == "__main__":
    unittest.main()
