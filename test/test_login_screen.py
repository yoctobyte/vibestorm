"""The login screen, driven the way a person drives it.

This file used to hand-build the events it fed in, in exactly the shape the
code was looking for. That is not a test of the screen, it is a test that the
screen agrees with itself: it passed while choosing "Second Life" in the grid
dropdown and pressing Connect logged in to `http://127.0.0.1:9000/`.

What went wrong is worth stating once. `UIDropDownMenu.selected_option` is a
`(display text, object id)` pair in the pygame_gui this project pins, and was
a bare string in older ones. Every `preset == "Second Life"` in the screen was
written against the string, so on the installed library all of them were false
and all of them fell through to the Local OpenSim branch. The test agreed
because it assigned a bare string to `selected_option` itself, which is a
value the library never produces.

So the rules here:

- **Click the control.** `click()` feeds real mouse events and hands whatever
  pygame_gui posts back to `process_event`, so what is under test is the
  screen against the library rather than the screen against this file's idea
  of the library.
- **Take shapes from the library.** Where a control's state has to be set
  directly, the shape is read off a live widget rather than written out here.

Priority B is logging in to the Second Life main grid at the home location,
and this screen is the way in. Most of what follows is about that path.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

SL_URI = "https://login.agni.lindenlab.com/cgi-bin/login.cgi"
OSGRID_URI = "http://login.osgrid.org/"
LOCAL_URI = "http://127.0.0.1:9000/"


def isolate_the_saved_profile(case) -> None:
    """Point the screen at a profile file that does not exist.

    `credentials.get_profile_path()` returns the *relative* path
    `local/vibestorm-login.env` unless an environment variable overrides it,
    so a test run from the repository root reads the machine owner's real
    stored credentials -- fills the fields with them, and, since the
    constructor auto-connects when a profile is complete, tries to log in with
    them the moment the screen is built inside an event loop. Every case here
    overrides the variable first.
    """
    directory = tempfile.mkdtemp(prefix="vibestorm-login-test-")
    case.addCleanup(os.rmdir, directory)
    patch = mock.patch.dict(
        os.environ,
        {"VIBESTORM_LOGIN_PROFILE": str(Path(directory) / "absent.env")},
    )
    patch.start()
    case.addCleanup(patch.stop)


class _ScreenCase(unittest.TestCase):
    SIZE = (800, 600)

    def setUp(self) -> None:
        isolate_the_saved_profile(self)
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

    def screen(self, **profile):
        """A login screen, optionally pre-filled the way `run.sh` fills it."""
        import argparse

        from vibestorm.viewer.login_screen import LoginScreen

        args = argparse.Namespace(**profile) if profile else None
        return LoginScreen(self.SIZE, args=args)

    def click(self, screen, element) -> None:
        """Press a control for real and deliver whatever the library posts.

        The mouse events go in through `process_event` and the posted UI
        events come back out of the queue, which is the loop `run_viewer`
        runs. Nothing here decides what a button press looks like.
        """
        self.pygame.event.clear()
        for kind in (self.pygame.MOUSEBUTTONDOWN, self.pygame.MOUSEBUTTONUP):
            screen.process_event(
                self.pygame.event.Event(kind, {"pos": element.rect.center, "button": 1})
            )
            screen.update(0.02)
        for event in self.pygame.event.get():
            screen.process_event(event)

    def choose_preset(self, screen, name: str) -> None:
        """Select a grid preset, in the shape the installed library stores.

        Reading the shape off the live widget rather than writing it out is
        the whole point: this is the exact thing the old test got wrong, and
        a version of pygame_gui that changes it again must not quietly make
        this file agree with the screen instead of with the library.
        """
        current = screen.preset_dropdown.selected_option
        if isinstance(current, tuple):
            screen.preset_dropdown.selected_option = (name,) + tuple(current[1:])
        elif isinstance(current, list):
            screen.preset_dropdown.selected_option = [name, *current[1:]]
        else:
            screen.preset_dropdown.selected_option = name
        screen._apply_preset_defaults()


class PresetShapeTests(_ScreenCase):
    """The bug itself: what the dropdown hands back, and reading it."""

    def test_the_preset_name_is_read_whatever_shape_it_is_stored_in(self) -> None:
        screen = self.screen()
        raw = screen.preset_dropdown.selected_option
        self.assertEqual(screen._selected_preset(), "Local OpenSim")
        self.assertIn(
            type(raw).__name__,
            ("str", "tuple", "list"),
            f"pygame_gui now stores the selection as {type(raw).__name__}",
        )

    def test_a_pair_is_read_as_its_display_text(self) -> None:
        """Pinned directly, because this is the shape the pin produces and the
        one every comparison in the screen used to get wrong."""
        screen = self.screen()
        screen.preset_dropdown.selected_option = ("Second Life", "Second Life")
        self.assertEqual(screen._selected_preset(), "Second Life")

    def test_a_bare_string_still_reads(self) -> None:
        """Older pygame_gui, and the pin allows the whole 0.x range."""
        screen = self.screen()
        screen.preset_dropdown.selected_option = "OSgrid"
        self.assertEqual(screen._selected_preset(), "OSgrid")


class PresetDefaultTests(_ScreenCase):
    """Which URI and which starting point each preset fills in.

    The starting points are not arbitrary and are shared with `run.sh`: local
    goes to a known spot in the test region, OSgrid goes to `last`, and
    Second Life goes to **home**, because `last` drops the avatar wherever the
    previous session ended and on a grid we do not control that is not a known
    starting state. `./gui.sh sl` announces the home default out loud; this
    screen used to overwrite it with `last` on the way past.
    """

    CASES = (
        ("Local OpenSim", LOCAL_URI, "uri:Vibestorm Test&128&128&25"),
        ("OSgrid", OSGRID_URI, "last"),
        ("Second Life", SL_URI, "home"),
    )

    def test_each_preset_fills_its_own_uri_and_start(self) -> None:
        for name, uri, start in self.CASES:
            with self.subTest(name):
                screen = self.screen()
                self.choose_preset(screen, name)
                self.assertEqual(screen.uri_entry.get_text(), uri)
                self.assertEqual(screen.start_entry.get_text(), start)

    def test_the_second_life_preset_starts_at_home(self) -> None:
        """Its own test because it is the owner's stated requirement for B,
        and because `last` is the more common viewer default -- so this is the
        line most likely to be 'corrected' back."""
        screen = self.screen()
        self.choose_preset(screen, "Second Life")
        self.assertEqual(screen.start_entry.get_text(), "home")

    def test_the_uri_field_is_only_editable_for_custom(self) -> None:
        """It is hidden for the fixed presets, so a wrong URI in it is a URI
        nobody can correct without switching preset first."""
        screen = self.screen()
        self.choose_preset(screen, "Second Life")
        self.assertFalse(screen.uri_entry.visible)
        self.choose_preset(screen, "Custom")
        self.assertTrue(screen.uri_entry.visible)

    def test_launching_with_the_second_life_uri_selects_that_preset(self) -> None:
        """`./gui.sh sl` passes the URI down; the screen has to recognise it,
        or it opens on Local OpenSim with the credentials already filled in.
        """
        screen = self.screen(
            first="Test", last="User", password="secret", login_uri=SL_URI, start="home"
        )
        self.assertEqual(screen._selected_preset(), "Second Life")
        self.assertEqual(screen.uri_entry.get_text(), SL_URI)
        self.assertEqual(screen.start_entry.get_text(), "home")


class ButtonTests(_ScreenCase):
    """Real clicks, so the library decides what a press is."""

    def test_clicking_quit_asks_to_quit(self) -> None:
        screen = self.screen()
        self.assertFalse(screen.quit_requested)
        self.click(screen, screen.quit_button)
        self.assertTrue(screen.quit_requested)

    def test_clicking_connect_with_empty_fields_complains(self) -> None:
        """And does not start a login: `LoginClient` would be handed a blank
        password and the grid would answer with something less useful."""
        screen = self.screen()
        self.click(screen, screen.login_button)
        self.assertIn("fill in", screen.status_label.text.lower())
        self.assertFalse(screen.connecting)


class ConnectRequestTests(unittest.IsolatedAsyncioTestCase):
    """What Connect actually sends, which is the whole of priority B.

    `_start_login` builds a `LoginRequest` and hands it to `LoginClient`. The
    client is replaced here so nothing leaves the machine -- and so the
    request can be read. Sending a probe login to Linden Lab's production
    endpoint is not something a test gets to do.
    """

    SIZE = (800, 600)

    async def asyncSetUp(self) -> None:
        isolate_the_saved_profile(self)
        try:
            import pygame
            import pygame_gui  # noqa: F401 - checked for the same reason
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode(self.SIZE)
        self.addCleanup(pygame.quit)

        from vibestorm.viewer import login_screen as module

        self.requests = []
        outer = self

        class _FakeClient:
            async def login(self, request):
                outer.requests.append(request)
                raise module.LoginError("no grid in a test")

        self.previous_client = module.LoginClient
        module.LoginClient = _FakeClient
        self.addCleanup(setattr, module, "LoginClient", self.previous_client)
        self.module = module

    def _filled(self, preset: str):
        screen = self.module.LoginScreen(self.SIZE)
        current = screen.preset_dropdown.selected_option
        screen.preset_dropdown.selected_option = (
            (preset,) + tuple(current[1:]) if isinstance(current, tuple) else preset
        )
        screen._apply_preset_defaults()
        screen.first_entry.set_text("Vibestorm")
        screen.last_entry.set_text("Tester")
        screen.password_entry.set_text("secret")
        return screen

    async def test_the_second_life_preset_connects_to_second_life(self) -> None:
        """The failure this replaces: it connected to 127.0.0.1 instead, with
        the dropdown showing Second Life, because the preset comparison never
        matched. Nothing said so -- the local sim answered."""
        screen = self._filled("Second Life")
        screen._start_login()
        await self._settle(screen)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].login_uri, SL_URI)
        self.assertEqual(self.requests[0].start, "home")

    async def test_each_preset_connects_to_its_own_grid(self) -> None:
        for preset, uri in (
            ("Local OpenSim", LOCAL_URI),
            ("OSgrid", OSGRID_URI),
            ("Second Life", SL_URI),
        ):
            with self.subTest(preset):
                self.requests.clear()
                screen = self._filled(preset)
                screen._start_login()
                await self._settle(screen)
                self.assertEqual([r.login_uri for r in self.requests], [uri])

    async def test_the_credentials_typed_in_are_the_ones_sent(self) -> None:
        screen = self._filled("Second Life")
        screen._start_login()
        await self._settle(screen)
        request = self.requests[0]
        self.assertEqual(request.credentials.first, "Vibestorm")
        self.assertEqual(request.credentials.last, "Tester")
        self.assertEqual(request.credentials.password, "secret")

    async def _settle(self, screen) -> None:
        import asyncio

        for _ in range(20):
            await asyncio.sleep(0)
            if screen.login_task is not None and screen.login_task.done():
                break
        screen.update(0.02)


if __name__ == "__main__":
    unittest.main()
