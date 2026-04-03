import unittest
import socket
from uuid import UUID

from vibestorm.login.client import LoginClient, LoginError, sl_password_hash
from vibestorm.login.models import LoginCredentials, LoginRequest


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
                }

        import xmlrpc.client

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

    def test_login_sync_raises_on_failure(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {"login": "false", "message": "bad password"}

        import xmlrpc.client

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

        import xmlrpc.client

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(LoginError, "timed out after 2.5s"):
                client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]
