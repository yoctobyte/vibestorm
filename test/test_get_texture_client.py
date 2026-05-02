import socket
import unittest
import urllib.error
from uuid import UUID

from vibestorm.caps.get_texture_client import GetTextureClient, GetTextureError


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "image/x-j2c", status: int = 200):
        self._body = body
        self._content_type = content_type
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body

    @property
    def headers(self):
        outer = self

        class _Headers:
            def get_content_type(self) -> str:
                return outer._content_type

        return _Headers()


class GetTextureClientTests(unittest.TestCase):
    def test_fetch_builds_query_and_returns_bytes(self) -> None:
        client = GetTextureClient()
        texture_id = UUID("11111111-2222-3333-4444-555555555555")
        captured: dict[str, object] = {}

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _FakeResponse(b"FAKEJ2KBYTES", content_type="image/x-j2c")

        urllib.request.urlopen = fake_urlopen
        try:
            result = client._fetch_sync(
                "http://example.invalid/caps/get-texture",
                texture_id,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original

        self.assertEqual(result.texture_id, texture_id)
        self.assertEqual(result.data, b"FAKEJ2KBYTES")
        self.assertEqual(result.content_type, "image/x-j2c")
        self.assertEqual(captured["method"], "GET")
        self.assertIn(f"texture_id={texture_id}", captured["url"])
        self.assertIn("Accept", captured["headers"])

    def test_fetch_appends_query_when_url_already_has_one(self) -> None:
        client = GetTextureClient()
        texture_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        captured: dict[str, object] = {}

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse(b"X")

        urllib.request.urlopen = fake_urlopen
        try:
            client._fetch_sync(
                "http://example.invalid/caps/get-texture?token=abc",
                texture_id,
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original

        url = captured["url"]
        assert isinstance(url, str)
        self.assertIn("token=abc", url)
        self.assertIn(f"texture_id={texture_id}", url)
        self.assertEqual(url.count("?"), 1)

    def test_fetch_wraps_url_error(self) -> None:
        client = GetTextureClient()
        texture_id = UUID("00000000-0000-0000-0000-000000000001")

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(GetTextureError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-texture",
                    texture_id,
                    "Vibestorm",
                )
        finally:
            urllib.request.urlopen = original

        self.assertIn("connection refused", str(ctx.exception))

    def test_fetch_wraps_timeout(self) -> None:
        client = GetTextureClient(timeout_seconds=0.5)
        texture_id = UUID("00000000-0000-0000-0000-000000000002")

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):
            raise socket.timeout()

        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(GetTextureError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-texture",
                    texture_id,
                    "Vibestorm",
                )
        finally:
            urllib.request.urlopen = original

        self.assertIn("timed out", str(ctx.exception))

    def test_fetch_rejects_empty_body(self) -> None:
        client = GetTextureClient()
        texture_id = UUID("00000000-0000-0000-0000-000000000003")

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(request, timeout):
            return _FakeResponse(b"")

        urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(GetTextureError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-texture",
                    texture_id,
                    "Vibestorm",
                )
        finally:
            urllib.request.urlopen = original

        self.assertIn("empty body", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
