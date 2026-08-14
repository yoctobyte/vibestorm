"""Tests for the sound flags byte.

Values are OpenSim's ``SoundFlags`` enum in ``SoundModule.cs``. The pin test
also guards against the wrong source: ``LSL_Constants.cs`` has ``SOUND_*``
constants that look authoritative and are not — they are llLinkPlaySound
parameters with a different meaning per value.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.sound_flags import (
    SOUND_FLAG_LOOP,
    SOUND_FLAG_QUEUE,
    SOUND_FLAG_STOP,
    SOUND_FLAG_SYNC_MASK,
    SOUND_FLAG_SYNC_MASTER,
    SOUND_FLAG_SYNC_PENDING,
    SOUND_FLAG_SYNC_SLAVE,
    decode_sound_flags,
)

_SOUND_MODULE = (
    Path(__file__).resolve().parents[1]
    / "opensim-source"
    / "OpenSim"
    / "Region"
    / "CoreModules"
    / "World"
    / "Sound"
    / "SoundModule.cs"
)


class SourcePinTests(unittest.TestCase):
    def test_values_match_the_opensim_enum(self) -> None:
        if not _SOUND_MODULE.exists():
            self.skipTest("opensim-source not present")
        text = _SOUND_MODULE.read_text(encoding="utf-8", errors="replace")
        body = text.split("enum SoundFlags", 1)[1].split("}", 1)[0]
        shifts = {
            name: 1 << int(shift)
            for name, shift in re.findall(r"(\w+)\s*=\s*1 << (\d+)", body)
        }

        self.assertEqual(shifts["LOOP"], SOUND_FLAG_LOOP)
        self.assertEqual(shifts["SYNC_MASTER"], SOUND_FLAG_SYNC_MASTER)
        self.assertEqual(shifts["SYNC_SLAVE"], SOUND_FLAG_SYNC_SLAVE)
        self.assertEqual(shifts["SYNC_PENDING"], SOUND_FLAG_SYNC_PENDING)
        self.assertEqual(shifts["QUEUE"], SOUND_FLAG_QUEUE)
        self.assertEqual(shifts["STOP"], SOUND_FLAG_STOP)

    def test_not_taken_from_the_lsl_constants(self) -> None:
        # LSL's SOUND_TRIGGER is 2 and SOUND_SYNC is 4. On the wire, 2 is
        # SYNC_MASTER and 4 is SYNC_SLAVE. Sourcing from LSL_Constants would
        # produce names that are wrong but entirely plausible.
        self.assertEqual(decode_sound_flags(2).set_flags, ("sync master",))
        self.assertEqual(decode_sound_flags(4).set_flags, ("sync slave",))


class DecodeTests(unittest.TestCase):
    def test_zero_is_none(self) -> None:
        decoded = decode_sound_flags(0)

        self.assertEqual(decoded.set_flags, ())
        self.assertEqual(decoded.describe(), "none")
        self.assertFalse(decoded.is_looping)
        self.assertFalse(decoded.is_stop)

    def test_loop(self) -> None:
        decoded = decode_sound_flags(SOUND_FLAG_LOOP)

        self.assertTrue(decoded.is_looping)
        self.assertEqual(decoded.describe(), "loop")

    def test_stop_is_distinguished_from_silence(self) -> None:
        # A STOP flag still names a sound; it means "go quiet", not "no sound
        # was ever set". Consumers need to tell those apart.
        decoded = decode_sound_flags(SOUND_FLAG_STOP)

        self.assertTrue(decoded.is_stop)
        self.assertFalse(decoded.is_looping)

    def test_sync_mask_covers_the_three_sync_bits(self) -> None:
        self.assertEqual(
            SOUND_FLAG_SYNC_MASK,
            SOUND_FLAG_SYNC_MASTER | SOUND_FLAG_SYNC_SLAVE | SOUND_FLAG_SYNC_PENDING,
        )
        self.assertTrue(decode_sound_flags(SOUND_FLAG_SYNC_SLAVE).is_synchronised)
        self.assertFalse(decode_sound_flags(SOUND_FLAG_LOOP).is_synchronised)

    def test_combined_flags(self) -> None:
        decoded = decode_sound_flags(SOUND_FLAG_LOOP | SOUND_FLAG_QUEUE)

        self.assertEqual(decoded.set_flags, ("loop", "queue"))

    def test_unnamed_bits_are_reported(self) -> None:
        decoded = decode_sound_flags(SOUND_FLAG_LOOP | 0x80)

        self.assertEqual(decoded.unknown_bits, 0x80)
        self.assertIn("unknown 0x80", decoded.describe())

    def test_only_a_byte_is_considered(self) -> None:
        self.assertEqual(decode_sound_flags(0x101).raw, 0x01)


class AttachedSoundStateTests(unittest.TestCase):
    def test_stop_flag_counts_as_silent(self) -> None:
        from uuid import UUID

        from vibestorm.viewer3d.scene import AttachedSoundState

        state = AttachedSoundState(
            sound_id=UUID(int=5), owner_id=None, gain=1.0, flags=SOUND_FLAG_STOP
        )

        self.assertTrue(state.is_silent)

    def test_a_playing_loop_is_not_silent(self) -> None:
        from uuid import UUID

        from vibestorm.viewer3d.scene import AttachedSoundState

        state = AttachedSoundState(
            sound_id=UUID(int=5), owner_id=None, gain=1.0, flags=SOUND_FLAG_LOOP
        )

        self.assertFalse(state.is_silent)
        self.assertEqual(state.describe_flags(), "loop")

    def test_null_sound_is_still_silent(self) -> None:
        from uuid import UUID

        from vibestorm.viewer3d.scene import AttachedSoundState

        state = AttachedSoundState(
            sound_id=UUID(int=0), owner_id=None, gain=1.0, flags=SOUND_FLAG_LOOP
        )

        self.assertTrue(state.is_silent)


if __name__ == "__main__":
    unittest.main()
