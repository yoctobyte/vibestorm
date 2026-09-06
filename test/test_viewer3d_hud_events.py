"""Every control in the HUD, pressed, with nothing allowed to raise.

The owner's first priority is a reasonable visualization of the world *without
crashes*, and a viewer's crashes do not mostly live in the renderer. They live
in the widgets: a handler that reads an attribute the library renamed, a
callback invoked with no selection made, a window whose close event nobody
expected. `LoginScreen.resize` was exactly that -- it called a method
pygame_gui has not got, so dragging the window raised out of the event loop,
and nothing caught it because no test had ever called the method.

So this presses all sixty-odd buttons, finishes every text entry, moves the
slider and picks from every list, with every window open and every callback
supplied. It asserts nothing about what any of them *do* -- the tests beside
it do that -- only that none of them raises. What it does assert is that the
sweep reaches real code: a run in which every handler quietly bounced off a
guard would report no failures and mean nothing, so the callbacks a press can
reach without a selection first are named and checked.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class HUDControlSweepTests(unittest.TestCase):
    SIZE = (1280, 800)

    #: Opened by hand, because most of them are closed until asked for and a
    #: hidden window's buttons are still built, still wired and still
    #: reachable the moment anyone opens it.
    WINDOWS = (
        "diagnostics_window",
        "heightmap_window",
        "render_settings_window",
        "inventory_window",
        "inspector_window",
        "options_window",
        "teleport_window",
        "help_window",
        "asset_viewer_window",
    )

    #: What a press can reach with nothing selected. The rest of the callbacks
    #: need a row picked in the inventory or the inspector first, which is a
    #: different test's business; these are here so that "nothing raised"
    #: cannot be true because nothing ran.
    REACHABLE = frozenset(
        {"chat", "zoom_in", "zoom_out", "center", "teleport", "mode", "setting"}
    )

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode(self.SIZE)
        self.calls: list[str] = []

    def tearDown(self) -> None:
        self.pygame.quit()

    def _recording(self, name: str):
        def record(*_args, **_kwargs) -> None:
            self.calls.append(name)

        return record

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.region_name = "Vibestorm Test"
        scene.avatar_position = (128.0, 128.0, 25.0)
        return scene

    def _hud(self):
        from vibestorm.viewer3d.hud import HUD

        hud = HUD(
            self.SIZE,
            on_chat_submit=self._recording("chat"),
            on_zoom_in=self._recording("zoom_in"),
            on_zoom_out=self._recording("zoom_out"),
            on_center=self._recording("center"),
            on_teleport=self._recording("teleport"),
            on_inventory_open_folder=self._recording("open_folder"),
            on_object_inventory_request=self._recording("object_inventory"),
            on_view_asset=self._recording("view_asset"),
            on_save_asset=self._recording("save_asset"),
            on_sync_object_to_folder=self._recording("sync"),
            on_upload_files=self._recording("upload"),
            on_upload_object_files=self._recording("upload_object"),
            on_render_mode_change=self._recording("mode"),
            on_render_setting_change=self._recording("setting"),
        )
        for name in self.WINDOWS:
            getattr(hud, name).show()
        return hud

    def _events_for(self, element):
        """The UI events pygame_gui would post for this kind of control."""
        gui, pygame = self.pygame_gui, self.pygame
        elements = gui.elements
        if isinstance(element, elements.UIButton):
            yield pygame.event.Event(gui.UI_BUTTON_PRESSED, {"ui_element": element})
        elif isinstance(element, elements.UITextEntryLine):
            for kind in (gui.UI_TEXT_ENTRY_FINISHED, gui.UI_TEXT_ENTRY_CHANGED):
                yield pygame.event.Event(kind, {"ui_element": element, "text": "42"})
        elif isinstance(element, elements.UISelectionList):
            for kind in (
                gui.UI_SELECTION_LIST_NEW_SELECTION,
                gui.UI_SELECTION_LIST_DOUBLE_CLICKED_SELECTION,
            ):
                # A row that is not in the list: picking one that is needs a
                # populated scene, and a handler must survive either.
                yield pygame.event.Event(
                    kind, {"ui_element": element, "text": "no such row"}
                )
        elif isinstance(element, elements.UIHorizontalSlider):
            yield pygame.event.Event(
                gui.UI_HORIZONTAL_SLIDER_MOVED, {"ui_element": element, "value": 0.5}
            )
        elif isinstance(element, elements.UIWindow):
            for kind in (gui.UI_WINDOW_CLOSE, gui.UI_WINDOW_RESIZED):
                yield pygame.event.Event(kind, {"ui_element": element})

    def test_no_control_in_the_hud_raises_when_it_is_used(self) -> None:
        hud = self._hud()
        scene = self._scene()
        hud.update(0.6, scene)
        pressed = 0

        for element in hud.manager.get_sprite_group().sprites():
            for event in self._events_for(element):
                label = f"{type(element).__name__} {getattr(element, 'text', '')!r}"
                with self.subTest(control=label):
                    hud.process_event(event)
                    hud.update(0.016, scene)
                pressed += 1

        # Sixty-odd buttons and a dozen other controls. The number is a floor
        # rather than an equality: a HUD that grows a window should not fail
        # here, but one that quietly stopped building its widgets should.
        self.assertGreater(pressed, 60, "the sweep found almost no controls")
        self.assertEqual(
            self.REACHABLE - set(self.calls),
            set(),
            "a control the sweep should reach never fired its callback",
        )

    def test_the_keys_the_app_forwards_do_not_raise(self) -> None:
        # The app hands the HUD every key before deciding whether the world
        # wants it, so every key reaches this whatever it is bound to.
        hud = self._hud()
        scene = self._scene()

        for key in (
            self.pygame.K_RETURN,
            self.pygame.K_ESCAPE,
            self.pygame.K_TAB,
            self.pygame.K_F1,
            self.pygame.K_a,
        ):
            with self.subTest(key=key):
                hud.process_event(
                    self.pygame.event.Event(
                        self.pygame.KEYDOWN, {"key": key, "mod": 0, "unicode": "a"}
                    )
                )
                hud.update(0.016, scene)


class MapHUDControlSweepTests(HUDControlSweepTests):
    """The 2D map HUD, which is a different shell with the same hazard."""

    WINDOWS = ("inventory_window", "teleport_window", "help_window")
    REACHABLE = frozenset({"chat", "zoom_in", "zoom_out", "center", "teleport"})

    def _scene(self):
        from vibestorm.viewer.scene import Scene

        scene = Scene(region_name="Vibestorm Test")
        scene.avatar_position = (128.0, 128.0, 25.0)
        return scene

    def _hud(self):
        from vibestorm.viewer.hud import HUD

        hud = HUD(
            self.SIZE,
            on_chat_submit=self._recording("chat"),
            on_zoom_in=self._recording("zoom_in"),
            on_zoom_out=self._recording("zoom_out"),
            on_center=self._recording("center"),
            on_teleport=self._recording("teleport"),
        )
        for name in self.WINDOWS:
            getattr(hud, name).show()
        return hud

    def test_no_control_in_the_hud_raises_when_it_is_used(self) -> None:
        # Same sweep, fewer controls: this shell has no inspector and no
        # render settings, so the floor above does not apply to it.
        hud = self._hud()
        scene = self._scene()
        hud.update(0.6, scene)
        pressed = 0

        for element in hud.manager.get_sprite_group().sprites():
            for event in self._events_for(element):
                label = f"{type(element).__name__} {getattr(element, 'text', '')!r}"
                with self.subTest(control=label):
                    hud.process_event(event)
                    hud.update(0.016, scene)
                pressed += 1

        self.assertGreater(pressed, 15, "the sweep found almost no controls")
        self.assertEqual(self.REACHABLE - set(self.calls), set())


if __name__ == "__main__":
    unittest.main()
