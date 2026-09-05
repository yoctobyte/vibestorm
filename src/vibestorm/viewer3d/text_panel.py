"""Drawing a panel of plain text lines by hand.

Same reason as ``chat_ticker``: ``UITextBox``'s layout is roughly quadratic in
line count, and a panel that rebuilds is a panel that costs. Measured against a
live session on a GTX 1660 SUPER, one ``set_text`` on the diagnostics panel took
**73 ms** -- and because its first line is the framerate, it did that every
second. The panel opened to explain why the viewer was slow was dropping four
frames a second to do it.

Where this differs from the ticker: the ticker keeps the *newest* rows and drops
the oldest, because the newest chat line must never be pushed out of sight. A
readout is read top to bottom, so this keeps the top and hands the caller a
height, letting whatever contains it scroll.

Nothing here imports pygame_gui. The font is anything with ``size(text)`` and
``render_premul(text, colour)`` -- the interface pygame_gui's own font objects
expose -- so the panel draws in the same typeface as the rest of the HUD and
these functions are testable against a stub.
"""

from __future__ import annotations

from collections.abc import Sequence

from vibestorm.viewer3d.chat_ticker import TickerFont

#: Space left around the text on every side.
PANEL_PADDING = 4


def wrap_line(text: str, *, font: TickerFont, max_width: int) -> list[str]:
    """Break one line into the rows it occupies.

    A single word wider than the panel is left to overflow rather than broken:
    a UUID or a path chopped mid-token is harder to read than one that runs off
    the edge, and every one of these panels is resizable.
    """
    rows: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if current and font.size(candidate)[0] > max_width:
            rows.append(current)
            current = word
        else:
            current = candidate
    rows.append(current)
    return rows


def wrap_lines(
    lines: Sequence[str],
    *,
    font: TickerFont,
    max_width: int,
    cache: dict[tuple[str, int], list[str]] | None = None,
) -> list[str]:
    """Every row the whole panel occupies, in order.

    Wrapping asks the font for the width of every word, which is the bulk of
    what drawing a readout costs. Pass a ``cache`` and the lines that did not
    change between refreshes are free -- on the diagnostics panel that is
    sixteen of eighteen, since only the framerate and the sim's own stats move.
    The caller owns the dict and should drop it when the width changes.
    """
    rows: list[str] = []
    for line in lines:
        if cache is None:
            rows.extend(wrap_line(line, font=font, max_width=max_width))
            continue
        key = (line, max_width)
        wrapped = cache.get(key)
        if wrapped is None:
            wrapped = wrap_line(line, font=font, max_width=max_width)
            cache[key] = wrapped
        rows.extend(wrapped)
    return rows


def panel_height(row_count: int, *, line_height: int, padding: int = PANEL_PADDING) -> int:
    """How tall a surface has to be to hold ``row_count`` rows."""
    return max(1, row_count * line_height + padding * 2)


def draw_rows(
    surface,
    rows: Sequence[str],
    *,
    font: TickerFont,
    colour: tuple[int, int, int],
    background: tuple[int, int, int],
    line_height: int,
    padding: int = PANEL_PADDING,
) -> int:
    """Paint already-wrapped ``rows`` onto ``surface``. Returns rows drawn.

    Takes rows rather than lines because the caller has to wrap them anyway to
    know how tall a surface to make, and wrapping twice was costing the
    diagnostics panel about as much as drawing it.

    Rows past the bottom of the surface are not drawn. The caller decides
    whether that is a crop or a scroll by choosing how tall to make the
    surface -- :func:`panel_height` sizes one that holds everything.
    """
    import pygame

    surface.fill(background)
    _width, height = surface.get_size()

    y = padding
    drawn = 0
    for row in rows:
        if y + line_height > height:
            break
        if row:
            rendered = font.render_premul(row, pygame.Color(*colour))
            surface.blit(rendered, (padding, y), special_flags=pygame.BLEND_PREMULTIPLIED)
        y += line_height
        drawn += 1
    return drawn


def draw_lines(
    surface,
    lines: Sequence[str],
    *,
    font: TickerFont,
    colour: tuple[int, int, int],
    background: tuple[int, int, int],
    line_height: int,
    padding: int = PANEL_PADDING,
) -> int:
    """Wrap ``lines`` to the surface's width and paint them. Returns rows drawn."""
    usable_width = max(1, surface.get_size()[0] - padding * 2)
    rows = wrap_lines(lines, font=font, max_width=usable_width)
    return draw_rows(
        surface,
        rows,
        font=font,
        colour=colour,
        background=background,
        line_height=line_height,
        padding=padding,
    )


__all__ = [
    "PANEL_PADDING",
    "draw_lines",
    "draw_rows",
    "panel_height",
    "wrap_line",
    "wrap_lines",
]
