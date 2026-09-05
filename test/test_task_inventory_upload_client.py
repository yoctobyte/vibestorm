import unittest
from dataclasses import fields
from pathlib import Path
from urllib.error import URLError
from uuid import UUID

from vibestorm.caps.task_inventory_upload_client import (
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
    TaskNotecardUploadResult,
    TaskScriptUploadResult,
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
        # Named new_item_id because that is what the value is; see the
        # source pin in ScriptUploadFieldNamingTests.
        self.assertEqual(result.new_item_id, UUID("12345678-1111-2222-3333-444444444444"))
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
        self.assertEqual(result.new_item_id, UUID("12345678-1111-2222-3333-444444444444"))
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


class ScriptTaskCapNameTests(unittest.TestCase):
    """The client asked only for the name OpenSim marks as legacy.

    ``BunchOfCaps`` registers ``UpdateScriptTask`` and ``UpdateScriptTaskInventory``
    against the same handler, the second commented ``//legacy``. Requesting only
    the legacy alias works against today's OpenSim and would break silently —
    with "no task inventory caps available" — the day it is dropped.
    """

    def test_both_names_are_requested_current_first(self) -> None:
        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES

        self.assertEqual(
            SCRIPT_TASK_CAP_NAMES,
            ["UpdateScriptTask", "UpdateScriptTaskInventory"],
        )

    def test_both_names_exist_in_opensim(self) -> None:
        from pathlib import Path

        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES

        caps = (
            Path(__file__).resolve().parents[1]
            / "opensim-source" / "OpenSim" / "Region" / "ClientStack" / "Linden"
            / "Caps" / "BunchOfCaps" / "BunchOfCaps.cs"
        )
        if not caps.exists():
            self.skipTest("opensim-source not present")
        text = caps.read_text(encoding="utf-8", errors="replace")

        for name in SCRIPT_TASK_CAP_NAMES:
            self.assertIn(f'RegisterSimpleHandler("{name}"', text, name)

    def test_the_sync_path_asks_for_both_names_rather_than_one(self) -> None:
        # resolve_sync_caps needs a live session to reach, so no unit test
        # covers the call site; the list could go back to a single hard-coded
        # caps.get() and everything here would still pass. Check the source.
        #
        # It is pinned in the engine because that is where the one copy lives:
        # the viewer had its own, and its Upload button now goes through this.
        from pathlib import Path

        engine = (
            Path(__file__).resolve().parents[1]
            / "src" / "vibestorm" / "sync" / "engine.py"
        ).read_text(encoding="utf-8")

        self.assertIn("*SCRIPT_TASK_CAP_NAMES", engine)
        self.assertIn("first_resolved(caps, SCRIPT_TASK_CAP_NAMES)", engine)
        self.assertNotIn('caps.get("UpdateScriptTaskInventory")', engine)

    def test_no_second_copy_of_the_cap_names_has_appeared(self) -> None:
        # Two lists is how the current name gets added to one of them and not
        # the other, and the resulting client works right up until the sim
        # drops the legacy alias.
        from pathlib import Path

        source = Path(__file__).resolve().parents[1] / "src" / "vibestorm"
        defining = [
            path.relative_to(source).as_posix()
            for path in source.rglob("*.py")
            if "SCRIPT_TASK_CAP_NAMES = [" in path.read_text(encoding="utf-8", errors="replace")
        ]

        self.assertEqual(defining, ["sync/engine.py"])

    def test_the_current_name_wins_when_the_sim_offers_both(self) -> None:
        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES, first_resolved

        resolved = first_resolved(
            {
                "UpdateScriptTask": "http://sim/current",
                "UpdateScriptTaskInventory": "http://sim/legacy",
            },
            SCRIPT_TASK_CAP_NAMES,
        )

        self.assertEqual(resolved, "http://sim/current")

    def test_the_legacy_name_is_still_used_when_it_is_all_there_is(self) -> None:
        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES, first_resolved

        resolved = first_resolved(
            {"UpdateScriptTaskInventory": "http://sim/legacy"},
            SCRIPT_TASK_CAP_NAMES,
        )

        self.assertEqual(resolved, "http://sim/legacy")

    def test_an_empty_url_does_not_count_as_resolved(self) -> None:
        # resolve_seed_caps returns only the names the sim answered, but an
        # empty string would otherwise pass a truthiness-free lookup and be
        # POSTed to as a URL.
        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES, first_resolved

        resolved = first_resolved(
            {"UpdateScriptTask": "", "UpdateScriptTaskInventory": "http://sim/legacy"},
            SCRIPT_TASK_CAP_NAMES,
        )

        self.assertEqual(resolved, "http://sim/legacy")

    def test_no_script_cap_at_all_is_none(self) -> None:
        from vibestorm.sync.engine import SCRIPT_TASK_CAP_NAMES, first_resolved

        self.assertIsNone(
            first_resolved({"UpdateNotecardTaskInventory": "http://sim/nc"},
                            SCRIPT_TASK_CAP_NAMES)
        )


if __name__ == "__main__":
    unittest.main()


class ScriptUploadFieldNamingTests(unittest.TestCase):
    """Why the script result has no asset id.

    `TaskInventoryScriptUpdater` puts the *inventory item* id into a field
    called `new_asset`. Fetching that value through ViewerAsset returns 404,
    which reads as "the upload failed" when it succeeded — the mistake this
    naming exists to prevent, made for real on 2026-08-15 before the source
    was checked.
    """

    _UPDATE_ITEM_ASSET = (
        Path(__file__).resolve().parents[1] / "opensim-source" / "OpenSim" / "Region"
        / "ClientStack" / "Linden" / "Caps" / "BunchOfCaps" / "UpdateItemAsset.cs"
    )

    def test_the_script_updater_returns_the_item_id(self) -> None:
        if not self._UPDATE_ITEM_ASSET.exists():
            self.skipTest("opensim-source not present")
        text = self._UPDATE_ITEM_ASSET.read_text(encoding="utf-8", errors="replace")

        self.assertIn("uploadComplete.new_asset = m_inventoryItemID;", text)

    def test_the_notecard_updater_in_the_same_file_does_not(self) -> None:
        # The asymmetry is the reason neither can be inferred from the other.
        if not self._UPDATE_ITEM_ASSET.exists():
            self.skipTest("opensim-source not present")
        text = self._UPDATE_ITEM_ASSET.read_text(encoding="utf-8", errors="replace")

        self.assertIn("uploadComplete.new_asset = assetID.ToString();", text)
        self.assertIn("uploadComplete.new_inventory_item = m_inventoryItemID;", text)

    def test_the_script_result_exposes_no_asset_id(self) -> None:
        # A field called new_asset_id here would be a standing invitation to
        # fetch it.
        self.assertNotIn(
            "new_asset_id", {f.name for f in fields(TaskScriptUploadResult)}
        )
        self.assertIn("new_item_id", {f.name for f in fields(TaskScriptUploadResult)})

    def test_the_notecard_result_still_has_both(self) -> None:
        names = {f.name for f in fields(TaskNotecardUploadResult)}

        self.assertEqual(names, {"state", "new_asset_id", "new_inventory_item_id"})
