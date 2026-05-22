import unittest
from urllib.error import URLError
from uuid import UUID

from vibestorm.caps.asset_upload_client import (
    AssetUploadClient,
    AssetUploadError,
    NewFileInventoryRequest,
)


class AssetUploadClientTests(unittest.TestCase):
    def test_request_new_file_uploader_posts_inventory_metadata(self) -> None:
        client = AssetUploadClient()

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
                    b"<key>upload_price</key><integer>0</integer>"
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
            result = client._request_new_file_uploader_sync(
                "http://example.invalid/caps/new-file",
                NewFileInventoryRequest(
                    folder_id=UUID("11111111-2222-3333-4444-555555555555"),
                    name="vibestorm-empty-space.txt",
                    description="smoke",
                ),
                37468,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.uploader_url, "http://example.invalid/upload")
        self.assertEqual(result.state, "upload")
        self.assertEqual(result.upload_price, 0)
        self.assertEqual(captured["method"], "POST")
        body = captured["body"]
        self.assertIn(b"<key>asset_type</key><string>notecard</string>", body)
        self.assertIn(b"<key>inventory_type</key><string>notecard</string>", body)
        self.assertIn(
            b"<key>folder_id</key><uuid>11111111-2222-3333-4444-555555555555</uuid>",
            body,
        )
        self.assertIn(b"<key>name</key><string>vibestorm-empty-space.txt</string>", body)
        headers = captured["headers"]
        self.assertEqual(headers["X-secondlife-udp-listen-port"], "37468")
        self.assertEqual(headers["Content-type"], "application/llsd+xml")

    def test_upload_bytes_posts_raw_payload_and_parses_completion(self) -> None:
        client = AssetUploadClient()

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
                    b"<key>new_inventory_item</key><uuid>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</uuid>"
                    b"<key>new_next_owner_mask</key><integer>581632</integer>"
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
            result = client._upload_bytes_sync("http://example.invalid/upload", b" ", "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.state, "complete")
        self.assertEqual(result.new_asset_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.new_inventory_item_id, UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        self.assertEqual(result.new_next_owner_mask, 581632)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], b" ")
        self.assertEqual(captured["headers"]["Content-type"], "application/octet-stream")

    def test_request_new_file_uploader_surfaces_cap_error(self) -> None:
        client = AssetUploadClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>state</key><string>error</string>"
                    b"<key>error</key><map>"
                    b"<key>message</key><string>Uploader busy processing previous request</string>"
                    b"</map></map></llsd>"
                )

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(AssetUploadError, "Uploader busy"):
                client._request_new_file_uploader_sync(
                    "http://example.invalid/caps/new-file",
                    NewFileInventoryRequest(
                        folder_id=UUID("11111111-2222-3333-4444-555555555555"),
                        name="test.txt",
                    ),
                )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_upload_bytes_wraps_timeout(self) -> None:
        client = AssetUploadClient(timeout_seconds=2.5)

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError("timed out")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(AssetUploadError, "timed out after 2.5s"):
                client._upload_bytes_sync("http://example.invalid/upload", b" ", "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_upload_bytes_wraps_url_error(self) -> None:
        client = AssetUploadClient()

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise URLError("connection refused")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(AssetUploadError, "connection refused"):
                client._upload_bytes_sync("http://example.invalid/upload", b" ", "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
