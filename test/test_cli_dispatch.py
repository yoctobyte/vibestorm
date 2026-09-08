"""That every subcommand the CLI offers is a subcommand the CLI runs.

`main` is thirteen `if args.command == "..."` comparisons, `build_parser`
registers thirteen subcommands, and nothing made the two agree. A subcommand
renamed on one side -- or a branch that stopped matching for any other reason
-- fell off the end of the chain into the status line, which prints a
friendly sentence and returns **0**. `./run.sh tester sync-object --object
<uuid> --folder ./work --push` doing nothing and reporting success is the
worst answer available on the one command priorities C, D and E run through.

Two halves, and they are different tests:

- The **guard**, so falling off the end is an error rather than a status
  line. That is a change to the code, not only to the tests: it is what makes
  the failure visible at all.
- The **coverage**, comparing what the parser accepts against what the chain
  handles. The parser side is read from a live parser and the chain side is
  read out of the source, so neither is written down here -- a subcommand
  added later is covered without anyone remembering this file exists.
"""

import argparse
import io
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from vibestorm.app import cli

SOURCE = Path(cli.__file__)


def branch_names() -> list[str]:
    """The commands `main` actually handles, read off its source.

    Read rather than listed, for the same reason the UDP session's branch
    list is: a fourteenth subcommand should be covered by this file on the
    day it is written, not on the day somebody remembers.
    """
    return re.findall(r'args\.command == "([a-z0-9-]+)"', SOURCE.read_text())


def parser_names() -> list[str]:
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    raise AssertionError("the CLI has no subparsers any more")


class _Fixed:
    """A parser that returns one namespace, so `main` can be reached without
    going through `sys.argv` -- and without running any of the subcommands,
    every one of which wants a grid."""

    def __init__(self, **fields: object) -> None:
        self.namespace = argparse.Namespace(**fields)

    def parse_args(self) -> argparse.Namespace:
        return self.namespace


class _MainCase(unittest.TestCase):
    def run_main(self, **fields: object) -> tuple[int, str, str]:
        original = cli.build_parser
        cli.build_parser = lambda: _Fixed(**fields)
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main()
        finally:
            cli.build_parser = original
        return code, out.getvalue(), err.getvalue()


class DispatchCoverageTests(unittest.TestCase):
    """The parser and the chain, compared."""

    def test_every_subcommand_the_parser_offers_has_a_branch(self) -> None:
        self.assertEqual(sorted(set(parser_names()) - set(branch_names())), [])

    def test_no_branch_handles_a_subcommand_nobody_can_type(self) -> None:
        """The other direction, which is dead code rather than a broken
        feature -- but it is also what a half-finished rename looks like from
        the side the user is not on."""
        self.assertEqual(sorted(set(branch_names()) - set(parser_names())), [])

    def test_the_branch_list_is_still_the_source_s(self) -> None:
        """Anti-vacuity. If the comparison stops matching -- a `match`
        statement, a dispatch table -- both sets above go empty and both
        tests above pass while saying nothing."""
        self.assertGreaterEqual(len(branch_names()), 10)
        self.assertIn("sync-object", branch_names())


class FallThroughTests(_MainCase):
    """What happens to a command no branch claims."""

    def test_an_unhandled_command_is_an_error(self) -> None:
        code, _out, err = self.run_main(version=False, command="a-command-with-no-branch")
        self.assertEqual(code, 2)
        self.assertIn("no handler", err)

    def test_it_names_the_command_that_went_unhandled(self) -> None:
        """So the message is actionable rather than merely non-zero."""
        _code, _out, err = self.run_main(version=False, command="ghost-command")
        self.assertIn("ghost-command", err)

    def test_the_error_does_not_go_to_stdout(self) -> None:
        """Several of these subcommands are read by scripts. An error on
        stdout is an error parsed as output."""
        _code, out, _err = self.run_main(version=False, command="ghost-command")
        self.assertNotIn("no handler", out)

    def test_no_command_still_prints_the_status_line(self) -> None:
        """Running `vibestorm` bare is how the project has always reported
        what phase it is in, and that is not an error."""
        code, out, _err = self.run_main(version=False, command=None)
        self.assertEqual(code, 0)
        self.assertIn(":", out)

    def test_version_still_short_circuits(self) -> None:
        code, out, _err = self.run_main(version=True, command=None)
        self.assertEqual(code, 0)
        self.assertRegex(out.strip(), r"^\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
