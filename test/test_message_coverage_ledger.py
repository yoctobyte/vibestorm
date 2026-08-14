"""The message coverage ledger must describe the code that exists.

`spec/message-coverage.md` is the answer to "is this message supported?", and
it has drifted twice now — once claiming a dozen implemented messages were
`planned`, and once claiming `verified` for sub-decoders that had never seen
live data while omitting `SimStats` entirely. Both drifts were found by hand,
months apart.

These tests re-derive the two checkable halves automatically:

- a row claiming support must have code behind it, and
- a message with a parser must appear in the ledger at all.

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
