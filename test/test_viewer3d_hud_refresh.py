"""Refresh cost of the viewer3d HUD.

Profiled on a GTX 1660 SUPER against local OpenSim, the HUD was two thirds of
a 30.7 ms frame while the 3D pass itself took 4.3 ms. ``_refresh_ticker`` alone
was 6.1 ms of that, run every frame, because rebuilding a pygame_gui text box
re-parses and re-renders all of its markup. These tests pin the two things that
brought it down: refreshes are throttled, and a widget is only written when its
text actually changed.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class HUDRefreshCostTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((800, 600))

    def tearDown(self) -> None:
        self.pygame.quit()

    def _hud(self):
        from vibestorm.viewer3d.hud import HUD

        return HUD((800, 600), on_chat_submit=lambda _text: None)

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.region_name = "Vibestorm Test"
        scene.parcel_name = "Sandbox"
        scene.avatar_position = (128.0, 128.0, 25.0)
        return scene

    @staticmethod
    def _record(widget) -> list[str]:
        calls: list[str] = []
        original = widget.set_text

        def recording(text, *args, **kwargs):
            calls.append(text)
            return original(text, *args, **kwargs)

        widget.set_text = recording
        return calls

    def test_a_still_scene_stops_rewriting_the_status_bar(self) -> None:
        from vibestorm.viewer3d.hud import STATUS_REFRESH_INTERVAL_S

        hud = self._hud()
        scene = self._scene()
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)

        calls = self._record(hud.status_left)
        for _ in range(5):
            hud.update(STATUS_REFRESH_INTERVAL_S, scene)
        self.assertEqual(calls, [])

    def test_a_moving_avatar_still_updates_the_status_bar(self) -> None:
        from vibestorm.viewer3d.hud import STATUS_REFRESH_INTERVAL_S

        hud = self._hud()
        scene = self._scene()
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)

        calls = self._record(hud.status_left)
        scene.avatar_position = (140.0, 128.0, 25.0)
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)
        self.assertEqual(len(calls), 1)
        self.assertIn("140.0", calls[0])

    def test_the_first_frame_paints_rather_than_waiting_out_the_interval(self) -> None:
        # Starting the accumulator at zero would leave the bar blank for a
        # quarter of a second after login, which reads as a broken HUD.
        hud = self._hud()
        calls = self._record(hud.status_left)
        hud.update(0.016, self._scene())
        self.assertEqual(len(calls), 1)

    def test_sub_interval_frames_do_not_touch_the_widgets(self) -> None:
        from vibestorm.viewer3d.hud import STATUS_REFRESH_INTERVAL_S

        hud = self._hud()
        scene = self._scene()
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)

        calls = self._record(hud.status_left)
        step = STATUS_REFRESH_INTERVAL_S / 4.0  # exact in binary
        for i in range(3):
            scene.avatar_position = (130.0 + i, 128.0, 25.0)
            hud.update(step, scene)
        self.assertEqual(calls, [])

        scene.avatar_position = (200.0, 128.0, 25.0)
        hud.update(step, scene)
        self.assertEqual(len(calls), 1)
        self.assertIn("200.0", calls[0])

    def test_repeated_chat_refreshes_write_the_ticker_once(self) -> None:
        from vibestorm.viewer3d.hud import STATUS_REFRESH_INTERVAL_S
        from vibestorm.viewer3d.scene import ChatLine

        hud = self._hud()
        scene = self._scene()
        hud.chat_window.show()
        scene.chat_lines.append(ChatLine(kind="local", sender="Res", message="hello"))
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)

        calls = self._record(hud.ticker)
        for _ in range(5):
            hud.update(STATUS_REFRESH_INTERVAL_S, scene)
        self.assertEqual(calls, [], "unchanged chat must not be re-rendered")

    def test_a_closed_chat_window_skips_the_ticker_but_catches_up(self) -> None:
        from vibestorm.viewer3d.hud import STATUS_REFRESH_INTERVAL_S
        from vibestorm.viewer3d.scene import ChatLine

        hud = self._hud()
        scene = self._scene()
        hud.chat_window.hide()
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)

        calls = self._record(hud.ticker)
        scene.chat_lines.append(ChatLine(kind="local", sender="Res", message="hello"))
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)
        self.assertEqual(calls, [])

        hud.chat_window.show()
        hud.update(STATUS_REFRESH_INTERVAL_S, scene)
        self.assertEqual(len(calls), 1)
        self.assertIn("hello", calls[0])


if __name__ == "__main__":
    unittest.main()


class HUDImmediateRefreshTests(unittest.TestCase):
    """A direct user action must not wait out the refresh interval."""

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode((800, 600))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_switching_render_mode_updates_the_status_bar_on_the_next_frame(self) -> None:
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene

        hud = HUD((800, 600), on_chat_submit=lambda _text: None)
        scene = Scene()
        hud.update(0.016, scene)
        self.assertIn("mode=2D Map", hud.status_right.text)

        hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_BUTTON_PRESSED, {"ui_element": hud.render_mode_3d_button}
            )
        )
        # One short frame, far inside the throttle interval.
        hud.update(0.016, scene)
        self.assertIn("mode=3D", hud.status_right.text)
