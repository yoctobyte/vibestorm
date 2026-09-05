"""Tests for RemoveTaskInventory, the only way to delete a row from a prim.

Checked against the message template's field order and against the dispatcher,
not against a re-reading of the encoder. This one deletes, so "the bytes land
where the template says" is the whole safety argument: a LocalID written into
the ItemID slot would name some other object's item and remove that instead.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import encode_remove_task_inventory

AGENT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SESSION = UUID("11111111-2222-3333-4444-555555555555")
ITEM = UUID("144bb5bc-95a3-4e1f-ab61-03a5e0480e5d")
LOCAL_ID = 176203873


class RemoveTaskInventoryEncodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def _packet(self, **kwargs: object) -> bytes:
        params: dict[str, object] = {"object_local_id": LOCAL_ID, "item_id": ITEM}
        params.update(kwargs)
        return encode_remove_task_inventory(AGENT, SESSION, **params)  # type: ignore[arg-type]

    def test_dispatches_as_remove_task_inventory(self) -> None:
        self.assertEqual(
            self.dispatcher.dispatch(self._packet()).summary.name, "RemoveTaskInventory"
        )

    def test_low_287_header(self) -> None:
        self.assertTrue(self._packet().startswith(b"\xFF\xFF\x01\x1F"))

    def test_field_order_matches_the_template(self) -> None:
        # AgentData { AgentID, SessionID }, then InventoryData { LocalID, ItemID }.
        body = self.dispatcher.dispatch(self._packet()).body
        self.assertEqual(UUID(bytes=body[0:16]), AGENT)
        self.assertEqual(UUID(bytes=body[16:32]), SESSION)
        self.assertEqual(int.from_bytes(body[32:36], "little"), LOCAL_ID)
        self.assertEqual(UUID(bytes=body[36:52]), ITEM)

    def test_the_body_is_exactly_the_two_blocks(self) -> None:
        # Both blocks are Single and fixed width, so any trailing byte means a
        # field was written that the template does not declare.
        self.assertEqual(len(self.dispatcher.dispatch(self._packet()).body), 52)

    def test_a_zero_item_id_is_refused(self) -> None:
        # A zero here is not "no item"; it is a real lookup that could match
        # whatever the simulator does with an unset id. Refuse to send it.
        with self.assertRaises(ValueError):
            self._packet(item_id=UUID(int=0))

    def test_an_out_of_range_local_id_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._packet(object_local_id=0x1_0000_0000)


if __name__ == "__main__":
    unittest.main()
