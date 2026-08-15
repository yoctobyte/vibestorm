"""Tests for CreateInventoryItem and its reply.

This is the first half of creating a notecard or script. The second half is
`UpdateNotecardAgentInventory`; neither works alone. `NewFileAgentInventory` —
the capability this project reached for first — does not handle notecards at
all and silently stores them as asset type 0, a texture.
"""

import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    encode_create_inventory_item,
    parse_update_create_inventory_item,
)
from vibestorm.udp.template import dispatch_message

_ROOT = Path(__file__).resolve().parents[1]
_INVENTORY_ACCESS = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "CoreModules" / "Framework"
    / "InventoryAccess" / "InventoryAccessModule.cs"
)
_CONSTANTS = _ROOT / "opensim-source" / "OpenSim" / "Framework" / "Constants.cs"

_AGENT = UUID("11111111-1111-1111-1111-111111111111")
_SESSION = UUID("22222222-2222-2222-2222-222222222222")
_FOLDER = UUID("33333333-3333-3333-3333-333333333333")

#: OpenSim's `Constants.EmptyNotecardID`, observed live as the asset a freshly
#: created notecard points at before any content is uploaded.
EMPTY_NOTECARD_ID = UUID("4b6a777d-7bcd-4fc4-b06f-929b21e32925")


class SourcePinTests(unittest.TestCase):
    def test_a_new_notecard_points_at_the_shared_empty_asset(self) -> None:
        if not _INVENTORY_ACCESS.exists():
            self.skipTest("opensim-source not present")
        text = _INVENTORY_ACCESS.read_text(encoding="utf-8", errors="replace")

        self.assertIn("case (sbyte)AssetType.Notecard:", text)
        self.assertIn("Constants.EmptyNotecardID", text)

    def test_the_empty_notecard_id_is_the_one_the_sim_returned(self) -> None:
        # Live on 2026-08-15 a created notecard came back pointing at exactly
        # this asset. It is also what caught the UUID byte order being wrong:
        # the parser first produced 7d776a4b-cd7b-c44f-..., the same bytes in
        # the other order, which is only obviously wrong next to a known value.
        if not _CONSTANTS.exists():
            self.skipTest("opensim-source not present")
        text = _CONSTANTS.read_text(encoding="utf-8", errors="replace")

        self.assertIn(f'EmptyNotecard = "{EMPTY_NOTECARD_ID}"', text)

    def test_a_non_zero_transaction_id_takes_a_different_path(self) -> None:
        # Checked before the type switch, so a non-zero transaction id means
        # the notecard branch is never reached.
        if not _INVENTORY_ACCESS.exists():
            self.skipTest("opensim-source not present")
        text = _INVENTORY_ACCESS.read_text(encoding="utf-8", errors="replace")

        self.assertIn("HandleItemCreationFromTransaction", text)
        self.assertIn("transactionID.IsNotZero()", text)


class EncodeTests(unittest.TestCase):
    def _encoded(self, **kwargs) -> bytes:
        params = dict(
            name="note", asset_type=7, inv_type=7, description="d"
        )
        params.update(kwargs)
        return encode_create_inventory_item(_AGENT, _SESSION, _FOLDER, **params)

    def test_it_encodes_the_right_message(self) -> None:
        index = MessageDispatcher.from_repo_root(_ROOT).index

        summary = dispatch_message(self._encoded(), index).summary

        self.assertEqual(summary.name, "CreateInventoryItem")

    def test_the_transaction_id_defaults_to_zero(self) -> None:
        # Load-bearing: a non-zero value routes the request to the legacy
        # asset-transaction path and the notecard is never created.
        body = self._encoded()
        offset = 4 + 16 + 16 + 4 + 16

        self.assertEqual(body[offset : offset + 16], b"\x00" * 16)

    def test_asset_type_and_inv_type_are_kept_separate(self) -> None:
        # They agree for notecards (7/7) and disagree for others, so deriving
        # one from the other would be right until it silently was not.
        body = self._encoded(asset_type=21, inv_type=20)
        offset = 4 + 16 + 16 + 4 + 16 + 16 + 4

        self.assertEqual(body[offset], 21)
        self.assertEqual(body[offset + 1], 20)

    def test_names_are_length_prefixed_and_nul_terminated(self) -> None:
        body = self._encoded(name="hi", description="yo")

        self.assertIn(b"\x03hi\x00\x03yo\x00", body)

    def test_an_oversized_name_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "name"):
            self._encoded(name="x" * 300)

    def test_an_out_of_range_type_raises(self) -> None:
        # S8, so 128 does not fit and would wrap to a different type.
        with self.assertRaisesRegex(ValueError, "asset_type"):
            self._encoded(asset_type=128)


def _reply(*, item_id: UUID, asset_id: UUID, approved: bool = True,
           asset_type: int = 7, inv_type: int = 7, name: str = "note",
           callback_id: int = 1) -> bytes:
    """An UpdateCreateInventoryItem body with one item block."""
    name_b = name.encode() + b"\x00"
    return (
        _AGENT.bytes
        + bytes([1 if approved else 0])
        + UUID(int=0).bytes
        + bytes([1])  # one InventoryData block
        + item_id.bytes
        + _FOLDER.bytes
        + pack("<I", callback_id)
        + UUID(int=0).bytes * 3  # Creator, Owner, Group
        + pack("<5I", 0, 0, 0, 0, 0)  # Base/Owner/Group/Everyone/NextOwner
        + bytes([0])  # GroupOwned
        + asset_id.bytes
        + pack("<bb", asset_type, inv_type)
        + pack("<I", 0)  # Flags
        + bytes([0])  # SaleType
        + pack("<i", 0)  # SalePrice
        + bytes([len(name_b)]) + name_b
        + bytes([1]) + b"\x00"  # Description
        + pack("<i", 0)  # CreationDate
        + pack("<I", 0)  # CRC
    )


class ParseTests(unittest.TestCase):
    def _parse(self, body: bytes):
        index = MessageDispatcher.from_repo_root(_ROOT).index
        return parse_update_create_inventory_item(
            dispatch_message(b"\xFF\xFF\x01\x0B" + body, index)
        )

    def test_the_item_and_asset_ids_are_read(self) -> None:
        item_id = UUID("5b023ce0-b496-454d-97b7-0284623f4285")
        parsed = self._parse(_reply(item_id=item_id, asset_id=EMPTY_NOTECARD_ID))
        (item,) = parsed.items

        self.assertEqual(item.item_id, item_id)
        self.assertEqual(item.asset_id, EMPTY_NOTECARD_ID)

    def test_uuids_are_read_big_endian(self) -> None:
        """The bug this parser shipped with, and how it showed itself.

        Reading these as little-endian produces a well-formed UUID with the
        first three groups byte-reversed. Nothing downstream rejects it — the
        update capability simply reports "Failed to update inventory item
        asset", because the item id addresses nothing. It was only visible
        because the asset id had a known correct value to disagree with.
        """
        parsed = self._parse(
            _reply(item_id=UUID(int=7), asset_id=EMPTY_NOTECARD_ID)
        )

        self.assertEqual(parsed.items[0].asset_id, EMPTY_NOTECARD_ID)
        self.assertNotEqual(
            parsed.items[0].asset_id,
            UUID(bytes_le=EMPTY_NOTECARD_ID.bytes),
        )

    def test_the_callback_id_is_carried_through(self) -> None:
        # The only thing tying a reply to the request that caused it.
        parsed = self._parse(
            _reply(item_id=UUID(int=1), asset_id=UUID(int=2), callback_id=99)
        )

        self.assertEqual(parsed.items[0].callback_id, 99)

    def test_the_stored_types_are_reported(self) -> None:
        parsed = self._parse(
            _reply(item_id=UUID(int=1), asset_id=UUID(int=2),
                   asset_type=7, inv_type=7)
        )

        self.assertEqual((parsed.items[0].asset_type, parsed.items[0].inv_type), (7, 7))

    def test_rejection_is_not_reported_as_success(self) -> None:
        # The item block is present either way.
        parsed = self._parse(
            _reply(item_id=UUID(int=1), asset_id=UUID(int=2), approved=False)
        )

        self.assertFalse(parsed.sim_approved)
        self.assertEqual(len(parsed.items), 1)

    def test_the_name_is_read_without_its_terminator(self) -> None:
        parsed = self._parse(
            _reply(item_id=UUID(int=1), asset_id=UUID(int=2), name="my note")
        )

        self.assertEqual(parsed.items[0].name, "my note")

    def test_a_wrong_message_raises(self) -> None:
        index = MessageDispatcher.from_repo_root(_ROOT).index
        other = dispatch_message(b"\xFF\xFF\x01\x31" + b"\x00" * 80, index)

        with self.assertRaises(ValueError):
            parse_update_create_inventory_item(other)


if __name__ == "__main__":
    unittest.main()
