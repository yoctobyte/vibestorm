"""Premium in-game Pygame login screen with rich aesthetics."""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from pathlib import Path
from typing import TYPE_CHECKING

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UICheckBox, UIDropDownMenu, UILabel, UITextEntryLine

from vibestorm import __version__
from vibestorm.login.client import LoginClient, LoginError
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.util import credentials

if TYPE_CHECKING:
    from vibestorm.login.models import LoginBootstrap


class BackgroundParticle:
    """Subtle animated background particle for rich aesthetics."""

    def __init__(self, sw: int, sh: int):
        self.x = random.uniform(0, sw)
        self.y = random.uniform(0, sh)
        self.vx = random.uniform(-12, 12)  # slow, elegant drift
        self.vy = random.uniform(-12, 12)
        self.radius = random.uniform(4, 10)
        # Deep glowing soft blue/purple palette
        self.color = (
            random.randint(120, 180),
            random.randint(120, 180),
            random.randint(230, 255),
            random.randint(30, 70),  # alpha
        )

    def update(self, dt: float, sw: int, sh: int):
        self.x += self.vx * dt
        self.y += self.vy * dt
        # elegant screen bounds bounce
        if self.x < 0 or self.x > sw:
            self.vx = -self.vx
            self.x = max(0.0, min(self.x, float(sw)))
        if self.y < 0 or self.y > sh:
            self.vy = -self.vy
            self.y = max(0.0, min(self.y, float(sh)))

    def draw(self, surface: pygame.Surface):
        """Draw a glowing particle using a temporary surface with alpha blending."""
        r = int(self.radius * 2.5)
        glow_surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        # Simple radial gradient glow
        for dist in range(r, 0, -1):
            alpha = int(self.color[3] * (1.0 - (dist / r) ** 1.5))
            pygame.draw.circle(glow_surf, (*self.color[:3], alpha), (r, r), dist)
        surface.blit(glow_surf, (int(self.x - r), int(self.y - r)))


class LoginScreen:
    """Highly aesthetic in-game Pygame login interface."""

    def __init__(
        self,
        screen_size: tuple[int, int],
        ui_scale: float = 1.0,
        theme_path: Path | None = None,
        args: argparse.Namespace | None = None,
    ):
        self.screen_size = screen_size
        self.ui_scale = ui_scale

        manager_kwargs = {}
        if theme_path is not None:
            manager_kwargs["theme_path"] = str(theme_path)
        self.manager = pygame_gui.UIManager(screen_size, **manager_kwargs)

        # Profile state & helper
        self.profile_path = credentials.get_profile_path()
        self.profile_data = credentials.load_profile(self.profile_path)

        # Merge CLI arguments if supplied
        if args:
            if getattr(args, "first", None):
                self.profile_data["VIBESTORM_FIRST_NAME"] = args.first
            if getattr(args, "last", None):
                self.profile_data["VIBESTORM_LAST_NAME"] = args.last
            if getattr(args, "password", None):
                self.profile_data["VIBESTORM_PASSWORD"] = args.password
            if getattr(args, "login_uri", None):
                self.profile_data["VIBESTORM_LOGIN_URI"] = args.login_uri
            if getattr(args, "start", None):
                self.profile_data["VIBESTORM_START_LOCATION"] = args.start

        # UI state
        self.connecting = False
        self.login_task: asyncio.Task[LoginBootstrap] | None = None
        self.bootstrap: LoginBootstrap | None = None
        self.quit_requested = False

        # Visuals
        self.particles = [BackgroundParticle(screen_size[0], screen_size[1]) for _ in range(16)]

        self._build_ui()
        self._apply_preset_defaults()

        # Trigger auto-login if the credentials are complete and an event loop is running
        if (
            self.profile_data.get("VIBESTORM_FIRST_NAME")
            and self.profile_data.get("VIBESTORM_LAST_NAME")
            and self.profile_data.get("VIBESTORM_PASSWORD")
            and self.profile_data.get("VIBESTORM_LOGIN_URI")
        ):
            try:
                asyncio.get_running_loop()
                self._start_login()
            except RuntimeError:
                pass

    def _s(self, val: float) -> int:
        """Scale values based on UI scale."""
        return max(1, int(round(val * self.ui_scale)))

    def _build_ui(self) -> None:
        sw, sh = self.screen_size
        pwidth = self._s(460)
        pheight = self._s(490)
        px = (sw - pwidth) // 2
        py = (sh - pheight) // 2

        # A title label at the top of the container
        self.title_label = UILabel(
            relative_rect=pygame.Rect(px, py + self._s(15), pwidth, self._s(40)),
            text="VIBESTORM CLIENT",
            manager=self.manager,
        )

        label_w = self._s(120)
        field_w = self._s(280)
        row_h = self._s(32)
        spacing = self._s(10)

        # Row 1: Grid Selector Dropdown
        y_cursor = py + self._s(65)
        UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="Grid Preset:",
            manager=self.manager,
        )
        self.preset_dropdown = UIDropDownMenu(
            options_list=["Local OpenSim", "OSgrid", "Second Life", "Custom"],
            starting_option=self._get_starting_preset_name(),
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )

        # Row 2: Custom URI (only active when preset is Custom)
        y_cursor += row_h + spacing
        self.uri_label = UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="Login URI:",
            manager=self.manager,
        )
        self.uri_entry = UITextEntryLine(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )

        # Row 3: First Name
        y_cursor += row_h + spacing
        UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="First Name:",
            manager=self.manager,
        )
        self.first_entry = UITextEntryLine(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )

        # Row 4: Last Name
        y_cursor += row_h + spacing
        UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="Last Name:",
            manager=self.manager,
        )
        self.last_entry = UITextEntryLine(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )

        # Row 5: Password
        y_cursor += row_h + spacing
        UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="Password:",
            manager=self.manager,
        )
        self.password_entry = UITextEntryLine(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )
        self.password_entry.set_text_hidden(True)

        # Row 6: Start Location
        y_cursor += row_h + spacing
        UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, label_w, row_h),
            text="Start Location:",
            manager=self.manager,
        )
        self.start_entry = UITextEntryLine(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            manager=self.manager,
        )

        # Row 7: Remember Credentials Checkbox
        y_cursor += row_h + spacing
        self.remember_checkbox = UICheckBox(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, field_w, row_h),
            text="Remember Credentials",
            manager=self.manager,
        )
        # default to checked if we already have saved credentials
        if self.profile_data:
            self.remember_checkbox.set_state(True)

        # Row 8: Action Buttons (Login, Quit, Cancel)
        y_cursor += row_h + spacing + self._s(5)
        self.login_button = UIButton(
            relative_rect=pygame.Rect(px + self._s(150), y_cursor, self._s(135), row_h),
            text="Connect",
            manager=self.manager,
        )
        self.quit_button = UIButton(
            relative_rect=pygame.Rect(px + self._s(295), y_cursor, self._s(135), row_h),
            text="Quit",
            manager=self.manager,
        )

        # Row 9: Status / Error Label
        y_cursor += row_h + spacing
        self.status_label = UILabel(
            relative_rect=pygame.Rect(px + self._s(20), y_cursor, pwidth - self._s(40), row_h),
            text="",
            manager=self.manager,
        )

    def _get_starting_preset_name(self) -> str:
        uri = self.profile_data.get("VIBESTORM_LOGIN_URI", "").lower()
        if "login.osgrid.org" in uri:
            return "OSgrid"
        elif "login.agni.lindenlab.com" in uri:
            return "Second Life"
        elif "127.0.0.1" in uri or "localhost" in uri:
            return "Local OpenSim"
        elif uri:
            return "Custom"
        return "Local OpenSim"

    def _apply_preset_defaults(self) -> None:
        """Populate input fields using existing profile data or defaults."""
        preset = self.preset_dropdown.selected_option
        preset_uri = "http://127.0.0.1:9000/"
        preset_start = "uri:Vibestorm Test&128&128&25"

        if preset == "OSgrid":
            preset_uri = "http://login.osgrid.org/"
            preset_start = "last"
        elif preset == "Second Life":
            preset_uri = "https://login.agni.lindenlab.com/cgi-bin/login.cgi"
            preset_start = "last"
        elif preset == "Custom":
            preset_uri = self.profile_data.get("VIBESTORM_LOGIN_URI", "")
            preset_start = self.profile_data.get("VIBESTORM_START_LOCATION", "last")

        # Fill fields
        if preset != "Custom":
            self.uri_entry.set_text(preset_uri)
            self.start_entry.set_text(preset_start)
        else:
            self.uri_entry.set_text(self.profile_data.get("VIBESTORM_LOGIN_URI", preset_uri))
            self.start_entry.set_text(self.profile_data.get("VIBESTORM_START_LOCATION", preset_start))

        self.first_entry.set_text(self.profile_data.get("VIBESTORM_FIRST_NAME", ""))
        self.last_entry.set_text(self.profile_data.get("VIBESTORM_LAST_NAME", ""))
        self.password_entry.set_text(self.profile_data.get("VIBESTORM_PASSWORD", ""))

        # Show/hide custom URI field appropriately
        if preset == "Custom":
            self.uri_label.show()
            self.uri_entry.show()
            self.uri_entry.enable()
        else:
            self.uri_label.hide()
            self.uri_entry.hide()
            self.uri_entry.disable()

    def resize(self, size: tuple[int, int]) -> None:
        """Handle screen resize events."""
        self.screen_size = size
        self.manager.set_window_resolution(size)

        self.manager.clear()
        self._build_ui()
        self._apply_preset_defaults()

        if self.connecting:
            # Re-disable if we resize while connecting
            self.preset_dropdown.disable()
            self.uri_entry.disable()
            self.first_entry.disable()
            self.last_entry.disable()
            self.password_entry.disable()
            self.start_entry.disable()
            self.remember_checkbox.disable()
            self.login_button.set_text("Cancel")

    def process_event(self, event: pygame.event.Event) -> bool:
        """Process pygame events. Returns True if event was consumed."""
        if self.connecting:
            # While connecting, only cancel button or quit is allowed
            if event.type == pygame.USEREVENT:
                if event.user_type == pygame_gui.UI_BUTTON_PRESSED:
                    if event.ui_element == self.login_button:  # which is now named "Cancel"
                        self._cancel_login()
                        return True
                    elif event.ui_element == self.quit_button:
                        self.quit_requested = True
                        return True
            return False

        consumed = bool(self.manager.process_events(event))

        if event.type == pygame.USEREVENT:
            if event.user_type == pygame_gui.UI_DROP_DOWN_MENU_CHANGED:
                if event.ui_element == self.preset_dropdown:
                    self._apply_preset_defaults()
                    consumed = True

            elif event.user_type == pygame_gui.UI_BUTTON_PRESSED:
                if event.ui_element == self.login_button:
                    self._start_login()
                    consumed = True
                elif event.ui_element == self.quit_button:
                    self.quit_requested = True
                    consumed = True

            elif event.user_type == pygame_gui.UI_TEXT_ENTRY_FINISHED:
                # Pressing Enter in fields starts login
                if event.ui_element in (self.first_entry, self.last_entry, self.password_entry, self.start_entry):
                    self._start_login()
                    consumed = True

        return consumed

    def update(self, dt: float) -> None:
        """Update particle animations and UIManager."""
        sw, sh = self.screen_size
        for p in self.particles:
            p.update(dt, sw, sh)

        self.manager.update(dt)

        # Check async login status if task is active
        if self.login_task and self.login_task.done():
            try:
                self.bootstrap = self.login_task.result()
                # Success!
                self._save_credentials_if_checked()
                self.connecting = False
            except LoginError as exc:
                self.status_label.set_text(f"Login failed: {exc}")
                self._stop_connecting_state()
            except Exception as exc:
                self.status_label.set_text(f"Unexpected error: {exc}")
                self._stop_connecting_state()
            self.login_task = None

    def draw(self, surface: pygame.Surface) -> None:
        """Render the beautiful background gradient, particles, and glass panel."""
        # 1. Linear Gradient: deep dark purple to deep blue
        draw_gradient(surface, (15, 10, 30), (10, 20, 45))

        # 2. Draw background drifting glowing particles
        for p in self.particles:
            p.draw(surface)

        # 3. Draw premium glassmorphic panel frame
        sw, sh = self.screen_size
        pwidth = self._s(460)
        pheight = self._s(490)
        px = (sw - pwidth) // 2
        py = (sh - pheight) // 2

        # Translucent dark fill
        glass_surf = pygame.Surface((pwidth, pheight), pygame.SRCALPHA)
        pygame.draw.rect(glass_surf, (15, 23, 42, 225), (0, 0, pwidth, pheight), border_radius=14)
        # Highlight borders with glowing violet
        pygame.draw.rect(glass_surf, (139, 92, 246, 80), (0, 0, pwidth, pheight), width=2, border_radius=14)
        surface.blit(glass_surf, (px, py))

        # 4. Draw widgets on top
        self.manager.draw_ui(surface)

    def _start_login(self) -> None:
        first = self.first_entry.get_text().strip()
        last = self.last_entry.get_text().strip()
        password = self.password_entry.get_text()
        start_loc = self.start_entry.get_text().strip()

        preset = self.preset_dropdown.selected_option
        if preset == "Custom":
            uri = self.uri_entry.get_text().strip()
        elif preset == "OSgrid":
            uri = "http://login.osgrid.org/"
        elif preset == "Second Life":
            uri = "https://login.agni.lindenlab.com/cgi-bin/login.cgi"
        else:  # Local OpenSim
            uri = "http://127.0.0.1:9000/"

        if not first or not last or not password or not uri:
            self.status_label.set_text("Please fill in all credentials.")
            return

        self.status_label.set_text("Connecting to simulator...")
        self.connecting = True

        # Disable fields
        self.preset_dropdown.disable()
        self.uri_entry.disable()
        self.first_entry.disable()
        self.last_entry.disable()
        self.password_entry.disable()
        self.start_entry.disable()
        self.remember_checkbox.disable()

        # Turn Connect button into Cancel
        self.login_button.set_text("Cancel")

        request = LoginRequest(
            login_uri=uri,
            credentials=LoginCredentials(first=first, last=last, password=password),
            start=start_loc,
            version=__version__,
            platform="Linux",
            platform_version="Generic Linux",
        )

        self.login_task = asyncio.create_task(LoginClient().login(request))

    def _cancel_login(self) -> None:
        if self.login_task:
            self.login_task.cancel()
            self.login_task = None
        self.status_label.set_text("Connection cancelled.")
        self._stop_connecting_state()

    def _stop_connecting_state(self) -> None:
        self.connecting = False
        # Enable fields
        self.preset_dropdown.enable()
        if self.preset_dropdown.selected_option == "Custom":
            self.uri_entry.enable()
        self.first_entry.enable()
        self.last_entry.enable()
        self.password_entry.enable()
        self.start_entry.enable()
        self.remember_checkbox.enable()

        self.login_button.set_text("Connect")

    def _save_credentials_if_checked(self) -> None:
        if self.remember_checkbox.is_checked():
            preset = self.preset_dropdown.selected_option
            if preset == "Custom":
                uri = self.uri_entry.get_text().strip()
            elif preset == "OSgrid":
                uri = "http://login.osgrid.org/"
            elif preset == "Second Life":
                uri = "https://login.agni.lindenlab.com/cgi-bin/login.cgi"
            else:
                uri = "http://127.0.0.1:9000/"

            values = {
                "VIBESTORM_LOGIN_URI": uri,
                "VIBESTORM_FIRST_NAME": self.first_entry.get_text().strip(),
                "VIBESTORM_LAST_NAME": self.last_entry.get_text().strip(),
                "VIBESTORM_PASSWORD": self.password_entry.get_text(),
                "VIBESTORM_START_LOCATION": self.start_entry.get_text().strip(),
            }
            credentials.save_profile(self.profile_path, values)


def draw_gradient(surface: pygame.Surface, start_color: tuple[int, int, int], end_color: tuple[int, int, int]):
    """Draw a smooth vertical gradient by scaling a 1x256 pixel stripe."""
    h = surface.get_height()
    w = surface.get_width()
    stripe = pygame.Surface((1, 256))
    for y in range(256):
        r = start_color[0] + (end_color[0] - start_color[0]) * y // 255
        g = start_color[1] + (end_color[1] - start_color[1]) * y // 255
        b = start_color[2] + (end_color[2] - start_color[2]) * y // 255
        stripe.set_at((0, y), (r, g, b))
    pygame.transform.scale(stripe, (w, h), surface)
