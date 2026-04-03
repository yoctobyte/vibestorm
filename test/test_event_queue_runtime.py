import socket
import unittest
import urllib.error

from vibestorm.event_queue.client import EventQueueClient, EventQueueError


class EventQueueClientTests(unittest.TestCase):
    def test_poll_once_wraps_timeout(self) -> None:
        client = EventQueueClient(timeout_seconds=3.0)

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise socket.timeout("timed out")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(EventQueueError, "timed out after 3.0s"):
                client._poll_once_sync("http://example.invalid/eq", 0, False)
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    def test_poll_once_wraps_url_error(self) -> None:
        client = EventQueueClient()

        import urllib.request

        original = urllib.request.urlopen

        def fake_urlopen(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise urllib.error.URLError("connection refused")

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(EventQueueError, "connection refused"):
                client._poll_once_sync("http://example.invalid/eq", 0, False)
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]
