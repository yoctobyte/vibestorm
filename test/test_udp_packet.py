import unittest

from vibestorm.udp.packet import (
    LL_ACK_FLAG,
    LL_RELIABLE_FLAG,
    LL_ZERO_CODE_FLAG,
    PacketError,
    build_packet,
    parse_packet_header,
    split_packet,
)


class PacketParsingTests(unittest.TestCase):
    def test_parse_packet_header(self) -> None:
        packet = bytes([LL_ZERO_CODE_FLAG | LL_RELIABLE_FLAG, 0, 0, 0, 7, 2, 0xAA, 0xBB, 0x21])
        header = parse_packet_header(packet)
        self.assertTrue(header.is_zero_coded)
        self.assertTrue(header.is_reliable)
        self.assertEqual(header.sequence, 7)
        self.assertEqual(header.extra_header_length, 2)
        self.assertEqual(header.message_offset, 8)

    def test_split_packet_extracts_appended_acks(self) -> None:
        packet = bytes(
            [
                LL_ACK_FLAG,
                0,
                0,
                0,
                9,
                0,
                0x44,
                0,
                0,
                0,
                1,
                0,
                0,
                0,
                2,
                2,
            ],
        )
        view = split_packet(packet)
        self.assertEqual(view.header.sequence, 9)
        self.assertEqual(view.message, b"\x44")
        self.assertEqual(view.appended_acks, (1, 2))

    def test_split_packet_rejects_too_short(self) -> None:
        with self.assertRaises(PacketError):
            split_packet(b"\x00\x00")

    def test_build_packet_round_trips(self) -> None:
        packet = build_packet(b"\x44", sequence=9, flags=LL_RELIABLE_FLAG, appended_acks=(1, 2))
        view = split_packet(packet)
        self.assertEqual(view.header.sequence, 9)
        self.assertTrue(view.header.is_reliable)
        self.assertEqual(view.message, b"\x44")
        self.assertEqual(view.appended_acks, (1, 2))
