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


class PresetTableTests(unittest.TestCase):
    """The preset table on its own, without a display.

    The three copies of this mapping that used to live in the screen are the
    reason it exists: the grid you log in to and the grid your password is
    filed under have to be the same grid, and with one table they cannot
    disagree.
    """

    def test_every_offered_preset_has_a_uri(self) -> None:
        from vibestorm.viewer.login_screen import PRESET_START_LOCATIONS, PRESET_URIS

        self.assertEqual(set(PRESET_URIS), set(PRESET_START_LOCATIONS))
        self.assertEqual(PRESET_URIS["Second Life"], SL_URI)
        self.assertEqual(PRESET_START_LOCATIONS["Second Life"], "home")

    def test_custom_means_whatever_is_in_the_box(self) -> None:
        from vibestorm.viewer.login_screen import uri_for_preset

        self.assertEqual(uri_for_preset("Custom", "  http://elsewhere/  "), "http://elsewhere/")

    def test_a_name_that_is_not_in_the_table_falls_back_to_the_local_sim(self) -> None:
        """Not to the empty string, and least of all to a remote grid: a
        preset name that drifts out of the table must fail towards the
        machine the developer already trusts."""
        from vibestorm.viewer.login_screen import uri_for_preset

        self.assertEqual(uri_for_preset("Third Life", "ignored"), LOCAL_URI)


class CheckboxShapeTests(_ScreenCase):
    """What the checkbox hands back, and reading it.

    Exactly the fault `PresetShapeTests` covers, found a second time in the
    same file: `UICheckBox.is_checked` is a `bool` attribute on the pinned
    pygame_gui, not a method, so `is_checked()` raised inside the arm that
    runs when a login succeeds.
    """

    def test_the_tick_is_read_whatever_shape_it_is_stored_in(self) -> None:
        from vibestorm.viewer.login_screen import checkbox_is_checked

        screen = self.screen()
        box = screen.remember_checkbox
        self.assertIs(checkbox_is_checked(box), False)
        self.click(screen, box)
        self.assertIs(checkbox_is_checked(box), True)

    def test_both_shapes_of_the_accessor_are_accepted(self) -> None:
        """The attribute is what the pin produces; the method is what an
        older pygame_gui in the same allowed range would produce."""
        from vibestorm.viewer.login_screen import checkbox_is_checked

        self.assertIs(checkbox_is_checked(mock.Mock(is_checked=True)), True)
        self.assertIs(checkbox_is_checked(mock.Mock(is_checked=lambda: True)), True)
        self.assertIs(checkbox_is_checked(mock.Mock(is_checked=False)), False)
        self.assertIs(checkbox_is_checked(mock.Mock(is_checked=lambda: False)), False)

    def test_an_indeterminate_box_does_not_count_as_ticked(self) -> None:
        """Only a definite tick may write a password to disk."""
        from vibestorm.viewer.login_screen import checkbox_is_checked

        screen = self.screen()
        screen.remember_checkbox.set_state("indeterminate")
        self.assertIs(checkbox_is_checked(screen.remember_checkbox), False)


class RememberMeTests(_ScreenCase):
    """Ticking "Remember me" writes a password to disk. Under which URI, and at what mode.

    Nothing covered this path before. It ran the same chain of preset
    comparisons that the Connect button ran, so on the installed pygame_gui a
    user checking the box with Second Life selected would have had their
    Second Life password filed under `http://127.0.0.1:9000/` -- and then
    handed to the local sim on the next launch, because the saved URI is what
    the screen reopens on.
    """

    def setUp(self) -> None:
        super().setUp()
        directory = Path(tempfile.mkdtemp(prefix="vibestorm-remember-test-"))
        self.profile_path = directory / "saved.env"
        self.addCleanup(os.rmdir, directory)
        self.addCleanup(lambda: self.profile_path.unlink(missing_ok=True))
        patch = mock.patch.dict(os.environ, {"VIBESTORM_LOGIN_PROFILE": str(self.profile_path)})
        patch.start()
        self.addCleanup(patch.stop)

    def saved(self) -> dict:
        from vibestorm.util import credentials

        return credentials.load_profile(self.profile_path)

    def filled(self, preset: str, *, remember: bool = True):
        screen = self.screen()
        self.assertEqual(screen.profile_path, self.profile_path)
        self.choose_preset(screen, preset)
        screen.first_entry.set_text("Vibestorm")
        screen.last_entry.set_text("Tester")
        screen.password_entry.set_text("hunter2")
        self.set_remember(screen, remember)
        return screen

    def set_remember(self, screen, wanted: bool) -> None:
        """Tick the box by clicking it, and check the click took.

        Driving it through `set_state` would work too, but the fault this
        class exists to cover was a *reader* disagreeing with the library, and
        a test that sets the state through the library's setter and reads it
        back through the library's getter would have passed against the broken
        reader as happily as against the fixed one.
        """
        from vibestorm.viewer.login_screen import checkbox_is_checked

        if checkbox_is_checked(screen.remember_checkbox) != wanted:
            self.click(screen, screen.remember_checkbox)
        self.assertEqual(checkbox_is_checked(screen.remember_checkbox), wanted)

    def test_remembering_second_life_files_it_under_second_life(self) -> None:
        screen = self.filled("Second Life")
        screen._save_credentials_if_checked()
        saved = self.saved()
        self.assertEqual(saved["VIBESTORM_LOGIN_URI"], SL_URI)
        self.assertEqual(saved["VIBESTORM_START_LOCATION"], "home")
        self.assertEqual(saved["VIBESTORM_PASSWORD"], "hunter2")

    def test_an_unchecked_box_writes_nothing(self) -> None:
        screen = self.filled("Second Life", remember=False)
        screen._save_credentials_if_checked()
        self.assertFalse(self.profile_path.exists())

    def test_what_is_saved_is_what_the_screen_was_showing(self) -> None:
        """Read off the visible field rather than off the table, so this stays
        a test of the screen and not a restatement of the mapping."""
        for name in ("Local OpenSim", "OSgrid", "Second Life"):
            with self.subTest(name):
                self.profile_path.unlink(missing_ok=True)
                screen = self.filled(name)
                shown_uri = screen.uri_entry.get_text()
                shown_start = screen.start_entry.get_text()
                screen._save_credentials_if_checked()
                saved = self.saved()
                self.assertEqual(saved["VIBESTORM_LOGIN_URI"], shown_uri)
                self.assertEqual(saved["VIBESTORM_START_LOCATION"], shown_start)

    def test_a_remembered_grid_is_the_grid_the_screen_reopens_on(self) -> None:
        """The round trip, which is what the user actually experiences: check
        the box on a grid, and the next launch comes up on that same grid."""
        for name in ("Local OpenSim", "OSgrid", "Second Life"):
            with self.subTest(name):
                self.profile_path.unlink(missing_ok=True)
                self.filled(name)._save_credentials_if_checked()
                self.assertEqual(self.screen()._get_starting_preset_name(), name)

    def test_a_remembered_password_is_not_readable_by_anyone_else(self) -> None:
        import stat

        self.filled("Second Life")._save_credentials_if_checked()
        self.assertEqual(stat.S_IMODE(self.profile_path.stat().st_mode), 0o600)


class SuccessfulLoginTests(unittest.IsolatedAsyncioTestCase):
    """The moment a login succeeds, end to end, with the box ticked.

    This is where the checkbox fault actually bit. `update()` assigns the
    bootstrap and then calls `_save_credentials_if_checked()` *inside* the
    `try`, so the `TypeError` landed in the `except Exception` arm: a
    successful login put "Unexpected error: 'bool' object is not callable" on
    the status line, left `connecting` true, re-enabled every field, and saved
    nothing. The viewer went on regardless, because `bootstrap` had already
    been assigned on the line before -- which is why nobody noticed.
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

        directory = Path(tempfile.mkdtemp(prefix="vibestorm-success-test-"))
        self.profile_path = directory / "saved.env"
        self.addCleanup(os.rmdir, directory)
        self.addCleanup(lambda: self.profile_path.unlink(missing_ok=True))
        patch = mock.patch.dict(os.environ, {"VIBESTORM_LOGIN_PROFILE": str(self.profile_path)})
        patch.start()
        self.addCleanup(patch.stop)

        from vibestorm.viewer import login_screen as module

        self.bootstrap = object()
        outer = self

        class _FakeClient:
            async def login(self, request):
                return outer.bootstrap

        previous = module.LoginClient
        module.LoginClient = _FakeClient
        self.addCleanup(setattr, module, "LoginClient", previous)
        self.module = module

    async def _connected(self, *, remember: bool):
        import asyncio

        screen = self.module.LoginScreen(self.SIZE)
        current = screen.preset_dropdown.selected_option
        screen.preset_dropdown.selected_option = (
            ("Second Life",) + tuple(current[1:]) if isinstance(current, tuple) else "Second Life"
        )
        screen._apply_preset_defaults()
        screen.first_entry.set_text("Vibestorm")
        screen.last_entry.set_text("Tester")
        screen.password_entry.set_text("hunter2")
        screen.remember_checkbox.set_state(remember)
        screen._start_login()
        for _ in range(20):
            await asyncio.sleep(0)
            if screen.login_task is not None and screen.login_task.done():
                break
        screen.update(0.02)
        return screen

    async def test_a_successful_login_says_nothing_about_an_error(self) -> None:
        screen = await self._connected(remember=True)
        self.assertIs(screen.bootstrap, self.bootstrap)
        self.assertFalse(screen.connecting)
        self.assertNotIn("error", screen.status_label.text.lower())

    async def test_a_successful_login_with_the_box_ticked_saves_the_grid(self) -> None:
        from vibestorm.util import credentials

        await self._connected(remember=True)
        saved = credentials.load_profile(self.profile_path)
        self.assertEqual(saved["VIBESTORM_LOGIN_URI"], SL_URI)
        self.assertEqual(saved["VIBESTORM_START_LOCATION"], "home")

    async def test_a_successful_login_without_the_box_saves_nothing(self) -> None:
        await self._connected(remember=False)
        self.assertFalse(self.profile_path.exists())


if __name__ == "__main__":
    unittest.main()
