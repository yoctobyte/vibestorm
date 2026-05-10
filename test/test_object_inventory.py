import unittest
from uuid import UUID

from vibestorm.world.object_inventory import parse_task_inventory_text


class ObjectInventoryTests(unittest.TestCase):
    def test_parse_task_inventory_text_extracts_items(self) -> None:
        snapshot = parse_task_inventory_text(
            """
inv_item 0
{
    item_id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
    parent_id bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
    asset_id cccccccc-cccc-4ccc-8ccc-cccccccccccc
    type notecard
    inv_type notecard
    name Read Me|
    desc A useful note|
}
""",
            local_id=42,
            task_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            serial=3,
            filename="task.inv",
        )

        self.assertEqual(snapshot.local_id, 42)
        self.assertEqual(snapshot.item_count, 1)
        item = snapshot.items[0]
        self.assertEqual(item.name, "Read Me")
        self.assertEqual(item.description, "A useful note")
        self.assertEqual(item.asset_type, "notecard")
        self.assertEqual(item.inventory_type, "notecard")
        self.assertEqual(item.item_id, UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"))


if __name__ == "__main__":
    unittest.main()
