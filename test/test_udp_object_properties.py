"""ObjectProperties: the long form of an object's properties.

The simulator sends this for an object that has been selected, and this client
had no parser for it -- 52 of them arrived over the recorded sessions and every
one was decoded to a name and thrown away. It carries three things that are in
no other message:

- **the creator**, which `ObjectPropertiesFamily` omits. "Extract all its
  internals" is not complete without who made it.
- **the inventory serial**, which goes up whenever anything in the prim's
  inventory changes -- so a sync can tell whether there is anything to fetch
  without fetching it.
- **where a rezzed object came from**: item, folder and task.

The layout under test is `message_template.msg`'s and nothing else. These
build a body to that spec and read it back; `tools/verify_object_properties.py`
is the other half, against a real simulator.
"""

from __future__ import annotations

import struct
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from vibestorm.udp.messages import MessageDecodeError, parse_object_properties
from vibestorm.udp.template import (
    MessageDispatch,
    decode_message_number,
    load_template_summaries,
    template_path,
)

SUMMARIES = load_template_summaries(template_path(Path.cwd()))


def _dispatch(body: bytes) -> MessageDispatch:
    """Wrap a body as the dispatcher would, using the template's own numbering."""
    summary = SUMMARIES["ObjectProperties"]
    header = b"\xFF" + bytes([summary.message_number])
    return MessageDispatch(
        summary=summary,
        message_number=decode_message_number(header),
        body=body,
    )


def _string(value: str) -> bytes:
    """A ``Variable 1`` field: a one-byte length, then the bytes."""
    payload = value.encode("utf-8") + b"\x00"
    return bytes([len(payload)]) + payload


def _block(
    *,
    object_id: UUID = UUID(int=1),
    creator_id: UUID = UUID(int=2),
    owner_id: UUID = UUID(int=3),
    group_id: UUID = UUID(int=4),
    creation_date: int = 1_700_000_000,
    masks: tuple[int, int, int, int, int] = (0x7FFFFFFF, 0x7FFFFFFF, 0, 0, 0x82000),
    ownership_cost: int = 0,
    sale_type: int = 0,
    sale_price: int = 0,
    category: int = 0,
    inventory_serial: int = 7,
    item_id: UUID = UUID(int=5),
    folder_id: UUID = UUID(int=6),
    from_task_id: UUID = UUID(int=7),
    last_owner_id: UUID = UUID(int=8),
    name: str = "cube with script in inventory",
    description: str = "",
    touch_name: str = "Touch",
    sit_name: str = "Sit here",
    texture_ids: tuple[UUID, ...] = (UUID(int=9), UUID(int=10)),
) -> bytes:
    return (
        object_id.bytes
        + creator_id.bytes
        + owner_id.bytes
        + group_id.bytes
        + struct.pack("<Q", creation_date)
        + struct.pack("<5I", *masks)
        + struct.pack("<i", ownership_cost)
        + bytes([sale_type])
        + struct.pack("<i", sale_price)
        + bytes([13, 14, 15])  # the three aggregate-permission bytes
        + struct.pack("<I", category)
        + struct.pack("<h", inventory_serial)
        + item_id.bytes
        + folder_id.bytes
        + from_task_id.bytes
        + last_owner_id.bytes
        + _string(name)
        + _string(description)
        + _string(touch_name)
        + _string(sit_name)
        + bytes([len(texture_ids) * 16])
        + b"".join(t.bytes for t in texture_ids)
    )


class ObjectPropertiesTests(unittest.TestCase):
    def test_it_reads_the_creator_the_family_reply_does_not_carry(self) -> None:
        parsed = parse_object_properties(_dispatch(bytes([1]) + _block()))

        self.assertEqual(parsed.objects[0].creator_id, UUID(int=2))

    def test_it_reads_the_inventory_serial(self) -> None:
        # The reason to want this message for the sync track: it says whether
        # the prim's contents changed without fetching them.
        parsed = parse_object_properties(_dispatch(bytes([1]) + _block(inventory_serial=42)))

        self.assertEqual(parsed.objects[0].inventory_serial, 42)

    def test_an_inventory_serial_is_signed(self) -> None:
        # S16 in the template, not U16. A prim edited enough times wraps
        # negative, and reading it unsigned makes 65535 out of -1.
        parsed = parse_object_properties(_dispatch(bytes([1]) + _block(inventory_serial=-1)))

        self.assertEqual(parsed.objects[0].inventory_serial, -1)

    def test_the_creation_date_is_microseconds(self) -> None:
        # The template says only U64. The unit came off a real one: a prim
        # rezzed at 2026-09-05 23:09:36 UTC reported 1788649776000000, which is
        # that instant in microseconds and, as seconds or milliseconds, tens of
        # thousands of years away.
        parsed = parse_object_properties(
            _dispatch(bytes([1]) + _block(creation_date=1_788_649_776_000_000))
        )

        self.assertEqual(
            parsed.objects[0].created_at,
            datetime(2026, 9, 5, 23, 9, 36, tzinfo=UTC),
        )

    def test_no_creation_date_reads_as_no_date_not_1970(self) -> None:
        parsed = parse_object_properties(_dispatch(bytes([1]) + _block(creation_date=0)))

        self.assertIsNone(parsed.objects[0].created_at)

    def test_it_reads_the_permission_masks_in_order(self) -> None:
        parsed = parse_object_properties(
            _dispatch(bytes([1]) + _block(masks=(1, 2, 3, 4, 5)))
        )
        entry = parsed.objects[0]

        self.assertEqual(
            (
                entry.base_mask,
                entry.owner_mask,
                entry.group_mask,
                entry.everyone_mask,
                entry.next_owner_mask,
            ),
            (1, 2, 3, 4, 5),
        )

    def test_it_reads_all_four_strings(self) -> None:
        # Four in a row, each with its own length byte. Miscounting one slides
        # every field after it, and the texture blob then reads as garbage.
        parsed = parse_object_properties(
            _dispatch(
                bytes([1])
                + _block(name="a", description="bb", touch_name="ccc", sit_name="dddd")
            )
        )
        entry = parsed.objects[0]

        self.assertEqual(
            (entry.name, entry.description, entry.touch_name, entry.sit_name),
            ("a", "bb", "ccc", "dddd"),
        )

    def test_the_texture_field_is_uuids_end_to_end_not_a_string(self) -> None:
        parsed = parse_object_properties(
            _dispatch(bytes([1]) + _block(texture_ids=(UUID(int=9), UUID(int=10))))
        )

        self.assertEqual(parsed.objects[0].texture_ids, (UUID(int=9), UUID(int=10)))

    def test_a_prim_with_no_faces_reported_has_no_textures(self) -> None:
        parsed = parse_object_properties(_dispatch(bytes([1]) + _block(texture_ids=())))

        self.assertEqual(parsed.objects[0].texture_ids, ())

    def test_it_reads_every_block_in_a_multi_object_message(self) -> None:
        # ObjectData is Variable: selecting a linkset answers with one block
        # per prim, and reading only the first loses the rest.
        body = bytes([2]) + _block(object_id=UUID(int=100)) + _block(object_id=UUID(int=200))

        parsed = parse_object_properties(_dispatch(body))

        self.assertEqual(
            [entry.object_id for entry in parsed.objects],
            [UUID(int=100), UUID(int=200)],
        )

    def test_an_empty_body_is_an_error_not_an_empty_answer(self) -> None:
        with self.assertRaises(MessageDecodeError):
            parse_object_properties(_dispatch(b""))

    def test_a_truncated_block_is_an_error(self) -> None:
        with self.assertRaises(MessageDecodeError):
            parse_object_properties(_dispatch(bytes([1]) + _block()[:100]))

    def test_a_count_larger_than_the_body_is_an_error(self) -> None:
        with self.assertRaises(MessageDecodeError):
            parse_object_properties(_dispatch(bytes([4]) + _block()))

    def test_it_refuses_a_different_message(self) -> None:
        summary = SUMMARIES["ObjectPropertiesFamily"]
        header = b"\xFF" + bytes([summary.message_number])
        wrong = MessageDispatch(
            summary=summary, message_number=decode_message_number(header), body=b""
        )

        with self.assertRaises(MessageDecodeError):
            parse_object_properties(wrong)


if __name__ == "__main__":
    unittest.main()
