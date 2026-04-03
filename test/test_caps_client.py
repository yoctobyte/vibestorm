import socket
import unittest
import urllib.error

from vibestorm.caps.client import CapabilityClient, CapabilityError


class CapabilityClientTests(unittest.TestCase):
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
