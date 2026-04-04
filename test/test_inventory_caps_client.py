import unittest
from uuid import UUID

from vibestorm.caps.inventory_client import InventoryCapabilityClient, InventoryFolderRequest


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
