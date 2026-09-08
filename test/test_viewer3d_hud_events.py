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


class _HUDRoutingCase(unittest.TestCase):
    """One HUD with recording callbacks, and a way to press one control."""

    SIZE = (1280, 800)

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
        self.addCleanup(pygame.quit)
        self.calls: list[tuple] = []
        self.hud = self._hud()

    def _recording(self, name: str):
        def record(*args) -> None:
            self.calls.append((name, *args))

        return record

    def _hud(self):
        """Windows left as they start: closed. Which one a button opens is
        the thing being asked, and a HUD with everything already open cannot
        answer it."""
        from vibestorm.viewer3d.hud import HUD

        return HUD(
            self.SIZE,
            on_chat_submit=self._recording("chat"),
            on_zoom_in=self._recording("zoom_in"),
            on_zoom_out=self._recording("zoom_out"),
            on_center=self._recording("center"),
            on_teleport=self._recording("teleport"),
            on_render_mode_change=self._recording("mode"),
            on_render_setting_change=self._recording("setting"),
        )

    def press(self, attribute: str) -> None:
        button = getattr(self.hud, attribute)
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_BUTTON_PRESSED, {"ui_element": button}
            )
        )


class HUDRoutingTests(_HUDRoutingCase):
    """Which control does which thing, which the sweep above does not ask.

    `HUDControlSweepTests` presses every control and asserts that none of them
    raises. That is the crash half, and it is the half that has been paying;
    it is also, by its own docstring, everything the HUD's dispatch had. Forty
    branches of `if event.ui_element is self.<something>_button` were reached
    and none of their effects were checked, so a button wired to its
    neighbour's window, or a toggle pointed at the wrong render setting,
    passes every test in this file.

    The pairs are written out rather than read from the source. A test that
    derives the mapping from the code under test agrees with a swap.
    """

    # -- menus -------------------------------------------------------------

    MENUS = (
        ("file_button", "file"),
        ("view_button", "view"),
        ("debug_button", "debug"),
        ("tools_button", "tools"),
        ("help_button", "help"),
    )

    def test_each_menu_button_opens_its_own_menu(self) -> None:
        for attribute, menu in self.MENUS:
            with self.subTest(attribute):
                self.hud._open_menu = None
                self.press(attribute)
                self.assertEqual(self.hud._open_menu, menu)

    def test_pressing_an_open_menu_closes_it(self) -> None:
        """Or the menu bar is a trap: opened, and no way back out of it with
        the same button that opened it."""
        self.hud._open_menu = None
        self.press("file_button")
        self.press("file_button")
        self.assertIsNone(self.hud._open_menu)

    # -- windows -----------------------------------------------------------

    WINDOW_BUTTONS = (
        ("show_chat_button", "chat_window"),
        ("inventory_button", "inventory_window"),
        ("render_settings_button", "render_settings_window"),
        ("inspector_button", "inspector_window"),
        ("teleport_button", "teleport_window"),
        ("options_button", "options_window"),
        ("movement_help_button", "help_window"),
    )

    def test_each_button_opens_its_own_window(self) -> None:
        for attribute, window in self.WINDOW_BUTTONS:
            with self.subTest(attribute):
                for _name, other in self.WINDOW_BUTTONS:
                    getattr(self.hud, other).hide()
                self.press(attribute)
                self.assertTrue(
                    getattr(self.hud, window).visible,
                    f"{attribute} did not open {window}",
                )
                opened = [
                    other
                    for _name, other in self.WINDOW_BUTTONS
                    if getattr(self.hud, other).visible
                ]
                self.assertEqual(opened, [window], f"{attribute} also opened {opened}")

    TOGGLE_WINDOWS = (
        ("diagnostics_button", "diagnostics_window"),
        ("heightmap_button", "heightmap_window"),
    )

    def test_the_two_toggling_windows_close_again(self) -> None:
        """These two are the debug panels, and they are the only ones the same
        button both opens and closes."""
        for attribute, window in self.TOGGLE_WINDOWS:
            with self.subTest(attribute):
                getattr(self.hud, window).hide()
                self.press(attribute)
                self.assertTrue(getattr(self.hud, window).visible)
                self.press(attribute)
                self.assertFalse(getattr(self.hud, window).visible)

    # -- render settings ---------------------------------------------------

    RENDER_TOGGLES = (
        ("render_terrain_button", "render_terrain"),
        ("render_terrain_lines_button", "render_terrain_lines"),
        ("render_clouds_button", "render_clouds"),
        ("render_sky_button", "render_sky"),
        ("render_neighbours_button", "render_neighbours"),
        ("render_water_button", "render_water"),
        ("render_objects_button", "render_objects"),
    )

    def test_each_toggle_changes_its_own_setting(self) -> None:
        """Seven buttons, seven names, and nothing else checked that the
        button labelled Water is not the one that turns off the sky."""
        for attribute, setting in self.RENDER_TOGGLES:
            with self.subTest(attribute):
                self.calls.clear()
                self.press(attribute)
                changed = [call for call in self.calls if call[0] == "setting"]
                self.assertEqual(len(changed), 1, f"{attribute}: {self.calls}")
                self.assertEqual(changed[0][1], setting)

    def test_a_toggle_goes_both_ways(self) -> None:
        for attribute, _setting in self.RENDER_TOGGLES:
            with self.subTest(attribute):
                self.calls.clear()
                self.press(attribute)
                self.press(attribute)
                values = [call[2] for call in self.calls if call[0] == "setting"]
                self.assertEqual(len(values), 2)
                self.assertNotEqual(values[0], values[1])

    def _slide(self, value: float) -> None:
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_HORIZONTAL_SLIDER_MOVED,
                {"ui_element": self.hud.water_alpha_slider, "value": value},
            )
        )

    def test_the_slider_sets_the_water_alpha_as_a_fraction(self) -> None:
        """The slider runs 0 to 100 and the setting is 0 to 1.

        Worth pinning because the two look interchangeable at a glance and
        only one of them is wrong by a factor of a hundred -- which the clamp
        below would then hide, by turning every value into the floor.
        """
        self._slide(25)
        self.assertIn(("setting", "water_alpha", 0.25), self.calls)

    def test_the_water_never_goes_fully_clear(self) -> None:
        """A transparent sea is a hole in the world with the seabed showing
        through it, so the bottom of the slider is a tenth rather than none."""
        self._slide(0)
        self.assertIn(("setting", "water_alpha", 0.1), self.calls)

    # -- everything else ---------------------------------------------------

    def test_the_render_mode_buttons_pick_their_own_mode(self) -> None:
        from vibestorm.viewer3d.hud import RENDER_MODE_2D, RENDER_MODE_3D

        self.press("render_mode_3d_button")
        self.assertIn(("mode", RENDER_MODE_3D), self.calls)
        self.press("render_mode_2d_button")
        self.assertIn(("mode", RENDER_MODE_2D), self.calls)

    def test_choosing_the_mode_already_showing_changes_nothing(self) -> None:
        """Rebuilding the renderer because somebody clicked the button for the
        view they are already looking at throws away every uploaded mesh and
        texture for no change at all."""
        self.calls.clear()
        self.press("render_mode_2d_button")
        self.assertEqual([call for call in self.calls if call[0] == "mode"], [])

    def test_the_camera_buttons_call_their_own_callback(self) -> None:
        for attribute, name in (
            ("zoom_in_button", "zoom_in"),
            ("zoom_out_button", "zoom_out"),
            ("center_button", "center"),
        ):
            with self.subTest(attribute):
                self.calls.clear()
                self.press(attribute)
                self.assertEqual([call[0] for call in self.calls], [name])

    def test_quit_is_the_only_button_that_asks_to_quit(self) -> None:
        """A stray `quit_requested` on any other branch ends the session on a
        click nobody meant as one."""
        for attribute, _window in self.WINDOW_BUTTONS:
            self.press(attribute)
        for attribute, _setting in self.RENDER_TOGGLES:
            self.press(attribute)
        self.assertFalse(self.hud.quit_requested)

        self.press("file_quit_button")
        self.assertTrue(self.hud.quit_requested)



class HUDActionRoutingTests(_HUDRoutingCase):
    """The other half of the dispatch: the controls that need a selection.

    A press on Save Asset with nothing selected does nothing, correctly, so
    the sweep above cannot tell it apart from a press that reached the wrong
    handler -- both are silent. What can be told apart is *which* handler the
    branch calls, and that is the whole of what the branch decides. So each
    of these replaces the handlers on the instance and checks that the button
    called its own and nobody else's.

    These six inspector buttons are the GUI end of priorities C, D and E. The
    handoff has said for two days that the button press itself is the one
    thing not covered, because reaching it end to end needs a window and a
    live simulator together. The routing does not: it needs a HUD.
    """

    #: Every control that dispatches to a named handler, and the handler it
    #: is supposed to reach. Written out, not derived.
    ACTIONS = (
        ("inventory_open_button", "_open_selected_inventory_folder"),
        ("inspector_load_inventory_button", "_request_selected_object_inventory"),
        ("inspector_view_asset_button", "_view_selected_asset"),
        ("inspector_save_asset_button", "_save_selected_asset"),
        ("inspector_save_all_button", "_sync_selected_object_to_folder"),
        ("inspector_upload_files_button", "_open_upload_file_dialog"),
        ("inspector_upload_folder_button", "_open_upload_folder_dialog"),
        ("teleport_go_button", "_submit_teleport"),
    )

    def _trap(self, names):
        """Replace each named method with a recorder. Returns the record."""
        seen: list[str] = []

        def make(name):
            def record(*_args, **_kwargs):
                seen.append(name)

            return record

        for name in names:
            self.assertTrue(hasattr(self.hud, name), f"no handler {name}")
            setattr(self.hud, name, make(name))
        return seen

    def test_each_action_button_calls_its_own_handler(self) -> None:
        handlers = [handler for _button, handler in self.ACTIONS]
        for button, handler in self.ACTIONS:
            with self.subTest(button):
                self.hud = self._hud()
                seen = self._trap(handlers)
                self.press(button)
                self.assertEqual(seen, [handler])

    LISTS = (
        ("inventory_list", "_select_inventory_row"),
        ("inspector_list", "_select_inspector_row"),
    )

    def test_picking_a_row_reaches_the_right_list_handler(self) -> None:
        for element, handler in self.LISTS:
            with self.subTest(element):
                self.hud = self._hud()
                seen = self._trap([name for _e, name in self.LISTS])
                self.hud.process_event(
                    self.pygame.event.Event(
                        self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
                        {"ui_element": getattr(self.hud, element), "text": "a row"},
                    )
                )
                self.assertEqual(seen, [handler])

    def test_the_row_that_was_picked_is_the_row_that_is_passed(self) -> None:
        """A handler called with the empty string clears the selection, which
        is what a lost `text` field would look like from the outside."""
        passed: list[str] = []
        self.hud._select_inventory_row = lambda text: passed.append(text)
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
                {"ui_element": self.hud.inventory_list, "text": "Objects"},
            )
        )
        self.assertEqual(passed, ["Objects"])

    def test_a_double_click_selects_and_then_opens(self) -> None:
        """One click picks a folder and two open it, and the second has to do
        both -- a double click that only opened would open whatever was
        selected before it."""
        seen = self._trap(["_select_inventory_row", "_open_selected_inventory_folder"])
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_SELECTION_LIST_DOUBLE_CLICKED_SELECTION,
                {"ui_element": self.hud.inventory_list, "text": "Objects"},
            )
        )
        self.assertEqual(seen, ["_select_inventory_row", "_open_selected_inventory_folder"])

    RESIZES = (
        ("chat_window", "_layout_chat_window"),
        ("asset_viewer_window", "_layout_asset_viewer_window"),
        ("inventory_window", "_layout_inventory_window"),
        ("inspector_window", "_layout_inspector_window"),
    )

    def test_each_window_relays_out_its_own_contents_when_resized(self) -> None:
        """Four windows, four layouts, and a resize that ran the wrong one
        leaves a panel with its widgets off the edge of itself."""
        handlers = [handler for _window, handler in self.RESIZES]
        for window, handler in self.RESIZES:
            with self.subTest(window):
                self.hud = self._hud()
                seen = self._trap(handlers)
                self.hud.process_event(
                    self.pygame.event.Event(
                        self.pygame_gui.UI_WINDOW_RESIZED,
                        {"ui_element": getattr(self.hud, window)},
                    )
                )
                self.assertEqual(seen, [handler])

    def test_submitting_chat_sends_it_once_and_clears_the_box(self) -> None:
        """A box that keeps what was sent sends it again on the next return."""
        self.hud.chat_input.set_text("hello")
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_TEXT_ENTRY_FINISHED,
                {"ui_element": self.hud.chat_input, "text": "hello"},
            )
        )
        self.assertEqual(self.calls, [("chat", "hello")])
        self.assertEqual(self.hud.chat_input.get_text(), "")

    def test_an_empty_line_is_not_sent(self) -> None:
        self.hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_TEXT_ENTRY_FINISHED,
                {"ui_element": self.hud.chat_input, "text": "   "},
            )
        )
        self.assertEqual(self.calls, [])



if __name__ == "__main__":
    unittest.main()
