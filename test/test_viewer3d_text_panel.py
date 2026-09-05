"""Drawing a readout by hand instead of through a pygame_gui text box.

The point of the module is speed, and speed is not what these pin -- a timing
assertion on a shared machine is a flake. What they pin is that the cheap path
still produces the *same rows* as the expensive one: the wrap cache cannot
serve a stale answer, the height has to match what is actually drawn, and a
line too long for the panel has to survive rather than disappear.
"""

from __future__ import annotations

import unittest

from vibestorm.viewer3d.text_panel import (
    PANEL_PADDING,
    draw_rows,
    panel_height,
    wrap_line,
    wrap_lines,
)


class FakeFont:
    """A font where every character is exactly ten pixels wide."""

    def __init__(self) -> None:
        self.size_calls = 0

    def size(self, text: str) -> tuple[int, int]:
        self.size_calls += 1
        return (len(text) * 10, 12)

    def render_premul(self, text: str, colour) -> object:  # pragma: no cover
        raise AssertionError("these tests do not draw")


class WrapTests(unittest.TestCase):
    def test_a_line_that_fits_is_one_row(self) -> None:
        self.assertEqual(wrap_line("abc def", font=FakeFont(), max_width=200), ["abc def"])

    def test_a_long_line_breaks_between_words(self) -> None:
        rows = wrap_line("aaa bbb ccc ddd", font=FakeFont(), max_width=80)

        self.assertEqual(rows, ["aaa bbb", "ccc ddd"])

    def test_a_word_wider_than_the_panel_overflows_rather_than_vanishing(self) -> None:
        # These panels carry UUIDs and absolute paths. Breaking one mid-token
        # makes it unreadable; dropping it loses the thing the reader opened
        # the panel for. The window is resizable, so overflow is recoverable.
        long_word = "a" * 40

        rows = wrap_line(f"map: {long_word}", font=FakeFont(), max_width=80)

        self.assertEqual(rows, ["map:", long_word])
        self.assertIn(long_word, rows)

    def test_an_empty_line_still_occupies_a_row(self) -> None:
        # A blank separator that collapses shifts every line below it, and the
        # height would then disagree with what is drawn.
        self.assertEqual(wrap_line("", font=FakeFont(), max_width=200), [""])

    def test_the_cache_returns_what_wrapping_would_have(self) -> None:
        font = FakeFont()
        lines = ("aaa bbb ccc ddd", "short", "aaa bbb ccc ddd")

        uncached = wrap_lines(lines, font=font, max_width=80)
        cached = wrap_lines(lines, font=font, max_width=80, cache={})

        self.assertEqual(cached, uncached)

    def test_the_cache_actually_saves_the_font_lookups(self) -> None:
        # Without this the cache could be silently doing nothing -- the rows
        # would still be right, and the only symptom would be the frame time
        # the module exists to fix.
        font = FakeFont()
        cache: dict[tuple[str, int], list[str]] = {}
        lines = ("fps: 60.0", "region: Somewhere", "objects: 32")

        wrap_lines(lines, font=font, max_width=200, cache=cache)
        after_first = font.size_calls
        wrap_lines(lines, font=font, max_width=200, cache=cache)

        self.assertGreater(after_first, 0)
        self.assertEqual(font.size_calls, after_first, "a second pass re-measured")

    def test_a_changed_line_is_not_served_from_the_cache(self) -> None:
        # The framerate line changes every refresh. Keying on the line rather
        # than its position is what keeps that honest.
        font = FakeFont()
        cache: dict[tuple[str, int], list[str]] = {}

        wrap_lines(("fps: 60.0",), font=font, max_width=200, cache=cache)
        rows = wrap_lines(("fps: 12.5",), font=font, max_width=200, cache=cache)

        self.assertEqual(rows, ["fps: 12.5"])

    def test_a_different_width_does_not_reuse_the_old_rows(self) -> None:
        font = FakeFont()
        cache: dict[tuple[str, int], list[str]] = {}

        wide = wrap_lines(("aaa bbb ccc ddd",), font=font, max_width=200, cache=cache)
        narrow = wrap_lines(("aaa bbb ccc ddd",), font=font, max_width=80, cache=cache)

        self.assertEqual(wide, ["aaa bbb ccc ddd"])
        self.assertEqual(narrow, ["aaa bbb", "ccc ddd"])


class HeightTests(unittest.TestCase):
    def test_the_height_holds_every_row_that_would_be_drawn(self) -> None:
        # The surface is sized from panel_height and then scrolled. If the two
        # disagree by even one row, the last line of the readout is the one
        # that never appears -- and it is the one nothing else reports.
        font = FakeFont()
        lines = tuple(f"line {n} with some words in it" for n in range(12))
        rows = wrap_lines(lines, font=font, max_width=120)
        line_height = 16

        surface = FakeSurface(
            (120 + PANEL_PADDING * 2, panel_height(len(rows), line_height=line_height))
        )
        drawn = draw_rows(
            surface,
            rows,
            font=DrawingFont(),
            colour=(255, 255, 255),
            background=(0, 0, 0),
            line_height=line_height,
        )

        self.assertEqual(drawn, len(rows))

    def test_an_empty_panel_still_has_a_positive_height(self) -> None:
        # A zero-sized surface is not a surface pygame will make.
        self.assertGreater(panel_height(0, line_height=16), 0)


class FakeSurface:
    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size
        self.blits: list[tuple[object, tuple[int, int]]] = []

    def get_size(self) -> tuple[int, int]:
        return self._size

    def fill(self, colour) -> None:
        self.filled = colour

    def blit(self, source, position, **kwargs) -> None:
        self.blits.append((source, position))


class DrawingFont(FakeFont):
    def render_premul(self, text: str, colour) -> object:
        return f"<{text}>"


class DrawTests(unittest.TestCase):
    def test_rows_are_drawn_top_down_in_order(self) -> None:
        # The ticker draws the newest at the bottom and drops the oldest; a
        # readout is the other way round, and getting it backwards would put
        # the framerate at the bottom of a scrolled panel.
        surface = FakeSurface((200, 200))

        draw_rows(
            surface,
            ["first", "second", "third"],
            font=DrawingFont(),
            colour=(255, 255, 255),
            background=(10, 10, 10),
            line_height=20,
        )

        self.assertEqual([source for source, _pos in surface.blits],
                         ["<first>", "<second>", "<third>"])
        ys = [position[1] for _source, position in surface.blits]
        self.assertEqual(ys, sorted(ys))

    def test_rows_past_the_bottom_are_left_undrawn(self) -> None:
        surface = FakeSurface((200, 50))

        drawn = draw_rows(
            surface,
            ["a", "b", "c", "d", "e"],
            font=DrawingFont(),
            colour=(255, 255, 255),
            background=(10, 10, 10),
            line_height=20,
        )

        self.assertEqual(drawn, 2)
        self.assertEqual(len(surface.blits), 2)

    def test_a_blank_row_takes_its_space_without_a_blit(self) -> None:
        surface = FakeSurface((200, 200))

        draw_rows(
            surface,
            ["top", "", "bottom"],
            font=DrawingFont(),
            colour=(255, 255, 255),
            background=(10, 10, 10),
            line_height=20,
        )

        self.assertEqual([source for source, _pos in surface.blits], ["<top>", "<bottom>"])
        self.assertEqual([position[1] for _source, position in surface.blits],
                         [PANEL_PADDING, PANEL_PADDING + 40])


if __name__ == "__main__":
    unittest.main()
