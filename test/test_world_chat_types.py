"""Tests for chat type naming and the typing notifications.

``ChatFromSimulator`` uses one byte for several unrelated things: how a line
was delivered (whisper/say/shout), whether it came from the region or an
object's owner, and whether it is not a line at all but a typing indicator.
Treating them all as "chat" puts blank rows in the log.

Values are OpenSim's ``ChatTypeEnum``; the test re-parses it from source.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.chat_types import (
    CHAT_TYPE_BROADCAST,
    CHAT_TYPE_NAMES,
    CHAT_TYPE_SAY,
    CHAT_TYPE_SHOUT,
    CHAT_TYPE_START_TYPING,
    CHAT_TYPE_STOP_TYPING,
    CHAT_TYPE_WHISPER,
    chat_type_name,
    is_typing_notification,
)

_ENUM_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "opensim-source"
    / "OpenSim"
    / "Framework"
    / "ChatTypeEnum.cs"
)


class SourcePinTests(unittest.TestCase):
    def test_names_match_the_opensim_enum(self) -> None:
        if not _ENUM_SOURCE.exists():
            self.skipTest("opensim-source not present")
        text = _ENUM_SOURCE.read_text(encoding="utf-8", errors="replace")
        body = text.split("enum ChatTypeEnum", 1)[1].split("}", 1)[0]
        source = {
            int(value, 0): name for name, value in re.findall(r"(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", body)
        }

        self.assertTrue(source, "failed to parse ChatTypeEnum")
        missing = sorted(set(source) - set(CHAT_TYPE_NAMES))
        self.assertEqual(missing, [], f"unnamed chat types: {missing}")
        extra = sorted(set(CHAT_TYPE_NAMES) - set(source))
        self.assertEqual(extra, [], f"chat types OpenSim does not define: {extra}")


class ChatTypeNameTests(unittest.TestCase):
    def test_delivery_types(self) -> None:
        self.assertEqual(chat_type_name(CHAT_TYPE_WHISPER), "whisper")
        self.assertEqual(chat_type_name(CHAT_TYPE_SAY), "say")
        self.assertEqual(chat_type_name(CHAT_TYPE_SHOUT), "shout")

    def test_broadcast_is_the_high_byte_not_a_small_number(self) -> None:
        self.assertEqual(CHAT_TYPE_BROADCAST, 0xFF)
        self.assertEqual(chat_type_name(0xFF), "broadcast")

    def test_obsolete_type_three_is_reported_not_folded_into_say(self) -> None:
        # 3 is a dead second encoding of Say. Silently naming it "say" would
        # hide the fact that something is sending a value nothing sends.
        self.assertEqual(chat_type_name(3), "unknown type 3")

    def test_unknown_type_keeps_its_number(self) -> None:
        self.assertEqual(chat_type_name(200), "unknown type 200")

    def test_typing_notifications_are_identified(self) -> None:
        self.assertTrue(is_typing_notification(CHAT_TYPE_START_TYPING))
        self.assertTrue(is_typing_notification(CHAT_TYPE_STOP_TYPING))
        self.assertFalse(is_typing_notification(CHAT_TYPE_SAY))
        self.assertFalse(is_typing_notification(CHAT_TYPE_SHOUT))


if __name__ == "__main__":
    unittest.main()
