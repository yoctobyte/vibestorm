"""How large to draw the UI, given the window it is actually drawn in.

The viewer picks a scale from the monitor -- how large a pixel is on it --
which is the right question for a window that fills the monitor. It was also
the only question asked, and the answer is wrong for a small window on a large
one, which is exactly what anyone does to a viewer that is running slowly.

At a HiDPI scale of two in a 1280x800 frame the HUD was laid out as though it
had 640x410 to work with, and it does not fit in that. The chat window, which
is open from the first frame, came out 890x550 -- two thirds of the frame in
each direction, over the world, with "no chat yet" in it -- the heightmap
window sat with thirty-five of its 750 pixels on screen, and the login panel
ran off the top and the bottom. Every widget was equally oversized; the chat
window was merely the biggest.

Both screens read this, so the type does not change size between logging in
and arriving.
"""

from __future__ import annotations

import math

#: The frame the UI is laid out for, in unscaled pixels, and the default
#: window size at a scale of one. Every widget's size is a number against it.
UI_DESIGN_SIZE = (1180, 820)


def scale_the_window_can_hold(scale: float, screen_size: tuple[int, int]) -> float:
    """`scale`, brought down to what this window can actually show.

    Lowered only, never raised, and in the same quarter steps the automatic
    scale uses, rounded down: a window that holds all but a sliver of a scale
    does not hold it. The steps also mean a window dragged a few pixels does
    not change the scale, and so does not rebuild every widget.

    Both sides of the window are asked. A letterbox frame can hold two of the
    layout across and barely one of it down, and scaling to the wide side puts
    the status bar off the bottom.

    The cap stops at 1.0, because a window smaller than `UI_DESIGN_SIZE` at a
    scale of one is an ordinary small window and shrinking the type in it was
    never the complaint. The complaint is a UI drawn for the monitor rather
    than for its own frame.
    """
    width, height = screen_size
    holds = min(width / UI_DESIGN_SIZE[0], height / UI_DESIGN_SIZE[1])
    return min(scale, max(1.0, math.floor(holds * 4.0) / 4.0))
