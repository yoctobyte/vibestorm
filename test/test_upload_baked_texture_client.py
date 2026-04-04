import socket
import unittest
import urllib.error

from vibestorm.caps.upload_baked_texture_client import (
    UploadBakedTextureClient,
    UploadBakedTextureError,
)


class UploadBakedTextureClientTests(unittest.TestCase):
    def test_request_uploader_posts_empty_llsd_map(self) -> None:
        client = UploadBakedTextureClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>uploader</key><string>http://example.invalid/upload</string>"
                    b"<key>state</key><string>upload</string>"
                    b"</map></llsd>"
                )

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["body"] = request.data
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._request_uploader_sync(
                "http://example.invalid/caps/upload-baked",
                37468,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.uploader_url, "http://example.invalid/upload")
        self.assertEqual(result.state, "upload")
        self.assertEqual(captured["method"], "POST")
        self.assertIn(b"<llsd><map /></llsd>", captured["body"])
        headers = captured["headers"]
        self.assertEqual(headers["X-secondlife-udp-listen-port"], "37468")
        self.assertEqual(headers["Content-type"], "application/llsd+xml")

    def test_upload_texture_bytes_posts_raw_bytes_and_parses_completion(self) -> None:
        client = UploadBakedTextureClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>state</key><string>complete</string>"
                    b"<key>new_asset</key><string>12345678-1111-2222-3333-444444444444</string>"
                    b"<key>new_inventory_item</key><uuid />"
                    b"</map></llsd>"
                )

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["body"] = request.data
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._upload_texture_bytes_sync(
                "http://example.invalid/upload",
                b"\x00\x01\x02",
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.state, "complete")
        self.assertEqual(result.new_asset_id, "12345678-1111-2222-3333-444444444444")
        self.assertIsNone(result.new_inventory_item_id)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], b"\x00\x01\x02")
        headers = captured["headers"]
        self.assertEqual(headers["Content-type"], "application/octet-stream")

    def test_request_uploader_wraps_timeout(self) -> None:
        client = UploadBakedTextureClient(timeout_seconds=2.5)

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise socket.timeout("timed out")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(UploadBakedTextureError, "timed out after 2.5s"):
                client._request_uploader_sync("http://example.invalid/caps/upload-baked", None, "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_upload_texture_bytes_wraps_url_error(self) -> None:
        client = UploadBakedTextureClient()

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(UploadBakedTextureError, "connection refused"):
                client._upload_texture_bytes_sync("http://example.invalid/upload", b"", "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]
