import unittest
from uuid import UUID

from vibestorm.world.texture_entry import TextureEntryDecodeError, parse_texture_entry


class TextureEntryTests(unittest.TestCase):
    def test_parse_default_texture_id(self) -> None:
        default_id = UUID("11111111-1111-1111-1111-111111111111")

        entry = parse_texture_entry(default_id.bytes)

        assert entry is not None
        self.assertEqual(entry.default_texture_id, default_id)
        self.assertEqual(entry.face_texture_ids, ())
        self.assertEqual(entry.texture_for_face(3), default_id)

    def test_parse_face_texture_overrides(self) -> None:
        default_id = UUID("11111111-1111-1111-1111-111111111111")
        face_id = UUID("22222222-2222-2222-2222-222222222222")
        # Face 8 uses the same MSB-first mask encoding pinned by the
        # AgentSetAppearance texture-entry tests.
        payload = default_id.bytes + bytes([0x82, 0x00]) + face_id.bytes + b"\x00"

        entry = parse_texture_entry(payload)

        assert entry is not None
        self.assertEqual(entry.face_texture_ids, ((8, face_id),))
        self.assertEqual(entry.texture_for_face(0), default_id)
        self.assertEqual(entry.texture_for_face(8), face_id)

    def test_parse_multi_face_mask_assigns_all_faces(self) -> None:
        default_id = UUID("11111111-1111-1111-1111-111111111111")
        face_id = UUID("33333333-3333-3333-3333-333333333333")

        entry = parse_texture_entry(default_id.bytes + bytes([0x03]) + face_id.bytes)

        assert entry is not None
        self.assertEqual(entry.face_texture_ids, ((0, face_id), (1, face_id)))

    def test_truncated_default_raises(self) -> None:
        with self.assertRaises(TextureEntryDecodeError):
            parse_texture_entry(b"\x00" * 15)

    def test_truncated_face_uuid_raises(self) -> None:
        default_id = UUID("11111111-1111-1111-1111-111111111111")

        with self.assertRaises(TextureEntryDecodeError):
            parse_texture_entry(default_id.bytes + b"\x01" + b"\x00" * 15)


if __name__ == "__main__":
    unittest.main()
