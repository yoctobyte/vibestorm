"""Decoder for notecard assets.

A notecard is not always a text file. A viewer writes one inside a container:

    Linden text version 2\\n{\\nLLEmbeddedItems version 1\\n{\\ncount 0\\n}\\nText length <N>\\n<N bytes>}

— the exact string OpenSim's own writer emits in ``OSSL_Api.osMakeNotecard``.
But the OpenSim *library*'s notecards are plain UTF-8 with no container at all;
`Welcome` was fetched live on 2026-08-14 and starts straight into its text. So
a client that assumes either form gets the other one wrong, and this decoder
recognises the container by its header rather than by where the asset came
from.

The container layout is read from ``SLUtil.ParseNotecardToArray``, which parses
it back. That reader is deliberately rigid — it checks fixed byte offsets, and
it *refuses outright* if the embedded-item count is anything but ``0``, because
LSL cannot read notecards with embedded items.

This decoder is less strict on that one point, on purpose. The text length is
found by searching for ``Text length`` rather than by a fixed offset, so the
text is still recoverable when items are embedded; only the embedded-inventory
block itself is unparseable, and nothing in the tree describes its layout.
Returning no text at all for a notecard whose text is right there would lose
information a reader wants. The item count is reported so a caller can say what
was skipped.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The literal `SLUtil.ParseNotecardToArray` checks at offset 0.
CONTAINER_MAGIC = b"Linden text version 2"

#: The shortest container this decoder will read: enough bytes to cover the
#: fixed offsets, which end at the count's newline at 59.
#:
#: `SLUtil.ParseNotecardToArray` uses 79 instead, and that number is too big.
#: A container holding zero characters of text is 77 bytes and one holding a
#: single character is 78, so **OpenSim cannot read a notecard with fewer than
#: two characters in it** — including ones OpenSim itself wrote. Inheriting
#: that would mean rejecting valid notecards, so this guards only what it
#: actually indexes and lets the length checks below catch the rest.
MINIMUM_CONTAINER_LENGTH = 60

#: What OpenSim's own reader demands, kept for the test that pins the
#: divergence rather than because anything here uses it.
OPENSIM_MINIMUM_CONTAINER_LENGTH = 79

_TEXT_LENGTH_MARKER = b"Text length"

#: Where the reader expects the embedded-item count digit and its newline.
_COUNT_DIGIT_OFFSET = 58


class NotecardDecodeError(ValueError):
    """Raised when notecard bytes claim to be a container but are malformed."""


@dataclass(slots=True, frozen=True)
class Notecard:
    text: str
    #: False for the OpenSim library's notecards, which are bare UTF-8.
    is_container: bool
    #: Items embedded in the notecard. Their inventory blocks are *not*
    #: decoded — no file in `opensim-source/` describes that layout — so a
    #: non-zero count means this asset carries content beyond `text`.
    embedded_item_count: int = 0

    @property
    def has_undecoded_items(self) -> bool:
        return self.embedded_item_count > 0

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.split("\n"))

    def describe(self) -> str:
        parts = [
            "container" if self.is_container else "plain text",
            f"chars={len(self.text)}",
            f"lines={len(self.lines)}",
        ]
        if self.has_undecoded_items:
            parts.append(f"embedded_items={self.embedded_item_count} (not decoded)")
        return " ".join(parts)


def _embedded_item_count(data: bytes) -> int:
    """The count at the offset OpenSim's reader checks, as an integer.

    The reader only ever tests for the single character ``0`` here, so a
    multi-digit count is outside what it handles; this reads the digits up to
    the newline so the number is reported rather than truncated to its first
    digit.
    """
    end = data.find(b"\n", _COUNT_DIGIT_OFFSET)
    if end < 0:
        raise NotecardDecodeError("notecard embedded-item count is unterminated")
    try:
        return int(data[_COUNT_DIGIT_OFFSET:end])
    except ValueError as exc:
        raise NotecardDecodeError(
            f"notecard embedded-item count is not a number: "
            f"{data[_COUNT_DIGIT_OFFSET:end]!r}"
        ) from exc


def decode_notecard(data: bytes) -> Notecard:
    """Decode notecard asset bytes, container or not.

    Plain bytes are returned as-is rather than rejected: that is what the
    OpenSim library ships, and treating it as an error would make the common
    case the failure case.
    """
    if not data.startswith(CONTAINER_MAGIC):
        return Notecard(
            text=data.decode("utf-8", errors="replace"),
            is_container=False,
        )

    if len(data) < MINIMUM_CONTAINER_LENGTH:
        raise NotecardDecodeError(
            f"notecard claims to be a container but is only {len(data)} bytes; "
            f"the fixed header fields alone need {MINIMUM_CONTAINER_LENGTH}"
        )

    item_count = _embedded_item_count(data)

    marker = data.find(_TEXT_LENGTH_MARKER, 60)
    if marker < 0:
        raise NotecardDecodeError("notecard container has no 'Text length' field")
    start = marker + len(_TEXT_LENGTH_MARKER) + 1
    end = data.find(b"\n", start)
    if end < 0:
        raise NotecardDecodeError("notecard text length is unterminated")
    try:
        text_length = int(data[start:end])
    except ValueError as exc:
        raise NotecardDecodeError(
            f"notecard text length is not a number: {data[start:end]!r}"
        ) from exc
    if text_length < 0:
        raise NotecardDecodeError(f"notecard text length is negative: {text_length}")

    text_start = end + 1
    if text_start + text_length > len(data):
        raise NotecardDecodeError(
            f"notecard declares {text_length} bytes of text but only "
            f"{len(data) - text_start} remain"
        )
    return Notecard(
        text=data[text_start : text_start + text_length].decode(
            "utf-8", errors="replace"
        ),
        is_container=True,
        embedded_item_count=item_count,
    )


__all__ = [
    "CONTAINER_MAGIC",
    "MINIMUM_CONTAINER_LENGTH",
    "OPENSIM_MINIMUM_CONTAINER_LENGTH",
    "Notecard",
    "NotecardDecodeError",
    "decode_notecard",
]
