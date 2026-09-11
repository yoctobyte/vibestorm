import socket
import unittest

from vibestorm.util import remote_url
import urllib.error

from http_fakes import serve_body

from vibestorm.event_queue.client import EventQueueClient, EventQueueError


class EventQueueClientTests(unittest.TestCase):
    def test_poll_once_sends_viewer_like_headers(self) -> None:
        client = EventQueueClient()

        import urllib.request

        class FakeResponse:
            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
                return False

            def read(self, amt: int = -1) -> bytes:
                return serve_body(self, b'<?xml version="1.0"?><llsd><map><key>id</key><integer>1</integer></map></llsd>', amt)

        captured: dict[str, object] = {}
        original = remote_url.open_http

        def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
            captured["headers"] = dict(request.header_items())
            captured["method"] = request.get_method()
            return FakeResponse()

        remote_url.open_http = fake_urlopen  # type: ignore[assignment]
        try:
            result = client._poll_once_sync("http://example.invalid/eq", 0, False, 37468, "Vibestorm")
        finally:
            remote_url.open_http = original  # type: ignore[assignment]

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.payload, {"id": 1})
        headers = captured["headers"]
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(headers["X-secondlife-udp-listen-port"], "37468")
        self.assertEqual(headers["User-agent"], "Vibestorm")

    def test_poll_once_wraps_timeout(self) -> None:
        client = EventQueueClient(timeout_seconds=3.0)

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise socket.timeout("timed out")

        remote_url.open_http = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(EventQueueError, "timed out after 3.0s"):
                client._poll_once_sync("http://example.invalid/eq", 0, False)
        finally:
            remote_url.open_http = original  # type: ignore[assignment]

    def test_poll_once_wraps_url_error(self) -> None:
        client = EventQueueClient()

        import urllib.request

        original = remote_url.open_http

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError("connection refused")

        remote_url.open_http = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(EventQueueError, "connection refused"):
                client._poll_once_sync("http://example.invalid/eq", 0, False)
        finally:
            remote_url.open_http = original  # type: ignore[assignment]
