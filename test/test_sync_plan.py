"""The sync planner's rules.

These are the decisions that make an object-to-folder sync safe to run
repeatedly: which files are already in step, which have been edited here,
which moved in world, and which changed on both sides and must not be
silently overwritten. All of it is decidable from an inventory listing, a
folder and the recorded state, so it is tested without a simulator.
"""

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from vibestorm.sync.plan import (
    CONFLICT,
    SKIP,
    TRANSFER,
    UNCHANGED,
    plan_pull,
    plan_push,
)
from vibestorm.sync.state import SyncState, SyncedItem, content_digest


@dataclass
class Row:
    """Stands in for a task-inventory row, with the fields the planner reads."""

    name: str
    asset_type: int
    item_id: UUID | None = None
    asset_id: UUID | None = None


def _row(name="Greeter", asset_type=10, asset_id=None) -> Row:
    return Row(
        name=name,
        asset_type=asset_type,
        item_id=uuid4(),
        asset_id=asset_id or uuid4(),
    )


class _FolderCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.task_id = uuid4()
        self.state = SyncState(task_id=str(self.task_id))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.folder / name
        path.write_text(text, encoding="utf-8")
        return path

    def track(self, file_name: str, text: str, row: Row) -> None:
        """Record that ``text`` is what both sides last agreed on for ``row``."""
        self.state.record(
            SyncedItem(
                file_name=file_name,
                item_name=row.name,
                asset_type=row.asset_type,
                item_id=str(row.item_id),
                synced_digest=content_digest(text.encode()),
                synced_asset_id=str(row.asset_id),
            )
        )


class PlanPullTests(_FolderCase):
    def test_a_row_with_no_local_file_is_fetched(self) -> None:
        [entry] = plan_pull([_row()], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(entry.file_name, "Greeter.lsl")

    def test_non_text_assets_are_skipped(self) -> None:
        [entry] = plan_pull([_row(asset_type=0)], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, SKIP)
        self.assertIn("text", entry.reason)

    def test_an_untracked_local_file_is_a_conflict_not_an_overwrite(self) -> None:
        # The folder has a file we have never synced. It might be the user's
        # only copy of something; refusing is the only safe answer.
        self.write("Greeter.lsl", "mine")
        [entry] = plan_pull([_row()], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, CONFLICT)
        self.assertIn("not tracked", entry.reason)

    def test_an_untracked_local_file_can_be_overwritten_on_request(self) -> None:
        self.write("Greeter.lsl", "mine")
        [entry] = plan_pull(
            [_row()], folder=self.folder, state=self.state, overwrite_untracked=True
        )
        self.assertEqual(entry.action, TRANSFER)

    def test_matching_content_and_asset_is_left_alone(self) -> None:
        row = _row()
        self.write("Greeter.lsl", "same")
        self.track("Greeter.lsl", "same", row)
        [entry] = plan_pull([row], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, UNCHANGED)

    def test_a_new_asset_id_means_the_in_world_copy_changed(self) -> None:
        row = _row()
        self.write("Greeter.lsl", "same")
        self.track("Greeter.lsl", "same", row)
        moved = Row(
            name=row.name, asset_type=row.asset_type, item_id=row.item_id, asset_id=uuid4()
        )
        [entry] = plan_pull([moved], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertIn("in-world", entry.reason)

    def test_a_local_edit_is_not_overwritten_by_an_unchanged_asset(self) -> None:
        row = _row()
        self.track("Greeter.lsl", "original", row)
        self.write("Greeter.lsl", "my edit")
        [entry] = plan_pull([row], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, UNCHANGED)
        self.assertIn("ahead", entry.reason)

    def test_edits_on_both_sides_are_reported_as_a_conflict(self) -> None:
        row = _row()
        self.track("Greeter.lsl", "original", row)
        self.write("Greeter.lsl", "my edit")
        moved = Row(
            name=row.name, asset_type=row.asset_type, item_id=row.item_id, asset_id=uuid4()
        )
        [entry] = plan_pull([moved], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, CONFLICT)

    def test_awkward_item_names_become_safe_file_names(self) -> None:
        [entry] = plan_pull(
            [_row(name="path/to: script")], folder=self.folder, state=self.state
        )
        self.assertEqual(entry.file_name, "path_to_ script.lsl")
        self.assertNotIn("/", entry.file_name)


class PlanPushTests(_FolderCase):
    def test_an_edited_file_is_uploaded(self) -> None:
        row = _row()
        self.track("Greeter.lsl", "original", row)
        path = self.write("Greeter.lsl", "edited")
        [entry] = plan_push([path], {row.name: row}, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(entry.item_id, str(row.item_id))

    def test_an_unchanged_file_is_left_alone(self) -> None:
        row = _row()
        self.track("Greeter.lsl", "same", row)
        path = self.write("Greeter.lsl", "same")
        [entry] = plan_push([path], {row.name: row}, state=self.state)
        self.assertEqual(entry.action, UNCHANGED)

    def test_an_untracked_file_matching_a_row_is_uploaded(self) -> None:
        row = _row()
        path = self.write("Greeter.lsl", "content")
        [entry] = plan_push([path], {row.name: row}, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertIn("not tracked", entry.reason)

    def test_edits_on_both_sides_are_reported_as_a_conflict(self) -> None:
        row = _row()
        self.track("Greeter.lsl", "original", row)
        path = self.write("Greeter.lsl", "edited")
        moved = Row(
            name=row.name, asset_type=row.asset_type, item_id=row.item_id, asset_id=uuid4()
        )
        [entry] = plan_push([path], {moved.name: moved}, state=self.state)
        self.assertEqual(entry.action, CONFLICT)

    def test_a_script_with_no_row_is_created(self) -> None:
        path = self.write("Brand New.lsl", "content")
        [entry] = plan_push([path], {}, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertTrue(entry.create)
        self.assertEqual(entry.item_name, "Brand New")

    def test_a_notecard_with_no_row_needs_the_create_capability(self) -> None:
        # A notecard has no create-from-nothing message: it is built in agent
        # inventory and copied in, so without that capability there is nothing
        # the sync can do but say so.
        path = self.write("Readme.txt", "content")
        [entry] = plan_push([path], {}, state=self.state)
        self.assertEqual(entry.action, SKIP)
        self.assertIn("capability", entry.reason)

    def test_a_notecard_with_no_row_is_created_when_it_can_be(self) -> None:
        path = self.write("Readme.txt", "content")
        [entry] = plan_push([path], {}, state=self.state, can_create_notecards=True)
        self.assertEqual(entry.action, TRANSFER)
        self.assertTrue(entry.create)
        self.assertEqual(entry.asset_type, 7)
        self.assertEqual(entry.item_name, "Readme")

    def test_creation_can_be_disabled(self) -> None:
        path = self.write("Brand New.lsl", "content")
        [entry] = plan_push([path], {}, state=self.state, can_create=False)
        self.assertEqual(entry.action, SKIP)

    def test_unrelated_files_are_skipped_not_uploaded(self) -> None:
        path = self.write("notes.pdf", "x")
        [entry] = plan_push([path], {}, state=self.state)
        self.assertEqual(entry.action, SKIP)
        self.assertIn("uploadable", entry.reason)

    def test_a_pulled_name_matches_its_row_on_the_way_back(self) -> None:
        # The round trip that matters: pull names the file, push has to find
        # the same row again. An in-world name needing sanitising is where
        # the two directions would drift apart.
        from vibestorm.sync.naming import file_name_for_item

        row = _row(name="path/to: script")
        file_name = file_name_for_item(row.name, row.asset_type)
        path = self.write(file_name, "content")
        [entry] = plan_push([path], {row.name: row}, state=self.state)
        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(entry.item_id, str(row.item_id))
        self.assertFalse(entry.create, "it must update the row, not make a second one")


if __name__ == "__main__":
    unittest.main()


class SuffixInItemNameTests(_FolderCase):
    """In-world item names often already end in ``.lsl``.

    Found live: the test prim holds an item genuinely called
    ``vibestorm-sync-88338.lsl``. Pull writes that name unchanged (the suffix
    is already there), but ``Path.stem`` then yields
    ``vibestorm-sync-88338``, which matches no row. The sync did not fail --
    it decided the file was new and created a *second* row beside the one it
    had just pulled from.
    """

    def test_a_row_whose_name_ends_in_the_suffix_round_trips(self) -> None:
        from vibestorm.sync.naming import file_name_for_item

        row = _row(name="vibestorm-sync-88338.lsl")
        file_name = file_name_for_item(row.name, row.asset_type)
        self.assertEqual(file_name, "vibestorm-sync-88338.lsl")

        self.track(file_name, "original", row)
        path = self.write(file_name, "edited")

        [entry] = plan_push([path], {row.name: row}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertFalse(entry.create, "it must update the row, not create a duplicate")
        self.assertEqual(entry.item_id, str(row.item_id))

    def test_a_notecard_named_with_its_suffix_round_trips(self) -> None:
        row = _row(name="readme.txt", asset_type=7)
        self.track("readme.txt", "original", row)
        path = self.write("readme.txt", "edited")

        [entry] = plan_push([path], {row.name: row}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertFalse(entry.create)

    def test_a_hand_made_file_still_matches_an_item_without_a_suffix(self) -> None:
        # The friendlier match must survive: someone writes greeter.lsl by hand
        # for an object holding an item plainly called "Greeter".
        row = _row(name="Greeter")
        path = self.write("greeter.lsl", "content")

        [entry] = plan_push([path], {row.name: row}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertFalse(entry.create)
        self.assertEqual(entry.item_id, str(row.item_id))

    def test_two_items_claiming_one_file_name_is_a_conflict(self) -> None:
        # Items "notes" and "notes.lsl" both want the file notes.lsl. There is
        # no right answer, so the sync must say so rather than pick one and
        # overwrite the other's content on the next pull.
        plain = _row(name="notes")
        suffixed = _row(name="notes.lsl")
        path = self.write("notes.lsl", "content")

        [entry] = plan_push(
            [path], {plain.name: plain, suffixed.name: suffixed}, state=self.state
        )

        self.assertEqual(entry.action, CONFLICT)
        self.assertIn("more than one", entry.reason)

    def test_pull_refuses_to_write_a_name_two_items_claim(self) -> None:
        entries = plan_pull(
            [_row(name="notes"), _row(name="notes.lsl")],
            folder=self.folder,
            state=self.state,
        )
        self.assertEqual([entry.action for entry in entries], [CONFLICT, CONFLICT])


class RenamedItemTests(_FolderCase):
    """A bound file follows its row through a rename.

    Found live: copying a notecard into an object that already held an item of
    that name produced ``sync-made-1788630937 1``. Matching on the name then
    missed the row entirely -- the sync reported a failure for a copy that had
    succeeded, and a later push would have created a third copy.
    """

    def test_push_follows_a_renamed_row_by_its_recorded_id(self) -> None:
        row = _row(name="Greeter")
        self.track("Greeter.lsl", "original", row)
        path = self.write("Greeter.lsl", "edited")

        renamed = Row(
            name="Greeter 1",
            asset_type=row.asset_type,
            item_id=row.item_id,
            asset_id=row.asset_id,
        )
        [entry] = plan_push([path], {renamed.name: renamed}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertFalse(entry.create, "a rename must not spawn a second row")
        self.assertEqual(entry.item_id, str(row.item_id))

    def test_pull_writes_a_renamed_row_back_to_its_own_file(self) -> None:
        row = _row(name="Greeter")
        self.write("Greeter.lsl", "same")
        self.track("Greeter.lsl", "same", row)

        renamed = Row(
            name="Greeter 1", asset_type=row.asset_type, item_id=row.item_id, asset_id=uuid4()
        )
        [entry] = plan_pull([renamed], folder=self.folder, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertEqual(
            entry.file_name, "Greeter.lsl", "it must update the file it came from"
        )

    def test_a_bound_file_is_not_accused_of_colliding_with_itself(self) -> None:
        # "Greeter" is bound to Greeter.lsl. A second item genuinely called
        # "Greeter.lsl" would otherwise be read as a collision for both.
        bound = _row(name="Greeter")
        self.write("Greeter.lsl", "same")
        self.track("Greeter.lsl", "same", bound)
        path = self.folder / "Greeter.lsl"

        [entry] = plan_push([path], {bound.name: bound}, state=self.state)
        self.assertNotEqual(entry.action, CONFLICT)


class BinaryExportTests(_FolderCase):
    """Exporting the asset types sync cannot author.

    Pull can write them; push must never send one back. The interesting cases
    are the ones where the safety comes from the *asset type* rather than from
    bookkeeping that a user could delete.
    """

    def test_binary_assets_are_still_skipped_by_default(self) -> None:
        [entry] = plan_pull([_row(asset_type=0)], folder=self.folder, state=self.state)
        self.assertEqual(entry.action, SKIP)

    def test_include_binary_fetches_them_with_a_typed_suffix(self) -> None:
        rows = [
            _row(name="Cloud", asset_type=0),
            _row(name="Wave", asset_type=20),
            _row(name="Shirt", asset_type=5),
            _row(name="Chime", asset_type=1),
        ]
        entries = plan_pull(
            rows, folder=self.folder, state=self.state, include_binary=True
        )
        self.assertEqual([e.action for e in entries], [TRANSFER] * 4)
        self.assertEqual(
            [e.file_name for e in entries],
            ["Cloud.j2k", "Wave.animation", "Shirt.wearable", "Chime.sound"],
        )

    def test_an_exported_suffix_is_not_uploadable(self) -> None:
        row = _row(name="Cloud", asset_type=0)
        path = self.write("Cloud.j2k", "not really a texture")

        [entry] = plan_push([path], {row.name: row}, state=self.state)

        self.assertEqual(entry.action, SKIP)
        self.assertIn("uploadable", entry.reason)

    def test_a_script_file_does_not_match_a_texture_of_the_same_name(self) -> None:
        # Two of the three matching passes compare *stems*, which drops the
        # suffix and with it the only thing separating "Greeter" the texture
        # from "Greeter.lsl" the script the user is writing. Matching them would
        # push LSL source into a texture row through the script upload
        # capability. The right answer is that the file has no row yet.
        texture = _row(name="Greeter", asset_type=0)
        path = self.write("Greeter.lsl", "default { }")

        [entry] = plan_push([path], {texture.name: texture}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertTrue(entry.create, "it should make a script row of its own")
        self.assertEqual(entry.asset_type, 10)

    def test_a_notecard_file_still_matches_a_notecard_of_the_same_name(self) -> None:
        # The same rule must not break the match it exists to allow.
        note = _row(name="Readme", asset_type=7)
        self.write("Readme.txt", "hello")
        self.track("Readme.txt", "hello", note)
        path = self.write("Readme.txt", "edited")

        [entry] = plan_push([path], {note.name: note}, state=self.state)

        self.assertEqual(entry.action, TRANSFER)
        self.assertFalse(entry.create)
        self.assertEqual(entry.item_id, str(note.item_id))

    def test_a_binary_export_does_not_collide_with_a_script_of_the_same_name(self) -> None:
        # "Greeter" the script and "Greeter" the texture want different files,
        # so neither is a collision.
        rows = [_row(name="Greeter", asset_type=10), _row(name="Greeter", asset_type=0)]
        entries = plan_pull(
            rows, folder=self.folder, state=self.state, include_binary=True
        )
        self.assertEqual([e.action for e in entries], [TRANSFER, TRANSFER])
        self.assertEqual([e.file_name for e in entries], ["Greeter.lsl", "Greeter.j2k"])

    def test_two_binary_rows_wanting_one_file_name_still_conflict(self) -> None:
        rows = [_row(name="Cloud", asset_type=0), _row(name="Cloud", asset_type=0)]
        entries = plan_pull(
            rows, folder=self.folder, state=self.state, include_binary=True
        )
        self.assertEqual([e.action for e in entries], [CONFLICT, CONFLICT])
