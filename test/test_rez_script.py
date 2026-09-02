"""Tests for RezScript, the create half of object script sync.

The encoder is checked against the message template's field order and against
what `Scene.RezScript` branches on, not against a re-reading of the encoder.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import DEFAULT_SCRIPT_ASSET_ID, encode_rez_script

AGENT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SESSION = UUID("11111111-2222-3333-4444-555555555555")
PART = UUID("d7f47f7e-4328-4d17-a665-19feaec7b1e9")


class RezScriptEncodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def _packet(self, **kwargs: object) -> bytes:
        params: dict[str, object] = {
            "part_id": PART,
            "object_local_id": 176203873,
            "name": "sync.lsl",
        }
        params.update(kwargs)
        return encode_rez_script(AGENT, SESSION, **params)  # type: ignore[arg-type]

    def test_dispatches_as_rez_script(self) -> None:
        dispatched = self.dispatcher.dispatch(self._packet())
        self.assertEqual(dispatched.summary.name, "RezScript")

    def test_low_304_header(self) -> None:
        self.assertTrue(self._packet().startswith(b"\xFF\xFF\x01\x30"))

    def test_item_id_is_zero_which_is_what_selects_rez_new_script(self) -> None:
        """`Scene.RezScript` branches on `itemBase.ID.IsZero()`.

        A non-zero id means "copy this out of agent inventory" instead, so the
        zero is the entire difference between creating and copying.
        """
        body = self.dispatcher.dispatch(self._packet()).body
        item_id = body[53:69]  # after AgentData (48) + UpdateBlock (5)
        self.assertEqual(UUID(bytes=item_id), UUID(int=0))

    def test_part_id_travels_in_the_folder_id_field(self) -> None:
        """`RezNewScript` calls `GetSceneObjectPart(itemBase.Folder)`.

        OpenSim's own comment on that line is "The part ID is the folder ID!".
        A real folder id here finds no part and the call does nothing at all.
        """
        body = self.dispatcher.dispatch(self._packet()).body
        self.assertEqual(UUID(bytes=body[69:85]), PART)

    def test_agent_and_session_lead_the_body(self) -> None:
        body = self.dispatcher.dispatch(self._packet()).body
        self.assertEqual(UUID(bytes=body[0:16]), AGENT)
        self.assertEqual(UUID(bytes=body[16:32]), SESSION)
        self.assertEqual(UUID(bytes=body[32:48]), UUID(int=0), "GroupID")

    def test_object_local_id_is_in_the_update_block(self) -> None:
        body = self.dispatcher.dispatch(self._packet(object_local_id=4242)).body
        self.assertEqual(int.from_bytes(body[48:52], "little"), 4242)

    def test_name_and_description_are_nul_terminated_variable_fields(self) -> None:
        packet = self._packet(name="a.lsl", description="hi")
        self.assertIn(b"\x06a.lsl\x00", packet)
        self.assertIn(b"\x03hi\x00", packet)

    def test_new_script_info_variable_block_carries_its_count(self) -> None:
        """A Variable block needs its u8 count even when empty.

        The packet is deserialised in full before the handler runs, so a block
        the deserialiser expects and cannot find makes the whole packet
        malformed. Dispatching it is the check.
        """
        self.assertEqual(self._packet()[-1], 0)

    def test_over_long_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._packet(name="x" * 300)

    def test_out_of_range_local_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._packet(object_local_id=2**32)

    def test_default_script_asset_id_matches_opensim_constants(self) -> None:
        """Pinned to `Constants.DefaultScriptID`.

        A freshly created row points at this asset, so it is how a caller tells
        "our create made this row" from "this row was already here".
        """
        self.assertEqual(str(DEFAULT_SCRIPT_ASSET_ID), "2074003b-8d5f-40e5-8b20-581c1c50aedb")


if __name__ == "__main__":
    unittest.main()
