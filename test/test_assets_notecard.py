"""Tests for the notecard asset decoder.

Two forms, and the interesting part is that they coexist. The container is
built from OpenSim's own writer literal in `OSSL_Api.osMakeNotecard`, so the
fixture is derived from the tree rather than from memory. The plain form is a
real library asset.
"""

import unittest
from pathlib import Path

from vibestorm.assets.notecard import (
    CONTAINER_MAGIC,
    MINIMUM_CONTAINER_LENGTH,
    OPENSIM_MINIMUM_CONTAINER_LENGTH,
    NotecardDecodeError,
    decode_notecard,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "test" / "fixtures" / "library" / "notecard-Welcome.bin"
_SLUTIL = _ROOT / "opensim-source" / "OpenSim" / "Framework" / "SLUtil.cs"
_OSSL = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "ScriptEngine" / "Shared"
    / "Api" / "Implementation" / "OSSL_Api.cs"
)

#: Byte-for-byte what `osMakeNotecard` writes, with the count left open so
#: tests can vary it.
_WRITER_PREFIX = (
    b"Linden text version 2\n{\nLLEmbeddedItems version 1\n{\ncount "
)


def _container(text: bytes, *, item_count: int = 0) -> bytes:
    body = text
    return (
        _WRITER_PREFIX
        + str(item_count).encode()
        + b"\n}\nText length "
        + str(len(body)).encode()
        + b"\n"
        + body
        + b"}"
    )


class SourcePinTests(unittest.TestCase):
    def test_the_container_prefix_matches_opensim_s_writer(self) -> None:
        if not _OSSL.exists():
            self.skipTest("opensim-source not present")
        text = _OSSL.read_text(encoding="utf-8", errors="replace")

        self.assertIn(
            'AppendASCII("Linden text version 2\\n{\\nLLEmbeddedItems version 1'
            '\\n{\\ncount 0\\n}\\nText length ")',
            text,
        )

    def test_the_fixed_offsets_the_reader_checks_line_up(self) -> None:
        # SLUtil.ParseNotecardToArray reads by absolute offset: 0..21 is the
        # magic, 24..49 the embedded-items line, 52..57 "count", and 58 the
        # count digit. If the writer and those offsets ever disagree, every
        # notecard OpenSim writes becomes unreadable to OpenSim.
        built = _container(b"hi")

        self.assertEqual(built[0:21], CONTAINER_MAGIC)
        self.assertEqual(built[24:49], b"LLEmbeddedItems version 1")
        self.assertEqual(built[52:57], b"count")
        self.assertEqual(built[58:59], b"0")
        self.assertEqual(built[59:60], b"\n")

    def test_opensim_cannot_read_its_own_shortest_notecards(self) -> None:
        """Why this decoder's minimum is 60 rather than OpenSim's 79.

        A container holding no text at all is 77 bytes and one holding a
        single character is 78, so `SLUtil.ParseNotecardToArray` returns an
        empty array for notecards `osMakeNotecard` is perfectly happy to
        write. Inheriting that bound would reject valid assets.
        """
        if not _SLUTIL.exists():
            self.skipTest("opensim-source not present")
        text = _SLUTIL.read_text(encoding="utf-8", errors="replace")

        self.assertIn(f"data.Length < {OPENSIM_MINIMUM_CONTAINER_LENGTH}", text)
        self.assertEqual(len(_container(b"")), 77)
        self.assertEqual(len(_container(b"x")), 78)
        self.assertLess(len(_container(b"x")), OPENSIM_MINIMUM_CONTAINER_LENGTH)

        # And this decoder reads them anyway.
        self.assertEqual(decode_notecard(_container(b"")).text, "")
        self.assertEqual(decode_notecard(_container(b"x")).text, "x")

    def test_opensim_refuses_notecards_with_embedded_items(self) -> None:
        """Why this decoder is deliberately more permissive there.

        The reader bails when the count byte is not '0'. That is right for LSL,
        which cannot represent embedded items — but a client displaying a
        notecard still wants its text.
        """
        if not _SLUTIL.exists():
            self.skipTest("opensim-source not present")
        text = _SLUTIL.read_text(encoding="utf-8", errors="replace")

        self.assertIn("if (data[58] != '0' || data[59] != '\\n')", text)


class PlainNotecardTests(unittest.TestCase):
    """The OpenSim library ships notecards with no container at all."""

    def setUp(self) -> None:
        if not _FIXTURE.exists():
            self.skipTest("library notecard fixture not present")
        self.data = _FIXTURE.read_bytes()

    def test_the_library_notecard_really_has_no_container(self) -> None:
        # The assumption worth checking rather than carrying: a notecard is
        # not necessarily wrapped.
        self.assertFalse(self.data.startswith(CONTAINER_MAGIC))

    def test_it_decodes_as_plain_text(self) -> None:
        notecard = decode_notecard(self.data)

        self.assertFalse(notecard.is_container)
        self.assertEqual(notecard.embedded_item_count, 0)
        self.assertIn("thank you for using OpenSim", notecard.text)
        self.assertEqual(len(notecard.text), 321)

    def test_plain_text_is_not_treated_as_an_error(self) -> None:
        # The common case must not be the failure case.
        self.assertIn("plain text", decode_notecard(self.data).describe())


class ContainerTests(unittest.TestCase):
    def test_the_text_is_taken_from_the_declared_length(self) -> None:
        notecard = decode_notecard(_container(b"line one\nline two"))

        self.assertTrue(notecard.is_container)
        self.assertEqual(notecard.text, "line one\nline two")
        self.assertEqual(notecard.lines, ("line one", "line two"))

    def test_the_trailing_brace_is_not_part_of_the_text(self) -> None:
        # osMakeNotecard appends "}" after the body; the declared length is
        # what bounds the text, so a decoder reading to end-of-buffer would
        # append a stray brace to every notecard.
        notecard = decode_notecard(_container(b"hello"))

        self.assertEqual(notecard.text, "hello")
        self.assertFalse(notecard.text.endswith("}"))

    def test_an_empty_notecard_decodes_to_empty_text(self) -> None:
        notecard = decode_notecard(_container(b""))

        self.assertEqual(notecard.text, "")
        self.assertTrue(notecard.is_container)

    def test_utf8_survives_the_byte_length(self) -> None:
        # Text length is a *byte* count, not a character count, so a multibyte
        # character is where an off-by-one shows up.
        body = "café ☕".encode("utf-8")
        notecard = decode_notecard(_container(body))

        self.assertEqual(notecard.text, "café ☕")


class EmbeddedItemTests(unittest.TestCase):
    def test_the_text_is_still_recovered_when_items_are_embedded(self) -> None:
        # OpenSim's reader returns nothing at all here. This one returns the
        # text and says what it skipped, because the text is right there.
        notecard = decode_notecard(_container(b"still readable", item_count=3))

        self.assertEqual(notecard.text, "still readable")
        self.assertEqual(notecard.embedded_item_count, 3)
        self.assertTrue(notecard.has_undecoded_items)

    def test_the_description_admits_the_items_are_not_decoded(self) -> None:
        description = decode_notecard(
            _container(b"x", item_count=2)
        ).describe()

        self.assertIn("embedded_items=2", description)
        self.assertIn("not decoded", description)

    def test_a_multi_digit_count_is_read_whole(self) -> None:
        # The reader only ever compares one character, so a count of 12 would
        # truncate to 1 if this read a single digit.
        notecard = decode_notecard(_container(b"x", item_count=12))

        self.assertEqual(notecard.embedded_item_count, 12)

    def test_no_items_does_not_read_as_undecoded_content(self) -> None:
        notecard = decode_notecard(_container(b"x"))

        self.assertFalse(notecard.has_undecoded_items)
        self.assertNotIn("embedded", notecard.describe())


class MalformedContainerTests(unittest.TestCase):
    def test_a_truncated_header_raises(self) -> None:
        with self.assertRaisesRegex(NotecardDecodeError, "60"):
            decode_notecard(CONTAINER_MAGIC + b"\n{\n")

    def test_a_missing_text_length_raises(self) -> None:
        broken = _container(b"hello").replace(b"Text length", b"Text lenXXX")

        with self.assertRaisesRegex(NotecardDecodeError, "Text length"):
            decode_notecard(broken)

    def test_a_length_longer_than_the_asset_raises(self) -> None:
        # Silently returning the short read would present a truncated notecard
        # as a complete one.
        broken = _container(b"hello").replace(b"Text length 5", b"Text length 500")

        with self.assertRaisesRegex(NotecardDecodeError, "remain"):
            decode_notecard(broken)

    def test_a_non_numeric_length_raises(self) -> None:
        broken = _container(b"hello").replace(b"Text length 5", b"Text length ?")

        with self.assertRaises(NotecardDecodeError):
            decode_notecard(broken)


if __name__ == "__main__":
    unittest.main()


class SessionSummaryTests(unittest.TestCase):
    def test_a_plain_notecard_is_summarised(self) -> None:
        if not _FIXTURE.exists():
            self.skipTest("library notecard fixture not present")
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(7, _FIXTURE.read_bytes())

        self.assertIn("plain text", summary)
        self.assertIn("chars=321", summary)

    def test_a_container_notecard_is_named_as_one(self) -> None:
        from vibestorm.udp.session import _summarize_fetched_asset

        summary = _summarize_fetched_asset(7, _container(b"hello\nworld"))

        self.assertIn("container", summary)
        self.assertIn("lines=2", summary)

    def test_a_script_still_gets_no_note(self) -> None:
        # LSL text needs no decoding, and a note implying it was inspected
        # would be noise on every script fetch.
        from vibestorm.udp.session import _summarize_fetched_asset

        self.assertEqual(_summarize_fetched_asset(10, b"default\n{\n}\n"), "")
