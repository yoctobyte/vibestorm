"""Wrapping and row selection for the hand-drawn chat ticker.

The ticker used to be a `UITextBox`, whose layout is roughly quadratic in line
count: eight lines cost 23.5 ms to rebuild, and it rebuilt whenever anyone
spoke. Drawing it by hand is ten times cheaper but takes on wrapping and
overflow, which the text box handled. These pin that part; the font is a stub
with fixed-width glyphs so the arithmetic is checkable by hand.
"""

from __future__ import annotations

import unittest

from vibestorm.viewer3d.chat_ticker import (
    TickerEntry,
    visible_rows,
    wrap_entry,
)

WHITE = (222, 222, 222)
AMBER = (255, 204, 102)


class _FixedFont:
    """Every glyph is 10 wide and 20 tall, so widths are just character counts."""

    CHAR_WIDTH = 10

    def size(self, text: str) -> tuple[int, int]:
        return (len(text) * self.CHAR_WIDTH, 20)

    def render_premul(self, text, colour):  # pragma: no cover - not used here
        raise AssertionError("these tests do not draw")


def _entry(sender="Ann", message="hello", colour=AMBER) -> TickerEntry:
    return TickerEntry(
        sender=sender, message=message, sender_colour=colour, body_colour=WHITE
    )


class WrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.font = _FixedFont()

    def test_a_short_line_is_one_row(self) -> None:
        rows = wrap_entry(_entry(), font=self.font, max_width=1000)
        self.assertEqual(len(rows), 1)

    def test_the_sender_keeps_its_own_colour(self) -> None:
        [row] = wrap_entry(_entry(), font=self.font, max_width=1000)
        self.assertEqual(row.segments[0].text, "Ann: ")
        self.assertEqual(row.segments[0].colour, AMBER)
        self.assertEqual(row.segments[1].colour, WHITE)

    def test_a_long_message_wraps(self) -> None:
        # "Ann: " is 50px, leaving 50px on row one: one five-letter word.
        rows = wrap_entry(
            _entry(message="aaaaa bbbbb ccccc"), font=self.font, max_width=100
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].segments[0].text, "Ann: ")
        self.assertEqual(rows[0].segments[1].text, "aaaaa")
        self.assertEqual(rows[1].segments[0].text, "bbbbb")
        self.assertEqual(rows[2].segments[0].text, "ccccc")

    def test_wrapped_rows_have_no_leading_space(self) -> None:
        rows = wrap_entry(
            _entry(message="aaaaa bbbbb ccccc"), font=self.font, max_width=100
        )
        for row in rows[1:]:
            self.assertFalse(row.segments[0].text.startswith(" "))

    def test_a_word_wider_than_the_box_overflows_rather_than_breaking(self) -> None:
        # A chopped URL is harder to read than one that runs off the edge, and
        # the window can be widened.
        rows = wrap_entry(
            _entry(sender="", message="aaaaaaaaaaaaaaaaaaaa"),
            font=self.font,
            max_width=50,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].segments[0].text, "aaaaaaaaaaaaaaaaaaaa")

    def test_an_empty_message_still_shows_the_sender(self) -> None:
        [row] = wrap_entry(_entry(message=""), font=self.font, max_width=1000)
        self.assertEqual(row.segments[0].text, "Ann: ")


class VisibleRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.font = _FixedFont()

    def test_it_keeps_the_newest_when_there_is_not_room(self) -> None:
        # The one thing a chat ticker must never do is push the newest message
        # out of sight to make room for an older one.
        entries = [_entry(message=str(index)) for index in range(10)]
        rows = visible_rows(entries, font=self.font, max_width=1000, max_rows=3)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1].segments[1].text, "9")
        self.assertEqual(rows[0].segments[1].text, "7")

    def test_wrapped_rows_count_against_the_budget(self) -> None:
        # Two chat lines, but the first wraps to three rows. A ticker that
        # counted chat lines rather than display rows would overflow the box.
        entries = [
            _entry(message="aaaaa bbbbb ccccc"),
            _entry(message="x"),
        ]
        rows = visible_rows(entries, font=self.font, max_width=100, max_rows=4)

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1].segments[1].text, "x")

    def test_no_room_means_no_rows(self) -> None:
        self.assertEqual(
            visible_rows([_entry()], font=self.font, max_width=1000, max_rows=0), []
        )

    def test_nothing_to_show_is_not_an_error(self) -> None:
        self.assertEqual(
            visible_rows([], font=self.font, max_width=1000, max_rows=8), []
        )

    def test_rows_come_back_oldest_first(self) -> None:
        entries = [_entry(message="one"), _entry(message="two")]
        rows = visible_rows(entries, font=self.font, max_width=1000, max_rows=8)
        self.assertEqual(
            [row.segments[1].text for row in rows], ["one", "two"]
        )


if __name__ == "__main__":
    unittest.main()
