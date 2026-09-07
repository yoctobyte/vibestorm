import socket
import unittest
import urllib.error
from uuid import UUID

from http_fakes import FakeHeaders, serve_body

from vibestorm.caps.get_texture_client import GetTextureClient, GetTextureError


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        content_type: str = "image/x-j2c",
        status: int = 200,
        **declared: str,
    ):
        self._body = body
        self._content_type = content_type
        self.status = status
        #: Header fields the response declares, e.g. `Content_Length="9"`.
        self._declared = declared

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amt: int = -1) -> bytes:
        return serve_body(self, self._body, amt)

    @property
    def headers(self) -> FakeHeaders:
        return FakeHeaders(self._content_type, **self._declared)


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


class BodyCeilingTests(unittest.TestCase):
    """A texture the size of the far end's imagination.

    `MAX_OBJECT_TEXTURE_EDGE` shrinks what gets drawn and `MAX_TEXTURE_PIXELS`
    refuses an oversized raster, but both of those happen once the bytes are
    in memory. This is the ceiling on getting them there.
    """

    def _fetch(self, body: bytes, limit: int) -> None:
        import urllib.request

        import vibestorm.caps.get_texture_client as module

        original, original_limit = urllib.request.urlopen, module.MAX_ASSET_BODY_BYTES
        urllib.request.urlopen = lambda request, timeout: _FakeResponse(body)
        module.MAX_ASSET_BODY_BYTES = limit
        try:
            module.GetTextureClient()._fetch_sync(
                "http://example.invalid/caps/get-texture",
                UUID("11111111-2222-3333-4444-555555555555"),
                "Vibestorm",
            )
        finally:
            urllib.request.urlopen = original
            module.MAX_ASSET_BODY_BYTES = original_limit

    def test_an_oversized_body_is_a_get_texture_error(self) -> None:
        """The client's own type -- a foreign exception here escapes every
        handler in the texture pipeline and reaches the frame loop."""
        with self.assertRaisesRegex(GetTextureError, "exceeds the 64 byte limit"):
            self._fetch(b"x" * 65, 64)

    def test_a_body_within_the_limit_is_untouched(self) -> None:
        self._fetch(b"x" * 64, 64)



if __name__ == "__main__":
    unittest.main()
