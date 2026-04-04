import socket
import unittest
import urllib.error
from uuid import UUID

from vibestorm.caps.client import CapabilityClient, CapabilityError


class CapabilityClientTests(unittest.TestCase):
    def test_resolve_seed_caps_sends_viewer_like_headers(self) -> None:
        client = CapabilityClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>EventQueueGet</key><string>http://example.invalid/eq</string>"
                    b"</map></llsd>"
                )

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            captured["timeout"] = timeout
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._resolve_seed_caps_sync(
                "http://example.invalid/seed",
                ["EventQueueGet"],
                37468,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result["EventQueueGet"], "http://example.invalid/eq")
        headers = captured["headers"]
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(headers["Content-type"], "application/llsd+xml")
        self.assertEqual(headers["Accept"], "application/llsd+xml")
        self.assertEqual(headers["X-secondlife-udp-listen-port"], "37468")
        self.assertEqual(headers["User-agent"], "Vibestorm")

    def test_fetch_capability_value_parses_llsd_map(self) -> None:
        client = CapabilityClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>MeshUploadEnabled</key><boolean>1</boolean>"
                    b"</map></llsd>"
                )

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._fetch_capability_value_sync("http://example.invalid/features", 37468, "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result, {"MeshUploadEnabled": True})

    def test_post_capability_value_serializes_nested_llsd_map(self) -> None:
        client = CapabilityClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return b'<?xml version="1.0"?><llsd><map><key>ok</key><boolean>1</boolean></map></llsd>'

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            captured["body"] = request.data
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._post_capability_value_sync(
                "http://example.invalid/inventory",
                {
                    "folders": [
                        {
                            "folder_id": UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"),
                            "owner_id": UUID("11111111-2222-3333-4444-555555555555"),
                            "fetch_folders": True,
                            "fetch_items": True,
                            "sort_order": 0,
                        }
                    ]
                },
                37468,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result, {"ok": True})
        headers = captured["headers"]
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(headers["Content-type"], "application/llsd+xml")
        body = captured["body"]
        self.assertIn(b"<key>folders</key>", body)
        self.assertIn(b"<uuid>49cb1ed7-e8b2-4de5-84d7-4222f540634c</uuid>", body)
        self.assertIn(b"<key>fetch_items</key><boolean>true</boolean>", body)

    def test_resolve_seed_caps_wraps_timeout(self) -> None:
        client = CapabilityClient(timeout_seconds=2.0)

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise socket.timeout("timed out")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(CapabilityError, "timed out after 2.0s"):
                client._resolve_seed_caps_sync("http://example.invalid/seed", ["EventQueueGet"])
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_resolve_seed_caps_wraps_url_error(self) -> None:
        client = CapabilityClient()

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(CapabilityError, "connection refused"):
                client._resolve_seed_caps_sync("http://example.invalid/seed", ["EventQueueGet"])
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]
