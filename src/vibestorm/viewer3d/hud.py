"""Viewer UI shell built on pygame_gui.

The HUD owns its own UIManager; the app forwards pygame events to
``hud.process_event`` and ``hud.update(dt)`` once per frame, then
``hud.draw(surface)`` last so it renders on top of the world.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.scene import Scene


CHAT_TICKER_LINES = 8
BASE_MENU_HEIGHT = 30
BASE_STATUS_HEIGHT = 24
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
        self.help_text = help_text
        self._open_menu: str | None = None
        self._last_chat_container_size: tuple[int, int] | None = None
        self._last_inventory_text: str | None = None
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
            width=self._s(170),
            rows=(
                ("Show Chat", "show_chat_button"),
                ("Inventory", "inventory_button"),
            ),
        )
        self.debug_menu = self._build_menu_panel(
            x=self._s(122),
            y=menu_h,
            width=self._s(170),
            rows=(
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

    def _build_aux_windows(self, sw: int, sh: int) -> None:
        import pygame
        from pygame_gui.elements import UIButton, UILabel, UITextBox, UITextEntryLine, UIWindow

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

        self.inventory_window = UIWindow(
            rect=pygame.Rect(
                max(self._s(120), sw - self._s(520)),
                self._s(110),
                self._s(460),
                self._s(390),
            ),
            manager=self.manager,
            window_display_title="Inventory",
            resizable=True,
        )
        self.inventory_text = UITextBox(
            html_text="Inventory has not loaded yet.",
            relative_rect=pygame.Rect(self._s(8), self._s(8), self._s(420), self._s(320)),
            manager=self.manager,
            container=self.inventory_window.get_container(),
        )
        self.inventory_window.hide()

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
        elif event.type == pygame_gui.UI_WINDOW_RESIZED and event.ui_element is self.chat_window:
            self._layout_chat_window()
            return True
        return False

    def update(self, time_delta_s: float, scene: Scene | None = None) -> None:
        if scene is not None:
            self._refresh_ticker(scene)
            self._refresh_status(scene)
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
            self.zoom_in_button,
            self.zoom_out_button,
            self.center_button,
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
            self.inventory_window,
            self.inventory_text,
        ):
            element.kill()
        self._last_chat_container_size = None
        self._last_inventory_text = None
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
        objects = len(scene.object_markers)
        avatars = len(scene.avatar_markers)
        chat = len(scene.chat_lines)
        tile = "map=ready" if scene.map_tile_path is not None else "map=pending"
        self.status_left.set_text(left)
        self.status_right.set_text(f"{tile} objects={objects} avatars={avatars} chat={chat}")
        self._refresh_inventory(scene)

    def _refresh_inventory(self, scene: Scene) -> None:
        html = _inventory_snapshot_html(scene.inventory_snapshot)
        if html == self._last_inventory_text:
            return
        self._last_inventory_text = html
        try:
            self.inventory_text.set_text(html)
        except Exception:  # pragma: no cover
            pass

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


def _inventory_snapshot_html(snapshot: object | None) -> str:
    if snapshot is None:
        return "Inventory has not loaded yet."
    folders = getattr(snapshot, "folders", ())
    folder_count = getattr(snapshot, "folder_count", len(folders))
    total_item_count = getattr(snapshot, "total_item_count", 0)
    rows = [
        f"<b>Folders:</b> {folder_count}",
        f"<b>Items:</b> {total_item_count}",
    ]
    for folder in list(folders)[:12]:
        folder_name = _folder_display_name(folder)
        item_count = getattr(folder, "item_count", len(getattr(folder, "items", ())))
        rows.append(f"<br><b>{_html_escape(folder_name)}</b> ({item_count} items)")
        for item in list(getattr(folder, "items", ()))[:8]:
            name = _html_escape(getattr(item, "name", "") or "(unnamed)")
            asset_id = getattr(item, "asset_id", None)
            inv_type = getattr(item, "inv_type", None)
            suffix = f" inv={inv_type}" if inv_type is not None else ""
            if asset_id is not None:
                suffix += f" asset={asset_id}"
            rows.append(f"&nbsp;&nbsp;{name}{_html_escape(suffix)}")
    resolved = getattr(snapshot, "resolved_items", ())
    if resolved:
        rows.append("<br><b>Resolved current outfit links</b>")
        for item in list(resolved)[:8]:
            name = _html_escape(getattr(item, "name", "") or "(unnamed)")
            rows.append(f"&nbsp;&nbsp;{name}")
    return "<br>".join(rows)


def _folder_display_name(folder: object) -> str:
    for category in getattr(folder, "categories", ()):
        name = getattr(category, "name", "")
        if name:
            return str(name)
    folder_id = getattr(folder, "folder_id", None)
    return str(folder_id) if folder_id is not None else "Folder"


__all__ = ["HUD", "CHAT_TICKER_LINES", "DEFAULT_HELP_TEXT"]
