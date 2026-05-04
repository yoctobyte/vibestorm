"""Tests for viewer3d HUD render-mode menu wiring."""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class RenderModeMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode((640, 480))

    def tearDown(self) -> None:
        self.pygame.quit()

    def _click(self, hud, button) -> bool:
        event = self.pygame.event.Event(
            self.pygame_gui.UI_BUTTON_PRESSED, {"ui_element": button}
        )
        return hud.process_event(event)

    def test_default_render_mode_is_2d_map(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_2D

        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        self.assertEqual(hud.render_mode, RENDER_MODE_2D)

    def test_clicking_3d_button_changes_mode_and_fires_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        consumed = self._click(hud, hud.render_mode_3d_button)

        self.assertTrue(consumed)
        self.assertEqual(hud.render_mode, RENDER_MODE_3D)
        self.assertEqual(calls, [RENDER_MODE_3D])

    def test_clicking_same_mode_twice_only_fires_callback_once(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        self._click(hud, hud.render_mode_3d_button)
        self._click(hud, hud.render_mode_3d_button)

        self.assertEqual(calls, [RENDER_MODE_3D])
        self.assertEqual(hud.render_mode, RENDER_MODE_3D)

    def test_switching_back_to_2d_fires_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_2D, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        self._click(hud, hud.render_mode_3d_button)
        self._click(hud, hud.render_mode_2d_button)

        self.assertEqual(calls, [RENDER_MODE_3D, RENDER_MODE_2D])
        self.assertEqual(hud.render_mode, RENDER_MODE_2D)

    def test_render_mode_button_hides_menu(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        # Open the View menu first.
        self._click(hud, hud.view_button)
        self.assertEqual(hud._open_menu, "view")

        self._click(hud, hud.render_mode_3d_button)

        self.assertIsNone(hud._open_menu)

    def test_status_bar_shows_active_mode(self) -> None:
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        scene = Scene()

        hud.update(0.016, scene)

        self.assertIn("mode=2D Map", hud.status_right.text)

        self._click(hud, hud.render_mode_3d_button)
        hud.update(0.016, scene)

        self.assertIn("mode=3D", hud.status_right.text)

    def test_callback_optional(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        # No callback provided — switching should still work without raising.
        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        self._click(hud, hud.render_mode_3d_button)

        self.assertEqual(hud.render_mode, RENDER_MODE_3D)


if __name__ == "__main__":
    unittest.main()
