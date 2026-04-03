import unittest

from vibestorm.udp.packet import LL_ZERO_CODE_FLAG
from vibestorm.udp.zerocode import decode_zerocode, encode_zerocode


class ZerocodeTests(unittest.TestCase):
    def test_encode_then_decode_round_trips(self) -> None:
        packet = bytes([0x00, 0, 0, 0, 1, 0, 0x11, 0x00, 0x00, 0x22, 0x00, 0x33])
        encoded = encode_zerocode(packet)

        self.assertTrue(encoded[0] & LL_ZERO_CODE_FLAG)
        self.assertEqual(decode_zerocode(encoded), packet)
