"""Tests for the wearable asset decoder.

Two real fixtures, both fetched over `ViewerAsset` on 2026-08-14: the OpenSim
library's ``Shirt`` (clothing, type 5) and ``Hair`` (body part, type 13). They
matter separately — the two inventory types share one file format, and only
having one of them would leave that assumption untested.

The format is line-oriented with counted lists, so the failure mode is a count
read at the wrong place silently swallowing or leaking lines.
"""

import re
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.assets.wearable import (
    BAKE_TEXTURE_INDICES,
    HEADER_LINE_COUNT,
    TEXTURE_SLOT_COUNT,
    WEARABLE_TYPE_NAMES,
    WearableDecodeError,
    decode_wearable,
)

_ROOT = Path(__file__).resolve().parents[1]
_SHIRT = _ROOT / "test" / "fixtures" / "library" / "clothing-Shirt.bin"
_HAIR = _ROOT / "test" / "fixtures" / "library" / "bodypart-Hair.bin"
_GATHERER = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "Framework" / "Scenes"
    / "UuidGatherer.cs"
)
_WEARABLE_CS = _ROOT / "opensim-source" / "OpenSim" / "Framework" / "AvatarWearable.cs"
_APPEARANCE_CS = (
    _ROOT / "opensim-source" / "OpenSim" / "Framework" / "AvatarAppearance.cs"
)


def _build(body: str, *, version: int = 22, name: str = "Thing") -> bytes:
    return (
        f"LLWearable version {version}\n{name}\n\n\tpermissions 0\n{body}"
    ).encode()


class SourcePinTests(unittest.TestCase):
    def test_the_wearable_type_numbers_come_from_avatarwearable(self) -> None:
        if not _WEARABLE_CS.exists():
            self.skipTest("opensim-source not present")
        text = _WEARABLE_CS.read_text(encoding="utf-8", errors="replace")

        sourced = {
            int(value): name.lower()
            for name, value in re.findall(
                r"public static readonly int (\w+) = (\d+);", text
            )
            # The file also carries maxima, which are counts rather than types.
            if not name.startswith("MAX") and "VERSION" not in name
        }

        self.assertEqual(sourced, WEARABLE_TYPE_NAMES)

    def test_opensim_skips_four_lines_before_the_key_value_body(self) -> None:
        # This is the one thing a sample file cannot settle: the description
        # line is empty in both fixtures, so three skips and four are
        # indistinguishable unless the source is consulted.
        if not _GATHERER.exists():
            self.skipTest("opensim-source not present")
        text = _GATHERER.read_text(encoding="utf-8", errors="replace")
        walker = text[text.index("RecordWearableAssetUuids(AssetBase") :][:800]
        walker = walker[: walker.index("while (ostmp.ReadLine")]

        self.assertEqual(walker.count("SkipLine()"), HEADER_LINE_COUNT)
        self.assertEqual(HEADER_LINE_COUNT, 4)

    def test_keys_are_split_on_spaces_and_tabs(self) -> None:
        if not _GATHERER.exists():
            self.skipTest("opensim-source not present")
        text = _GATHERER.read_text(encoding="utf-8", errors="replace")

        self.assertIn(
            "wearableSeps = new byte[]{(byte)' ', (byte)'\\t'}", text
        )

    def test_the_texture_constants_come_from_avatarappearance(self) -> None:
        if not _APPEARANCE_CS.exists():
            self.skipTest("opensim-source not present")
        text = _APPEARANCE_CS.read_text(encoding="utf-8", errors="replace")

        self.assertIn("TEXTURE_COUNT = 45", text)
        self.assertEqual(TEXTURE_SLOT_COUNT, 45)
        self.assertIn(
            "BAKE_INDICES = new byte[] { 8, 9, 10, 11, 19, 20, 40, 41, 42, 43, 44 }",
            text,
        )
        self.assertEqual(BAKE_TEXTURE_INDICES, {8, 9, 10, 11, 19, 20, 40, 41, 42, 43, 44})

    def test_visual_parameter_ids_are_deliberately_not_named(self) -> None:
        """Why `parameters` is a bare id-to-value map.

        `VPElement` is the closest thing in the tree and is a different
        numbering: a 0-based index into the AgentSetAppearance array, with 253
        entries. The wearables carry ids past 1000, so the two cannot be the
        same table, and nothing here maps between them.
        """
        if not _APPEARANCE_CS.exists() or not _HAIR.exists():
            self.skipTest("opensim-source or fixture not present")
        text = _APPEARANCE_CS.read_text(encoding="utf-8", errors="replace")
        vp_element = text[text.index("public enum VPElement") :]
        highest_index = max(
            int(value) for value in re.findall(r"= (\d+),", vp_element[:60000])
        )

        self.assertLess(highest_index, 300)
        self.assertGreater(max(decode_wearable(_HAIR.read_bytes()).parameters), 1000)


class ClothingTests(unittest.TestCase):
    def setUp(self) -> None:
        if not _SHIRT.exists():
            self.skipTest("clothing fixture not present")
        self.shirt = decode_wearable(_SHIRT.read_bytes())

    def test_the_header_decodes(self) -> None:
        self.assertEqual(self.shirt.version, 22)
        self.assertEqual(self.shirt.name, "New Shirt")
        self.assertEqual(self.shirt.description, "")

    def test_the_type_is_named_from_source(self) -> None:
        # An inventory asset type of 5 (clothing) carrying a wearable type of
        # 4 (shirt) is exactly the sort of thing worth pinning: the two
        # numbering schemes are unrelated and easy to conflate.
        self.assertEqual(self.shirt.wearable_type, 4)
        self.assertEqual(self.shirt.type_name, "shirt")

    def test_all_ten_parameters_are_read(self) -> None:
        self.assertEqual(len(self.shirt.parameters), 10)
        self.assertEqual(self.shirt.parameters[781], ".78")
        self.assertEqual(self.shirt.parameters[868], "0")

    def test_the_texture_slot_is_read_as_an_index_not_a_count(self) -> None:
        self.assertEqual(
            self.shirt.textures, {1: UUID("5748decc-f629-461c-9a36-a35a221fe21f")}
        )
        self.assertFalse(self.shirt.is_bake_slot(1))

    def test_permissions_are_kept_rather_than_dropped(self) -> None:
        # Not decoded — no bit meanings are sourceable — but losing them
        # silently would be worse than reporting them as written.
        self.assertEqual(self.shirt.extra["base_mask"], "00000000")
        self.assertEqual(
            self.shirt.extra["creator_id"], "11111111-1111-0000-0000-000100bba000"
        )

    def test_the_permissions_line_itself_is_part_of_the_header(self) -> None:
        # Where the header ends, checked against the file rather than only
        # against the source: `permissions 0` is the fourth skipped line, so
        # it must not appear as a key, while the `base_mask` inside its block
        # must. Reading three header lines instead of four puts it in `extra`.
        self.assertNotIn("permissions", self.shirt.extra)
        self.assertIn("base_mask", self.shirt.extra)

    def test_the_brace_lines_do_not_become_extra_entries(self) -> None:
        self.assertNotIn("{", self.shirt.extra)
        self.assertNotIn("}", self.shirt.extra)


class BodypartTests(unittest.TestCase):
    """The same format under a different inventory type — worth its own check."""

    def setUp(self) -> None:
        if not _HAIR.exists():
            self.skipTest("body part fixture not present")
        self.hair = decode_wearable(_HAIR.read_bytes())

    def test_it_decodes_as_the_same_format(self) -> None:
        self.assertEqual(self.hair.version, 22)
        self.assertEqual(self.hair.name, "New Hair")
        self.assertEqual(self.hair.wearable_type, 2)
        self.assertEqual(self.hair.type_name, "hair")

    def test_a_ninety_parameter_list_is_read_whole(self) -> None:
        # Nine times the shirt's, so an off-by-one in the count handling shows
        # up here as a stray parameter line leaking into `extra`.
        self.assertEqual(len(self.hair.parameters), 90)
        self.assertEqual(self.hair.parameters[1012], ".25")

    def test_the_textures_line_after_the_parameters_is_still_found(self) -> None:
        # It only is if the parameter list consumed exactly 90 lines. One too
        # few and "1012 .25" would be read as a key; one too many and the
        # textures line would be swallowed.
        self.assertEqual(
            self.hair.textures, {4: UUID("7ca39b4c-bd19-4699-aff7-f93fd03d3e7b")}
        )

    def test_negative_parameter_values_survive(self) -> None:
        self.assertEqual(self.hair.parameters[870], "-.29")


class CountHandlingTests(unittest.TestCase):
    def test_a_key_after_the_lists_is_not_swallowed(self) -> None:
        wearable = decode_wearable(
            _build("type 4\nparameters 2\n1 .5\n2 .5\ntextures 0\nlater yes\n")
        )

        self.assertEqual(wearable.parameters, {1: ".5", 2: ".5"})
        self.assertEqual(wearable.textures, {})
        self.assertEqual(wearable.extra["later"], "yes")

    def test_a_zero_parameter_count_reads_nothing(self) -> None:
        wearable = decode_wearable(_build("type 4\nparameters 0\ntype 9\n"))

        self.assertEqual(wearable.parameters, {})
        self.assertEqual(wearable.wearable_type, 9)

    def test_a_count_past_the_end_of_the_file_raises(self) -> None:
        # Reading what is there would present a wearable with fewer parameters
        # than it has, which renders as a different avatar rather than a error.
        with self.assertRaisesRegex(WearableDecodeError, "parameters"):
            decode_wearable(_build("parameters 40\n1 .5\n"))

    def test_too_many_textures_raises(self) -> None:
        with self.assertRaisesRegex(WearableDecodeError, "textures"):
            decode_wearable(_build("textures 9\n1 " + str(UUID(int=1)) + "\n"))


class MalformedInputTests(unittest.TestCase):
    def test_an_empty_asset_raises(self) -> None:
        with self.assertRaises(WearableDecodeError):
            decode_wearable(b"")

    def test_a_non_wearable_raises_rather_than_decoding_to_nothing(self) -> None:
        # An animation fetched under the wrong type must not read as an
        # untyped wearable with no parameters.
        with self.assertRaisesRegex(WearableDecodeError, "LLWearable"):
            decode_wearable(b"Linden text version 2\n{\n")

    def test_a_header_with_no_version_raises(self) -> None:
        with self.assertRaisesRegex(WearableDecodeError, "malformed"):
            decode_wearable(b"LLWearable 22\nName\n\n\tpermissions 0\n")

    def test_a_non_numeric_version_raises(self) -> None:
        with self.assertRaisesRegex(WearableDecodeError, "version"):
            decode_wearable(b"LLWearable version x\nName\n\n\tpermissions 0\n")

    def test_a_truncated_header_raises(self) -> None:
        with self.assertRaises(WearableDecodeError):
            decode_wearable(b"LLWearable version 22\nName\n")


class MissingTypeTests(unittest.TestCase):
    def test_a_wearable_with_no_type_line_is_not_reported_as_body(self) -> None:
        # Type 0 is a real wearable type, so defaulting to it would turn
        # "unknown" into a specific and wrong claim.
        wearable = decode_wearable(_build("parameters 0\n"))

        self.assertIsNone(wearable.wearable_type)
        self.assertEqual(wearable.type_name, "untyped")

    def test_an_unnamed_type_number_is_reported_raw(self) -> None:
        wearable = decode_wearable(_build("type 99\n"))

        self.assertEqual(wearable.type_name, "type99")


class SessionSummaryTests(unittest.TestCase):
    def test_clothing_is_summarised(self) -> None:
        if not _SHIRT.exists():
            self.skipTest("clothing fixture not present")
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(5, _SHIRT.read_bytes())

        self.assertIn("shirt", summary)
        self.assertIn("params=10", summary)

    def test_a_body_part_uses_the_same_decoder(self) -> None:
        if not _HAIR.exists():
            self.skipTest("body part fixture not present")
        from vibestorm.udp.session import _summarize_fetched_asset

        self.assertIn("hair", _summarize_fetched_asset(13, _HAIR.read_bytes()))

    def test_undecodable_bytes_do_not_fail_a_good_fetch(self) -> None:
        from vibestorm.udp.session import _summarize_fetched_asset

        self.assertIn("undecodable wearable", _summarize_fetched_asset(5, b"nope"))


if __name__ == "__main__":
    unittest.main()
