"""The sync engine's execution, driven against fakes rather than a simulator.

The rules live in the planner and are tested there. What matters here is that
the engine carries a plan out correctly: it writes what it fetched, records
state that makes the *next* run a no-op, decodes and re-encodes notecards, and
refuses to push a notecard whose embedded items the text cannot represent.

The round trip -- pull, then push with nothing edited, and get "nothing to do"
-- is the property that makes a watch loop safe to run repeatedly. It is the
one an implementation is most likely to get wrong, because a push that fails to
record the asset id the sim assigned looks exactly like an in-world edit on the
following tick.
"""

import asyncio
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from vibestorm.assets.notecard import encode_notecard
from vibestorm.sync.engine import (
    SyncOutcome,
    pull_object_to_folder,
    push_folder_to_object,
    rows_from_snapshot,
)
from vibestorm.sync.state import SyncState
from vibestorm.world.object_inventory import ObjectInventoryItem, ObjectInventorySnapshot


def _item(name, asset_type="lsltext", item_id=None, asset_id=None) -> ObjectInventoryItem:
    return ObjectInventoryItem(
        item_id=item_id or uuid4(),
        asset_id=asset_id or uuid4(),
        parent_id=None,
        name=name,
        description="",
        asset_type=asset_type,
        inventory_type=asset_type,
        raw_fields={},
    )


def _snapshot(items, local_id=42, task_id=None) -> ObjectInventorySnapshot:
    return ObjectInventorySnapshot(
        local_id=local_id,
        task_id=task_id,
        serial=1,
        filename="task.tmp",
        items=tuple(items),
        raw_text="",
    )


@dataclass
class FakeUploadResult:
    state: str = "complete"
    compiled: bool = True
    new_item_id: UUID | None = None
    errors: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class _EngineCase(unittest.IsolatedAsyncioTestCase):
    """Patches the engine's three touch points with the network."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.task_id = uuid4()
        self.local_id = 42
        self.assets: dict[UUID, bytes] = {}
        self.items: list[ObjectInventoryItem] = []
        self.uploads: list[tuple[UUID, bytes]] = []
        self.created_names: list[str] = []
        self.compile_ok = True

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- fakes ------------------------------------------------------------

    async def _fake_inventory(self, client, local_id, **kwargs):
        return _snapshot(self.items, local_id=self.local_id, task_id=self.task_id)

    async def _fake_fetch(self, client, *, asset_id, asset_type, **kwargs):
        return self.assets.get(asset_id)

    async def _fake_create(self, client, session, *, names, **kwargs):
        from vibestorm.sync.task_inventory import CreatedRow

        made = []
        for name in names:
            self.created_names.append(name)
            item = _item(name)
            self.items.append(item)
            made.append(CreatedRow(name=name, item_id=item.item_id, asset_id=item.asset_id))
        return made, []

    def _install(self, patcher):
        import vibestorm.sync.engine as engine

        patcher(engine, "await_object_inventory", self._fake_inventory)
        patcher(engine, "fetch_task_asset", self._fake_fetch)
        patcher(engine, "create_task_script_rows", self._fake_create)

        outer = self

        class FakeUploader:
            def __init__(self, *a, **k):
                pass

            async def upload_task_script(self, cap, item_id, task_id, body, **kwargs):
                outer.uploads.append((item_id, body))
                # A push changes the asset, exactly as the sim does; without
                # this the round-trip test would pass for the wrong reason.
                outer._reassign_asset(item_id)
                return FakeUploadResult(compiled=outer.compile_ok, errors=[] if outer.compile_ok else ["syntax error"])

            async def upload_task_notecard(self, cap, item_id, task_id, body, **kwargs):
                outer.uploads.append((item_id, body))
                outer._reassign_asset(item_id)
                return FakeUploadResult()

        patcher(engine, "TaskInventoryUploadClient", FakeUploader)

    def _reassign_asset(self, item_id: UUID) -> None:
        for index, item in enumerate(self.items):
            if item.item_id == item_id:
                self.items[index] = _item(
                    item.name, item.asset_type, item_id=item.item_id, asset_id=uuid4()
                )
                return

    # -- helpers ----------------------------------------------------------

    def add_asset(self, name, data: bytes, asset_type="lsltext"):
        item = _item(name, asset_type)
        self.items.append(item)
        self.assets[item.asset_id] = data
        return item

    async def pull(self, **kwargs) -> SyncOutcome:
        return await pull_object_to_folder(
            None, task_id=self.task_id, local_id=self.local_id, folder=self.folder, **kwargs
        )

    async def push(self, **kwargs) -> SyncOutcome:
        class FakeSession:
            caps_udp_listen_port = None

        return await push_folder_to_object(
            None,
            FakeSession(),
            handle=1,
            task_id=self.task_id,
            local_id=self.local_id,
            folder=self.folder,
            script_cap="http://cap/script",
            notecard_cap="http://cap/notecard",
            **kwargs,
        )

    def asyncSetUp_patchers(self):
        pass


class PullTests(_EngineCase):
    async def asyncSetUp(self) -> None:
        self._install(lambda obj, name, value: self.patch(obj, name, value))

    def patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, original)

    async def test_a_script_is_written_and_recorded(self) -> None:
        self.add_asset("Greeter", b"default { state_entry() {} }")
        outcome = await self.pull()

        self.assertEqual(outcome.transferred, ["Greeter.lsl"])
        self.assertEqual(
            (self.folder / "Greeter.lsl").read_bytes(), b"default { state_entry() {} }"
        )
        state = SyncState.load(self.folder, task_id=self.task_id)
        self.assertIsNotNone(state.by_file_name("Greeter.lsl"))

    async def test_pulling_twice_changes_nothing_the_second_time(self) -> None:
        self.add_asset("Greeter", b"body")
        await self.pull()
        outcome = await self.pull()

        self.assertEqual(outcome.transferred, [])
        self.assertEqual(outcome.unchanged, ["Greeter.lsl"])

    async def test_a_notecard_is_unwrapped_to_its_text(self) -> None:
        self.add_asset("Readme", encode_notecard("hello notecard"), asset_type="notecard")
        await self.pull()
        self.assertEqual((self.folder / "Readme.txt").read_text(), "hello notecard")

    async def test_an_untracked_local_file_is_reported_not_clobbered(self) -> None:
        self.add_asset("Greeter", b"in world")
        (self.folder / "Greeter.lsl").write_bytes(b"my local work")

        outcome = await self.pull()

        self.assertEqual(outcome.transferred, [])
        self.assertEqual(len(outcome.conflicts), 1)
        self.assertEqual((self.folder / "Greeter.lsl").read_bytes(), b"my local work")

    async def test_an_asset_that_never_arrives_is_a_failure_not_an_empty_file(self) -> None:
        item = _item("Ghost")
        self.items.append(item)  # no bytes registered for it

        outcome = await self.pull()

        self.assertEqual(outcome.transferred, [])
        self.assertEqual(len(outcome.failed), 1)
        self.assertFalse((self.folder / "Ghost.lsl").exists())

    async def test_a_notecard_with_embedded_items_is_marked_unpushable(self) -> None:
        # Two embedded items, per the container header, with no way to
        # re-encode them from the text alone.
        container = (
            b"Linden text version 2\n{\nLLEmbeddedItems version 1\n{\ncount 2\n"
            b"}\n}\nText length 5\nhello"
        )
        self.add_asset("Notes", container, asset_type="notecard")

        await self.pull()

        state = SyncState.load(self.folder, task_id=self.task_id)
        record = state.by_file_name("Notes.txt")
        self.assertTrue(record.readonly)
        self.assertIn("embedded", record.readonly_reason)


class PushTests(_EngineCase):
    async def asyncSetUp(self) -> None:
        self._install(lambda obj, name, value: self.patch(obj, name, value))

    def patch(self, obj, name, value):
        original = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, original)

    async def test_an_edited_file_is_uploaded(self) -> None:
        item = self.add_asset("Greeter", b"original")
        await self.pull()
        (self.folder / "Greeter.lsl").write_bytes(b"edited")

        outcome = await self.push()

        self.assertEqual(outcome.transferred, ["Greeter.lsl"])
        self.assertEqual(self.uploads[-1][1], b"edited")
        self.assertEqual(self.uploads[-1][0], item.item_id)

    async def test_pull_then_push_with_no_edits_uploads_nothing(self) -> None:
        self.add_asset("Greeter", b"body")
        await self.pull()

        outcome = await self.push()

        self.assertEqual(self.uploads, [])
        self.assertEqual(outcome.unchanged, ["Greeter.lsl"])

    async def test_pushing_twice_uploads_once(self) -> None:
        # The regression this guards: a push that does not record the asset id
        # the sim assigned makes the next run think the object changed.
        self.add_asset("Greeter", b"original")
        await self.pull()
        (self.folder / "Greeter.lsl").write_bytes(b"edited")

        await self.push()
        outcome = await self.push()

        self.assertEqual(len(self.uploads), 1, "the second push had nothing to send")
        self.assertEqual(outcome.unchanged, ["Greeter.lsl"])

    async def test_a_new_script_file_creates_a_row_then_fills_it(self) -> None:
        (self.folder / "Brand New.lsl").write_bytes(b"default {}")

        outcome = await self.push()

        self.assertEqual(self.created_names, ["Brand New"])
        self.assertEqual(outcome.created, ["Brand New.lsl"])
        self.assertEqual(self.uploads[-1][1], b"default {}")

    async def test_a_notecard_is_wrapped_before_upload(self) -> None:
        self.add_asset("Readme", encode_notecard("first"), asset_type="notecard")
        await self.pull()
        (self.folder / "Readme.txt").write_text("second")

        await self.push()

        sent = self.uploads[-1][1]
        self.assertEqual(sent, encode_notecard("second"))

    async def test_a_script_that_does_not_compile_is_reported_as_failed(self) -> None:
        self.add_asset("Greeter", b"original")
        await self.pull()
        (self.folder / "Greeter.lsl").write_bytes(b"not lsl")
        self.compile_ok = False

        outcome = await self.push()

        self.assertEqual(outcome.transferred, [])
        self.assertEqual(len(outcome.failed), 1)
        self.assertIn("compile", outcome.failed[0][1])

    async def test_the_state_file_is_not_treated_as_something_to_upload(self) -> None:
        self.add_asset("Greeter", b"body")
        await self.pull()

        outcome = await self.push()

        self.assertNotIn(
            ".vibestorm-sync.json", [name for name, _reason in outcome.skipped]
        )
        self.assertEqual(self.uploads, [])


class RowsFromSnapshotTests(unittest.TestCase):
    def test_asset_type_strings_become_numbers(self) -> None:
        rows = rows_from_snapshot(_snapshot([_item("A", "lsltext"), _item("B", "notecard")]))
        self.assertEqual([row.asset_type for row in rows], [10, 7])

    def test_an_unrecognisable_type_is_dropped_rather_than_guessed(self) -> None:
        rows = rows_from_snapshot(_snapshot([_item("A", "who knows")]))
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()


class ChangedFilesTests(unittest.TestCase):
    """The cheap local check that decides whether a push is worth making."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.task_id = uuid4()
        self.state = SyncState(task_id=str(self.task_id))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _changed(self):
        from vibestorm.sync.watch import changed_files

        return [path.name for path in changed_files(self.folder, self.state)]

    def _track(self, name: str, text: str) -> None:
        from vibestorm.sync.state import SyncedItem, content_digest

        self.state.record(
            SyncedItem(
                file_name=name,
                item_name=Path(name).stem,
                asset_type=10,
                synced_digest=content_digest(text.encode()),
            )
        )

    def test_a_new_file_counts_as_changed(self) -> None:
        (self.folder / "A.lsl").write_text("x")
        self.assertEqual(self._changed(), ["A.lsl"])

    def test_an_unchanged_tracked_file_does_not(self) -> None:
        (self.folder / "A.lsl").write_text("x")
        self._track("A.lsl", "x")
        self.assertEqual(self._changed(), [])

    def test_an_edited_file_does(self) -> None:
        (self.folder / "A.lsl").write_text("y")
        self._track("A.lsl", "x")
        self.assertEqual(self._changed(), ["A.lsl"])

    def test_a_rewrite_with_identical_content_does_not(self) -> None:
        # Saving without editing, or a checkout that rewrites mtimes, must not
        # trigger an upload -- which is why this compares content, not mtime.
        path = self.folder / "A.lsl"
        path.write_text("x")
        self._track("A.lsl", "x")
        path.write_text("x")
        self.assertEqual(self._changed(), [])

    def test_the_state_file_and_dotfiles_are_ignored(self) -> None:
        (self.folder / ".vibestorm-sync.json").write_text("{}")
        (self.folder / ".hidden.lsl").write_text("x")
        self.assertEqual(self._changed(), [])

    def test_files_that_cannot_be_uploaded_are_ignored(self) -> None:
        (self.folder / "notes.pdf").write_text("x")
        self.assertEqual(self._changed(), [])

    def test_a_missing_folder_is_not_an_error(self) -> None:
        from vibestorm.sync.watch import changed_files

        self.assertEqual(changed_files(self.folder / "nope", self.state), [])
