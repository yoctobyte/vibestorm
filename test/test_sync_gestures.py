"""A gesture round trip: the third text type, and the one nothing checks.

A script that will not compile comes back with errors. A notecard has no
structure to get wrong. A gesture has plenty of structure and the simulator
looks at none of it -- `UpdateGestureItemAsset` stores what it is sent, and a
malformed gesture fails later, when somebody tries to play it, with nothing
pointing back at the upload that caused it.

So the push parses it first. That is the whole of the difference between this
type and the other two, and it is why these tests spend more time on the
refusal than on the success.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from http_fakes import serve_body  # noqa: E402
from test_assets_gesture import _gesture  # noqa: E402
from test_sync_engine import _EngineCase  # noqa: E402

from vibestorm.assets.gesture import GestureDecodeError  # noqa: E402
from vibestorm.caps.task_inventory_upload_client import (  # noqa: E402
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)
from vibestorm.sync import engine  # noqa: E402
from vibestorm.sync.engine import (  # noqa: E402
    GESTURE_ASSET_TYPE,
    GESTURE_TASK_CAP_NAME,
    _decode_for_disk,
    _encode_for_upload,
    resolve_sync_caps,
)
from vibestorm.sync.naming import (  # noqa: E402
    TEXT_ASSET_TYPES,
    asset_file_suffix,
    upload_kind_for_path,
)
from vibestorm.sync.plan import SKIP, TRANSFER, plan_pull, plan_push  # noqa: E402
from vibestorm.sync.state import SyncState  # noqa: E402

_ANIM = "b906c4ba-703b-1940-32a3-0c7f7d791510"


def _valid() -> bytes:
    """One animation step, which is the four-line kind."""
    return _gesture(steps=f"0\nanim\n{_ANIM}\n0")


def _edited() -> bytes:
    """The same gesture with its trailing flag flipped: valid, and not equal
    to what was pulled, so a push has something to send."""
    return _gesture(steps=f"0\nanim\n{_ANIM}\n1")


class _Row:
    def __init__(self, name: str, asset_type: int):
        self.name = name
        self.asset_type = asset_type
        self.item_id = uuid4()
        self.asset_id = uuid4()


class NamingTests(unittest.TestCase):
    def test_a_gesture_is_one_of_the_text_types(self) -> None:
        self.assertIn(GESTURE_ASSET_TYPE, TEXT_ASSET_TYPES)

    def test_the_suffix_and_the_upload_kind_agree(self) -> None:
        """Pull writes the suffix and push reads it back. If the two ever
        disagree, a pulled gesture stops matching its own row on the way in
        and sync creates a duplicate instead of updating."""
        suffix = asset_file_suffix(GESTURE_ASSET_TYPE)
        self.assertEqual(suffix, ".gesture")
        self.assertEqual(upload_kind_for_path(Path(f"a{suffix}")), ("gesture", "gesture"))


class PullTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.state = SyncState(task_id=str(uuid4()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_gesture_row_is_pulled_without_asking_for_binary(self) -> None:
        """It used to need `include_binary`, which also brought textures and
        animations. A gesture is text and comes out by default now."""
        [entry] = plan_pull(
            [_Row("Wave", GESTURE_ASSET_TYPE)], folder=self.folder, state=self.state
        )
        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(entry.file_name, "Wave.gesture")

    def test_a_texture_row_still_is_not(self) -> None:
        """The floor: adding one type to the set must not have opened it."""
        [entry] = plan_pull([_Row("Cloud", 0)], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, SKIP)

    def test_a_gesture_is_written_as_it_arrived_and_is_pushable(self) -> None:
        """Unlike a notecard, which is a container whose text is extracted and
        which is therefore marked read-only when it carries embedded items."""
        data, readonly = _decode_for_disk(_valid(), GESTURE_ASSET_TYPE)
        self.assertEqual(data, _valid())
        self.assertIsNone(readonly)


class PushTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.state = SyncState(task_id=str(uuid4()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_gesture_file_matches_its_row(self) -> None:
        row = _Row("Wave", GESTURE_ASSET_TYPE)
        path = self.folder / "Wave.gesture"
        path.write_bytes(_valid())
        [entry] = plan_push([path], {row.name: row}, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(entry.asset_type, GESTURE_ASSET_TYPE)
        self.assertEqual(entry.item_id, str(row.item_id))

    def test_a_valid_gesture_is_sent_byte_for_byte(self) -> None:
        """Checked, not re-encoded. Round-tripping it through the decoder
        would drop anything the decoder does not model, and a gesture is
        already the asset -- there is no container to rebuild."""
        self.assertEqual(_encode_for_upload(_valid(), GESTURE_ASSET_TYPE), _valid())

    def test_a_malformed_gesture_is_refused_before_it_is_sent(self) -> None:
        """The simulator would have taken it. Nothing checks a gesture on the
        far side, so an unreadable one sits in the object until somebody
        plays it and it does nothing."""
        for name, payload in (
            ("truncated mid-step", _valid()[:30]),
            ("a step count that lies", _gesture(steps="", count=3)),
            ("not a gesture at all", b"default { state_entry() { } }"),
            ("empty", b""),
        ):
            with self.subTest(name):
                with self.assertRaises(GestureDecodeError):
                    _encode_for_upload(payload, GESTURE_ASSET_TYPE)

    def test_the_other_text_types_are_unaffected_by_the_check(self) -> None:
        """The floor under the refusal: a script is not parsed here, and
        making the gesture branch too eager would have broken the type this
        whole feature was modelled on."""
        self.assertEqual(_encode_for_upload(b"default { }", 10), b"default { }")


class EnginePushTests(_EngineCase):
    """The upload loop, not the encoder it calls.

    Everything above tests a function in isolation, and isolation is exactly
    what let a gesture be sent to the notecard capability with every one of
    those tests still green: the routing lives in the loop.
    """

    async def asyncSetUp(self) -> None:
        self._install(lambda obj, name, value: self.patch(obj, name, value))
        self.item = self.add_asset("Wave", _valid(), asset_type="gesture")
        await self.pull()

    async def test_a_gesture_goes_out_through_the_gesture_capability(self) -> None:
        (self.folder / "Wave.gesture").write_bytes(_edited())
        outcome = await self.push()
        self.assertEqual(outcome.failed, [])
        self.assertEqual(self.calls, [("gesture", "http://cap/gesture")])

    async def test_a_malformed_gesture_is_reported_and_never_sent(self) -> None:
        """Not merely reported: the upload must not happen. A gesture the
        simulator accepts and nothing can play is worse than a failure."""
        (self.folder / "Wave.gesture").write_bytes(b"nonsense")
        outcome = await self.push()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(outcome.failed), 1)
        name, reason = outcome.failed[0]
        self.assertEqual(name, "Wave.gesture")
        self.assertIn("not a readable gesture", reason)

    async def test_a_sim_without_the_capability_skips_rather_than_guesses(self) -> None:
        (self.folder / "Wave.gesture").write_bytes(_edited())
        outcome = await self.push(gesture_cap=None)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            outcome.skipped, [("Wave.gesture", "no capability for this asset type")]
        )


class UploadClientTests(unittest.IsolatedAsyncioTestCase):
    """`upload_task_gesture`, which is nine lines and three of them matter."""

    def setUp(self) -> None:
        self.item_id = uuid4()
        self.task_id = uuid4()
        self.posted: list[tuple[str, bytes]] = []
        self.prelude_state = "upload"

    def _serve(self):
        outer = self

        class FakeResponse:
            def __init__(self, body: bytes):
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, amt: int = -1) -> bytes:
                return serve_body(self, self._body, amt)

        def fake_urlopen(request, timeout):
            outer.posted.append((request.full_url, request.data))
            if len(outer.posted) == 1:
                return FakeResponse(
                    b'<?xml version="1.0"?><llsd><map>'
                    b"<key>state</key><string>" + outer.prelude_state.encode() + b"</string>"
                    b"<key>uploader</key><string>http://example.invalid/up/1</string>"
                    b"</map></llsd>"
                )
            return FakeResponse(
                b'<?xml version="1.0"?><llsd><map>'
                b"<key>state</key><string>complete</string>"
                b"<key>new_asset</key><string>12345678-1111-2222-3333-444444444444</string>"
                b"</map></llsd>"
            )

        return fake_urlopen

    async def _upload(self, body: bytes = b"") -> object:
        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = self._serve()  # type: ignore[assignment]
        try:
            return await TaskInventoryUploadClient().upload_task_gesture(
                "http://example.invalid/cap/gesture",
                self.item_id,
                self.task_id,
                body or _valid(),
            )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]

    async def test_the_bytes_go_to_the_uploader_not_the_capability(self) -> None:
        """Two hops. Posting the asset back to the capability url asks it to
        hand out a second uploader and drops the gesture on the floor."""
        result = await self._upload()
        self.assertEqual(len(self.posted), 2)
        self.assertEqual(self.posted[0][0], "http://example.invalid/cap/gesture")
        self.assertEqual(self.posted[1][0], "http://example.invalid/up/1")
        self.assertEqual(self.posted[1][1], _valid())
        self.assertEqual(result.state, "complete")

    async def test_the_prelude_names_both_ids(self) -> None:
        """`UpdateTaskInventory` rejects a zero item id, and a missing task id
        makes it an agent-inventory update against an object that is not
        there."""
        await self._upload()
        body = self.posted[0][1]
        self.assertIn(str(self.item_id).encode(), body)
        self.assertIn(str(self.task_id).encode(), body)

    async def test_a_prelude_that_is_not_an_upload_is_an_error(self) -> None:
        """Otherwise `prelude.uploader_url` is None and the asset is POSTed
        to the string "None"."""
        # Not "error": `request_uploader` catches that one itself, and a
        # test that used it would pass with this check deleted.
        self.prelude_state = "pending"
        with self.assertRaises(TaskInventoryUploadError) as caught:
            await self._upload()
        self.assertEqual(len(self.posted), 1)
        self.assertIn("gesture", str(caught.exception))

    async def test_a_failure_says_gesture_rather_than_notecard(self) -> None:
        """The second hop shares its implementation with the notecard path,
        which is why the label is a parameter: an owner told a gesture failed
        to upload as a notecard goes looking in the wrong place."""
        import urllib.request

        class Boom:
            def __enter__(self):
                raise TimeoutError("too slow")

            def __exit__(self, *a):
                return False

        served = self._serve()

        def fake_urlopen(request, timeout):
            if not self.posted:
                return served(request, timeout)
            self.posted.append((request.full_url, request.data))
            return Boom()

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
        try:
            with self.assertRaises(TaskInventoryUploadError) as caught:
                await TaskInventoryUploadClient().upload_task_gesture(
                    "http://example.invalid/cap/gesture", self.item_id, self.task_id, _valid()
                )
        finally:
            urllib.request.urlopen = original  # type: ignore[assignment]
        message = str(caught.exception).lower()
        self.assertIn("gesture", message)
        self.assertNotIn("notecard", message)


class CapabilityTests(unittest.TestCase):
    def test_the_gesture_capability_is_asked_for(self) -> None:
        """A capability nobody requests is a capability the simulator never
        resolves, and the push would report "no capability for this asset
        type" for a sim that offers one."""
        asked: list[list[str]] = []

        class FakeClient:
            def __init__(self, timeout_seconds: float = 10.0) -> None:
                pass

            async def resolve_seed_caps(self, seed, names, **kwargs):
                asked.append(list(names))
                return {name: f"http://example.invalid/{name}" for name in names}

        class FakeSession:
            bootstrap = type("B", (), {"seed_capability": "http://example.invalid/seed"})()
            caps_udp_listen_port = 9000

        original = engine.CapabilityClient
        engine.CapabilityClient = FakeClient
        try:
            caps = asyncio.run(resolve_sync_caps(FakeSession()))
        finally:
            engine.CapabilityClient = original

        self.assertIn(GESTURE_TASK_CAP_NAME, asked[0])
        self.assertTrue(caps.gesture)


if __name__ == "__main__":
    unittest.main()
