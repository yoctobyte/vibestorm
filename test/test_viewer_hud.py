import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class HUDTests(unittest.TestCase):
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

    def test_enter_in_chat_input_submits_text(self) -> None:
        from vibestorm.viewer.hud import HUD

        submitted: list[str] = []
        hud = HUD((640, 480), on_chat_submit=submitted.append)
        hud.chat_input.set_text(" hello world ")

        event = self.pygame.event.Event(
            self.pygame_gui.UI_TEXT_ENTRY_FINISHED,
            {"ui_element": hud.chat_input, "text": " hello world "},
        )

        hud.process_event(event)

        self.assertEqual(submitted, ["hello world"])
        self.assertEqual(hud.chat_input.get_text(), "")

    def test_blank_chat_input_is_ignored(self) -> None:
        from vibestorm.viewer.hud import HUD

        submitted: list[str] = []
        hud = HUD((640, 480), on_chat_submit=submitted.append)

        event = self.pygame.event.Event(
            self.pygame_gui.UI_TEXT_ENTRY_FINISHED,
            {"ui_element": hud.chat_input, "text": "   "},
        )

        hud.process_event(event)

        self.assertEqual(submitted, [])

    def test_zoom_buttons_call_callbacks(self) -> None:
        from vibestorm.viewer.hud import HUD

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_zoom_in=lambda: calls.append("in"),
            on_zoom_out=lambda: calls.append("out"),
            on_center=lambda: calls.append("center"),
        )

        for element in (hud.zoom_in_button, hud.zoom_out_button, hud.center_button):
            event = self.pygame.event.Event(
                self.pygame_gui.UI_BUTTON_PRESSED,
                {"ui_element": element},
            )
            hud.process_event(event)

        self.assertEqual(calls, ["in", "out", "center"])

    def test_ui_scale_enlarges_shell_dimensions(self) -> None:
        from vibestorm.viewer.hud import HUD

        hud = HUD((1280, 720), on_chat_submit=lambda text: None, ui_scale=2.0)

        self.assertEqual(hud.menu_height, 60)
        self.assertEqual(hud.status_height, 48)
        self.assertEqual(hud.file_button.relative_rect.height, 48)

    def test_file_quit_button_sets_quit_requested(self) -> None:
        from vibestorm.viewer.hud import HUD

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        event = self.pygame.event.Event(
            self.pygame_gui.UI_BUTTON_PRESSED,
            {"ui_element": hud.file_quit_button},
        )

        consumed = hud.process_event(event)

        self.assertTrue(consumed)
        self.assertTrue(hud.quit_requested)

    def test_status_bar_reflects_scene_counts(self) -> None:
        from vibestorm.viewer.hud import HUD
        from vibestorm.viewer.scene import ChatLine, Marker, Scene

        hud = HUD((800, 600), on_chat_submit=lambda text: None)
        scene = Scene(region_name="Test Region")
        scene.avatar_position = (128.0, 129.0, 25.0)
        scene.object_markers[1] = Marker(1, 9, (1, 2, 3), (1, 1, 1), 0.0)
        scene.avatar_markers[2] = Marker(2, 47, (1, 2, 3), (1, 1, 1), 0.0)
        scene.chat_lines.append(ChatLine(kind="local", sender="A", message="hi"))

        hud.update(0.016, scene)

        self.assertIn("Test Region", hud.status_left.text)
        self.assertIn("128.0, 129.0, 25.0", hud.status_left.text)
        self.assertIn("Parcel: unknown", hud.status_left.text)
        self.assertIn("objects=1", hud.status_right.text)
        self.assertIn("avatars=1", hud.status_right.text)

    def test_teleport_button_calls_callback_with_position(self) -> None:
        from vibestorm.viewer.hud import HUD

        submitted: list[tuple[float, float, float]] = []
        hud = HUD((800, 600), on_chat_submit=lambda text: None, on_teleport=submitted.append)
        hud.teleport_x.set_text("10")
        hud.teleport_y.set_text("20.5")
        hud.teleport_z.set_text("30")

        event = self.pygame.event.Event(
            self.pygame_gui.UI_BUTTON_PRESSED,
            {"ui_element": hud.teleport_go_button},
        )
        hud.process_event(event)

        self.assertEqual(submitted, [(10.0, 20.5, 30.0)])
        self.assertIn("requested", hud.teleport_status.text)

    def test_inventory_window_reflects_snapshot(self) -> None:
        from uuid import UUID

        from vibestorm.caps.inventory_client import (
            InventoryFetchSnapshot,
            InventoryFolderContents,
            InventoryItemEntry,
        )
        from vibestorm.viewer.hud import HUD
        from vibestorm.viewer.scene import Scene

        hud = HUD((800, 600), on_chat_submit=lambda text: None)
        scene = Scene(region_name="Test Region")
        scene.inventory_snapshot = InventoryFetchSnapshot(
            folders=(
                InventoryFolderContents(
                    folder_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
                    owner_id=None,
                    agent_id=None,
                    descendents=1,
                    version=1,
                    categories=(),
                    items=(
                        InventoryItemEntry(
                            item_id=None,
                            asset_id=UUID("87654321-1111-2222-3333-444444444444"),
                            parent_id=None,
                            name="Test Asset",
                            description="",
                            type=0,
                            inv_type=0,
                            flags=0,
                        ),
                    ),
                ),
            )
        )

        hud.update(0.016, scene)

        self.assertIn("Test Asset", hud.inventory_text.html_text)


class ViewerAppScaleTests(unittest.TestCase):
    def test_auto_ui_scale_uses_1080p_as_1x_and_4k_as_2x(self) -> None:
        from vibestorm.viewer.app import _auto_ui_scale

        class Display:
            def __init__(self, size: tuple[int, int]) -> None:
                self.size = size

            def get_desktop_sizes(self) -> list[tuple[int, int]]:
                return [self.size]

        class PygameModule:
            error = RuntimeError

            def __init__(self, size: tuple[int, int]) -> None:
                self.display = Display(size)

        self.assertEqual(_auto_ui_scale(PygameModule((1920, 1080))), 1.0)
        self.assertEqual(_auto_ui_scale(PygameModule((3840, 2160))), 2.0)
        self.assertEqual(_auto_ui_scale(PygameModule((2560, 1440))), 1.25)


if __name__ == "__main__":
    unittest.main()
