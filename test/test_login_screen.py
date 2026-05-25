import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class LoginScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode((800, 600))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_ui_elements_creation(self) -> None:
        from vibestorm.viewer.login_screen import LoginScreen

        screen = LoginScreen((800, 600))
        self.assertIsNotNone(screen.preset_dropdown)
        self.assertIsNotNone(screen.uri_entry)
        self.assertIsNotNone(screen.first_entry)
        self.assertIsNotNone(screen.last_entry)
        self.assertIsNotNone(screen.password_entry)
        self.assertIsNotNone(screen.start_entry)
        self.assertIsNotNone(screen.login_button)
        self.assertIsNotNone(screen.quit_button)
        self.assertIsNotNone(screen.status_label)

    def test_preset_selection_updates_fields(self) -> None:
        from vibestorm.viewer.login_screen import LoginScreen

        screen = LoginScreen((800, 600))

        # Change preset to OSgrid
        screen.preset_dropdown.selected_option = "OSgrid"
        screen._apply_preset_defaults()
        self.assertEqual(screen.uri_entry.get_text(), "http://login.osgrid.org/")
        self.assertEqual(screen.start_entry.get_text(), "last")

        # Change preset to Second Life
        screen.preset_dropdown.selected_option = "Second Life"
        screen._apply_preset_defaults()
        self.assertEqual(screen.uri_entry.get_text(), "https://login.agni.lindenlab.com/cgi-bin/login.cgi")
        self.assertEqual(screen.start_entry.get_text(), "last")

    def test_quit_button_sets_quit_requested(self) -> None:
        from vibestorm.viewer.login_screen import LoginScreen

        screen = LoginScreen((800, 600))
        event = self.pygame.event.Event(
            self.pygame.USEREVENT,
            {
                "user_type": self.pygame_gui.UI_BUTTON_PRESSED,
                "ui_element": screen.quit_button,
            },
        )
        consumed = screen.process_event(event)
        self.assertTrue(consumed)
        self.assertTrue(screen.quit_requested)


if __name__ == "__main__":
    unittest.main()
