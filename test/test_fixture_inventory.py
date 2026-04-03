import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID
from struct import pack

from vibestorm.fixtures.inventory import build_fixture_inventory, write_fixture_inventory


class FixtureInventoryTests(unittest.TestCase):
    def test_build_fixture_inventory_classifies_rich_object_update(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "ObjectUpdate"
            target.mkdir(parents=True, exist_ok=True)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            texture_id = UUID("11223344-0000-0000-0000-000000000000")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (64).to_bytes(2, "little")
                + texture_id.bytes
                + (b"\x00" * 48)
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            (target / "001-seq000001.body.bin").write_bytes(body)
            (target / "001-seq000001.json").write_text(
                json.dumps(
                    {
                        "message_name": "ObjectUpdate",
                        "sequence": 1,
                        "at_seconds": 1.0,
                        "capture_reason": "world.object_update_rich",
                    },
                ),
                encoding="utf-8",
            )

            inventory = build_fixture_inventory(root)

            self.assertEqual(inventory["capture_count"], 1)
            self.assertEqual(inventory["captures"][0]["object_update"]["texture_entry_size"], 64)
            self.assertEqual(
                inventory["captures"][0]["object_update"]["default_texture_id"],
                "11223344-0000-0000-0000-000000000000",
            )
            self.assertEqual(inventory["backlog"][0]["key"], "object-update-rich-tail")

    def test_write_fixture_inventory_outputs_index_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            path = write_fixture_inventory(Path(tmpdir))
            self.assertTrue(path.exists())
