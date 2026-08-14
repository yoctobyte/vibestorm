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

from vibestorm.viewer3d.perspective import HOVER_TEXT_CACHE_MAX, PerspectiveRenderer


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


if __name__ == "__main__":
    unittest.main()
