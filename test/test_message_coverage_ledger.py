"""The message coverage ledger must describe the code that exists.

`spec/message-coverage.md` is the answer to "is this message supported?", and
it has drifted twice now — once claiming a dozen implemented messages were
`planned`, and once claiming `verified` for sub-decoders that had never seen
live data while omitting `SimStats` entirely. Both drifts were found by hand,
months apart.

These tests re-derive the checkable parts automatically:

- a row claiming support must have code behind it,
- a message with a parser must appear in the ledger at all, and
- so must a message the client can *send*.

The third was added after the teleport work, which found the client sending
`TeleportLocationRequest` into silence with no row anywhere: a message with
neither parser nor row is invisible to the second check. Fourteen more
outbound messages turned out to be in the same position. What no test can
cover is a message this client neither sends nor parses, which is
indistinguishable from a message that does not concern us.

What they deliberately do *not* check is the difference between `tested` and
`verified`. That distinction is about live-sim evidence, which no test running
offline can confirm — asserting it here would be the same overclaim the
distinction exists to prevent.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LEDGER = _ROOT / "spec" / "message-coverage.md"
_SOURCE_DIR = _ROOT / "src" / "vibestorm"

#: Statuses that assert the client does something with the message.
_SUPPORTED = {"parse-only", "handled", "verified", "tested"}

#: Messages a parser exists for but which are deliberately absent from the
#: ledger, with the reason. Keeping this list short is the point: it is the
#: set of exceptions someone has justified.
_LEDGER_EXEMPT: set[str] = set()


def _ledger_rows() -> list[tuple[list[str], str]]:
    """(message names, status) for each table row of the ledger."""
    rows: list[tuple[list[str], str]] = []
    for line in _LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        names = re.findall(r"`(\w+)`", cells[0])
        status = cells[3]
        if names and status:
            rows.append((names, status))
    return rows


def _source_text() -> str:
    parts = []
    for path in _SOURCE_DIR.rglob("*.py"):
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


class LedgerParseTests(unittest.TestCase):
    def test_the_ledger_is_parseable_and_populated(self) -> None:
        # If this breaks, the checks below would silently pass on zero rows.
        rows = _ledger_rows()

        self.assertGreater(len(rows), 20, "ledger rows did not parse")
        self.assertTrue(all(names for names, _status in rows))

    def test_every_status_is_one_the_scale_defines(self) -> None:
        scale = {"planned", "parse-only", "handled", "verified", "tested"}

        for names, status in _ledger_rows():
            self.assertIn(status, scale, f"{names}: unknown status {status!r}")


class ClaimedSupportTests(unittest.TestCase):
    def test_a_row_claiming_support_has_code_behind_it(self) -> None:
        """The direction that would let someone trust a message that is absent."""
        source = _source_text()
        missing = [
            name
            for names, status in _ledger_rows()
            if status in _SUPPORTED
            for name in names
            if name not in source
        ]

        self.assertEqual(
            missing, [], f"ledger claims support with no code: {missing}"
        )


def _encoded_message_names() -> dict[str, str]:
    """{encoder name: wire message name} for every outbound builder.

    Read from the message-number prefix each encoder writes, resolved through
    the same template the client dispatches with. That is exact where a naming
    convention would be a guess, and it covers all three prefix widths (High is
    one byte, Medium two, Low four) without the test knowing which is which.
    """
    from vibestorm.udp.dispatch import MessageDispatcher
    from vibestorm.udp.template import dispatch_message

    index = MessageDispatcher.from_repo_root(_ROOT).index
    messages_py = (_SOURCE_DIR / "udp" / "messages.py").read_text(encoding="utf-8")
    blocks = re.split(r"^def ", messages_py, flags=re.MULTILINE)
    found: dict[str, str] = {}
    for block in blocks:
        match = re.match(r"(encode_\w+)", block)
        if not match:
            continue
        literal = re.search(r'b"((?:\\x[0-9A-Fa-f]{2})+)"', block)
        if not literal:
            continue
        prefix = bytes.fromhex(literal.group(1).replace("\\x", ""))
        try:
            summary = dispatch_message(prefix, index).summary
        except (KeyError, ValueError):
            continue
        found[match.group(1)] = summary.name
    return found


class OutboundCompletenessTests(unittest.TestCase):
    """The direction that let the teleport request go unlisted.

    A message with no parser *and* no ledger row is invisible to the check
    below — it looks for parsers that lack rows, not for gaps with neither.
    Outbound builders close half of that hole: the client demonstrably speaks
    any message it can encode, so the ledger owes each one a row. What stays
    uncovered is a message this client neither sends nor parses, which no test
    can distinguish from a message that does not concern us.
    """

    def test_the_encoders_resolve_to_real_message_names(self) -> None:
        encoded = _encoded_message_names()

        self.assertGreater(len(encoded), 15, "failed to resolve encoder prefixes")

    def test_every_encoded_message_appears_in_the_ledger(self) -> None:
        encoded = _encoded_message_names()
        ledger_names = {name for names, _status in _ledger_rows() for name in names}
        missing = sorted(
            {name for name in encoded.values()} - ledger_names - _LEDGER_EXEMPT
        )

        self.assertEqual(
            missing,
            [],
            "messages the client sends but the ledger omits: " + ", ".join(missing),
        )


class LedgerCompletenessTests(unittest.TestCase):
    def test_every_parsed_message_appears_in_the_ledger(self) -> None:
        """The direction that let SimStats go unlisted for months.

        The wire names come from the guard each parser opens with —
        ``if message.summary.name != "SimStats"`` — rather than from the
        parser's own identifier. Deriving them from the identifier means
        guessing at the capitalisation, and the guess is wrong: the parser
        ``parse_simulator_viewer_time`` decodes ``SimulatorViewerTimeMessage``.
        Reading the literal also excludes sub-decoders like
        ``parse_texture_entry`` for free, because a decoder that is handed a
        field rather than a packet has no name to check.
        """
        messages_py = (_SOURCE_DIR / "udp" / "messages.py").read_text(encoding="utf-8")
        parsed = set(re.findall(r'summary\.name != "(\w+)"', messages_py))

        self.assertGreater(len(parsed), 20, "failed to find parser name guards")

        ledger_names = {
            name for names, _status in _ledger_rows() for name in names
        }
        missing = sorted(parsed - ledger_names - _LEDGER_EXEMPT)

        self.assertEqual(
            missing,
            [],
            "messages with a parser but no ledger row: " + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
