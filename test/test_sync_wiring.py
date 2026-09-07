"""The push loop's wiring, for every type that goes through it.

These tests exist because of a mutation battery. The gesture round trip's
battery found eight survivors and all eight were one mistake: every test drove
a *piece* -- `_encode_for_upload`, `create_task_texture`,
`create_task_script_rows` -- and nothing drove the loop that decides which
piece runs. Routing a gesture to the notecard capability was invisible.

So the same battery was pointed at the two types built earlier, on the
assumption that nothing about them was different. Eleven of twelve mutants
survived: notecards could go out through the script capability, either create
flag could stop reaching the planner, a capability could stop being resolved,
and a created row could be recorded under the name that was *asked for* rather
than the one the object gave it -- which is the bug that makes the next push
create a second copy.

None of that is subtle. It was simply in the one place nothing looked.

The rule this file is written to: **a type is not wired up until a test drives
`push_folder_to_object` and observes which capability the bytes went to.**
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sync_engine import _EngineCase, _item  # noqa: E402

from vibestorm.sync import engine  # noqa: E402
from vibestorm.sync.state import SyncState  # noqa: E402
from vibestorm.sync.engine import (  # noqa: E402
    NEW_FILE_CAP_NAME,
    NOTECARD_AGENT_CAP_NAME,
    resolve_sync_caps,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _WiringCase(_EngineCase):
    """The engine with every create helper faked, so the loop is what runs."""

    async def asyncSetUp(self) -> None:
        self._install(lambda obj, name, value: self.patch(obj, name, value))
        #: (kind, name asked for, bytes) per create.
        self.creates: list[tuple[str, str, bytes]] = []
        #: What the object renames a created row to. "" means no rename.
        self.rename_to = ""
        outer = self

        def make(kind: str, asset_type: str):
            async def fake(client, session, *, name, **kwargs):
                data = kwargs.get("data") or kwargs.get("text", "").encode()
                outer.creates.append((kind, name, data))
                assigned = outer.rename_to or name
                item = _item(assigned, asset_type)
                outer.items.append(item)
                outer.assets[item.asset_id] = data
                return item.item_id, assigned

            return fake

        self.patch(engine, "create_task_notecard", make("notecard", "notecard"))
        self.patch(engine, "create_task_texture", make("texture", "texture"))
        self.patch(engine, "create_task_gesture", make("gesture", "gesture"))

    async def push_creating(self, **kwargs):
        """A push with every create capability the sim could offer."""
        kwargs.setdefault("notecard_agent_cap", "http://cap/notecard-agent")
        kwargs.setdefault("new_file_cap", "http://cap/new-file")
        kwargs.setdefault("gesture_agent_cap", "http://cap/gesture-agent")
        kwargs.setdefault("agent_folder_id", uuid4())
        return await self.push(**kwargs)


class UpdateRoutingTests(_WiringCase):
    """Which capability an *edited* file's bytes are sent to."""

    async def test_a_script_goes_to_the_script_capability(self) -> None:
        self.add_asset("Greeter", b"original", asset_type="lsltext")
        await self.pull()
        (self.folder / "Greeter.lsl").write_bytes(b"edited")
        await self.push()
        self.assertEqual(self.calls, [("script", "http://cap/script")])

    async def test_a_notecard_goes_to_the_notecard_capability(self) -> None:
        from vibestorm.assets.notecard import encode_notecard

        self.add_asset("Notes", encode_notecard("original"), asset_type="notecard")
        await self.pull()
        (self.folder / "Notes.txt").write_bytes(b"edited")
        await self.push()
        self.assertEqual(self.calls, [("notecard", "http://cap/notecard")])


class CreateGatingTests(_WiringCase):
    """A create needs its own capability, and only its own."""

    async def test_a_notecard_is_created_when_the_capability_is_there(self) -> None:
        (self.folder / "Notes.txt").write_bytes(b"hello")
        outcome = await self.push_creating()
        self.assertEqual(outcome.created, ["Notes.txt"])
        self.assertEqual([kind for kind, _n, _d in self.creates], ["notecard"])

    async def test_a_notecard_is_not_created_without_it(self) -> None:
        (self.folder / "Notes.txt").write_bytes(b"hello")
        outcome = await self.push_creating(notecard_agent_cap=None)
        self.assertEqual(self.creates, [])
        self.assertEqual(outcome.created, [])
        self.assertEqual(len(outcome.skipped), 1)

    async def test_a_texture_is_created_when_the_capability_is_there(self) -> None:
        (self.folder / "sunset.png").write_bytes(PNG)
        outcome = await self.push_creating()
        self.assertEqual(outcome.created, ["sunset.png"])
        self.assertEqual([kind for kind, _n, _d in self.creates], ["texture"])

    async def test_a_texture_is_not_created_without_it(self) -> None:
        (self.folder / "sunset.png").write_bytes(PNG)
        outcome = await self.push_creating(new_file_cap=None)
        self.assertEqual(self.creates, [])
        self.assertEqual(len(outcome.skipped), 1)

    async def test_no_agent_folder_disables_every_create(self) -> None:
        """The folder is the second half of both conditions: the capability
        says the sim can, the folder says where to put it."""
        (self.folder / "Notes.txt").write_bytes(b"hello")
        (self.folder / "sunset.png").write_bytes(PNG)
        (self.folder / "Wave.gesture").write_bytes(b"anything")
        outcome = await self.push_creating(agent_folder_id=None)
        self.assertEqual(self.creates, [])
        self.assertEqual(len(outcome.skipped), 3)

    async def test_one_capability_does_not_unlock_another_type(self) -> None:
        (self.folder / "Notes.txt").write_bytes(b"hello")
        (self.folder / "sunset.png").write_bytes(PNG)
        outcome = await self.push_creating(new_file_cap=None)
        self.assertEqual([kind for kind, _n, _d in self.creates], ["notecard"])
        self.assertEqual(outcome.created, ["Notes.txt"])


class AssignedNameTests(_WiringCase):
    """A created row is bound to the name the *object* gave it.

    The simulator renames a copy whose name collides -- `Notes` arrives as
    `Notes 1` -- and nothing in the reply says so. Recording the requested name
    means the next push finds no row for `Notes.txt` and creates a second one,
    every time it runs.
    """

    async def test_a_renamed_notecard_is_not_created_twice(self) -> None:
        self.rename_to = "Notes 1"
        (self.folder / "Notes.txt").write_bytes(b"hello")
        await self.push_creating()
        outcome = await self.push_creating()
        self.assertEqual(len(self.creates), 1, "created a second copy")
        self.assertEqual(outcome.created, [])

    async def test_a_renamed_texture_is_not_created_twice(self) -> None:
        self.rename_to = "sunset 1"
        (self.folder / "sunset.png").write_bytes(PNG)
        await self.push_creating()
        outcome = await self.push_creating()
        self.assertEqual(len(self.creates), 1, "created a second copy")
        self.assertEqual(outcome.created, [])


class ExistingTextureTests(_WiringCase):
    """An image whose name is already in the object.

    There is no `UpdateTextureTaskInventory`, so the only alternative to
    skipping it is uploading a second asset that arrives as `sunset 1`. The
    names come out of the inventory snapshot, and a snapshot read that dropped
    them would make every push add another copy.
    """

    async def test_an_image_already_in_the_object_is_skipped(self) -> None:
        self.items.append(_item("sunset", "texture"))
        (self.folder / "sunset.png").write_bytes(PNG)
        outcome = await self.push_creating()
        self.assertEqual(self.creates, [])
        self.assertEqual(len(outcome.skipped), 1)

    async def test_the_match_ignores_case_and_the_suffix(self) -> None:
        self.items.append(_item("Sunset", "texture"))
        (self.folder / "sunset.png").write_bytes(PNG)
        outcome = await self.push_creating()
        self.assertEqual(self.creates, [])
        self.assertEqual(len(outcome.skipped), 1)


class PullDispatchTests(_WiringCase):
    """The pull loop's half of the same question: which decoder runs.

    Its battery found two survivors where the push loop's found eight, and
    both were narrow -- but narrow is what a dispatch bug looks like until the
    input that shows it turns up.
    """

    async def test_a_gesture_is_written_as_the_object_holds_it(self) -> None:
        """Byte for byte, including bytes that are not valid UTF-8.

        Routing a gesture through `decode_notecard` looks harmless -- that
        function returns unrecognised bytes as text, so a plain gesture comes
        back identical -- and it is not: the fallback decodes with
        ``errors="replace"`` and re-encodes, so any byte that is not valid
        UTF-8 comes out of the pull as U+FFFD and can never be pushed back.

        A trigger word typed in a viewer that was not speaking UTF-8 is all
        this takes.
        """
        raw = b"2\n255\n0\n/caf\xe9\n\n1\n0\nanim\nb906c4ba-703b-1940-32a3-0c7f7d791510\n0\n"
        self.assertNotEqual(raw.decode("utf-8", errors="replace").encode("utf-8"), raw)
        self.add_asset("Cafe", raw, asset_type="gesture")

        await self.pull()

        written = self.folder / "Cafe.gesture"
        self.assertEqual(written.read_bytes(), raw)
        record = SyncState.load(self.folder, task_id=self.task_id).by_file_name("Cafe.gesture")
        self.assertFalse(record.readonly, "a gesture pulled intact is pushable")

    async def test_an_exported_binary_is_marked_unpushable_and_says_why(self) -> None:
        """`include_binary` exports what sync cannot author. A file written
        without that mark reads as editable, and the next push sends whatever
        an image editor happened to save -- to a capability that does not
        exist for its type."""
        self.add_asset("Cloud", b"\xff\x4f\xff\x51 not really a codestream", asset_type="texture")

        outcome = await self.pull(include_binary=True)

        record = SyncState.load(self.folder, task_id=self.task_id).by_file_name("Cloud.j2k")
        self.assertIsNotNone(record, f"nothing pulled; skipped={outcome.skipped}")
        self.assertTrue(record.readonly)
        self.assertIn("texture", record.readonly_reason)


class CapabilityResolutionTests(unittest.TestCase):
    """Every capability the push branches on has to be asked for and kept.

    A field that is resolved but never assigned reads as "the sim does not
    offer it", and the feature it gates goes quiet rather than failing.
    """

    def test_every_capability_the_engine_uses_survives_resolution(self) -> None:
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

        for name in (NOTECARD_AGENT_CAP_NAME, NEW_FILE_CAP_NAME):
            self.assertIn(name, asked[0], name)
        fields = ("script", "notecard", "notecard_agent", "gesture", "gesture_agent", "new_file")
        for field in fields:
            self.assertTrue(getattr(caps, field), field)
        self.assertTrue(caps.can_create_notecards)
        self.assertTrue(caps.can_create_gestures)
        self.assertTrue(caps.can_upload_textures)


if __name__ == "__main__":
    unittest.main()
