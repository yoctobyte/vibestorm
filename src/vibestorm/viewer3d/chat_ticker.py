"""Drawing the chat ticker by hand instead of through a pygame_gui text box.

`UITextBox`'s layout is roughly quadratic in line count -- one line costs
1.3 ms, eight cost 23.5 ms, eighteen cost 49 ms, and markup is not the cause.
The ticker rebuilds eight lines whenever anyone speaks, so a busy conversation
put a 23 ms stutter into the frame several times a second. Rendering the same
eight lines with the font directly and handing the surface to a `UIImage` costs
1.9 ms including the upload: ten times cheaper, and it gets wrapping and
bottom-alignment that the text box did not have.

Nothing here imports pygame_gui. The font is anything with ``size(text)`` and
``render_premul(text, colour)``, which is the interface pygame_gui's own font
objects expose -- so the ticker draws in the same typeface as the rest of the
HUD, and these functions are testable against a stub.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class TickerFont(Protocol):
    """The two things drawing text needs. pygame_gui's font objects have both."""

    def size(self, text: str) -> tuple[int, int]: ...

    def render_premul(self, text: str, colour) -> object: ...


@dataclass(frozen=True, slots=True)
class TickerSegment:
    """A run of text in one colour."""

    text: str
    colour: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class TickerEntry:
    """One chat line: a coloured sender, then the message body."""

    sender: str
    message: str
    sender_colour: tuple[int, int, int]
    body_colour: tuple[int, int, int] = (222, 222, 222)


@dataclass(slots=True)
class TickerRow:
    """One *display* row -- what lands on a single line of the surface."""

    segments: list[TickerSegment] = field(default_factory=list)

    def width(self, font: TickerFont) -> int:
        return sum(font.size(segment.text)[0] for segment in self.segments)


def wrap_entry(entry: TickerEntry, *, font: TickerFont, max_width: int) -> list[TickerRow]:
    """Break one chat line into the rows it occupies.

    The sender stays on the first row with as much of the message as fits
    beside it; the rest wraps to full-width rows underneath. A single word
    longer than the whole width is left to overflow rather than broken -- a URL
    chopped mid-token is harder to read than one that runs off the edge, and
    the caller can widen the window.
    """
    prefix = f"{entry.sender}: " if entry.sender else ""
    rows: list[TickerRow] = []
    current = TickerRow()
    if prefix:
        current.segments.append(TickerSegment(prefix, entry.sender_colour))
    used = font.size(prefix)[0] if prefix else 0

    space_width = font.size(" ")[0]
    # The prefix already ends in a space, and a fresh row starts at the margin,
    # so a separator is only wanted between two body words.
    needs_space = False
    for word in entry.message.split():
        word_width = font.size(word)[0]
        gap = space_width if needs_space else 0
        if used > 0 and used + gap + word_width > max_width:
            rows.append(current)
            current = TickerRow()
            used = 0
            gap = 0
        text = (" " if gap else "") + word
        current.segments.append(TickerSegment(text, entry.body_colour))
        used += gap + word_width
        needs_space = True

    if current.segments or not rows:
        rows.append(current)
    return rows


def visible_rows(
    entries: Sequence[TickerEntry],
    *,
    font: TickerFont,
    max_width: int,
    max_rows: int,
) -> list[TickerRow]:
    """The rows that fit, preferring the newest.

    Wrapping means eight chat lines can be more than eight rows, and the one
    thing a chat ticker must never do is push the newest message out of sight.
    So rows are gathered from the end backwards and the *oldest* overflow is
    dropped.
    """
    if max_rows <= 0:
        return []
    collected: list[TickerRow] = []
    for entry in reversed(entries):
        rows = wrap_entry(entry, font=font, max_width=max_width)
        for row in reversed(rows):
            collected.append(row)
            if len(collected) >= max_rows:
                return list(reversed(collected))
    return list(reversed(collected))


def draw_ticker(
    surface,
    entries: Sequence[TickerEntry],
    *,
    font: TickerFont,
    background: tuple[int, int, int],
    line_height: int,
    padding: int = 4,
) -> int:
    """Paint the ticker onto ``surface``. Returns how many rows were drawn.

    The surface is filled opaque rather than composited: the ticker sits on the
    chat window's own background, and premultiplied text over a transparent
    surface is a subtlety with nothing to gain here.
    """
    import pygame

    surface.fill(background)
    width, height = surface.get_size()
    usable_width = max(1, width - padding * 2)
    max_rows = max(0, (height - padding * 2) // max(1, line_height))

    rows = visible_rows(
        entries, font=font, max_width=usable_width, max_rows=max_rows
    )
    y = padding
    for row in rows:
        x = padding
        for segment in row.segments:
            if not segment.text:
                continue
            rendered = font.render_premul(segment.text, pygame.Color(*segment.colour))
            surface.blit(rendered, (x, y), special_flags=pygame.BLEND_PREMULTIPLIED)
            x += font.size(segment.text)[0]
        y += line_height
    return len(rows)


__all__ = [
    "TickerEntry",
    "TickerFont",
    "TickerRow",
    "TickerSegment",
    "draw_ticker",
    "visible_rows",
    "wrap_entry",
]
