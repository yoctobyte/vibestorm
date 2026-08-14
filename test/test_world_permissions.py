"""Tests for permission mask decoding.

Bit values come from OpenSim's ``PermissionMask`` (OpenSim/Framework/Util.cs).
They are non-contiguous — the real rights start at bit 13 and skip 17 and 18 —
so these pin each name to its numeric bit rather than only checking that some
name comes out.
"""

import unittest

from vibestorm.world.permissions import (
    FOLDED_COPY,
    PERM_ALL,
    PERM_COPY,
    PERM_EXPORT,
    PERM_MODIFY,
    PERM_MOVE,
    PERM_SLAM,
    PERM_TRANSFER,
    decode_permissions,
)


class PermissionBitValueTests(unittest.TestCase):
    def test_bit_values_match_opensim(self) -> None:
        self.assertEqual(PERM_TRANSFER, 0x00002000)
        self.assertEqual(PERM_MODIFY, 0x00004000)
        self.assertEqual(PERM_COPY, 0x00008000)
        self.assertEqual(PERM_EXPORT, 0x00010000)
        self.assertEqual(PERM_MOVE, 0x00080000)

    def test_all_excludes_export(self) -> None:
        # OpenSim's own comment: Export is special and must be granted
        # explicitly, so it is not part of All.
        self.assertEqual(PERM_ALL & PERM_EXPORT, 0)
        self.assertEqual(PERM_ALL, PERM_COPY | PERM_MODIFY | PERM_TRANSFER | PERM_MOVE)


class DecodePermissionsTests(unittest.TestCase):
    def test_full_permissions_name_every_right(self) -> None:
        decoded = decode_permissions(PERM_ALL)

        self.assertEqual(set(decoded.granted), {"copy", "modify", "transfer", "move"})
        self.assertTrue(decoded.is_full)
        self.assertEqual(decoded.unknown_bits, 0)

    def test_no_permissions_reads_as_none(self) -> None:
        decoded = decode_permissions(0)

        self.assertEqual(decoded.granted, ())
        self.assertFalse(decoded.is_full)
        self.assertEqual(decoded.describe(), "none")

    def test_copy_only_does_not_imply_the_others(self) -> None:
        decoded = decode_permissions(PERM_COPY)

        self.assertEqual(decoded.granted, ("copy",))
        self.assertFalse(decoded.is_full)

    def test_folded_permissions_are_kept_separate(self) -> None:
        # The low nibble is a different encoding of the same rights; mixing it
        # into `granted` would report a copy right the object does not have.
        decoded = decode_permissions(FOLDED_COPY)

        self.assertEqual(decoded.granted, ())
        self.assertEqual(decoded.folded, ("copy",))

    def test_slam_bit_is_reported_not_swallowed(self) -> None:
        decoded = decode_permissions(PERM_SLAM)

        self.assertTrue(decoded.slam)
        self.assertEqual(decoded.unknown_bits, 0)

    def test_unrecognised_bits_survive_as_unknown(self) -> None:
        # Dropping them silently would be worse than the raw hex this replaced.
        decoded = decode_permissions(PERM_COPY | (1 << 17))

        self.assertEqual(decoded.granted, ("copy",))
        self.assertEqual(decoded.unknown_bits, 1 << 17)
        self.assertIn("unknown", decoded.describe())

    def test_describe_lists_rights_in_copy_modify_transfer_order(self) -> None:
        decoded = decode_permissions(PERM_TRANSFER | PERM_COPY | PERM_MODIFY)

        self.assertEqual(decoded.describe(), "copy, modify, transfer")


class InspectorPermissionRowTests(unittest.TestCase):
    def test_all_five_masks_render_with_names_and_hex(self) -> None:
        from vibestorm.viewer3d.hud import _permission_lines

        class _Props:
            base_mask = PERM_ALL
            owner_mask = PERM_ALL
            group_mask = 0
            everyone_mask = PERM_COPY
            next_owner_mask = PERM_TRANSFER

        rows = _permission_lines(_Props())

        self.assertEqual(len(rows), 5)
        self.assertIn("Base Perms: copy, modify, transfer, move (0x0008e000)", rows[0])
        self.assertIn("Group Perms: none", rows[2])
        self.assertIn("Everyone Perms: copy", rows[3])
        self.assertIn("Next Owner Perms: transfer", rows[4])

    def test_missing_attributes_do_not_raise(self) -> None:
        from vibestorm.viewer3d.hud import _permission_lines

        self.assertEqual(len(_permission_lines(object())), 5)


if __name__ == "__main__":
    unittest.main()
