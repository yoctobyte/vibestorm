"""Matching files back to the inventory rows they came from.

These began as tests of a private helper in ``viewer3d/app.py``, back when the
viewer had its own copy of the rule. The rule now lives in ``vibestorm.sync``
and both directions call it, so this tests it where it is -- and where a CLI
sync, a watch loop and the viewer's Upload button all reach it.

The failure mode worth keeping in mind throughout: a miss here is silent. Push
decides the file is new, creates a *second* row beside the one it was pulled
from, and reports success.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

from vibestorm.sync.naming import (
    asset_file_suffix,
    colliding_file_names,
    file_name_for_item,
    match_files_to_rows,
    safe_filename,
    upload_kind_for_path,
)

SCRIPT = 10
NOTECARD = 7
TEXTURE = 0


@dataclass(frozen=True)
class Row:
    name: str
    asset_type: int


def _match(files: list[str], rows: list[Row]) -> tuple[list[tuple[str, str]], list[str]]:
    matched, unmatched = match_files_to_rows(
        [Path(name) for name in files],
        rows,
        name_of=lambda row: row.name,
        asset_type_of=lambda row: row.asset_type,
    )
    return (
        [(path.name, row.name) for path, row in matched],
        [path.name for path in unmatched],
    )


class MatchFilesToRowsTests(unittest.TestCase):
    def test_a_script_file_finds_its_script_row(self) -> None:
        matched, unmatched = _match(["Main Script.lsl"], [Row("Main Script", SCRIPT)])

        self.assertEqual(matched, [("Main Script.lsl", "Main Script")])
        self.assertEqual(unmatched, [])

    def test_a_text_file_finds_its_notecard_row(self) -> None:
        matched, _unmatched = _match(["Config Note.txt"], [Row("Config Note", NOTECARD)])

        self.assertEqual(matched, [("Config Note.txt", "Config Note")])

    def test_a_file_with_no_row_is_reported_rather_than_guessed_at(self) -> None:
        matched, unmatched = _match(["Main Script.lsl"], [Row("Other Script", SCRIPT)])

        self.assertEqual(matched, [])
        self.assertEqual(unmatched, ["Main Script.lsl"])

    def test_an_item_named_with_its_suffix_still_matches(self) -> None:
        # In-world names routinely carry the suffix already: the local test
        # prim holds an item genuinely called ``vibestorm-sync-88338.lsl``.
        # Matching on the file's stem misses that, and a miss creates a
        # duplicate row rather than failing.
        matched, unmatched = _match(
            ["vibestorm-sync-88338.lsl"], [Row("vibestorm-sync-88338.lsl", SCRIPT)]
        )

        self.assertEqual(matched, [("vibestorm-sync-88338.lsl", "vibestorm-sync-88338.lsl")])
        self.assertEqual(unmatched, [])

    def test_a_hand_made_file_matches_an_item_named_differently_cased(self) -> None:
        matched, _unmatched = _match(["greeter.lsl"], [Row("Greeter", SCRIPT)])

        self.assertEqual(matched, [("greeter.lsl", "Greeter")])

    def test_a_script_never_matches_a_row_of_another_type(self) -> None:
        # The later passes match on the stem, which drops the suffix -- the
        # only thing separating a script from a texture. Without the type
        # check a prim holding a texture called ``Greeter`` would capture a
        # new ``Greeter.lsl`` and push LSL source into a texture row.
        matched, unmatched = _match(["My Texture.lsl"], [Row("My Texture", TEXTURE)])

        self.assertEqual(matched, [])
        self.assertEqual(unmatched, ["My Texture.lsl"])

    def test_a_file_of_a_type_that_cannot_be_uploaded_matches_nothing(self) -> None:
        matched, unmatched = _match(["readme.md"], [Row("readme", NOTECARD)])

        self.assertEqual(matched, [])
        self.assertEqual(unmatched, ["readme.md"])

    def test_one_row_is_claimed_by_only_one_file(self) -> None:
        # Two files can both look like a match for one row -- the exact name
        # and the sanitised stem. Handing the row to both would upload one
        # over the other and report two successes.
        matched, unmatched = _match(["Greeter.lsl", "greeter.lsl"], [Row("Greeter", SCRIPT)])

        self.assertEqual(len(matched), 1)
        self.assertEqual(len(unmatched), 1)


class FileNameTests(unittest.TestCase):
    def test_pull_and_push_agree_on_the_name(self) -> None:
        # The round trip: whatever pull writes, push has to find again. These
        # two functions disagreeing is the duplicate-row bug in its purest
        # form, so pin them against each other rather than against a literal.
        for name, asset_type in (
            ("Main Script", SCRIPT),
            ("Config Note", NOTECARD),
            ("already.lsl", SCRIPT),
            ("awkward/name: here", SCRIPT),
        ):
            with self.subTest(name=name):
                written = file_name_for_item(name, asset_type)
                matched, unmatched = _match([written], [Row(name, asset_type)])

                self.assertEqual(unmatched, [])
                self.assertEqual(matched, [(written, name)])

    def test_a_name_is_not_given_its_suffix_twice(self) -> None:
        self.assertEqual(file_name_for_item("greeter.lsl", SCRIPT), "greeter.lsl")

    def test_an_empty_name_still_produces_a_file_name(self) -> None:
        self.assertEqual(safe_filename("   "), "unnamed")
        self.assertEqual(file_name_for_item("", SCRIPT), "unnamed.lsl")

    def test_a_verified_container_gets_its_real_extension(self) -> None:
        self.assertEqual(asset_file_suffix(SCRIPT), ".lsl")
        self.assertEqual(asset_file_suffix(NOTECARD), ".txt")
        self.assertEqual(asset_file_suffix(TEXTURE), ".j2k")

    def test_an_unverified_type_gets_its_own_name_rather_than_a_guess(self) -> None:
        # A guessed extension is worse than an honest one: ``.ogg`` on a sound
        # this tree has never opened invites a tool to fail confusingly on the
        # day it turns out to be something else.
        self.assertEqual(asset_file_suffix(1), ".sound")
        self.assertEqual(asset_file_suffix(9999), ".bin")

    def test_only_the_authorable_types_are_uploadable(self) -> None:
        self.assertEqual(upload_kind_for_path(Path("a.lsl")), ("lsltext", "lsl"))
        self.assertEqual(upload_kind_for_path(Path("a.txt")), ("notecard", "notecard"))
        self.assertIsNone(upload_kind_for_path(Path("a.j2k")))
        self.assertIsNone(upload_kind_for_path(Path("a")))


class CollisionTests(unittest.TestCase):
    def test_two_items_wanting_one_file_name_are_reported(self) -> None:
        # An object may legitimately hold both ``notes`` and ``notes.lsl``.
        # Both want the file ``notes.lsl``, and no folder can hold two files
        # with one name -- so the sync has to say so rather than write one
        # over the other and push the survivor back to whichever row it found
        # first.
        collisions = colliding_file_names(
            [Row("notes", SCRIPT), Row("notes.lsl", SCRIPT)],
            name_of=lambda row: row.name,
            asset_type_of=lambda row: row.asset_type,
        )

        self.assertEqual(collisions, {"notes.lsl"})

    def test_distinct_names_do_not_collide(self) -> None:
        collisions = colliding_file_names(
            [Row("notes", SCRIPT), Row("notes", NOTECARD)],
            name_of=lambda row: row.name,
            asset_type_of=lambda row: row.asset_type,
        )

        self.assertEqual(collisions, set())


if __name__ == "__main__":
    unittest.main()
