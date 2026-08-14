"""The capability coverage ledger must describe the code that exists.

The sibling of ``test_message_coverage_ledger``. `spec/capability-coverage.md`
drifted the same way and for the same reason: every row read `planned` while
nine capabilities were resolved and used every session. It also carried a
status its own scale does not define, and a capability name the client does
not actually ask for.

The checkable halves are the same two, plus one this ledger needs and the
message ledger does not: the status scale here is written out in the document
itself, so a row can contradict it.

What is deliberately not checked is `used` versus `verified` — that is a claim
about live evidence, and no offline test can confirm it.
"""

import re
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LEDGER = _ROOT / "spec" / "capability-coverage.md"
_SOURCE_DIR = _ROOT / "src" / "vibestorm"

#: Statuses that assert the client does something with the capability.
_SUPPORTED = {"resolved", "used", "verified"}

#: Capabilities the code asks for that are deliberately absent from the
#: ledger. Keeping this empty is the point.
_LEDGER_EXEMPT: set[str] = set()


def _ledger_rows() -> list[tuple[list[str], str]]:
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


def _declared_scale() -> set[str]:
    """The scale as the document itself writes it, not as a test asserts it."""
    text = _LEDGER.read_text(encoding="utf-8")
    body = text.split("## Status Scale", 1)[1].split("##", 1)[0]
    return set(re.findall(r"^- `(\S+)`:", body, re.MULTILINE))


def _requested_capabilities() -> set[str]:
    """Capability names the client asks the simulator to resolve.

    Two shapes: a list literal passed inline to ``resolve_seed_caps``, and a
    list literal bound to a name with `cap` in it, which is how the session
    prelude and the script-cap aliases are written. Calls that pass a variable
    holding user input — the ``caps`` CLI subcommand forwards whatever was
    typed — contribute nothing, which is correct: the user is not the ledger's
    subject.
    """
    names: set[str] = set()
    for path in _SOURCE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Inline: resolve_seed_caps(seed, ["EventQueueGet"], ...). Excluding
        # ')' from the run-up keeps a call that passes a variable from
        # swallowing an unrelated list further down the file.
        for literal in re.findall(r"resolve_seed_caps\([^[)]*\[(.*?)\]", text, re.DOTALL):
            names.update(re.findall(r'"(\w+)"', literal))
        # Bound: requested_caps = [...] / SCRIPT_TASK_CAP_NAMES = [...]
        for literal in re.findall(r"(?i)^\s*\w*cap\w* = \[(.*?)\]", text, re.MULTILINE | re.DOTALL):
            names.update(re.findall(r'"(\w+)"', literal))
    return names


def _source_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in _SOURCE_DIR.rglob("*.py")
    )


class LedgerParseTests(unittest.TestCase):
    def test_the_ledger_is_parseable_and_populated(self) -> None:
        rows = _ledger_rows()

        self.assertGreater(len(rows), 15, "ledger rows did not parse")
        self.assertTrue(all(names for names, _status in rows))

    def test_the_scale_parses(self) -> None:
        # Every check below is vacuous if the scale comes back empty.
        self.assertGreaterEqual(len(_declared_scale()), 4)


class StatusScaleTests(unittest.TestCase):
    def test_every_status_is_one_the_document_defines(self) -> None:
        """Caught `handled`, borrowed from the message ledger's scale."""
        scale = _declared_scale()

        for names, status in _ledger_rows():
            self.assertIn(status, scale, f"{names}: {status!r} is not in the scale")

    def test_the_supported_set_is_part_of_the_scale(self) -> None:
        # If the document renames a status, _SUPPORTED silently stops matching
        # any row and the claimed-support check below passes on nothing.
        self.assertTrue(_SUPPORTED <= _declared_scale())


class ClaimedSupportTests(unittest.TestCase):
    def test_a_row_claiming_support_has_code_behind_it(self) -> None:
        source = _source_text()
        missing = [
            name
            for names, status in _ledger_rows()
            if status in _SUPPORTED
            for name in names
            if name not in source
        ]

        self.assertEqual(missing, [], f"ledger claims support with no code: {missing}")


class LedgerCompletenessTests(unittest.TestCase):
    def test_every_requested_capability_appears_in_the_ledger(self) -> None:
        requested = _requested_capabilities()

        self.assertGreater(len(requested), 8, "failed to find requested capabilities")

        ledger_names = {name for names, _status in _ledger_rows() for name in names}
        missing = sorted(requested - ledger_names - _LEDGER_EXEMPT)

        self.assertEqual(
            missing,
            [],
            "capabilities the client requests but the ledger omits: " + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
