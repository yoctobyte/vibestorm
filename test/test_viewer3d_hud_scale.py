"""The HUD's scale against the window it is actually drawn in.

`_auto_ui_scale` asks how large a pixel is on the monitor, which is the right
question for a window that fills it. It was also the only question asked, and
a small window on a large monitor is exactly what anyone does to a viewer that
is running slowly -- which is what the owner does here, having said so.

At a HiDPI scale of two in a 1280x800 frame the whole HUD was laid out as
though it had 640x410 to work with. The chat window, which is open from the
first frame, came out 890x550: two thirds of the frame in each direction, over
the world, with "no chat yet" in it. Four other windows were larger than the
frame or positioned off the bottom of it. A screenshot is what found it.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from vibestorm.viewer.ui_scale import (  # noqa: E402
    UI_DESIGN_SIZE,
    scale_the_window_can_hold,
)


class ScaleTheWindowCanHoldTests(unittest.TestCase):
    """The arithmetic on its own, where the answers can be exact."""

    def test_a_window_the_size_it_is_drawn_for_holds_the_scale(self) -> None:
        for scale in (1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                held = scale_the_window_can_hold(
                    scale,
                    (
                        int(UI_DESIGN_SIZE[0] * scale),
                        int(UI_DESIGN_SIZE[1] * scale),
                    ),
                )
                self.assertEqual(held, scale)

    def test_a_small_window_on_a_large_monitor_gives_the_scale_back(self) -> None:
        # The case that prompted this: 1280x800 holds 1.08 of the layout
        # across and 0.97 of it down, which is not two of it in either.
        self.assertEqual(scale_the_window_can_hold(2.0, (1280, 800)), 1.0)

    def test_a_window_holding_one_and_a_half_gets_one_and_a_half(self) -> None:
        """Quarter steps and rounded down, like the automatic scale itself.

        The point of the steps is that a window dragged a few pixels does not
        change the scale, and so does not rebuild every widget in the HUD.
        """
        holds_one_and_three_quarters = (
            int(UI_DESIGN_SIZE[0] * 1.8),
            int(UI_DESIGN_SIZE[1] * 1.8),
        )

        self.assertEqual(
            scale_the_window_can_hold(2.0, holds_one_and_three_quarters), 1.75
        )

    def test_both_sides_of_the_window_are_asked(self) -> None:
        """A frame can hold two of the layout one way and one of it the other.

        Asking only the wide side of a letterbox window scales the HUD to it
        and puts the status bar off the bottom; asking only the tall side of
        a narrow one runs the menu bar off the right. Neither shows up in a
        window that is merely a smaller version of the design frame, which
        every other case here is.
        """
        letterbox = (UI_DESIGN_SIZE[0] * 2, int(UI_DESIGN_SIZE[1] * 1.1))
        upright = (UI_DESIGN_SIZE[0], UI_DESIGN_SIZE[1] * 2)

        self.assertEqual(scale_the_window_can_hold(2.0, letterbox), 1.0)
        self.assertEqual(scale_the_window_can_hold(2.0, upright), 1.0)

    def test_a_scale_is_never_raised_to_fill_a_window(self) -> None:
        # A wall-sized window does not mean the viewer wants huge type; the
        # monitor's own pixel size already said what it wanted.
        self.assertEqual(scale_the_window_can_hold(1.0, (7680, 4320)), 1.0)

    def test_a_window_smaller_than_the_layout_stops_at_one(self) -> None:
        """The floor, and it is deliberate.

        A 900x600 window at a scale of one is an ordinary small window, and
        shrinking the type in it was never the complaint. The complaint is a
        HUD laid out for the monitor rather than for its own frame.
        """
        self.assertEqual(scale_the_window_can_hold(1.0, (900, 600)), 1.0)
        self.assertEqual(scale_the_window_can_hold(2.0, (900, 600)), 1.0)

    def test_a_viewer_that_asked_for_small_type_keeps_it(self) -> None:
        self.assertEqual(scale_the_window_can_hold(0.75, (900, 600)), 0.75)


class HUDWindowsFitTheFrameTests(unittest.TestCase):
    """And the same thing where it is visible: the windows themselves."""

    #: A shrunken window on a HiDPI monitor -- the frame from the screenshot
    #: that found this.
    SMALL = (1280, 800)
    #: And the default one at the same scale, which was never the problem and
    #: must not change.
    LARGE = (2360, 1640)

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()

    def tearDown(self) -> None:
        self.pygame.quit()

    def _hud(self, size, *, scale: float = 2.0):
        from vibestorm.viewer3d.hud import HUD

        self.pygame.display.set_mode(size)
        return HUD(size, on_chat_submit=lambda _text: None, ui_scale=scale)

    def _windows(self, hud):
        for name in dir(hud):
            element = getattr(hud, name, None)
            if isinstance(element, self.pygame_gui.elements.UIWindow):
                yield name, element.rect

    def test_every_window_fits_inside_a_shrunken_frame(self) -> None:
        """Not merely small enough: on the frame, corner to corner.

        Before this, `heightmap_window` was 690x750 with its top at y=765 in
        an 800-tall frame -- thirty-five pixels of it visible -- and the
        inspector and the asset viewer were each clamped to the whole frame
        because they were larger than it.
        """
        width, height = self.SMALL
        hud = self._hud(self.SMALL)

        for name, rect in self._windows(hud):
            with self.subTest(window=name):
                self.assertGreaterEqual(rect.left, 0, f"{name} starts off the left")
                self.assertGreaterEqual(rect.top, 0, f"{name} starts above the frame")
                self.assertLessEqual(rect.right, width, f"{name} runs off the right")
                self.assertLessEqual(rect.bottom, height, f"{name} runs off the bottom")

    def test_the_chat_window_leaves_the_world_visible(self) -> None:
        """The one window that is open from the first frame.

        Every other window here is opened by asking for it, so a viewer who
        finds one too big can close it. This one is what the world is seen
        through, and at two thirds of the frame in each direction it was
        most of what a new session showed.
        """
        width, height = self.SMALL
        hud = self._hud(self.SMALL)

        rect = hud.chat_window.rect

        self.assertLess(rect.width * 2, width, "the chat window is half the frame wide")
        self.assertLess(rect.height * 2, height, "the chat window is half the frame tall")

    def test_a_window_that_holds_the_scale_is_left_alone(self) -> None:
        """The other half of it: nothing changes where nothing was wrong.

        The default window is 1180x820 at a scale of one, so at two it is
        this, and the HUD in it was never oversized.
        """
        hud = self._hud(self.LARGE)

        self.assertEqual(hud.ui_scale, 2.0)

    def test_dragging_a_window_small_and_large_again_restores_the_scale(self) -> None:
        """Which is why the scale as asked for is kept as well as the one used.

        Deriving the next scale from the current one would ratchet: every
        shrink would lower it and no widening would ever put it back.
        """
        hud = self._hud(self.LARGE)

        self.pygame.display.set_mode(self.SMALL)
        hud.resize(self.SMALL)
        shrunken = hud.ui_scale
        self.pygame.display.set_mode(self.LARGE)
        hud.resize(self.LARGE)

        self.assertEqual(shrunken, 1.0)
        self.assertEqual(hud.ui_scale, 2.0)

    def test_the_windows_fit_after_a_resize_too(self) -> None:
        # `resize` rebuilds every element, and it is the path an owner who
        # drags the window actually takes -- the constructor is only the
        # first frame.
        width, height = self.SMALL
        hud = self._hud(self.LARGE)

        self.pygame.display.set_mode(self.SMALL)
        hud.resize(self.SMALL)

        for name, rect in self._windows(hud):
            with self.subTest(window=name):
                self.assertLessEqual(rect.right, width, f"{name} runs off the right")
                self.assertLessEqual(rect.bottom, height, f"{name} runs off the bottom")


class StatusBarIsOnTheFrameTests(unittest.TestCase):
    """The bar along the bottom, which was never on any frame at all.

    It is anchored to the bottom, and pygame_gui reads a bottom-anchored
    rect's `y` as an offset *up from the bottom edge*. It was given
    `sh - status_h`, an absolute coordinate, so the bar landed at
    `sh + sh - status_h` -- one whole window below the window, at every size
    and every scale since it was written.

    Which is why the framerate could be moved out of the diagnostics panel
    and into the status bar without anyone noticing it had gone. Both HUDs
    had the same line.
    """

    SIZE = (1280, 800)

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode(self.SIZE)

    def tearDown(self) -> None:
        self.pygame.quit()

    def _huds(self):
        from vibestorm.viewer.hud import HUD as HUD2D
        from vibestorm.viewer3d.hud import HUD as HUD3D

        yield "viewer3d", HUD3D(self.SIZE, on_chat_submit=lambda _text: None)
        yield "viewer", HUD2D(self.SIZE, on_chat_submit=lambda _text: None)

    def test_both_bars_are_inside_the_frame(self) -> None:
        width, height = self.SIZE

        for name, hud in self._huds():
            for bar in ("menu_bar", "status_bar"):
                with self.subTest(hud=name, bar=bar):
                    rect = getattr(hud, bar).rect
                    self.assertGreaterEqual(rect.top, 0, f"{bar} is above the frame")
                    self.assertLessEqual(rect.bottom, height, f"{bar} is below the frame")
                    self.assertLessEqual(rect.right, width, f"{bar} runs off the right")

    def test_nothing_in_a_bar_hangs_over_the_edge_of_it(self) -> None:
        """The bar's *container* is what clips, and it is not the bar.

        A `UIPanel` spends three pixels at each edge on its border and its
        shadow. At 30 and 24 pixels tall every menu button hung three pixels
        over the bottom of its container and both status labels hung five
        over, so the type in them was cut. This is what pins the heights: a
        theme with thicker chrome fails here rather than shaving the text.
        """
        for name, hud in self._huds():
            for bar in ("menu_bar", "status_bar"):
                inside = getattr(hud, bar).get_container()
                for element in inside.elements:
                    with self.subTest(hud=name, bar=bar, element=type(element).__name__):
                        self.assertTrue(
                            inside.rect.contains(element.rect),
                            f"{element.rect} hangs out of {bar}'s {inside.rect}",
                        )

    def test_the_status_bar_is_along_the_bottom(self) -> None:
        # Not merely on the frame: where it is for. Anchored, so it stays
        # there when the window is resized rather than being repositioned.
        _width, height = self.SIZE

        for name, hud in self._huds():
            with self.subTest(hud=name):
                self.assertEqual(hud.status_bar.rect.bottom, height)


class LoginScreenFitsTheFrameTests(unittest.TestCase):
    """The same rule on the other screen, which has the same layout problem.

    The login panel is 460x490 and centred, so at a scale of two in an
    800-tall frame its top was at -90: the title ran off the top and the quit
    button off the bottom. It reads the same cap, which also means the type
    does not change size between logging in and arriving.
    """

    SMALL = (1280, 800)

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode(self.SMALL)

    def tearDown(self) -> None:
        self.pygame.quit()

    def _screen(self, size, *, scale: float = 2.0):
        from vibestorm.viewer.login_screen import LoginScreen

        return LoginScreen(size, ui_scale=scale)

    def _rects(self, screen):
        for name in (
            "title_label",
            "uri_entry",
            "first_entry",
            "last_entry",
            "password_entry",
            "start_entry",
            "remember_checkbox",
            "login_button",
            "quit_button",
            "status_label",
        ):
            yield name, getattr(screen, name).rect
        # The checkbox's caption is a separate element, and pygame_gui puts it
        # to the right of the checkbox's *rect* rather than beside the box --
        # so it is the one thing here whose position nothing in the login
        # screen sets directly. It was 123 pixels past the panel's edge.
        yield "remember caption", screen.remember_checkbox.text_label.rect

    def test_the_panel_is_the_rectangle_that_is_drawn(self) -> None:
        """`draw` reads `panel_rect`, and `_build_ui` sets it.

        It used to be worked out twice from the same four lines, once in
        each, which is two layouts the first time either moves. Pinned here
        because the test below asserts every widget *against* it: a
        `panel_rect` that had drifted would agree with a rectangle nobody
        can see.
        """
        width, height = self.SMALL
        screen = self._screen(self.SMALL)

        self.assertEqual(screen.panel_rect.size, (460, 490))
        self.assertEqual(screen.panel_rect.centerx, width // 2)
        self.assertEqual(screen.panel_rect.centery, height // 2)

    def test_nothing_hangs_out_of_the_login_panel(self) -> None:
        """Stronger than staying on the frame, and it is the visible claim.

        The panel is the glass rectangle everything is drawn on; a widget
        outside it is floating on the starfield whether or not it is on the
        window.
        """
        screen = self._screen(self.SMALL)

        for name, rect in self._rects(screen):
            with self.subTest(element=name):
                self.assertTrue(
                    screen.panel_rect.contains(rect),
                    f"{name} at {rect} is outside the panel {screen.panel_rect}",
                )

    def test_the_login_panel_stays_on_the_frame(self) -> None:
        width, height = self.SMALL
        screen = self._screen(self.SMALL)

        for name, rect in self._rects(screen):
            with self.subTest(element=name):
                self.assertGreaterEqual(rect.top, 0, f"{name} is above the frame")
                self.assertLessEqual(rect.bottom, height, f"{name} is below the frame")
                self.assertGreaterEqual(rect.left, 0, f"{name} is off the left")
                self.assertLessEqual(rect.right, width, f"{name} is off the right")

    def test_a_resize_puts_it_back_on(self) -> None:
        screen = self._screen((2360, 1640))
        self.assertEqual(screen.ui_scale, 2.0)

        self.pygame.display.set_mode(self.SMALL)
        screen.resize(self.SMALL)

        self.assertEqual(screen.ui_scale, 1.0)
        for name, rect in self._rects(screen):
            with self.subTest(element=name):
                self.assertGreaterEqual(rect.top, 0, f"{name} is above the frame")
                self.assertLessEqual(rect.bottom, self.SMALL[1], f"{name} is below it")


if __name__ == "__main__":
    unittest.main()
