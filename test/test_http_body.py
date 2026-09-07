"""The ceiling on an HTTP body, and the difference between two ways to have one.

A length check after the read is not a ceiling. By the time there is a length
to measure the memory already exists, which is the whole failure -- and worse,
a check in that position *passes* even when the bound it is supposed to be
guarding has been deleted, so it hides its own removal. That mistake was made
once already in the mesh decoder and caught by a mutant surviving. These tests
are written so it cannot be made again here: the ones that matter assert on
what the response was *asked for*, not on what came back.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from http_fakes import FakeHeaders  # noqa: E402

from vibestorm.util.http_body import (  # noqa: E402
    MAX_ASSET_BODY_BYTES,
    MAX_LLSD_BODY_BYTES,
    read_bounded,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "vibestorm"


class Boom(RuntimeError):
    """Stands in for a client's own error type."""


class OtherBoom(RuntimeError):
    """A second one, so "the type passed in" is a real claim."""


class _Response:
    """A response that records every read, so a test can see the bound."""

    def __init__(self, body: bytes, *, chunk: int | None = None, **declared: str):
        self._body = body
        self._chunk = chunk
        self._served = 0
        self.asks: list[int] = []
        self.headers = FakeHeaders(**declared)

    def read(self, amt: int = -1) -> bytes:
        self.asks.append(amt)
        end = len(self._body) if amt < 0 else min(len(self._body), self._served + amt)
        if self._chunk is not None:
            end = min(end, self._served + self._chunk)
        chunk = self._body[self._served : end]
        self._served = end
        return chunk


class _EndlessResponse:
    """A server that never stops sending, which is the case being defended.

    It cannot be asked how big it is, because it has no size. If the reader
    has no ceiling this hangs the test rather than failing it, which is a
    fair model of what it does to a viewer.
    """

    def __init__(self) -> None:
        self.given = 0
        self.headers = FakeHeaders()

    def read(self, amt: int = -1) -> bytes:
        if amt < 0:
            raise AssertionError("unbounded read against an endless response")
        self.given += amt
        return b"\x00" * amt


def _read(response: object, limit: int, error: type[Exception] = Boom) -> bytes:
    return read_bounded(response, max_bytes=limit, what="a body", error=error)


class ReadBoundedTests(unittest.TestCase):
    def test_a_body_under_the_limit_comes_back_whole(self) -> None:
        self.assertEqual(_read(_Response(b"hello"), 1024), b"hello")

    def test_an_empty_body_comes_back_empty(self) -> None:
        """Not an error here. Whether an empty body is one is the client's call."""
        self.assertEqual(_read(_Response(b""), 1024), b"")

    def test_a_body_exactly_at_the_limit_is_accepted(self) -> None:
        """The limit is inclusive, and a boundary nobody pins drifts."""
        self.assertEqual(_read(_Response(b"x" * 64), 64), b"x" * 64)

    def test_one_byte_past_the_limit_is_refused(self) -> None:
        with self.assertRaisesRegex(Boom, "exceeds the 64 byte limit"):
            _read(_Response(b"x" * 65), 64)

    def test_the_error_is_the_type_the_caller_passed(self) -> None:
        """A shared exception class would escape every caller's handlers.

        That is how `DecompressionBombError` got past `decode_j2k`: it
        subclasses `Exception` and not `OSError`, so the handler that was
        meant to catch it never saw it. The type comes in from the caller so
        the answer cannot depend on remembering to catch a new one.
        """
        with self.assertRaises(OtherBoom):
            _read(_Response(b"x" * 65), 64, error=OtherBoom)

    def test_the_reader_never_asks_for_more_than_the_limit(self) -> None:
        """The claim that makes this a ceiling rather than a measurement.

        Ten megabytes offered against a 64 byte limit: the sum of what was
        *asked for* has to stay at the limit plus the one byte that detects
        the overrun. A post-hoc `len()` check would pass every other test in
        this file and fail this one.
        """
        response = _Response(b"x" * (10 * 1024 * 1024))
        with self.assertRaises(Boom):
            _read(response, 64)
        self.assertLessEqual(sum(response.asks), 65)

    def test_a_server_that_never_stops_is_still_bounded(self) -> None:
        response = _EndlessResponse()
        with self.assertRaises(Boom):
            _read(response, 4096)
        self.assertLessEqual(response.given, 4097)

    def test_a_body_arriving_in_small_pieces_is_reassembled(self) -> None:
        """A short read is not the end of a body -- a chunked response is all
        short reads, and treating the first one as the whole thing would
        silently truncate every asset off a grid that streams them."""
        response = _Response(b"abcdefghij", chunk=3)
        self.assertEqual(_read(response, 1024), b"abcdefghij")
        self.assertGreater(len(response.asks), 1)


class DeclaredLengthTests(unittest.TestCase):
    """`Content-Length` is an optimisation, never the bound.

    A header is a claim, which is exactly what the J2K size was: patch two
    fields and Pillow reports 8192x8192 having read four pixels. So a
    declared length that is over the limit saves the transfer, and a declared
    length that is under it buys nothing at all.
    """

    def test_a_declared_length_over_the_limit_is_refused_before_reading(self) -> None:
        response = _Response(b"x" * 10, Content_Length="999999")
        with self.assertRaisesRegex(Boom, "declares 999,999 bytes"):
            _read(response, 64)
        self.assertEqual(response.asks, [])

    def test_a_declared_length_exactly_at_the_limit_is_accepted(self) -> None:
        """The early reject is an optimisation and must not narrow the limit.

        A body of exactly `max_bytes` is legal -- the loop accepts it -- so a
        response honest enough to declare that size must not be refused for
        its honesty. Off by one here turns the header into a second, tighter
        ceiling that only truthful servers are held to.
        """
        response = _Response(b"x" * 64, Content_Length="64")
        self.assertEqual(_read(response, 64), b"x" * 64)

    def test_a_declared_length_that_lies_low_does_not_get_past_the_ceiling(self) -> None:
        response = _Response(b"x" * 5000, Content_Length="10")
        with self.assertRaisesRegex(Boom, "exceeds the"):
            _read(response, 64)

    def test_no_declared_length_is_not_an_error(self) -> None:
        self.assertEqual(_read(_Response(b"hello"), 1024), b"hello")

    def test_a_malformed_declared_length_is_ignored_rather_than_fatal(self) -> None:
        """A grid sending nonsense in a header is not a reason to refuse the
        body: the loop bounds it either way."""
        self.assertEqual(_read(_Response(b"hello", Content_Length="banana"), 1024), b"hello")

    def test_a_response_without_headers_at_all_is_read_anyway(self) -> None:
        class Bare:
            def read(self, amt: int = -1) -> bytes:
                return b"" if getattr(self, "done", False) else self._go()

            def _go(self) -> bytes:
                self.done = True
                return b"hi"

        self.assertEqual(_read(Bare(), 1024), b"hi")


class LimitTests(unittest.TestCase):
    def test_the_asset_limit_is_generous_but_finite(self) -> None:
        """A floor and a ceiling. The floor is a real mesh or texture; the
        ceiling is the point of the exercise, and a bound with only a floor
        passes every test while being 2**60."""
        self.assertGreaterEqual(MAX_ASSET_BODY_BYTES, 16 * 1024 * 1024)
        self.assertLessEqual(MAX_ASSET_BODY_BYTES, 128 * 1024 * 1024)

    def test_a_control_plane_body_is_held_to_a_tighter_limit(self) -> None:
        """LLSD carries structure, not content. If a seed-caps response is
        asset-sized something is wrong regardless of whether we can hold it."""
        self.assertLess(MAX_LLSD_BODY_BYTES, MAX_ASSET_BODY_BYTES)
        self.assertGreaterEqual(MAX_LLSD_BODY_BYTES, 4 * 1024 * 1024)


class NoUnboundedReadsTests(unittest.TestCase):
    """Nothing in the client may read an HTTP body without a ceiling.

    Eleven call sites were found by hand. A twelfth added later would be
    found by nobody, which is what this is for -- the defect is not any one
    of those lines, it is that `response.read()` is the obvious thing to
    write and is always wrong.
    """

    #: `.read()` with no argument, on anything.
    UNBOUNDED = re.compile(r"\.read\(\s*\)")

    def test_no_module_that_speaks_http_reads_without_a_limit(self) -> None:
        offenders = []
        for path in sorted(SRC.rglob("*.py")):
            source = path.read_text()
            if "urllib.request" not in source:
                continue
            for number, line in enumerate(source.splitlines(), 1):
                if self.UNBOUNDED.search(line):
                    offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "unbounded HTTP reads:\n" + "\n".join(offenders))

    def test_the_scan_would_notice_one(self) -> None:
        """The floor. A scan that matches nothing reports nothing and means
        nothing, so it is shown catching the exact line it was written for."""
        self.assertTrue(self.UNBOUNDED.search("                data = response.read()"))
        self.assertFalse(self.UNBOUNDED.search("data = response.read(wanted)"))

    def test_the_scan_looked_at_the_files_it_was_meant_to(self) -> None:
        """And the other floor: that it found the HTTP modules at all."""
        seen = {
            str(path.relative_to(SRC))
            for path in SRC.rglob("*.py")
            if "urllib.request" in path.read_text()
        }
        self.assertIn("caps/get_texture_client.py", seen)
        self.assertIn("event_queue/client.py", seen)
        self.assertGreaterEqual(len(seen), 8)


if __name__ == "__main__":
    unittest.main()
