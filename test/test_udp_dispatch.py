import unittest
from pathlib import Path

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.template import (
    build_template_index,
    decode_message_number,
    dispatch_message,
    template_path,
)


class MessageNumberDecodeTests(unittest.TestCase):
    def test_decode_high_frequency_number(self) -> None:
        decoded = decode_message_number(bytes([0x02, 0xAA]))
        self.assertEqual(decoded.frequency, "High")
        self.assertEqual(decoded.message_number, 0x02)
        self.assertEqual(decoded.encoded_length, 1)

    def test_decode_medium_frequency_number(self) -> None:
        decoded = decode_message_number(bytes([0xFF, 0x02, 0xAA]))
        self.assertEqual(decoded.frequency, "Medium")
        self.assertEqual(decoded.message_number, 0xFF02)
        self.assertEqual(decoded.encoded_length, 2)

    def test_decode_low_frequency_number(self) -> None:
        decoded = decode_message_number(bytes([0xFF, 0xFF, 0x00, 0x03, 0xAA]))
        self.assertEqual(decoded.frequency, "Low")
        self.assertEqual(decoded.message_number, 0xFFFF0003)
        self.assertEqual(decoded.encoded_length, 4)

    def test_decode_fixed_frequency_number(self) -> None:
        decoded = decode_message_number(bytes([0xFF, 0xFF, 0xFF, 0xFB, 0xAA]))
        self.assertEqual(decoded.frequency, "Fixed")
        self.assertEqual(decoded.message_number, 0xFFFFFFFB)
        self.assertEqual(decoded.encoded_length, 4)


class MessageDispatchTests(unittest.TestCase):
    def test_dispatch_packet_ack(self) -> None:
        index = build_template_index(template_path(Path.cwd()))
        dispatched = dispatch_message(bytes([0xFF, 0xFF, 0xFF, 0xFB, 0x01, 0x02]), index)
        self.assertEqual(dispatched.summary.name, "PacketAck")
        self.assertEqual(dispatched.summary.frequency, "Fixed")
        self.assertEqual(dispatched.body, b"\x01\x02")

    def test_dispatch_use_circuit_code(self) -> None:
        index = build_template_index(template_path(Path.cwd()))
        dispatched = dispatch_message(bytes([0xFF, 0xFF, 0x00, 0x03, 0x99]), index)
        self.assertEqual(dispatched.summary.name, "UseCircuitCode")
        self.assertEqual(dispatched.message_number.frequency, "Low")
        self.assertEqual(dispatched.body, b"\x99")

    def test_dispatcher_helper_works_from_repo_root(self) -> None:
        dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        dispatched = dispatcher.dispatch(bytes([0x02, 0x01]))
        self.assertEqual(dispatched.summary.name, "CompletePingCheck")
