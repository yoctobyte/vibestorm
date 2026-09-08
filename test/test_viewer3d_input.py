"""What each key and each mouse gesture actually does.

This file used to hold one test, for F1 to F3. Everything else `handle_event`
does -- every movement key, the fly toggle, chat focus, centring on the
avatar, the orbit camera's zoom, rotate, pan and lift, and the map camera's
pan and zoom -- was covered by nothing at all. A binding could be dropped, or
wired to the wrong control flag, and the suite would stay green.

That is the finding this project keeps hitting from a different direction:
`handle_event` is a dispatch, and a dispatch has to be tested as one. Tests of
`AgentControlFlags`, of `Camera3D.orbit_zoom`, of `Bus.dispatch` -- all of
which exist -- say nothing about whether pressing W moves the avatar forward.

The pairs are pinned rather than derived from the table under test. A test
that reads the same dictionary the code reads agrees with a swapped entry.
"""

import unittest


def _pygame():
    try:
        import pygame
    except ImportError:  # pragma: no cover
        return None
    return pygame


class _InputCase(unittest.TestCase):
    """One camera, one bus, and a record of everything the bus was handed."""

    def setUp(self) -> None:
        pygame = _pygame()
        if pygame is None:  # pragma: no cover
            self.skipTest("pygame not available")
        from vibestorm.bus import Bus
        from vibestorm.bus.commands import AddControlFlags, RemoveControlFlags
        from vibestorm.viewer3d.camera import Camera3D

        pygame.init()
        self.addCleanup(pygame.quit)
        self.pygame = pygame
        self.camera = Camera3D()
        self.bus = Bus()
        self.added: list[int] = []
        self.removed: list[int] = []
        self.bus.register_handler(AddControlFlags, lambda cmd: self.added.append(cmd.flags))
        self.bus.register_handler(RemoveControlFlags, lambda cmd: self.removed.append(cmd.flags))

    def send(self, event_type: int, **fields):
        from vibestorm.viewer3d.input import handle_event

        event = self.pygame.event.Event(event_type, **fields)
        return handle_event(event, self.camera, self.bus)

    def press(self, key: int):
        return self.send(self.pygame.KEYDOWN, key=key)

    def release(self, key: int):
        return self.send(self.pygame.KEYUP, key=key)

    def with_mods(self, mods: int):
        """Hold a modifier for the duration of one test."""
        previous = self.pygame.key.get_mods()
        self.pygame.key.set_mods(mods)
        self.addCleanup(self.pygame.key.set_mods, previous)


class MovementKeyTests(_InputCase):
    """The keys that move the avatar, and which bit each one sets.

    Pinned as pairs on purpose. Two of these -- forward and back, left and
    right -- differ by one bit, and a viewer whose W walks backwards is a
    viewer nobody can use while every unit test underneath it passes.
    """

    def pairs(self):
        from vibestorm.udp.control_flags import AgentControlFlags as F

        p = self.pygame
        return (
            ("forward", p.K_w, F.AT_POS),
            ("forward (arrow)", p.K_UP, F.AT_POS),
            ("back", p.K_s, F.AT_NEG),
            ("back (arrow)", p.K_DOWN, F.AT_NEG),
            ("turn left", p.K_a, F.TURN_LEFT),
            ("turn left (arrow)", p.K_LEFT, F.TURN_LEFT),
            ("turn right", p.K_d, F.TURN_RIGHT),
            ("turn right (arrow)", p.K_RIGHT, F.TURN_RIGHT),
            ("step left", p.K_q, F.LEFT_POS),
            ("step right", p.K_e, F.LEFT_NEG),
            ("up", p.K_PAGEUP, F.UP_POS),
            ("down", p.K_PAGEDOWN, F.UP_NEG),
            ("fly", p.K_f, F.FLY),
        )

    def test_pressing_a_key_adds_exactly_its_own_flag(self) -> None:
        for label, key, flag in self.pairs():
            with self.subTest(label):
                self.added.clear()
                self.press(key)
                self.assertEqual(self.added, [int(flag)])

    def test_releasing_a_key_removes_exactly_its_own_flag(self) -> None:
        """The half that is invisible while it is broken.

        A missing remove does not stop anything happening -- it stops it
        stopping. The avatar walks into the sea and the key that was let go of
        is still held down as far as the simulator knows.
        """
        for label, key, flag in self.pairs():
            with self.subTest(label):
                self.removed.clear()
                self.release(key)
                self.assertEqual(self.removed, [int(flag)])

    def test_the_arrow_keys_are_the_letters(self) -> None:
        """Same bit, not merely both bound: an arrow bound to its neighbour
        is a viewer that walks sideways when you ask it to walk."""
        p = self.pygame
        for letter, arrow in ((p.K_w, p.K_UP), (p.K_s, p.K_DOWN),
                              (p.K_a, p.K_LEFT), (p.K_d, p.K_RIGHT)):
            with self.subTest(letter=letter):
                self.added.clear()
                self.press(letter)
                self.press(arrow)
                self.assertEqual(len(self.added), 2)
                self.assertEqual(self.added[0], self.added[1])

    def test_every_movement_flag_is_a_different_bit(self) -> None:
        """Two keys sharing a bit by accident would each undo the other."""
        by_flag: dict[int, list[str]] = {}
        for label, _key, flag in self.pairs():
            by_flag.setdefault(int(flag), []).append(label)
        shared = {
            flag: labels
            for flag, labels in by_flag.items()
            if len({label.split(" (")[0] for label in labels}) > 1
        }
        self.assertEqual(shared, {})

    def test_a_key_nobody_bound_dispatches_nothing(self) -> None:
        """`Bus.dispatch` raises for a command with no handler, so a stray
        binding would surface as a `BusError` out of the frame loop."""
        self.press(self.pygame.K_z)
        self.release(self.pygame.K_z)
        self.assertEqual((self.added, self.removed), ([], []))


class IntentKeyTests(_InputCase):
    """Keys the app acts on itself rather than sending to the simulator."""

    def test_f_keys_choose_a_camera(self) -> None:
        p = self.pygame
        for key, preset in ((p.K_F1, "sim"), (p.K_F2, "avatar_behind"), (p.K_F3, "avatar_eye")):
            with self.subTest(preset):
                self.assertEqual(self.press(key).camera_preset, preset)

    def test_c_centres_on_the_avatar(self) -> None:
        self.assertTrue(self.press(self.pygame.K_c).request_center_on_avatar)

    def test_return_focuses_chat_and_sends_no_movement(self) -> None:
        """It has to consume the key. Return reaching the movement table as
        well would type a message and walk at the same time."""
        intent = self.press(self.pygame.K_RETURN)
        self.assertTrue(intent.chat_input_focus)
        self.assertEqual(self.added, [])

    def test_quit_is_asked_for_rather_than_taken(self) -> None:
        self.assertTrue(self.send(self.pygame.QUIT).quit_requested)

    def test_an_ordinary_key_asks_for_nothing(self) -> None:
        intent = self.press(self.pygame.K_w)
        self.assertFalse(intent.quit_requested)
        self.assertFalse(intent.chat_input_focus)
        self.assertFalse(intent.request_center_on_avatar)
        self.assertIsNone(intent.camera_preset)


class OrbitCameraTests(_InputCase):
    """The camera gestures, in the mode the viewer starts a 3D session in."""

    def setUp(self) -> None:
        super().setUp()
        self.camera.set_mode("orbit")

    def test_the_wheel_zooms_the_boom(self) -> None:
        before = self.camera.distance
        self.send(self.pygame.MOUSEWHEEL, y=1)
        self.assertLess(self.camera.distance, before)
        self.send(self.pygame.MOUSEWHEEL, y=-1)
        self.assertAlmostEqual(self.camera.distance, before)

    def test_a_right_drag_turns_the_camera(self) -> None:
        before = self.camera.yaw
        self.send(self.pygame.MOUSEMOTION, rel=(40, 0), buttons=(0, 0, 1), pos=(0, 0))
        self.assertNotEqual(self.camera.yaw, before)

    def test_a_left_drag_turns_nothing(self) -> None:
        """The left button picks objects. A camera that also turned under it
        would move the world out from under every click."""
        before = (self.camera.yaw, self.camera.pitch)
        self.send(self.pygame.MOUSEMOTION, rel=(40, 20), buttons=(1, 0, 0), pos=(0, 0))
        self.assertEqual((self.camera.yaw, self.camera.pitch), before)

    def test_shift_and_a_right_drag_pans_instead_of_turning(self) -> None:
        self.with_mods(self.pygame.KMOD_LSHIFT)
        before_yaw = self.camera.yaw
        before_target = tuple(self.camera.target)
        self.send(self.pygame.MOUSEMOTION, rel=(40, 20), buttons=(0, 0, 1), pos=(0, 0))
        self.assertEqual(self.camera.yaw, before_yaw)
        self.assertNotEqual(tuple(self.camera.target), before_target)

    def test_shift_and_page_up_lifts_the_camera_rather_than_the_avatar(self) -> None:
        """The one binding that means two things.

        Unshifted, PageUp is `UP_POS` and flies the avatar. Shifted, it raises
        the camera and must *not* also fly -- a lift that leaked the flag
        would send the avatar up every time somebody adjusted the view.
        """
        from vibestorm.udp.control_flags import AgentControlFlags as F

        self.with_mods(self.pygame.KMOD_LSHIFT)
        before = tuple(self.camera.target)
        self.press(self.pygame.K_PAGEUP)
        self.assertNotEqual(tuple(self.camera.target), before)
        self.assertEqual(self.added, [])

        # And down is up's opposite rather than a second copy of it, which is
        # the way a lift bound twice to the same sign would read.
        self.press(self.pygame.K_PAGEDOWN)
        self.assertEqual(self.added, [])
        self.assertEqual(tuple(self.camera.target), before)

        # And without the modifier it is the avatar again.
        self.pygame.key.set_mods(0)
        self.press(self.pygame.K_PAGEUP)
        self.assertEqual(self.added, [int(F.UP_POS)])


class MapCameraTests(_InputCase):
    """The 2D mode, which shares the same two gestures and means them
    differently. A branch that stopped checking the mode would pan the map
    with the orbit camera's numbers and look almost right."""

    def setUp(self) -> None:
        super().setUp()
        self.camera.set_mode("map")

    def test_the_wheel_zooms_the_map(self) -> None:
        before = self.camera.zoom
        self.send(self.pygame.MOUSEWHEEL, y=1)
        self.assertGreater(self.camera.zoom, before)

    def test_a_right_drag_pans_the_map(self) -> None:
        before = tuple(self.camera.world_center)
        self.send(self.pygame.MOUSEMOTION, rel=(40, 20), buttons=(0, 0, 1), pos=(0, 0))
        self.assertNotEqual(tuple(self.camera.world_center), before)

    def test_the_boom_is_not_what_the_wheel_moves_here(self) -> None:
        before = self.camera.distance
        self.send(self.pygame.MOUSEWHEEL, y=1)
        self.assertEqual(self.camera.distance, before)


if __name__ == "__main__":
    unittest.main()
