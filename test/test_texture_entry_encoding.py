"""Tests for SL TextureEntry face mask encoding used in AgentSetAppearance.

The SL TE format uses MSB-first 7-bit group encoding for face bitmasks, not
standard LEB128. OpenMetaverse decodes via:
    faceBits = (faceBits << 7) | (b & 0x7F)
with bit 7 of each byte as the continuation flag.
"""

import unittest
from uuid import UUID

from vibestorm.udp.session import _encode_face_mask, _build_bake_texture_entry


def _decode_face_mask_msb(data: bytes) -> int:
    """Decode a face mask the way OpenMetaverse does (MSB-first accumulation)."""
    face_bits = 0
    for b in data:
        face_bits = (face_bits << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return face_bits


class FaceMaskEncodingTests(unittest.TestCase):
    def _roundtrip(self, face_index: int) -> int:
        encoded = _encode_face_mask(face_index)
        return _decode_face_mask_msb(encoded)

    def test_face_0_encodes_to_single_byte(self) -> None:
        self.assertEqual(_encode_face_mask(0), bytes([0x01]))

    def test_bake_face_8_encodes_correctly(self) -> None:
        # face 8 = head bake; reference encoding from Firestorm capture
        self.assertEqual(_encode_face_mask(8), bytes([0x82, 0x00]))

    def test_bake_face_9(self) -> None:
        self.assertEqual(_encode_face_mask(9), bytes([0x84, 0x00]))

    def test_bake_face_10(self) -> None:
        self.assertEqual(_encode_face_mask(10), bytes([0x88, 0x00]))

    def test_bake_face_11(self) -> None:
        self.assertEqual(_encode_face_mask(11), bytes([0x90, 0x00]))

    def test_bake_face_20(self) -> None:
        # face 20 = hair bake; three-byte encoding
        self.assertEqual(_encode_face_mask(20), bytes([0xC0, 0x80, 0x00]))

    def test_all_bake_faces_roundtrip(self) -> None:
        bake_indices = [8, 9, 10, 11, 19, 20, 40, 41, 42, 43, 44]
        for face in bake_indices:
            with self.subTest(face=face):
                mask = self._roundtrip(face)
                self.assertEqual(mask, 1 << face,
                    f"face {face}: got mask {mask:#x}, expected {1 << face:#x}")

    def test_build_bake_texture_entry_places_uuids_at_correct_faces(self) -> None:
        """TE built by _build_bake_texture_entry must parse to the right face slots."""
        face_uuids = {
            8: UUID("11111111-1111-1111-1111-111111111111"),
            9: UUID("22222222-2222-2222-2222-222222222222"),
        }
        te = _build_bake_texture_entry(face_uuids, b"")

        # Skip default UUID (16 bytes), then parse entries
        pos = 16
        found: dict[int, str] = {}
        while pos < len(te):
            # Read face mask (MSB-first)
            face_bits = 0
            while pos < len(te):
                b = te[pos]
                pos += 1
                face_bits = (face_bits << 7) | (b & 0x7F)
                if not (b & 0x80):
                    break
            if face_bits == 0:
                break  # null terminator
            uuid_hex = te[pos:pos + 16].hex()
            pos += 16
            for i in range(45):
                if face_bits & (1 << i):
                    found[i] = uuid_hex

        self.assertEqual(found.get(8), face_uuids[8].bytes.hex())
        self.assertEqual(found.get(9), face_uuids[9].bytes.hex())
        self.assertNotIn(10, found)
