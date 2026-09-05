"""ObjectAttach and ObjectDetach, checked against the message template.

Wearing a prim is the last thing in this client's object handling that
``viewer3d/linkset.py`` asserts and nothing has watched: its docstring says
"an attached prim is a child of the avatar", which is the same shape as a
linkset's child and a seated avatar, both of which *were* observed live. These
encoders exist so ``tools/verify_attachment_frame.py`` can go and look.

The header bytes are not spelled out here. ``message_template.msg`` is the
authority on what number a message has, so these read it and compare -- a test
that repeats the constant from the encoder agrees with it however wrong it is.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.messages import encode_object_attach, encode_object_detach
from vibestorm.udp.template import load_template_summaries, template_path

AGENT = UUID("11111111-2222-3333-4444-555555555555")
SESSION = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def _header_for(name: str) -> bytes:
    """The on-the-wire message number for ``name``, from the template.

    Low messages are ``\\xFF\\xFF`` and then a big-endian id; that is the
    encoding, not a fact about any one message, so it is written once here.
    """
    summary = load_template_summaries(template_path(Path.cwd()))[name]
    assert summary.frequency == "Low", f"{name} is {summary.frequency}, not Low"
    return b"\xFF\xFF" + summary.message_number.to_bytes(2, "big")


class ObjectAttachTests(unittest.TestCase):
    def test_it_is_the_message_the_template_names(self) -> None:
        packet = encode_object_attach(AGENT, SESSION, [42])

        self.assertEqual(packet[:4], _header_for("ObjectAttach"))

    def test_the_agent_block_carries_the_attachment_point(self) -> None:
        # AgentData is AgentID, SessionID, AttachmentPoint -- in that order,
        # and the point is a single byte after the two UUIDs.
        packet = encode_object_attach(AGENT, SESSION, [42], attachment_point=6)

        self.assertEqual(packet[4:20], AGENT.bytes)
        self.assertEqual(packet[20:36], SESSION.bytes)
        self.assertEqual(packet[36], 6)

    def test_each_object_carries_a_local_id_and_a_rotation(self) -> None:
        # ObjectData is a variable block: a U8 count, then that many
        # (ObjectLocalID, Rotation) pairs. LLQuaternion is three floats on the
        # wire -- w is recovered as the non-negative root.
        packet = encode_object_attach(
            AGENT, SESSION, [42, 43], rotation=(0.1, 0.2, 0.3)
        )

        self.assertEqual(packet[37], 2)
        first = struct.unpack_from("<Ifff", packet, 38)
        second = struct.unpack_from("<Ifff", packet, 38 + 16)
        self.assertEqual(first[0], 42)
        self.assertEqual(second[0], 43)
        for got, want in zip(first[1:], (0.1, 0.2, 0.3), strict=True):
            self.assertAlmostEqual(got, want, places=6)

    def test_no_rotation_is_the_default(self) -> None:
        packet = encode_object_attach(AGENT, SESSION, [42])

        self.assertEqual(struct.unpack_from("<fff", packet, 42), (0.0, 0.0, 0.0))

    def test_it_refuses_an_empty_list(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_attach(AGENT, SESSION, [])

    def test_it_refuses_an_attachment_point_that_is_not_a_byte(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_attach(AGENT, SESSION, [42], attachment_point=256)

    def test_it_refuses_a_local_id_that_is_not_a_u32(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_attach(AGENT, SESSION, [0x1_0000_0000])

    def test_it_refuses_more_objects_than_the_count_can_hold(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_attach(AGENT, SESSION, list(range(256)))


class ObjectDetachTests(unittest.TestCase):
    def test_it_is_the_message_the_template_names(self) -> None:
        packet = encode_object_detach(AGENT, SESSION, [42])

        self.assertEqual(packet[:4], _header_for("ObjectDetach"))

    def test_it_carries_the_agent_then_the_local_ids(self) -> None:
        # No attachment point on this one: AgentData is the two UUIDs and
        # nothing else, so the count follows immediately.
        packet = encode_object_detach(AGENT, SESSION, [42, 43])

        self.assertEqual(packet[4:20], AGENT.bytes)
        self.assertEqual(packet[20:36], SESSION.bytes)
        self.assertEqual(packet[36], 2)
        self.assertEqual(struct.unpack_from("<II", packet, 37), (42, 43))

    def test_it_refuses_an_empty_list(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_detach(AGENT, SESSION, [])

    def test_it_refuses_a_local_id_that_is_not_a_u32(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_detach(AGENT, SESSION, [-1])


if __name__ == "__main__":
    unittest.main()
