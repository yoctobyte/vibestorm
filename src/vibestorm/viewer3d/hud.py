"""Viewer UI shell built on pygame_gui.

The HUD owns its own UIManager; the app forwards pygame events to
``hud.process_event`` and ``hud.update(dt)`` once per frame, then
``hud.draw(surface)`` last so it renders on top of the world.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.scene import Scene


CHAT_TICKER_LINES = 8
BASE_MENU_HEIGHT = 30
BASE_STATUS_HEIGHT = 24
RENDER_MODE_2D = "2d-map"
RENDER_MODE_3D = "3d"
RENDER_MODE_LABELS: dict[str, str] = {
    RENDER_MODE_2D: "2D Map",
    RENDER_MODE_3D: "3D",
}
DEFAULT_HELP_TEXT = """Vibestorm 2D movement

W / Up: move forward
S / Down: move backward
A / Left: turn left
D / Right: turn right
Q / E: strafe left / right
Page Up / Page Down: move up / down
F: toggle fly
C: center on avatar
Mouse wheel: zoom
Right drag: pan the map
Enter: focus chat
"""


@dataclass(slots=True, frozen=True)
class InventoryDisplayRow:
    text: str
    detail_html: str
    folder_id: UUID | None = None
    can_open: bool = False


class HUD:
    """Main-menu strip, status bar, and a resizable chat subwindow.

    Event flow:
    - Caller forwards every pygame event to ``process_event``.
    - On UI_TEXT_ENTRY_FINISHED of the chat box, ``on_chat_submit(text)`` is invoked.
    """

    def __init__(
        self,
        screen_size: tuple[int, int],
        *,
        on_chat_submit: Callable[[str], None],
        on_zoom_in: Callable[[], None] | None = None,
        on_zoom_out: Callable[[], None] | None = None,
        on_center: Callable[[], None] | None = None,
        on_teleport: Callable[[tuple[float, float, float]], None] | None = None,
        on_inventory_open_folder: Callable[[UUID], None] | None = None,
        on_render_mode_change: Callable[[str], None] | None = None,
        on_render_setting_change: Callable[[str, object], None] | None = None,
        initial_render_mode: str = RENDER_MODE_2D,
        help_text: str = DEFAULT_HELP_TEXT,
        theme_path: str | None = None,
        ui_scale: float = 1.0,
    ) -> None:
        import pygame
        import pygame_gui

        self._pygame = pygame
        self._pygame_gui = pygame_gui
        self.screen_size = screen_size
        self.ui_scale = max(0.75, float(ui_scale))
        self.on_chat_submit = on_chat_submit
        self.on_zoom_in = on_zoom_in
        self.on_zoom_out = on_zoom_out
        self.on_center = on_center
        self.on_teleport = on_teleport
        self.on_inventory_open_folder = on_inventory_open_folder
        self.on_render_mode_change = on_render_mode_change
        self.on_render_setting_change = on_render_setting_change
        self.render_mode: str = (
            initial_render_mode if initial_render_mode in RENDER_MODE_LABELS else RENDER_MODE_2D
        )
        self.help_text = help_text
        self._last_fps = 0.0
        self._last_diagnostics_html: str | None = None
        self._open_menu: str | None = None
        self._last_chat_container_size: tuple[int, int] | None = None
        self._last_inventory_signature: tuple[tuple[object, ...], ...] | None = None
        self._inventory_row_details: dict[str, str] = {}
        self._inventory_openable_rows: dict[str, UUID] = {}
        self._selected_inventory_row: str | None = None
        self._last_heightmap_signature: (
            tuple[int, int, int, float | None, float | None] | None
        ) = None
        self._render_setting_values: dict[str, object] = {
            "render_terrain": True,
            "render_terrain_lines": True,
            "render_water": True,
            "render_objects": True,
            "water_alpha": 0.72,
        }
        self.quit_requested = False

        manager_kwargs: dict = {}
        if theme_path is not None:
            manager_kwargs["theme_path"] = theme_path
        self.manager = pygame_gui.UIManager(screen_size, **manager_kwargs)

        self._build_widgets()

    # ------------------------------------------------------------------ wiring

    @property
    def menu_height(self) -> int:
        return self._s(BASE_MENU_HEIGHT)

    @property
    def status_height(self) -> int:
        return self._s(BASE_STATUS_HEIGHT)

    def _s(self, value: float) -> int:
        return max(1, int(round(value * self.ui_scale)))

    def _build_widgets(self) -> None:
        import pygame
        from pygame_gui.elements import (
            UIButton,
            UILabel,
            UIPanel,
            UITextBox,
            UITextEntryLine,
            UIWindow,
        )

        sw, sh = self.screen_size
        menu_h = self.menu_height
        status_h = self.status_height

        self.menu_bar = UIPanel(
            relative_rect=pygame.Rect(0, 0, sw, menu_h),
            manager=self.manager,
            anchors={"left": "left", "right": "right", "top": "top"},
        )
        self.file_button = UIButton(
            relative_rect=pygame.Rect(self._s(6), self._s(3), self._s(52), self._s(24)),
            text="File",
            manager=self.manager,
            container=self.menu_bar,
        )
        self.view_button = UIButton(
            relative_rect=pygame.Rect(self._s(62), self._s(3), self._s(56), self._s(24)),
            text="View",
            manager=self.manager,
            container=self.menu_bar,
        )
        self.debug_button = UIButton(
            relative_rect=pygame.Rect(self._s(122), self._s(3), self._s(68), self._s(24)),
            text="Debug",
            manager=self.manager,
            container=self.menu_bar,
        )
        self.tools_button = UIButton(
            relative_rect=pygame.Rect(self._s(194), self._s(3), self._s(66), self._s(24)),
            text="Tools",
            manager=self.manager,
            container=self.menu_bar,
        )
        self.help_button = UIButton(
            relative_rect=pygame.Rect(self._s(264), self._s(3), self._s(58), self._s(24)),
            text="Help",
            manager=self.manager,
            container=self.menu_bar,
        )
        self.title_label = UILabel(
            relative_rect=pygame.Rect(
                self._s(336),
                self._s(4),
                max(self._s(160), sw // 2 - self._s(120)),
                self._s(22),
            ),
            text="Vibestorm",
            manager=self.manager,
            container=self.menu_bar,
        )

        self.status_bar = UIPanel(
            relative_rect=pygame.Rect(0, sh - status_h, sw, status_h),
            manager=self.manager,
            anchors={"left": "left", "right": "right", "bottom": "bottom"},
        )
        self.status_left = UILabel(
            relative_rect=pygame.Rect(
                self._s(6),
                self._s(1),
                max(self._s(240), sw - self._s(440)),
                self._s(22),
            ),
            text="Region: (none)",
            manager=self.manager,
            container=self.status_bar,
        )
        self.status_right = UILabel(
            relative_rect=pygame.Rect(
                max(self._s(250), sw - self._s(430)),
                self._s(1),
                self._s(420),
                self._s(22),
            ),
            text="objects=0 avatars=0 chat=0",
            manager=self.manager,
            container=self.status_bar,
        )

        self.file_menu = self._build_menu_panel(
            x=self._s(6),
            y=menu_h,
            width=self._s(150),
            rows=(("Quit", "file_quit_button"),),
        )
        self.view_menu = self._build_menu_panel(
            x=self._s(62),
            y=menu_h,
            width=self._s(200),
            rows=(
                ("Show Chat", "show_chat_button"),
                ("Inventory", "inventory_button"),
                ("Render Settings", "render_settings_button"),
                ("Render: 2D Map", "render_mode_2d_button"),
                ("Render: 3D", "render_mode_3d_button"),
            ),
        )
        self.debug_menu = self._build_menu_panel(
            x=self._s(122),
            y=menu_h,
            width=self._s(180),
            rows=(
                ("Diagnostics", "diagnostics_button"),
                ("Sim Debug", "heightmap_button"),
                ("Zoom In", "zoom_in_button"),
                ("Zoom Out", "zoom_out_button"),
                ("Center", "center_button"),
            ),
        )
        self.tools_menu = self._build_menu_panel(
            x=self._s(194),
            y=menu_h,
            width=self._s(180),
            rows=(
                ("Teleport", "teleport_button"),
                ("Options", "options_button"),
            ),
        )
        self.help_menu = self._build_menu_panel(
            x=self._s(264),
            y=menu_h,
            width=self._s(150),
            rows=(("Movement Help", "movement_help_button"),),
        )
        self._hide_all_menus()

        chat_w = min(max(self._s(430), sw // 3), max(self._s(360), sw - self._s(40)))
        chat_h = min(
            max(self._s(260), sh // 3),
            max(self._s(220), sh - menu_h - status_h - self._s(40)),
        )
        self.chat_window = UIWindow(
            rect=pygame.Rect(self._s(20), sh - status_h - chat_h - self._s(16), chat_w, chat_h),
            manager=self.manager,
            window_display_title="Chat",
            resizable=True,
        )

        container = self.chat_window.get_container()
        self.ticker = UITextBox(
            html_text="",
            relative_rect=pygame.Rect(0, 0, 1, 1),
            manager=self.manager,
            container=container,
        )
        self.chat_input = UITextEntryLine(
            relative_rect=pygame.Rect(0, 0, 1, 1),
            manager=self.manager,
            container=container,
        )
        self.chat_input.set_text_length_limit(1023)
        self.chat_input.placeholder_text = "Enter chat..."
        self._layout_chat_window()

        self._build_aux_windows(sw, sh)

    def _build_menu_panel(
        self,
        *,
        x: int,
        y: int,
        width: int,
        rows: tuple[tuple[str, str], ...],
    ):
        import pygame
        from pygame_gui.elements import UIButton, UIPanel

        row_h = self._s(28)
        panel = UIPanel(
            relative_rect=pygame.Rect(x, y, width, self._s(8) + row_h * len(rows)),
            manager=self.manager,
        )
        for index, (text, attr_name) in enumerate(rows):
            button = UIButton(
                relative_rect=pygame.Rect(
                    self._s(4),
                    self._s(4) + index * row_h,
                    width - self._s(8),
                    self._s(24),
                ),
                text=text,
                manager=self.manager,
                container=panel,
            )
            setattr(self, attr_name, button)
        return panel

    def _hide_all_menus(self) -> None:
        for menu in (
            self.file_menu,
            self.view_menu,
            self.debug_menu,
            self.tools_menu,
            self.help_menu,
        ):
            menu.hide()

    def _toggle_menu(self, name: str) -> None:
        menus = {
            "file": self.file_menu,
            "view": self.view_menu,
            "debug": self.debug_menu,
            "tools": self.tools_menu,
            "help": self.help_menu,
        }
        if self._open_menu == name:
            self._hide_all_menus()
            self._open_menu = None
            return
        self._hide_all_menus()
        menus[name].show()
        self._open_menu = name

    def _set_render_mode(self, mode: str) -> None:
        if mode == self.render_mode:
            return
        if mode not in RENDER_MODE_LABELS:
            return
        self.render_mode = mode
        if self.on_render_mode_change is not None:
            self.on_render_mode_change(mode)

    def _toggle_render_bool(self, name: str) -> None:
        value = not bool(self._render_setting_values.get(name, True))
        self._set_render_setting(name, value)

    def _set_render_setting(self, name: str, value: object) -> None:
        self._render_setting_values[name] = value
        self._refresh_render_settings_controls()
        if self.on_render_setting_change is not None:
            self.on_render_setting_change(name, value)

    def _refresh_render_settings_controls(self) -> None:
        labels = {
            "render_terrain": "Terrain Surface",
            "render_terrain_lines": "Mesh Lines",
            "render_water": "Water",
            "render_objects": "Objects",
        }
        buttons = {
            "render_terrain": self.render_terrain_button,
            "render_terrain_lines": self.render_terrain_lines_button,
            "render_water": self.render_water_button,
            "render_objects": self.render_objects_button,
        }
        for key, button in buttons.items():
            marker = "x" if bool(self._render_setting_values.get(key, True)) else " "
            button.set_text(f"[{marker}] {labels[key]}")
        alpha = max(0.1, min(1.0, float(self._render_setting_values.get("water_alpha", 0.72))))
        percent = int(round(alpha * 100.0))
        self.water_alpha_label.set_text(f"Water opacity: {percent}%")
        if int(round(self.water_alpha_slider.get_current_value())) != percent:
            self.water_alpha_slider.set_current_value(percent)

    def _build_aux_windows(self, sw: int, sh: int) -> None:
        import pygame
        from pygame_gui.elements import (
            UIButton,
            UIHorizontalSlider,
            UIImage,
            UILabel,
            UISelectionList,
            UITextBox,
            UITextEntryLine,
            UIWindow,
        )

        self.help_window = UIWindow(
            rect=pygame.Rect(self._s(70), self._s(70), self._s(460), self._s(360)),
            manager=self.manager,
            window_display_title="Movement Help",
            resizable=True,
        )
        self.help_text_box = UITextBox(
            html_text=_plain_text_to_html(self.help_text),
            relative_rect=pygame.Rect(self._s(8), self._s(8), self._s(430), self._s(300)),
            manager=self.manager,
            container=self.help_window.get_container(),
        )
        self.help_window.hide()

        self.teleport_window = UIWindow(
            rect=pygame.Rect(
                max(self._s(80), sw - self._s(430)),
                self._s(70),
                self._s(360),
                self._s(230),
            ),
            manager=self.manager,
            window_display_title="Teleport",
            resizable=False,
        )
        tp_container = self.teleport_window.get_container()
        self.teleport_label = UILabel(
            relative_rect=pygame.Rect(self._s(10), self._s(10), self._s(320), self._s(24)),
            text="Local destination",
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_x = UITextEntryLine(
            relative_rect=pygame.Rect(self._s(10), self._s(42), self._s(96), self._s(30)),
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_y = UITextEntryLine(
            relative_rect=pygame.Rect(self._s(116), self._s(42), self._s(96), self._s(30)),
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_z = UITextEntryLine(
            relative_rect=pygame.Rect(self._s(222), self._s(42), self._s(96), self._s(30)),
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_x.set_text("128")
        self.teleport_y.set_text("128")
        self.teleport_z.set_text("25")
        self.teleport_go_button = UIButton(
            relative_rect=pygame.Rect(self._s(10), self._s(86), self._s(120), self._s(30)),
            text="Teleport",
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_status = UILabel(
            relative_rect=pygame.Rect(self._s(10), self._s(126), self._s(320), self._s(24)),
            text="",
            manager=self.manager,
            container=tp_container,
        )
        self.teleport_window.hide()

        self.options_window = UIWindow(
            rect=pygame.Rect(self._s(90), self._s(90), self._s(360), self._s(170)),
            manager=self.manager,
            window_display_title="Options",
            resizable=False,
        )
        self.options_text = UITextBox(
            html_text=(
                "Options are currently command-backed where available.<br>"
                "Use --ui-scale, --width, and --height for startup sizing."
            ),
            relative_rect=pygame.Rect(self._s(8), self._s(8), self._s(330), self._s(100)),
            manager=self.manager,
            container=self.options_window.get_container(),
        )
        self.options_window.hide()

        self.render_settings_window = UIWindow(
            rect=pygame.Rect(self._s(110), self._s(90), self._s(360), self._s(310)),
            manager=self.manager,
            window_display_title="Render Settings",
            resizable=False,
        )
        rs_container = self.render_settings_window.get_container()
        self.render_terrain_button = UIButton(
            relative_rect=pygame.Rect(self._s(10), self._s(12), self._s(220), self._s(28)),
            text="",
            manager=self.manager,
            container=rs_container,
        )
        self.render_terrain_lines_button = UIButton(
            relative_rect=pygame.Rect(self._s(10), self._s(48), self._s(220), self._s(28)),
            text="",
            manager=self.manager,
            container=rs_container,
        )
        self.render_water_button = UIButton(
            relative_rect=pygame.Rect(self._s(10), self._s(84), self._s(220), self._s(28)),
            text="",
            manager=self.manager,
            container=rs_container,
        )
        self.render_objects_button = UIButton(
            relative_rect=pygame.Rect(self._s(10), self._s(120), self._s(220), self._s(28)),
            text="",
            manager=self.manager,
            container=rs_container,
        )
        self.water_alpha_label = UILabel(
            relative_rect=pygame.Rect(self._s(10), self._s(166), self._s(300), self._s(24)),
            text="Water opacity: 72%",
            manager=self.manager,
            container=rs_container,
        )
        self.water_alpha_slider = UIHorizontalSlider(
            relative_rect=pygame.Rect(self._s(10), self._s(198), self._s(300), self._s(28)),
            start_value=72,
            value_range=(10, 100),
            manager=self.manager,
            container=rs_container,
            click_increment=5,
        )
        self._refresh_render_settings_controls()
        self.render_settings_window.hide()

        self.inventory_window = UIWindow(
            rect=pygame.Rect(
                max(self._s(80), sw - self._s(680)),
                self._s(110),
                self._s(620),
                self._s(440),
            ),
            manager=self.manager,
            window_display_title="Inventory Manager",
            resizable=True,
        )
        inv_container = self.inventory_window.get_container()
        self.inventory_summary = UILabel(
            relative_rect=pygame.Rect(self._s(8), self._s(8), self._s(580), self._s(24)),
            text="Inventory has not loaded yet.",
            manager=self.manager,
            container=inv_container,
        )
        self.inventory_open_button = UIButton(
            relative_rect=pygame.Rect(self._s(8), self._s(38), self._s(110), self._s(28)),
            text="Open",
            manager=self.manager,
            container=inv_container,
        )
        self.inventory_open_button.disable()
        self.inventory_list = UISelectionList(
            relative_rect=pygame.Rect(self._s(8), self._s(74), self._s(285), self._s(296)),
            item_list=[],
            manager=self.manager,
            container=inv_container,
            allow_multi_select=False,
        )
        self.inventory_details = UITextBox(
            html_text="Select an inventory row.",
            relative_rect=pygame.Rect(self._s(305), self._s(40), self._s(285), self._s(330)),
            manager=self.manager,
            container=inv_container,
        )
        self.inventory_window.hide()

        self.diagnostics_window = UIWindow(
            rect=pygame.Rect(
                max(self._s(120), sw - self._s(450)),
                self._s(70),
                self._s(390),
                self._s(300),
            ),
            manager=self.manager,
            window_display_title="Diagnostics",
            resizable=True,
        )
        self.diagnostics_text = UITextBox(
            html_text="Diagnostics pending.",
            relative_rect=pygame.Rect(self._s(8), self._s(8), self._s(350), self._s(230)),
            manager=self.manager,
            container=self.diagnostics_window.get_container(),
        )
        if self.render_mode != RENDER_MODE_3D:
            self.diagnostics_window.hide()

        self.heightmap_window = UIWindow(
            rect=pygame.Rect(
                max(self._s(80), sw - self._s(390)),
                self._s(390),
                self._s(330),
                self._s(360),
            ),
            manager=self.manager,
            window_display_title="Sim Debug Heightmap",
            resizable=True,
        )
        hm_container = self.heightmap_window.get_container()
        self.heightmap_image = UIImage(
            relative_rect=pygame.Rect(self._s(10), self._s(10), self._s(256), self._s(256)),
            image_surface=heightmap_debug_surface(self._pygame, None, size=self._s(256)),
            manager=self.manager,
            container=hm_container,
        )
        self.heightmap_status = UILabel(
            relative_rect=pygame.Rect(self._s(10), self._s(276), self._s(290), self._s(24)),
            text="heightmap: none",
            manager=self.manager,
            container=hm_container,
        )
        self.heightmap_window.hide()

    # ------------------------------------------------------------------ pump

    def process_event(self, event: pygame.event.Event) -> bool:
        import pygame_gui

        self.manager.process_events(event)
        if event.type == pygame_gui.UI_TEXT_ENTRY_FINISHED and event.ui_element is self.chat_input:
            text = event.text.strip()
            self.chat_input.set_text("")
            if text:
                self.on_chat_submit(text)
            return True
        elif event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element is self.file_button:
                self._toggle_menu("file")
                return True
            if event.ui_element is self.view_button:
                self._toggle_menu("view")
                return True
            if event.ui_element is self.debug_button:
                self._toggle_menu("debug")
                return True
            if event.ui_element is self.tools_button:
                self._toggle_menu("tools")
                return True
            if event.ui_element is self.help_button:
                self._toggle_menu("help")
                return True
            if event.ui_element is self.file_quit_button:
                self.quit_requested = True
                return True
            if event.ui_element is self.show_chat_button:
                self.chat_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.inventory_button:
                self.inventory_window.show()
                self._layout_inventory_window()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.inventory_open_button:
                self._open_selected_inventory_folder()
                return True
            if event.ui_element is self.render_settings_button:
                self.render_settings_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.render_mode_2d_button:
                self._set_render_mode(RENDER_MODE_2D)
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.render_mode_3d_button:
                self._set_render_mode(RENDER_MODE_3D)
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.zoom_in_button and self.on_zoom_in is not None:
                self.on_zoom_in()
                return True
            elif event.ui_element is self.zoom_out_button and self.on_zoom_out is not None:
                self.on_zoom_out()
                return True
            elif event.ui_element is self.center_button and self.on_center is not None:
                self.on_center()
                return True
            if event.ui_element is self.render_terrain_button:
                self._toggle_render_bool("render_terrain")
                return True
            if event.ui_element is self.render_terrain_lines_button:
                self._toggle_render_bool("render_terrain_lines")
                return True
            if event.ui_element is self.render_water_button:
                self._toggle_render_bool("render_water")
                return True
            if event.ui_element is self.render_objects_button:
                self._toggle_render_bool("render_objects")
                return True
            if event.ui_element is self.diagnostics_button:
                if self.diagnostics_window.visible:
                    self.diagnostics_window.hide()
                else:
                    self.diagnostics_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.heightmap_button:
                if self.heightmap_window.visible:
                    self.heightmap_window.hide()
                else:
                    self.heightmap_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.teleport_button:
                self.teleport_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.options_button:
                self.options_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.movement_help_button:
                self.help_window.show()
                self._hide_all_menus()
                self._open_menu = None
                return True
            if event.ui_element is self.teleport_go_button:
                self._submit_teleport()
                return True
        elif event.type == pygame_gui.UI_HORIZONTAL_SLIDER_MOVED:
            if event.ui_element is self.water_alpha_slider:
                value = max(0.1, min(1.0, float(event.value) / 100.0))
                self._set_render_setting("water_alpha", value)
                return True
        elif event.type == pygame_gui.UI_SELECTION_LIST_NEW_SELECTION:
            if event.ui_element is self.inventory_list:
                selection = str(getattr(event, "text", "") or "")
                self._select_inventory_row(selection)
                return True
        elif event.type == pygame_gui.UI_SELECTION_LIST_DOUBLE_CLICKED_SELECTION:
            if event.ui_element is self.inventory_list:
                selection = str(getattr(event, "text", "") or "")
                self._select_inventory_row(selection)
                self._open_selected_inventory_folder()
                return True
        elif event.type == pygame_gui.UI_WINDOW_RESIZED and event.ui_element is self.chat_window:
            self._layout_chat_window()
            return True
        elif (
            event.type == pygame_gui.UI_WINDOW_RESIZED
            and event.ui_element is self.inventory_window
        ):
            self._layout_inventory_window()
            return True
        return False

    def update(self, time_delta_s: float, scene: Scene | None = None) -> None:
        if time_delta_s > 0.0:
            instant_fps = 1.0 / time_delta_s
            self._last_fps = (
                instant_fps
                if self._last_fps <= 0.0
                else self._last_fps * 0.85 + instant_fps * 0.15
            )
        if scene is not None:
            self._refresh_ticker(scene)
            self._refresh_status(scene)
            self._refresh_diagnostics(scene)
            self._refresh_heightmap(scene)
            self._refresh_render_settings_from_scene(scene)
        self._layout_chat_window_if_needed()
        self.manager.update(time_delta_s)

    def draw(self, surface: pygame.Surface) -> None:
        self.manager.draw_ui(surface)

    def focus_chat(self) -> None:
        self.chat_window.show()
        self.chat_input.focus()

    def is_text_entry_focused(self) -> bool:
        entries = (self.chat_input, self.teleport_x, self.teleport_y, self.teleport_z)
        return any(bool(getattr(entry, "is_focused", False)) for entry in entries)

    def resize(self, screen_size: tuple[int, int]) -> None:
        # Tear down + rebuild on resize. pygame_gui's set_window_resolution exists
        # but element rects are absolute, so a rebuild is the safest way.
        self.screen_size = screen_size
        self.manager.set_window_resolution(screen_size)
        for element in (
            self.menu_bar,
            self.status_bar,
            self.file_menu,
            self.view_menu,
            self.debug_menu,
            self.diagnostics_window,
            self.heightmap_window,
            self.render_settings_window,
            self.chat_window,
            self.ticker,
            self.chat_input,
            self.file_button,
            self.view_button,
            self.debug_button,
            self.tools_button,
            self.help_button,
            self.title_label,
            self.status_left,
            self.status_right,
            self.file_quit_button,
            self.show_chat_button,
            self.inventory_button,
            self.render_settings_button,
            self.render_mode_2d_button,
            self.render_mode_3d_button,
            self.zoom_in_button,
            self.zoom_out_button,
            self.center_button,
            self.diagnostics_button,
            self.heightmap_button,
            self.teleport_button,
            self.options_button,
            self.movement_help_button,
            self.help_window,
            self.help_text_box,
            self.teleport_window,
            self.teleport_label,
            self.teleport_x,
            self.teleport_y,
            self.teleport_z,
            self.teleport_go_button,
            self.teleport_status,
            self.options_window,
            self.options_text,
            self.render_terrain_button,
            self.render_terrain_lines_button,
            self.render_water_button,
            self.render_objects_button,
            self.water_alpha_label,
            self.water_alpha_slider,
            self.inventory_window,
            self.inventory_summary,
            self.inventory_open_button,
            self.inventory_list,
            self.inventory_details,
            self.diagnostics_text,
            self.heightmap_image,
            self.heightmap_status,
        ):
            element.kill()
        self._last_chat_container_size = None
        self._last_inventory_signature = None
        self._inventory_row_details = {}
        self._inventory_openable_rows = {}
        self._selected_inventory_row = None
        self._last_diagnostics_html = None
        self._last_heightmap_signature = None
        self._build_widgets()

    # ----------------------------------------------------------- refresh

    def _layout_chat_window_if_needed(self) -> None:
        container_size = tuple(self.chat_window.get_container().get_size())
        if container_size != self._last_chat_container_size:
            self._layout_chat_window()

    def _layout_chat_window(self) -> None:
        container = self.chat_window.get_container()
        cw, ch = container.get_size()
        margin = self._s(8)
        input_h = self._s(28)
        gap = self._s(6)
        content_w = max(1, cw - margin * 2)
        ticker_h = max(1, ch - margin * 2 - input_h - gap)
        self.ticker.set_relative_position((margin, margin))
        self.ticker.set_dimensions((content_w, ticker_h))
        self.chat_input.set_relative_position((margin, margin + ticker_h + gap))
        self.chat_input.set_dimensions((content_w, input_h))
        self._last_chat_container_size = (cw, ch)

    def _layout_inventory_window(self) -> None:
        container = self.inventory_window.get_container()
        cw, ch = container.get_size()
        margin = self._s(8)
        gap = self._s(10)
        summary_h = self._s(24)
        button_h = self._s(28)
        button_gap = self._s(8)
        list_y = margin + summary_h + button_gap + button_h + self._s(8)
        details_y = margin + summary_h + self._s(8)
        content_w = max(self._s(160), cw - margin * 2)
        left_w = max(self._s(180), min(self._s(340), (content_w - gap) // 2))
        right_w = max(self._s(120), content_w - left_w - gap)
        self.inventory_summary.set_relative_position((margin, margin))
        self.inventory_summary.set_dimensions((content_w, summary_h))
        self.inventory_open_button.set_relative_position(
            (margin, margin + summary_h + button_gap)
        )
        self.inventory_open_button.set_dimensions((min(left_w, self._s(110)), button_h))
        self.inventory_list.set_relative_position((margin, list_y))
        self.inventory_list.set_dimensions((left_w, max(self._s(80), ch - list_y - margin)))
        self.inventory_details.set_relative_position((margin + left_w + gap, details_y))
        self.inventory_details.set_dimensions((right_w, max(self._s(80), ch - details_y - margin)))

    def _refresh_ticker(self, scene: Scene) -> None:
        # Take last N chat lines, format with kind-colored prefix.
        lines: deque = scene.chat_lines
        recent = list(lines)[-CHAT_TICKER_LINES:]
        rows = []
        for line in recent:
            color = _kind_color_html(line.kind)
            sender = _html_escape(line.sender)
            message = _html_escape(line.message)
            rows.append(f"<font color='{color}'>{sender}</font>: {message}")
        html = "<br>".join(rows) if rows else "<i>no chat yet</i>"
        try:
            self.ticker.set_text(html)
        except Exception:  # pragma: no cover  - pygame_gui internal hiccups don't crash the viewer
            pass

    def _refresh_status(self, scene: Scene) -> None:
        sim = scene.region_name
        parcel = scene.parcel_name or "unknown"
        if scene.avatar_position is not None:
            x, y, z = scene.avatar_position
            pos = f"{x:.1f}, {y:.1f}, {z:.1f}"
        else:
            pos = "unknown"
        if sim:
            left = f"Pos: {pos} | Sim: {sim} | Parcel: {parcel}"
        elif scene.region_handle is not None:
            left = f"Pos: {pos} | Sim: 0x{scene.region_handle:016x} | Parcel: {parcel}"
        else:
            left = f"Pos: {pos} | Sim: (none) | Parcel: {parcel}"
        objects = len(scene.object_entities)
        avatars = len(scene.avatar_entities)
        chat = len(scene.chat_lines)
        tile = "map=ready" if scene.map_tile_path is not None else "map=pending"
        mode_label = RENDER_MODE_LABELS.get(self.render_mode, self.render_mode)
        self.status_left.set_text(left)
        self.status_right.set_text(
            f"mode={mode_label} {tile} objects={objects} avatars={avatars} chat={chat}"
        )
        self._refresh_inventory(scene)

    def _refresh_diagnostics(self, scene: Scene) -> None:
        mode_label = RENDER_MODE_LABELS.get(self.render_mode, self.render_mode)
        objects = len(scene.object_entities)
        avatars = len(scene.avatar_entities)
        textures = {
            entity.default_texture_id
            for entity in (*scene.object_entities.values(), *scene.avatar_entities.values())
            if entity.default_texture_id is not None
        }
        map_path = str(scene.map_tile_path) if scene.map_tile_path is not None else "(none)"

        terrain = scene.terrain_heightmap
        if terrain is None:
            terrain_text = "terrain: none"
            height_text = "height: n/a"
            patch_text = "patches: n/a"
            sample_text = "samples: n/a"
            layer_text = "layer: n/a"
            coeff_text = "coeff: n/a"
        else:
            source = scene.debug_terrain_source or "live"
            sample_min = terrain.sample_min if terrain.sample_min is not None else 0.0
            sample_max = terrain.sample_max if terrain.sample_max is not None else 0.0
            sample_mean = terrain.sample_mean if terrain.sample_mean is not None else 0.0
            terrain_text = (
                f"terrain: {source} {terrain.width}x{terrain.height} "
                f"patches={terrain.patch_count} rev={terrain.revision} "
                f"zscale={scene.terrain_z_scale:.2f}"
            )
            height_text = (
                f"height: min={sample_min:.2f} max={sample_max:.2f} "
                f"mean={sample_mean:.2f}"
            )
            patch_text = f"patch keys: {terrain.first_patch_keys or '()'}"
            sample_text = f"samples[0:4]: {[round(value, 2) for value in terrain.samples[:4]]}"
            stats = terrain.latest_layer_stats
            if stats is None:
                layer_text = "layer: n/a"
                coeff_text = "coeff: n/a"
            else:
                h_min = stats.height_min if stats.height_min is not None else 0.0
                h_max = stats.height_max if stats.height_max is not None else 0.0
                h_mean = stats.height_mean if stats.height_mean is not None else 0.0
                layer_text = (
                    f"layer: patches={stats.patch_count} pos={stats.positions} "
                    f"range={stats.ranges} dc={stats.dc_offsets} preq={stats.prequants}"
                )
                coeff_text = (
                    f"coeff: nz={stats.nonzero_coefficients} "
                    f"absmax={stats.coefficient_abs_max} "
                    f"h=min {h_min:.2f} max {h_max:.2f} mean {h_mean:.2f}"
                )

        if scene.avatar_position is None:
            water_text = f"water: level={scene.water_height:.1f} avatar=n/a"
        else:
            z = scene.avatar_position[2]
            relation = "under" if z < scene.water_height else "above"
            water_text = f"water: level={scene.water_height:.1f} avatar_z={z:.1f} {relation}"

        html = "<br>".join(
            _html_escape(line)
            for line in (
                f"fps: {self._last_fps:.1f}",
                f"mode: {mode_label}",
                f"region: {scene.region_name or scene.region_handle or '(none)'}",
                f"map: {map_path}",
                terrain_text,
                height_text,
                patch_text,
                sample_text,
                layer_text,
                coeff_text,
                water_text,
                f"objects: {objects}",
                f"avatars: {avatars}",
                f"textures: {len(textures)}",
                f"chat: {len(scene.chat_lines)}",
            )
        )
        if html == self._last_diagnostics_html:
            return
        self._last_diagnostics_html = html
        try:
            self.diagnostics_text.set_text(html)
        except Exception:  # pragma: no cover
            pass

    def _refresh_heightmap(self, scene: Scene) -> None:
        terrain = scene.terrain_heightmap
        if terrain is None:
            signature = None
            status = "heightmap: none"
        else:
            signature = (
                terrain.width,
                terrain.height,
                terrain.revision,
                terrain.sample_min,
                terrain.sample_max,
            )
            source = scene.debug_terrain_source or "live"
            sample_min = terrain.sample_min if terrain.sample_min is not None else 0.0
            sample_max = terrain.sample_max if terrain.sample_max is not None else 0.0
            status = (
                f"{source} {terrain.width}x{terrain.height} patches={terrain.patch_count} "
                f"min={sample_min:.2f} max={sample_max:.2f}"
            )

        if signature != self._last_heightmap_signature:
            container_width = self.heightmap_window.get_container().get_size()[0]
            size = max(self._s(64), min(self._s(256), container_width - self._s(20)))
            surface = heightmap_debug_surface(self._pygame, terrain, size=size)
            try:
                self.heightmap_image.set_image(surface)
                self.heightmap_image.set_dimensions((size, size))
            except Exception:  # pragma: no cover
                pass
            self._last_heightmap_signature = signature
        try:
            self.heightmap_status.set_text(status)
        except Exception:  # pragma: no cover
            pass

    def _refresh_render_settings_from_scene(self, scene: Scene) -> None:
        values: dict[str, object] = {
            "render_terrain": bool(scene.render_terrain),
            "render_terrain_lines": bool(scene.render_terrain_lines),
            "render_water": bool(scene.render_water),
            "render_objects": bool(scene.render_objects),
            "water_alpha": max(0.1, min(1.0, float(scene.water_alpha))),
        }
        if values == self._render_setting_values:
            return
        self._render_setting_values = values
        self._refresh_render_settings_controls()

    def _refresh_inventory(self, scene: Scene) -> None:
        rows = inventory_snapshot_rows(scene.inventory_snapshot)
        row_texts = tuple(row.text for row in rows)
        signature = tuple((row.text, row.detail_html, row.folder_id, row.can_open) for row in rows)
        if signature == self._last_inventory_signature:
            return
        self._last_inventory_signature = signature
        self._inventory_row_details = {row.text: row.detail_html for row in rows}
        self._inventory_openable_rows = {
            row.text: row.folder_id
            for row in rows
            if row.can_open and row.folder_id is not None
        }
        if self._selected_inventory_row not in self._inventory_row_details:
            self._selected_inventory_row = row_texts[0] if row_texts else None
        if scene.inventory_snapshot is None:
            folder_count = 0
            item_count = 0
        else:
            folder_count = getattr(scene.inventory_snapshot, "folder_count", 0)
            item_count = getattr(scene.inventory_snapshot, "total_item_count", 0)
        summary = (
            "Inventory has not loaded yet."
            if scene.inventory_snapshot is None
            else f"Folders: {folder_count} | Items: {item_count} | Rows: {len(row_texts)}"
        )
        try:
            self.inventory_summary.set_text(summary)
            self.inventory_list.set_item_list(list(row_texts))
            self._select_inventory_row(self._selected_inventory_row)
        except Exception:  # pragma: no cover
            pass

    def _select_inventory_row(self, selection: str | None) -> None:
        self._selected_inventory_row = selection if selection else None
        can_open = selection in self._inventory_openable_rows if selection else False
        detail = (
            self._inventory_row_details.get(selection, "Select an inventory row.")
            if selection
            else "Select an inventory row."
        )
        try:
            if can_open:
                self.inventory_open_button.enable()
            else:
                self.inventory_open_button.disable()
            self.inventory_open_button.set_text("Open")
            self.inventory_details.set_text(detail)
        except Exception:  # pragma: no cover
            pass

    def _open_selected_inventory_folder(self) -> None:
        if self.on_inventory_open_folder is None or self._selected_inventory_row is None:
            return
        folder_id = self._inventory_openable_rows.get(self._selected_inventory_row)
        if folder_id is None:
            return
        self.on_inventory_open_folder(folder_id)

    def _submit_teleport(self) -> None:
        try:
            position = (
                float(self.teleport_x.get_text()),
                float(self.teleport_y.get_text()),
                float(self.teleport_z.get_text()),
            )
        except ValueError:
            self.teleport_status.set_text("Enter numeric X, Y, and Z.")
            return
        if self.on_teleport is None:
            self.teleport_status.set_text("Teleport is not wired.")
            return
        self.on_teleport(position)
        self.teleport_status.set_text("Teleport requested.")


def _kind_color_html(kind: str) -> str:
    return {
        "local": "#dddddd",
        "im": "#a0d0ff",
        "alert": "#ffa080",
        "outbound": "#80ffa0",
    }.get(kind, "#aaaaaa")


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _plain_text_to_html(value: str) -> str:
    return "<br>".join(_html_escape(line) for line in value.strip().splitlines())


def heightmap_debug_surface(
    pygame_module,
    terrain: object | None,
    *,
    size: int = 256,
):
    """Return a square grayscale preview of the decoded region height samples."""

    size = max(1, int(size))
    if terrain is None:
        return pygame_module.Surface((size, size))

    width = int(getattr(terrain, "width", 0))
    height = int(getattr(terrain, "height", 0))
    samples = list(getattr(terrain, "samples", ()))
    if width <= 0 or height <= 0 or len(samples) < width * height:
        return pygame_module.Surface((size, size))

    sample_min = getattr(terrain, "sample_min", None)
    sample_max = getattr(terrain, "sample_max", None)
    if sample_min is None:
        sample_min = min(samples[: width * height])
    if sample_max is None:
        sample_max = max(samples[: width * height])
    value_range = float(sample_max) - float(sample_min)

    source = pygame_module.Surface((width, height))
    pixels = pygame_module.PixelArray(source)
    try:
        for y in range(height):
            row_start = y * width
            for x in range(width):
                if value_range <= 0.0:
                    gray = 128
                else:
                    normalized = (float(samples[row_start + x]) - float(sample_min)) / value_range
                    gray = max(0, min(255, int(round(normalized * 255.0))))
                pixels[x, y] = (gray << 16) | (gray << 8) | gray
    finally:
        del pixels

    if width == size and height == size:
        return source
    return pygame_module.transform.scale(source, (size, size))


def inventory_snapshot_rows(snapshot: object | None) -> tuple[InventoryDisplayRow, ...]:
    if snapshot is None:
        return ()
    folders = getattr(snapshot, "folders", ())
    rows: list[InventoryDisplayRow] = []
    loaded_folder_ids = {
        folder_id
        for folder_id in (getattr(folder, "folder_id", None) for folder in folders)
        if folder_id is not None
    }
    root_id = getattr(snapshot, "inventory_root_folder_id", None)
    cof_id = getattr(snapshot, "current_outfit_folder_id", None)
    for folder in folders:
        folder_id = getattr(folder, "folder_id", None)
        if folder_id == root_id:
            folder_name = "Inventory Root"
        elif folder_id == cof_id:
            folder_name = "Current Outfit"
        else:
            folder_name = _folder_display_name(folder)
        badges = []
        if folder_id == root_id:
            badges.append("root")
        if folder_id == cof_id:
            badges.append("current outfit")
        badge_text = f" [{', '.join(badges)}]" if badges else ""
        rows.append(
            InventoryDisplayRow(
                text=f"▾ ◼ {folder_name}{badge_text}",
                detail_html=_folder_detail_html(folder, folder_name),
            )
        )
        for category in getattr(folder, "categories", ()):
            child_name = getattr(category, "name", "") or "(unnamed folder)"
            category_id = getattr(category, "category_id", None)
            loaded = category_id in loaded_folder_ids
            marker = "▾ ◼" if loaded else "▸ ◻"
            status = "" if loaded else " (not loaded)"
            rows.append(
                InventoryDisplayRow(
                    text=f"   {marker} {child_name}{status}",
                    detail_html=_category_detail_html(category, loaded=loaded),
                    folder_id=category_id,
                    can_open=not loaded and category_id is not None,
                )
            )
        for item in getattr(folder, "items", ()):
            name = getattr(item, "name", "") or "(unnamed item)"
            marker = "↗" if bool(getattr(item, "is_link", False)) else "•"
            rows.append(
                InventoryDisplayRow(
                    text=f"      {marker} {name}",
                    detail_html=_item_detail_html(item),
                )
            )
    resolved = getattr(snapshot, "resolved_items", ())
    if resolved:
        rows.append(
            InventoryDisplayRow(
                text="▾ ↗ Resolved current outfit links",
                detail_html=(
                    "<b>Resolved current outfit links</b><br>"
                    f"Items: {len(resolved)}<br><br>"
                    "These are source items resolved through FetchInventory2."
                ),
            )
        )
        for item in resolved:
            name = getattr(item, "name", "") or "(unnamed item)"
            rows.append(
                InventoryDisplayRow(
                    text=f"      ↗ {name}",
                    detail_html=_item_detail_html(item),
                )
            )
    return tuple(rows)


def _inventory_snapshot_html(snapshot: object | None) -> str:
    if snapshot is None:
        return "Inventory has not loaded yet."
    rows = inventory_snapshot_rows(snapshot)
    if not rows:
        return "Inventory snapshot is empty."
    return "<br>".join(_html_escape(row.text) for row in rows)


def _folder_detail_html(folder: object, folder_name: str) -> str:
    folder_id = getattr(folder, "folder_id", None)
    owner_id = getattr(folder, "owner_id", None)
    agent_id = getattr(folder, "agent_id", None)
    descendents = getattr(folder, "descendents", None)
    version = getattr(folder, "version", None)
    categories = getattr(folder, "categories", ())
    items = getattr(folder, "items", ())
    return "<br>".join(
        (
            f"<b>{_html_escape(folder_name)}</b>",
            "Kind: folder",
            f"Folder ID: {_html_escape(str(folder_id)) if folder_id is not None else '(none)'}",
            f"Owner ID: {_html_escape(str(owner_id)) if owner_id is not None else '(none)'}",
            f"Agent ID: {_html_escape(str(agent_id)) if agent_id is not None else '(none)'}",
            f"Descendents: {descendents if descendents is not None else 'unknown'}",
            f"Version: {version if version is not None else 'unknown'}",
            f"Child folders in snapshot: {len(categories)}",
            f"Items in snapshot: {len(items)}",
        )
    )


def _category_detail_html(category: object, *, loaded: bool) -> str:
    name = getattr(category, "name", "") or "(unnamed folder)"
    category_id = getattr(category, "category_id", None)
    parent_id = getattr(category, "parent_id", None)
    type_default = getattr(category, "type_default", None)
    version = getattr(category, "version", None)
    status = "loaded in current snapshot" if loaded else "listed, contents not fetched yet"
    return "<br>".join(
        (
            f"<b>{_html_escape(name)}</b>",
            "Kind: folder",
            f"Status: {status}",
            "Category ID: "
            f"{_html_escape(str(category_id)) if category_id is not None else '(none)'}",
            f"Parent ID: {_html_escape(str(parent_id)) if parent_id is not None else '(none)'}",
            f"Default type: {type_default if type_default is not None else 'unknown'}",
            f"Version: {version if version is not None else 'unknown'}",
        )
    )


def _item_detail_html(item: object) -> str:
    name = getattr(item, "name", "") or "(unnamed item)"
    description = getattr(item, "description", "") or ""
    item_id = getattr(item, "item_id", None)
    asset_id = getattr(item, "asset_id", None)
    parent_id = getattr(item, "parent_id", None)
    type_value = getattr(item, "type", None)
    inv_type = getattr(item, "inv_type", None)
    flags = getattr(item, "flags", None)
    is_link = bool(getattr(item, "is_link", False))
    return "<br>".join(
        (
            f"<b>{_html_escape(name)}</b>",
            "Kind: item",
            f"Description: {_html_escape(description) if description else '(none)'}",
            f"Item ID: {_html_escape(str(item_id)) if item_id is not None else '(none)'}",
            f"Asset ID: {_html_escape(str(asset_id)) if asset_id is not None else '(none)'}",
            f"Parent ID: {_html_escape(str(parent_id)) if parent_id is not None else '(none)'}",
            f"Type: {type_value if type_value is not None else 'unknown'}",
            f"Inventory type: {inv_type if inv_type is not None else 'unknown'}",
            f"Flags: {flags if flags is not None else 'unknown'}",
            f"Link: {'yes' if is_link else 'no'}",
        )
    )


def _folder_display_name(folder: object) -> str:
    for category in getattr(folder, "categories", ()):
        name = getattr(category, "name", "")
        if name:
            return str(name)
    folder_id = getattr(folder, "folder_id", None)
    return str(folder_id) if folder_id is not None else "Folder"


__all__ = [
    "HUD",
    "CHAT_TICKER_LINES",
    "DEFAULT_HELP_TEXT",
    "InventoryDisplayRow",
    "heightmap_debug_surface",
    "inventory_snapshot_rows",
]
