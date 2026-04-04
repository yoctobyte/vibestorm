import unittest
import socket
import xmlrpc.client
from uuid import UUID

from vibestorm.login.client import LoginClient, LoginError, sl_password_hash
from vibestorm.login.models import DEFAULT_LOGIN_OPTIONS, LoginCredentials, LoginRequest


class LoginHelpersTests(unittest.TestCase):
    def test_sl_password_hash(self) -> None:
        self.assertEqual(sl_password_hash("changeme123"), "$1$c9cdcb06301f9c79e2d20c2fdeda0a02")


class LoginClientSyncTests(unittest.TestCase):
    def test_request_payload_hashes_password(self) -> None:
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )
        payload = LoginClient()._request_payload(request)
        self.assertEqual(payload["passwd"], "$1$c9cdcb06301f9c79e2d20c2fdeda0a02")
        self.assertEqual(payload["options"], list(DEFAULT_LOGIN_OPTIONS))

    def test_login_sync_maps_response(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )
        expected_payload = client._request_payload(request)

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                assert payload == expected_payload
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "inventory-root": [
                        {"folder_id": "49cb1ed7-e8b2-4de5-84d7-4222f540634c"},
                    ],
                    "inventory-skeleton": [
                        {
                            "name": "Current Outfit",
                            "folder_id": "d427dc3a-047a-4b9f-9aaf-15ccce179bf2",
                        },
                        {
                            "name": "My Outfits",
                            "folder_id": "256d4a5d-cb0d-7e27-ca95-ac42b50ec733",
                        },
                    ],
                    "initial-outfit": [
                        {
                            "folder_name": "Nightclub Female",
                            "gender": "female",
                        },
                    ],
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertEqual(result.agent_id, UUID("11111111-2222-3333-4444-555555555555"))
        self.assertEqual(result.session_id, UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        self.assertEqual(result.circuit_code, 7)
        self.assertEqual(result.sim_ip, "127.0.0.1")
        self.assertEqual(result.region_x, 256000)
        self.assertEqual(result.inventory_root_folder_id, UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"))
        self.assertEqual(result.current_outfit_folder_id, UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"))
        self.assertEqual(result.my_outfits_folder_id, UUID("256d4a5d-cb0d-7e27-ca95-ac42b50ec733"))
        self.assertEqual(result.initial_outfit_name, "Nightclub Female")
        self.assertEqual(result.initial_outfit_gender, "female")
        self.assertEqual(result.initial_baked_cache_entries, ())

    def test_login_sync_extracts_initial_baked_cache_entries(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "packed_appearance": {
                        "bakedcache": [
                            {
                                "textureindex": 8,
                                "cacheid": "12345678-1111-2222-3333-444444444444",
                                "textureid": "87654321-1111-2222-3333-444444444444",
                            }
                        ],
                        "bc8": [
                            {
                                "textureindex": 40,
                                "cacheid": "12345678-1111-2222-3333-555555555555",
                                "textureid": "87654321-1111-2222-3333-555555555555",
                            }
                        ],
                    },
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertEqual(len(result.initial_baked_cache_entries), 2)
        self.assertEqual(result.initial_baked_cache_entries[0].texture_index, 8)
        self.assertEqual(result.initial_baked_cache_entries[0].cache_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.initial_baked_cache_entries[1].texture_index, 40)
        self.assertEqual(result.initial_baked_cache_entries[1].cache_id, UUID("12345678-1111-2222-3333-555555555555"))
        self.assertIsNone(result.initial_packed_appearance)

    def test_login_sync_extracts_initial_packed_appearance(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "packed_appearance": {
                        "serial": 12,
                        "height": 1.93,
                        "te8": xmlrpc.client.Binary(b"\x01\x02\x03\x04"),
                        "visualparams": xmlrpc.client.Binary(b"\x05\x06\x07"),
                    },
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertIsNotNone(result.initial_packed_appearance)
        assert result.initial_packed_appearance is not None
        self.assertEqual(result.initial_packed_appearance.serial_num, 12)
        self.assertAlmostEqual(result.initial_packed_appearance.avatar_height, 1.93)
        self.assertEqual(result.initial_packed_appearance.texture_entry, b"\x01\x02\x03\x04")
        self.assertEqual(result.initial_packed_appearance.visual_params, b"\x05\x06\x07")

    def test_login_sync_raises_on_failure(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {"login": "false", "message": "bad password"}

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            with self.assertRaises(LoginError):
                client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

    def test_login_sync_wraps_timeout(self) -> None:
        client = LoginClient(timeout_seconds=2.5)
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                raise socket.timeout("timed out")

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(LoginError, "timed out after 2.5s"):
                client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]
