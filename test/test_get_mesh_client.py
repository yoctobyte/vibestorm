import socket
import unittest

from vibestorm.util import remote_url
from urllib.error import URLError
from uuid import UUID

from http_fakes import FakeHeaders, serve_body

from vibestorm.caps.get_mesh_client import GetMeshClient, GetMeshError


class _FakeResponse:
    def __init__(
        self,
        body: bytes,
        content_type: str = "application/vnd.ll.mesh",
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


class GetMeshClientTests(unittest.TestCase):
    def test_fetch_builds_query_and_returns_bytes(self) -> None:
        client = GetMeshClient()
        mesh_id = UUID("11111111-2222-3333-4444-555555555555")
        captured: dict[str, object] = {}

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = dict(request.header_items())
            captured["timeout"] = timeout
            return _FakeResponse(b"FAKEMESHBYTES")

        remote_url.open_http = fake_urlopen
        try:
            result = client._fetch_sync(
                "http://example.invalid/caps/get-mesh",
                mesh_id,
                "Vibestorm",
            )
        finally:
            remote_url.open_http = original

        self.assertEqual(result.mesh_id, mesh_id)
        self.assertEqual(result.data, b"FAKEMESHBYTES")
        self.assertEqual(result.content_type, "application/vnd.ll.mesh")
        self.assertEqual(captured["method"], "GET")
        self.assertIn(f"mesh_id={mesh_id}", captured["url"])
        self.assertIn("Accept", captured["headers"])

    def test_fetch_appends_query_when_url_already_has_one(self) -> None:
        client = GetMeshClient()
        mesh_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        captured: dict[str, object] = {}

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return _FakeResponse(b"X")

        remote_url.open_http = fake_urlopen
        try:
            client._fetch_sync(
                "http://example.invalid/caps/get-mesh?token=abc",
                mesh_id,
                "Vibestorm",
            )
        finally:
            remote_url.open_http = original

        url = captured["url"]
        assert isinstance(url, str)
        self.assertIn("token=abc", url)
        self.assertIn(f"mesh_id={mesh_id}", url)
        self.assertEqual(url.count("?"), 1)

    def test_fetch_wraps_url_error(self) -> None:
        client = GetMeshClient()
        mesh_id = UUID("00000000-0000-0000-0000-000000000001")

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(request, timeout):
            raise URLError("connection refused")

        remote_url.open_http = fake_urlopen
        try:
            with self.assertRaises(GetMeshError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-mesh",
                    mesh_id,
                    "Vibestorm",
                )
        finally:
            remote_url.open_http = original

        self.assertIn("connection refused", str(ctx.exception))

    def test_fetch_wraps_timeout(self) -> None:
        client = GetMeshClient(timeout_seconds=0.5)
        mesh_id = UUID("00000000-0000-0000-0000-000000000002")

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(request, timeout):
            raise socket.timeout()

        remote_url.open_http = fake_urlopen
        try:
            with self.assertRaises(GetMeshError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-mesh",
                    mesh_id,
                    "Vibestorm",
                )
        finally:
            remote_url.open_http = original

        self.assertIn("timed out", str(ctx.exception))

    def test_fetch_rejects_empty_body(self) -> None:
        client = GetMeshClient()
        mesh_id = UUID("00000000-0000-0000-0000-000000000003")

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(request, timeout):
            return _FakeResponse(b"")

        remote_url.open_http = fake_urlopen
        try:
            with self.assertRaises(GetMeshError) as ctx:
                client._fetch_sync(
                    "http://example.invalid/caps/get-mesh",
                    mesh_id,
                    "Vibestorm",
                )
        finally:
            remote_url.open_http = original

        self.assertIn("empty body", str(ctx.exception))


class BodyCeilingTests(unittest.TestCase):
    """The mesh fetch, bounded before `MAX_MESH_BLOCK_BYTES` gets a look in.

    That constant bounds one inflated block. This bounds the file the blocks
    come in, which is the allocation that happens first.
    """

    def _fetch(self, body: bytes, limit: int) -> None:
        import urllib.request

        import vibestorm.caps.get_mesh_client as module

        original, original_limit = remote_url.open_http, module.MAX_ASSET_BODY_BYTES
        remote_url.open_http = lambda request, timeout: _FakeResponse(body)
        module.MAX_ASSET_BODY_BYTES = limit
        try:
            module.GetMeshClient()._fetch_sync(
                "http://example.invalid/caps/get-mesh",
                UUID("11111111-2222-3333-4444-555555555555"),
                "Vibestorm",
            )
        finally:
            remote_url.open_http = original
            module.MAX_ASSET_BODY_BYTES = original_limit

    def test_an_oversized_body_is_a_get_mesh_error(self) -> None:
        with self.assertRaisesRegex(GetMeshError, "exceeds the 64 byte limit"):
            self._fetch(b"x" * 65, 64)

    def test_a_body_within_the_limit_is_untouched(self) -> None:
        self._fetch(b"x" * 64, 64)



if __name__ == "__main__":
    unittest.main()
