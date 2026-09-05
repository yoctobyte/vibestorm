"""When the HUD may reuse the frame it already painted.

Repainting costs a full-screen clear, a blit per element and a full-screen
texture upload -- 17 ms of a 1920x1080 frame on a GTX 1660 SUPER, of which the
upload alone is 6.4 ms -- for a HUD whose content changes a few times a second.
Skipping that is the single largest remaining win in the frame loop.

It is also the one with the worst failure mode. A HUD that is merely slow is
annoying; a HUD that shows last second's chat, or a button that never looks
pressed, is broken in a way that is hard to even describe. So these tests are
weighted towards "it redraws when it should", and the one that matters most is
the last: that an unforeseen change is bounded to half a second rather than
lasting forever.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from vibestorm.viewer3d.hud import FORCED_REDRAW_INTERVAL_S  # noqa: E402


class HUDDirtyTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((800, 600))
        from vibestorm.viewer3d.hud import HUD

        self.hud = HUD((800, 600), on_chat_submit=lambda _text: None)
        self.surface = pygame.Surface((800, 600), pygame.SRCALPHA)

    def tearDown(self) -> None:
        self.pygame.quit()

    def _settle(self) -> None:
        """Get to a steady state: painted, and with no pending refresh."""
        self._settle_with(self._scene())

    def _settle_with(self, scene) -> None:
        for _ in range(4):
            self.hud.update(FORCED_REDRAW_INTERVAL_S, scene)
            self.hud.draw(self.surface)
            self.hud.mark_drawn()
        self.hud.update(0.001, scene)
        self.hud.draw(self.surface)
        self.hud.mark_drawn()

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.region_name = "Vibestorm Test"
        scene.avatar_position = (128.0, 128.0, 25.0)
        return scene

    # -- it redraws when it must -------------------------------------------

    def test_the_first_frame_must_be_painted(self) -> None:
        # Nothing has been uploaded yet, so a skip here would draw a texture
        # that does not exist.
        self.assertTrue(self.hud.needs_redraw())

    def test_an_event_forces_a_redraw(self) -> None:
        self._settle()
        self.assertFalse(self.hud.needs_redraw())
        self.hud.process_event(
            self.pygame.event.Event(self.pygame.MOUSEMOTION, {"pos": (10, 10), "rel": (1, 1), "buttons": (0, 0, 0), "touch": False})
        )
        self.assertTrue(self.hud.needs_redraw())

    def test_new_chat_forces_a_redraw(self) -> None:
        # The one that matters most. Chat is the HUD content that changes
        # without any input from this user, so if the skip cannot see it, the
        # viewer silently stops showing what people say.
        from vibestorm.viewer3d.scene import ChatLine

        scene = self._scene()
        self._settle_with(scene)
        self.assertFalse(self.hud.needs_redraw())

        scene.chat_lines.append(ChatLine(kind="say", sender="Someone", message="hello"))
        # The ticker is throttled, so step past its interval to let it write.
        self.hud.update(1.0, scene)
        self.assertTrue(self.hud.needs_redraw())

    def test_a_resize_forces_a_redraw(self) -> None:
        self._settle()
        self.hud.resize((1024, 768))
        self.assertTrue(self.hud.needs_redraw())

    def test_it_redraws_at_least_every_forced_interval(self) -> None:
        # The safety net. The check reads pygame_gui's state rather than owning
        # it, so an element that repaints its own surface in place would slip
        # past. This bounds how long that can go unnoticed.
        self._settle()
        self.assertFalse(self.hud.needs_redraw())
        self.hud.update(FORCED_REDRAW_INTERVAL_S, self._scene())
        self.assertTrue(self.hud.needs_redraw())

    def test_a_focused_text_entry_always_redraws(self) -> None:
        # The text cursor blinks by painting into the entry's existing surface,
        # which the signature cannot see.
        self._settle()
        self.hud.focus_chat()
        self.hud.update(0.001, self._scene())
        self.hud.draw(self.surface)
        self.hud.mark_drawn()
        self.hud.update(0.001, self._scene())
        self.assertTrue(self.hud.needs_redraw())

    # -- it skips when it may ----------------------------------------------

    def test_an_idle_frame_is_skipped(self) -> None:
        self._settle()
        self.hud.update(0.001, self._scene())
        self.assertFalse(self.hud.needs_redraw())

    def test_marking_drawn_clears_the_forced_flag(self) -> None:
        self._settle()
        self.hud.process_event(
            self.pygame.event.Event(self.pygame.MOUSEMOTION, {"pos": (10, 10), "rel": (1, 1), "buttons": (0, 0, 0), "touch": False})
        )
        self.hud.update(0.001, self._scene())
        self.hud.draw(self.surface)
        self.hud.mark_drawn()
        self.hud.update(0.001, self._scene())
        self.assertFalse(self.hud.needs_redraw())


class HUDVisualSignatureTests(unittest.TestCase):
    """The signature has to be the draw, or the skip is a guess."""

    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((800, 600))
        from vibestorm.viewer3d.hud import HUD

        self.hud = HUD((800, 600), on_chat_submit=lambda _text: None)
        # The group builds its visible list lazily; until it has, both the
        # signature and the draw see nothing.
        self.hud.update(0.001)

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_it_covers_every_sprite_the_group_would_blit(self) -> None:
        # LayeredGUIGroup.draw is one call: surface.blits(self.visible). If the
        # signature is shorter than that list, something is drawn that nothing
        # is watching.
        visible = self.hud.manager.get_sprite_group().visible
        _pending, entries = self.hud._visual_signature()
        self.assertEqual(len(entries), len(visible))
        self.assertGreater(len(visible), 0)

    def test_hiding_a_window_changes_it(self) -> None:
        before = self.hud._visual_signature()
        self.hud.chat_window.hide()
        self.hud.update(0.001)
        self.assertNotEqual(self.hud._visual_signature(), before)

    def test_moving_a_window_changes_it(self) -> None:
        before = self.hud._visual_signature()
        rect = self.hud.chat_window.rect
        self.hud.chat_window.set_position((rect.x + 25, rect.y + 25))
        self.assertNotEqual(self.hud._visual_signature(), before)


if __name__ == "__main__":
    unittest.main()
