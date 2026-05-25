import unittest
from urllib.error import URLError
from uuid import UUID

from vibestorm.caps.task_inventory_upload_client import (
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)


class TaskInventoryUploadClientTests(unittest.TestCase):
    def test_request_uploader_posts_script_metadata(self) -> None:
        client = TaskInventoryUploadClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self) -> bytes:
                return (
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>uploader</key><string>http://example.invalid/upload-script</string>"
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
                "http://example.invalid/caps/update-script",
                {
                    "item_id": UUID("11111111-2222-3333-4444-555555555555"),
                    "task_id": UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                    "is_script_running": True,
                },
                37468,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.uploader_url, "http://example.invalid/upload-script")
        self.assertEqual(result.state, "upload")
        self.assertEqual(captured["method"], "POST")
        body = captured["body"]
        self.assertIn(
            b"<key>item_id</key><uuid>11111111-2222-3333-4444-555555555555</uuid>",
            body,
        )
        self.assertIn(
            b"<key>task_id</key><uuid>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</uuid>",
            body,
        )
        self.assertIn(b"<key>is_script_running</key><boolean>true</boolean>", body)
        headers = captured["headers"]
        self.assertEqual(headers["X-secondlife-udp-listen-port"], "37468")
        self.assertEqual(headers["Content-type"], "application/llsd+xml")

    def test_upload_script_bytes_success(self) -> None:
        client = TaskInventoryUploadClient()

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
                    b"<key>compiled</key><boolean>true</boolean>"
                    b"<key>errors</key><array></array>"
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
            result = client._upload_script_bytes_sync(
                "http://example.invalid/upload", b"default { state_entry() {} }", "Vibestorm"
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.state, "complete")
        self.assertTrue(result.compiled)
        self.assertEqual(result.new_asset_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.errors, [])
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], b"default { state_entry() {} }")
        self.assertEqual(captured["headers"]["Content-type"], "application/octet-stream")

    def test_upload_script_bytes_compile_error(self) -> None:
        client = TaskInventoryUploadClient()

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
                    b"<key>compiled</key><boolean>false</boolean>"
                    b"<key>errors</key><array>"
                    b"<string>(10, 15): Name not defined within scope</string>"
                    b"</array>"
                    b"</map></llsd>"
                )

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["body"] = request.data
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._upload_script_bytes_sync(
                "http://example.invalid/upload", b"invalid code", "Vibestorm"
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.state, "complete")
        self.assertFalse(result.compiled)
        self.assertEqual(result.new_asset_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.errors, ["(10, 15): Name not defined within scope"])

    def test_upload_notecard_bytes_success(self) -> None:
        client = TaskInventoryUploadClient()

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
                    b"</map></llsd>"
                )

        captured: dict[str, object] = {}
        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["body"] = request.data
            return FakeResponse()

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._upload_notecard_bytes_sync(
                "http://example.invalid/upload", b"hello task notecard", "Vibestorm"
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

        self.assertEqual(result.state, "complete")
        self.assertEqual(result.new_asset_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.new_inventory_item_id, UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))

    def test_request_uploader_surfaces_cap_error(self) -> None:
        client = TaskInventoryUploadClient()

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
                    b"<key>message</key><string>Failed to resolve prim</string>"
                    b"</map></map></llsd>"
                )

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *args, **kwargs: FakeResponse()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(TaskInventoryUploadError, "Failed to resolve prim"):
                client._request_uploader_sync(
                    "http://example.invalid/caps/update-script",
                    {"item_id": UUID("11111111-2222-3333-4444-555555555555")},
                    None,
                    "Vibestorm",
                )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_upload_bytes_wraps_timeout(self) -> None:
        client = TaskInventoryUploadClient(timeout_seconds=1.5)

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise TimeoutError("timed out")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(TaskInventoryUploadError, "timed out after 1.5s"):
                client._upload_script_bytes_sync("http://example.invalid/upload", b" ", "Vibestorm")
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
