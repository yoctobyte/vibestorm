"""No client hands its caller an exception from a layer underneath it.

Four times now the same defect has been found here, always the same shape:
something below the code raises a class the code was not written to expect,
and it leaves through every handler untouched.

* `PIL.Image.DecompressionBombError` past `decode_j2k`, which subclasses
  `Exception` and not `OSError`.
* `xml.parsers.expat.ExpatError` past `_login_sync`, which is neither an
  `xmlrpc.client.Error` nor an `OSError`.
* `xml.etree.ElementTree.ParseError` past every cap client, which
  subclasses `SyntaxError`.
* And, differently but for the same reason, an `AttributeError` from
  reading a wire field name off an object that had renamed it.

The pattern is not "these four classes". It is that a body which arrives
and turns out to be rubbish is the *expected* case on a public network, and
the code that handles it is written against the errors it can name. So this
file names the contract instead: each client, handed a body that is not
LLSD, raises its own documented error and nothing else.

The most common real cause is dull and worth stating: an intercepting proxy
or a load balancer answering with an HTML error page and an HTTP 200.
"""

from __future__ import annotations

import sys
import unittest

from vibestorm.util import remote_url
import urllib.request
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from http_fakes import FakeHeaders, serve_body  # noqa: E402

from vibestorm.caps.asset_upload_client import AssetUploadClient, AssetUploadError  # noqa: E402
from vibestorm.caps.client import CapabilityClient, CapabilityError  # noqa: E402
from vibestorm.caps.llsd import LlsdError, parse_xml_string_map, parse_xml_value  # noqa: E402
from vibestorm.caps.task_inventory_upload_client import (  # noqa: E402
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)
from vibestorm.caps.upload_baked_texture_client import (  # noqa: E402
    UploadBakedTextureClient,
    UploadBakedTextureError,
)
from vibestorm.event_queue.client import EventQueueClient, EventQueueError  # noqa: E402

#: What a misconfigured proxy answers with, at HTTP 200.
PROXY_ERROR_PAGE = b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"

#: And the other everyday one: a connection cut mid-document.
TRUNCATED = b'<?xml version="1.0"?><llsd><map><key>uploader</key>'


class _Response:
    def __init__(self, body: bytes):
        self._body = body
        self._served = 0
        self.status = 200
        self.headers = FakeHeaders("application/llsd+xml")

    def read(self, amt: int = -1) -> bytes:
        return serve_body(self, self._body, amt)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class LlsdParseTests(unittest.TestCase):
    """The layer boundary itself: `ET.ParseError` becomes `LlsdError`."""

    BAD = (
        ("a proxy error page", PROXY_ERROR_PAGE),
        ("a truncated document", TRUNCATED),
        ("an empty body", b""),
        ("plain text", b"Service Unavailable"),
        ("a stray null", b"\x00"),
    )

    def test_a_body_that_is_not_xml_is_an_llsd_error(self) -> None:
        for name, payload in self.BAD:
            for parse in (parse_xml_value, parse_xml_string_map):
                with self.subTest(f"{name} / {parse.__name__}"):
                    with self.assertRaises(LlsdError):
                        parse(payload)

    def test_well_formed_xml_that_is_not_llsd_is_also_an_llsd_error(self) -> None:
        """The floor: the parse succeeded, so this is the module's own check
        doing the work rather than the one being added around it."""
        with self.assertRaises(LlsdError):
            parse_xml_value(b"<notllsd><map/></notllsd>")

    def test_valid_llsd_still_parses(self) -> None:
        """And the other floor. A wrapper that rejected everything would pass
        every test above it."""
        body = b"<llsd><map><key>a</key><string>b</string></map></llsd>"
        self.assertEqual(parse_xml_string_map(body), {"a": "b"})


class _ContractCase:
    """One client entry point and the error it promises."""

    def __init__(self, name: str, error: type[Exception], call):
        self.name = name
        self.error = error
        self.call = call


ID = UUID("11111111-2222-3333-4444-555555555555")
URL = "http://example.invalid/caps/thing"

CASES = (
    _ContractCase(
        "seed caps",
        CapabilityError,
        lambda: CapabilityClient()._resolve_seed_caps_sync(URL, ["EventQueueGet"], "Vibestorm"),
    ),
    _ContractCase(
        "capability fetch",
        CapabilityError,
        lambda: CapabilityClient()._fetch_capability_value_sync(URL, "Vibestorm"),
    ),
    _ContractCase(
        "capability post",
        CapabilityError,
        lambda: CapabilityClient()._post_capability_value_sync(URL, {}, "Vibestorm"),
    ),
    _ContractCase(
        "event queue poll",
        EventQueueError,
        lambda: EventQueueClient()._poll_once_sync(URL, None, "Vibestorm", 9000),
    ),
    _ContractCase(
        "asset upload",
        AssetUploadError,
        lambda: AssetUploadClient()._upload_bytes_sync(URL, b"data", "Vibestorm"),
    ),
    _ContractCase(
        "baked texture upload",
        UploadBakedTextureError,
        lambda: UploadBakedTextureClient()._upload_texture_bytes_sync(URL, b"data", "Vibestorm"),
    ),
    _ContractCase(
        "task script upload",
        TaskInventoryUploadError,
        lambda: TaskInventoryUploadClient()._upload_script_bytes_sync(URL, b"data", "Vibestorm"),
    ),
    _ContractCase(
        "task notecard upload",
        TaskInventoryUploadError,
        lambda: TaskInventoryUploadClient()._upload_notecard_bytes_sync(URL, b"data", "Vibestorm"),
    ),
)


class ClientErrorContractTests(unittest.TestCase):
    def _against(self, body: bytes, case: _ContractCase) -> BaseException:
        original = remote_url.open_http
        remote_url.open_http = lambda *args, **kwargs: _Response(body)
        try:
            case.call()
        except BaseException as exc:  # noqa: BLE001 - the class is the assertion
            return exc
        finally:
            remote_url.open_http = original
        raise AssertionError(f"{case.name} accepted a body that is not LLSD")

    def test_a_proxy_error_page_raises_the_client_s_own_error(self) -> None:
        for case in CASES:
            with self.subTest(case.name):
                raised = self._against(PROXY_ERROR_PAGE, case)
                self.assertIsInstance(raised, case.error, f"{case.name}: {raised!r}")

    def test_a_truncated_document_raises_the_client_s_own_error(self) -> None:
        for case in CASES:
            with self.subTest(case.name):
                raised = self._against(TRUNCATED, case)
                self.assertIsInstance(raised, case.error, f"{case.name}: {raised!r}")

    def test_an_empty_body_raises_the_client_s_own_error(self) -> None:
        for case in CASES:
            with self.subTest(case.name):
                raised = self._against(b"", case)
                self.assertIsInstance(raised, case.error, f"{case.name}: {raised!r}")

    def test_every_client_that_parses_llsd_is_in_the_table(self) -> None:
        """The floor. A table that quietly stopped covering a client would
        keep passing, which is the failure mode of every table like this."""
        src = Path(__file__).resolve().parents[1] / "src" / "vibestorm"
        parsing = {
            str(path.relative_to(src))
            for path in src.rglob("*.py")
            if "parse_xml_value(" in path.read_text() or "parse_xml_string_map(" in path.read_text()
        }
        parsing.discard("caps/llsd.py")
        self.assertEqual(len(CASES), 8)
        self.assertEqual(
            parsing,
            {
                "caps/client.py",
                "caps/asset_upload_client.py",
                "caps/upload_baked_texture_client.py",
                "caps/task_inventory_upload_client.py",
                "event_queue/client.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
