import unittest
from uuid import UUID

from vibestorm.caps.inventory_client import (
    InventoryCapabilityClient,
    InventoryFolderRequest,
    InventoryItemRequest,
    parse_inventory_items_payload,
    parse_inventory_descendents_payload,
)


class InventoryCapabilityClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_inventory_descendents_posts_expected_shape(self) -> None:
        client = InventoryCapabilityClient()

        import vibestorm.caps.inventory_client as inventory_module

        captured: dict[str, object] = {}

        class FakeCapabilityClient:
            def __init__(self, timeout_seconds: float) -> None:
                captured["timeout_seconds"] = timeout_seconds

            async def post_capability_value(
                self,
                url: str,
                payload: dict[str, object],
                *,
                udp_listen_port: int | None = None,
                user_agent: str = "Vibestorm",
            ) -> object:
                captured["url"] = url
                captured["payload"] = payload
                captured["udp_listen_port"] = udp_listen_port
                captured["user_agent"] = user_agent
                return {"folders": []}

        original = inventory_module.CapabilityClient
        inventory_module.CapabilityClient = FakeCapabilityClient  # type: ignore[assignment]
        try:
            result = await client.fetch_inventory_descendents(
                "http://example.invalid/fid2",
                [
                    InventoryFolderRequest(
                        folder_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
                        owner_id=UUID("11111111-2222-3333-4444-555555555555"),
                    )
                ],
                udp_listen_port=37468,
            )
        finally:
            inventory_module.CapabilityClient = original  # type: ignore[assignment]

        self.assertEqual(result, {"folders": []})
        self.assertEqual(captured["url"], "http://example.invalid/fid2")
        self.assertEqual(captured["udp_listen_port"], 37468)
        payload = captured["payload"]
        self.assertEqual(len(payload["folders"]), 1)
        first = payload["folders"][0]
        self.assertEqual(first["folder_id"], UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"))
        self.assertEqual(first["owner_id"], UUID("11111111-2222-3333-4444-555555555555"))
        self.assertTrue(first["fetch_folders"])
        self.assertTrue(first["fetch_items"])
        self.assertEqual(first["sort_order"], 0)

    async def test_fetch_inventory_items_posts_expected_shape(self) -> None:
        client = InventoryCapabilityClient()

        import vibestorm.caps.inventory_client as inventory_module

        captured: dict[str, object] = {}

        class FakeCapabilityClient:
            def __init__(self, timeout_seconds: float) -> None:
                captured["timeout_seconds"] = timeout_seconds

            async def post_capability_value(
                self,
                url: str,
                payload: dict[str, object],
                *,
                udp_listen_port: int | None = None,
                user_agent: str = "Vibestorm",
            ) -> object:
                captured["url"] = url
                captured["payload"] = payload
                captured["udp_listen_port"] = udp_listen_port
                return {"items": []}

        original = inventory_module.CapabilityClient
        inventory_module.CapabilityClient = FakeCapabilityClient  # type: ignore[assignment]
        try:
            result = await client.fetch_inventory_items(
                "http://example.invalid/fi2",
                [InventoryItemRequest(item_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"))],
                udp_listen_port=37468,
            )
        finally:
            inventory_module.CapabilityClient = original  # type: ignore[assignment]

        self.assertEqual(result, {"items": []})
        payload = captured["payload"]
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["item_id"], UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"))

    def test_parse_inventory_descendents_payload_extracts_cof_items(self) -> None:
        snapshot = parse_inventory_descendents_payload(
            {
                "folders": [
                    {
                        "folder_id": "49cb1ed7-e8b2-4de5-84d7-4222f540634c",
                        "categories": [{"category_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "name": "Clothing"}],
                        "items": [],
                    },
                    {
                        "folder_id": "d427dc3a-047a-4b9f-9aaf-15ccce179bf2",
                        "items": [
                            {
                                "item_id": "02385379-afb8-48b3-8848-47c8333fed2d",
                                "asset_id": "1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b",
                                "name": "Shape",
                                "desc": "worn shape",
                                "type": 18,
                                "inv_type": 24,
                            },
                            {
                                "item_id": "a860475e-6234-40b8-b5b1-3df8fb1d3049",
                                "asset_id": "ffc4de4a-9845-41c1-9f9f-762a059d0bdc",
                                "name": "Skin",
                                "type": 18,
                                "inv_type": 24,
                            },
                        ],
                    },
                ],
            },
            inventory_root_folder_id=UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
            current_outfit_folder_id=UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"),
        )

        self.assertEqual(snapshot.folder_count, 2)
        self.assertEqual(snapshot.total_item_count, 2)
        self.assertIsNotNone(snapshot.current_outfit_folder)
        assert snapshot.current_outfit_folder is not None
        self.assertEqual(snapshot.current_outfit_folder.item_count, 2)
        self.assertEqual(snapshot.current_outfit_folder.link_item_count, 2)
        self.assertEqual(snapshot.current_outfit_folder.sample_item_names(), ("Shape", "Skin"))
        self.assertEqual(snapshot.current_outfit_folder.inventory_types, (24,))
        self.assertEqual(
            snapshot.current_outfit_link_targets,
            (
                UUID("1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b"),
                UUID("02385379-afb8-48b3-8848-47c8333fed2d"),
                UUID("ffc4de4a-9845-41c1-9f9f-762a059d0bdc"),
                UUID("a860475e-6234-40b8-b5b1-3df8fb1d3049"),
            ),
        )

    def test_parse_inventory_items_payload_extracts_source_items(self) -> None:
        items = parse_inventory_items_payload(
            {
                "items": [
                    {
                        "item_id": "02385379-afb8-48b3-8848-47c8333fed2d",
                        "asset_id": "1dc1368f-e8fe-f02d-a08d-9d9f11c1af6b",
                        "name": "Default Eyes",
                        "type": 13,
                        "inv_type": 18,
                        "flags": 3,
                    },
                    {
                        "item_id": "a860475e-6234-40b8-b5b1-3df8fb1d3049",
                        "asset_id": "ffc4de4a-9845-41c1-9f9f-762a059d0bdc",
                        "name": "Default Skin",
                        "type": 13,
                        "inv_type": 18,
                        "flags": 1,
                    },
                ]
            }
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].name, "Default Eyes")
        self.assertEqual(items[0].type, 13)
        self.assertEqual(items[0].inv_type, 18)
