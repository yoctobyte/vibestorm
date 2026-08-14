"""Tests for the ViewerAsset capability client.

Two sources meet in this module and only one of them is in the tree. The query
*keys* come from OpenSim's ``GetAssetsHandler.queryTypes`` and are pinned
below. The asset type *numbers* are libomv's, which ships as a DLL — so they
come from the LSL constants instead, the same pinned subset
``caps/inventory_types`` uses. The table is the intersection, and the pin tests
check both halves rather than the shape of the dict.
"""

import re
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

from vibestorm.caps.inventory_types import ASSET_TYPE_NAMES
from vibestorm.caps.viewer_asset_client import (
    ASSET_TYPE_QUERY_KEYS,
    ViewerAssetClient,
    ViewerAssetError,
    asset_type_query_key,
)

_ROOT = Path(__file__).resolve().parents[1]
_HANDLER = (
    _ROOT / "opensim-source" / "OpenSim" / "Capabilities" / "Handlers"
    / "GetAssets" / "GetAssetsHandler.cs"
)

_ASSET_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _source_query_keys() -> set[str]:
    text = _HANDLER.read_text(encoding="utf-8", errors="replace")
    body = text.split("queryTypes", 1)[1].split("};", 1)[0]
    return set(re.findall(r'\{"(\w+)",\s*AssetType\.', body))


class SourcePinTests(unittest.TestCase):
    def test_every_key_we_use_is_one_opensim_accepts(self) -> None:
        if not _HANDLER.exists():
            self.skipTest("opensim-source not present")
        source = _source_query_keys()

        self.assertTrue(source, "failed to parse queryTypes")
        for asset_type, key in ASSET_TYPE_QUERY_KEYS.items():
            self.assertIn(key, source, f"type {asset_type} uses unknown key {key}")

    def test_no_key_is_reused_for_two_types(self) -> None:
        keys = list(ASSET_TYPE_QUERY_KEYS.values())

        self.assertEqual(len(keys), len(set(keys)))

    def test_every_number_comes_from_the_pinned_asset_type_table(self) -> None:
        # The guard against inventing numbers for libomv-only types. Anything
        # here that ASSET_TYPE_NAMES does not know is a number with no source.
        unsourced = sorted(set(ASSET_TYPE_QUERY_KEYS) - set(ASSET_TYPE_NAMES))

        self.assertEqual(unsourced, [])

    def test_the_omissions_are_the_unsourceable_ones(self) -> None:
        # Named explicitly so that adding one later is a deliberate act with a
        # source behind it, rather than a quiet edit to a dict.
        if not _HANDLER.exists():
            self.skipTest("opensim-source not present")

        missing = _source_query_keys() - set(ASSET_TYPE_QUERY_KEYS.values())

        self.assertEqual(
            missing,
            {
                "callcard_id",
                "lslbyte_id",
                "txtr_tga_id",
                "snd_wav_id",
                "img_tga_id",
                "jpeg_id",
                "mesh_id",
                "lsltext_id",  # the second accepted key for LSLText
            },
        )


class TypeCheckIsNotEnforcedTests(unittest.TestCase):
    """A successful fetch is not evidence that the type was right.

    ``GetAssetsHandler`` compares ``asset.Type`` against the type the query key
    implies, and on mismatch logs ``asset with wrong type`` — then serves the
    bytes, because the ``return`` beneath the warning is commented out.

    Confirmed live 2026-08-14: the region map texture, requested as
    ``notecard_id``, came back as the same 4376 bytes it returns for
    ``texture_id``. So a caller cannot round-trip an asset to discover its
    type, and this client must never infer one from a 200.
    """

    def test_the_wrong_type_return_is_still_commented_out(self) -> None:
        if not _HANDLER.exists():
            self.skipTest("opensim-source not present")
        text = _HANDLER.read_text(encoding="utf-8", errors="replace")

        after_warning = text.split("asset with wrong type", 1)[1][:400]

        self.assertIn("//response.StatusCode", after_warning)
        self.assertIn("//return", after_warning)

    def test_this_client_does_not_claim_to_verify_the_type(self) -> None:
        # FetchedAsset reports the type the *caller* asked for, under a name
        # that says so. If it ever grows a field claiming the asset's actual
        # type, that field would be a fabrication.
        from vibestorm.caps.viewer_asset_client import FetchedAsset

        self.assertEqual(
            set(FetchedAsset.__dataclass_fields__),
            {"asset_id", "asset_type", "query_key", "content_type", "data"},
        )


#: Query keys confirmed against a live sim on 2026-08-14 by walking the grid
#: library and fetching one asset of each type it holds. Each returned the
#: matching ``application/vnd.ll.*`` content type.
LIVE_VERIFIED_TYPES = {0, 5, 7, 10, 13, 20, 21, 56}


class LiveCoverageTests(unittest.TestCase):
    """Which keys have actually been exercised, and which have not.

    Eight of twelve. The remaining four are not suspect, they are untried: the
    OpenSim library ships no sound, landmark, object or material asset, so
    nothing in reach produces one. Recording that here keeps the gap visible
    instead of letting "the client supports twelve types" stand unqualified.
    """

    def test_the_verified_types_are_all_in_the_table(self) -> None:
        self.assertTrue(LIVE_VERIFIED_TYPES <= set(ASSET_TYPE_QUERY_KEYS))

    def test_the_unverified_types_are_the_ones_the_library_lacks(self) -> None:
        unverified = set(ASSET_TYPE_QUERY_KEYS) - LIVE_VERIFIED_TYPES

        self.assertEqual(
            {ASSET_TYPE_QUERY_KEYS[t] for t in unverified},
            {"sound_id", "landmark_id", "object_id", "material_id"},
        )


class QueryKeyTests(unittest.TestCase):
    def test_known_types(self) -> None:
        self.assertEqual(asset_type_query_key(7), "notecard_id")
        self.assertEqual(asset_type_query_key(10), "script_id")
        self.assertEqual(asset_type_query_key(0), "texture_id")

    def test_an_unknown_type_raises_rather_than_guessing(self) -> None:
        # OpenSim answers an unrecognised key 404 *before* looking the asset
        # up, so a fallback key would report "the sim does not have it" when
        # the truth is "this client does not know that type".
        with self.assertRaises(ViewerAssetError) as caught:
            asset_type_query_key(49)  # mesh: real type, number not sourceable

        self.assertIn("49", str(caught.exception))

    def test_a_non_numeric_type_raises(self) -> None:
        with self.assertRaises(ViewerAssetError):
            asset_type_query_key("notecard")


class _FakeResponse:
    def __init__(self, data: bytes, status: int = 200, content_type: str = "text/plain"):
        self._data = data
        self.status = status
        self.headers = self
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = urllib.request.urlopen
        self.requests: list[str] = []

    def tearDown(self) -> None:
        urllib.request.urlopen = self._original  # type: ignore[assignment]

    def _install(self, response) -> None:
        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            self.requests.append(request.full_url)
            if isinstance(response, Exception):
                raise response
            return response

        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]

    def test_the_type_selects_the_query_key(self) -> None:
        self._install(_FakeResponse(b"Hello notecard"))
        client = ViewerAssetClient(timeout_seconds=1.0)

        fetched = client._fetch_sync(
            "http://sim/caps/asset", _ASSET_ID, 7, "Vibestorm"
        )

        self.assertEqual(fetched.data, b"Hello notecard")
        self.assertEqual(fetched.query_key, "notecard_id")
        self.assertEqual(fetched.asset_type, 7)
        self.assertIn(f"notecard_id={_ASSET_ID}", self.requests[0])

    def test_a_capability_url_that_already_has_a_query_is_appended_to(self) -> None:
        # OpenSim hands out cap URLs as a random path, but a proxy or a
        # rewritten seed cap can carry a query; joining with '?' twice makes
        # the type key part of the previous value.
        self._install(_FakeResponse(b"x"))
        client = ViewerAssetClient(timeout_seconds=1.0)

        client._fetch_sync("http://sim/caps?a=1", _ASSET_ID, 0, "Vibestorm")

        self.assertIn("?a=1&texture_id=", self.requests[0])
        self.assertEqual(self.requests[0].count("?"), 1)

    def test_an_unknown_type_never_reaches_the_network(self) -> None:
        self._install(_FakeResponse(b"x"))
        client = ViewerAssetClient(timeout_seconds=1.0)

        with self.assertRaises(ViewerAssetError):
            client._fetch_sync("http://sim/caps", _ASSET_ID, 49, "Vibestorm")

        self.assertEqual(self.requests, [])

    def test_partial_content_is_accepted(self) -> None:
        # The handler answers 206 for a range request and this client sends
        # none -- but a caching proxy in between may still produce one, and
        # treating 206 as an error would drop a body that is present.
        self._install(_FakeResponse(b"partial", status=206))
        client = ViewerAssetClient(timeout_seconds=1.0)

        fetched = client._fetch_sync("http://sim/caps", _ASSET_ID, 7, "Vibestorm")

        self.assertEqual(fetched.data, b"partial")

    def test_an_empty_body_is_an_error_not_an_empty_asset(self) -> None:
        # OpenSim answers a zero-length asset with 404, so an empty 200 is not
        # something the sim produces.
        self._install(_FakeResponse(b""))
        client = ViewerAssetClient(timeout_seconds=1.0)

        with self.assertRaisesRegex(ViewerAssetError, "empty body"):
            client._fetch_sync("http://sim/caps", _ASSET_ID, 7, "Vibestorm")

    def test_http_errors_name_the_key_that_was_used(self) -> None:
        self._install(
            urllib.error.HTTPError("http://sim/caps", 404, "Not Found", {}, None)
        )
        client = ViewerAssetClient(timeout_seconds=1.0)

        with self.assertRaises(ViewerAssetError) as caught:
            client._fetch_sync("http://sim/caps", _ASSET_ID, 7, "Vibestorm")

        # 404 means both "no such asset" and "not a key I know", so the key is
        # the one piece of information that makes the message actionable.
        self.assertIn("notecard_id", str(caught.exception))
        self.assertIn("404", str(caught.exception))

    def test_a_timeout_says_how_long_it_waited(self) -> None:
        self._install(TimeoutError("timed out"))
        client = ViewerAssetClient(timeout_seconds=2.5)

        with self.assertRaisesRegex(ViewerAssetError, "2.5s"):
            client._fetch_sync("http://sim/caps", _ASSET_ID, 7, "Vibestorm")


if __name__ == "__main__":
    unittest.main()
