"""That the launcher can reach every subcommand the CLI has.

`run.sh` is the documented way in -- every example in the handoff is
`./run.sh <profile> <command>` -- and its command names are *not* the CLI's:
`census` runs `world-census`, `eventq` runs `event-queue-once`, `udp` runs
`udp-probe`. So the two lists are a translation, maintained by hand, in two
languages, and nothing compared them.

They had diverged. `sync-object` -- the command priorities C, D and E run
through, and the one written out in the handoff four times -- was in the CLI
and not in the launcher, so the documented invocation answered

    Unknown command: sync-object

The launcher itself is fine about it: unlike `main`, its `case` ends in a
`*)` that prints and exits 2. The gap was that nobody noticed the arm was
missing, because a missing arm looks exactly like a command that was never
meant to exist.

Read out of both files rather than listed here, so a fourteenth subcommand is
covered on the day it is written.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SH = REPO_ROOT / "run.sh"

#: CLI subcommands the launcher deliberately does not offer, and why. Empty,
#: and worth keeping empty: an entry here is a feature the documented entry
#: point cannot reach.
NOT_IN_THE_LAUNCHER: dict[str, str] = {}


def launcher_text() -> str:
    return RUN_SH.read_text(encoding="utf-8")


def cli_subcommands() -> list[str]:
    import argparse

    from vibestorm.app.cli import build_parser

    parser = build_parser()
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    raise AssertionError("the CLI has no subparsers any more")


def is_command_words() -> set[str]:
    """The words `is_command` accepts.

    A third list, and the one that fails worst. The first argument is a
    profile name unless it is a command, so a command missing from *here*
    is not rejected -- it is taken as a profile, and the launcher runs its
    default command instead. `./run.sh sync-object --object <uuid> --folder
    ./work --push` logged in and ran a sixty-second protocol session.
    """
    body = launcher_text().split("is_command()", 1)[1].split("esac", 1)[0]
    words: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"\s{4}([a-z0-9|.\-]+)\)\s*$", line)
        if match:
            words.update(match.group(1).split("|"))
    return words


def _dispatch_body() -> str:
    return launcher_text().split('case "$command" in', 1)[1]


def _shell_functions() -> dict[str, str]:
    """Every `name() { ... }` in the launcher, by name.

    Brace-counted rather than regexed to the closing line, because a `do_*`
    body contains braces of its own -- `"${cli_base_args[@]}"` on every one of
    them -- and a non-greedy match to the first `}` stops before the CLI
    subcommand this file is looking for.
    """
    text = launcher_text()
    functions: dict[str, str] = {}
    for match in re.finditer(r"^([a-z0-9_]+)\(\)\s*\{$", text, re.M):
        depth, start = 0, match.end()
        for index in range(match.start(), len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    functions[match.group(1)] = text[start:index]
                    break
    return functions


def launcher_case_arms() -> dict[str, str]:
    """The words the launcher's top-level `case` accepts, and their bodies."""
    arms: dict[str, str] = {}
    current: list[str] = []
    collected: list[str] = []
    for line in _dispatch_body().splitlines():
        match = re.match(r"\s{2}([a-z0-9|.\-*]+)\)\s*$", line)
        if match:
            current = match.group(1).split("|")
            collected = []
            continue
        if current and line.strip() == ";;":
            for name in current:
                arms[name] = "\n".join(collected)
            current = []
            continue
        if current:
            collected.append(line)
    return arms


def reachable_cli_subcommands() -> set[str]:
    """CLI subcommands a person can actually get to through `run.sh`.

    Reached *through an arm*, which is the whole point: a `do_census` that
    still exists but that no `case` arm calls any more is a command nobody can
    run, and scanning the file for `vibestorm.app.cli ...` would count it.
    """
    functions = _shell_functions()
    found: set[str] = set()
    for body in launcher_case_arms().values():
        bodies = [body]
        for name in re.findall(r"\b(do_[a-z0-9_]+)\b", body):
            if name in functions:
                bodies.append(functions[name])
        for text in bodies:
            found.update(re.findall(r"vibestorm\.app\.cli ([a-z0-9-]+)", text))
    return found


class LauncherReachTests(unittest.TestCase):
    def test_every_cli_subcommand_is_reachable_from_the_launcher(self) -> None:
        missing = sorted(set(cli_subcommands()) - reachable_cli_subcommands() - set(NOT_IN_THE_LAUNCHER))
        self.assertEqual(missing, [], f"in the CLI, unreachable from ./run.sh: {missing}")

    def test_the_launcher_does_not_call_a_subcommand_that_is_gone(self) -> None:
        """The other direction: a rename done on the Python side leaves the
        launcher invoking a name argparse will reject, which is a failure at
        the end of a login rather than at the start of one."""
        self.assertEqual(sorted(reachable_cli_subcommands() - set(cli_subcommands())), [])

    def test_sync_object_has_an_arm_of_its_own(self) -> None:
        """Named, because this is the one that was missing and it is the one
        C, D and E run through."""
        self.assertIn("sync-object", launcher_case_arms())
        self.assertIn("sync-object", reachable_cli_subcommands())

    def test_the_exception_list_names_real_subcommands(self) -> None:
        self.assertEqual(sorted(set(NOT_IN_THE_LAUNCHER) - set(cli_subcommands())), [])

    def test_the_lists_are_not_empty(self) -> None:
        """Anti-vacuity: both readers are regexes over other people's files,
        and a regex that stops matching makes every test above pass."""
        self.assertGreaterEqual(len(cli_subcommands()), 10)
        self.assertGreaterEqual(len(reachable_cli_subcommands()), 10)
        self.assertGreaterEqual(len(launcher_case_arms()), 10)


class ThreeListsTests(unittest.TestCase):
    """`is_command`, the dispatch `case`, and the usage text.

    Three hand-maintained lists of the same names in one file. `sync-object`
    was missing from two of them.
    """

    def test_every_dispatch_arm_is_recognised_as_a_command(self) -> None:
        arms = set(launcher_case_arms()) - {"*"}
        self.assertEqual(sorted(arms - is_command_words()), [])

    def test_every_recognised_command_has_an_arm(self) -> None:
        """The other direction is a word that stops being read as a profile
        and lands on `*)`, which at least says so."""
        self.assertEqual(sorted(is_command_words() - set(launcher_case_arms())), [])

    def test_the_word_list_is_not_empty(self) -> None:
        self.assertGreaterEqual(len(is_command_words()), 10)


def _array_flags(name: str) -> set[str]:
    """Long options collected into a shell array like `session_args`."""
    flags: set[str] = set()
    for match in re.finditer(rf"\b{name}\+?=\(([^)]*)\)", launcher_text(), re.S):
        flags.update(re.findall(r"(--[a-z0-9-]+)", match.group(1)))
    return flags


def _parser_for(module: str, subcommand: str | None):
    import argparse

    if module == "vibestorm.app.cli":
        from vibestorm.app.cli import build_parser

        parser = build_parser()
        for action in parser._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                return action.choices.get(subcommand)
        return None
    if module == "vibestorm.viewer3d.app":
        from vibestorm.viewer3d.app import build_parser as viewer3d_parser

        return viewer3d_parser()
    if module == "vibestorm.viewer.app":
        from vibestorm.viewer.app import build_parser as viewer_parser

        return viewer_parser()
    return None


def _accepted_options(parser) -> set[str]:
    accepted: set[str] = set()
    for action in parser._actions:
        accepted.update(action.option_strings)
    return accepted


class LauncherFlagTests(unittest.TestCase):
    """Every long option the launcher passes is one argparse still accepts.

    The other half of the same contract as the subcommand names, and it fails
    later: a renamed flag is an argparse error *after* `prepare_login` has
    already asked for a password, or -- for the viewers -- after a window has
    opened. `--camera-sweep`, `--no-auto-bake-upload`, `--spawn-cube`,
    `--capture-mode` and `--agent-update-interval` are all set here and
    defined over there, with nothing in between.

    The flags are read out of each `do_*` body and out of the shell arrays it
    expands, so a new one is checked without being listed here.
    """

    def _targets(self):
        for name, body in sorted(_shell_functions().items()):
            match = re.search(r"-m (vibestorm\.[a-z0-9_.]+)(?: ([a-z0-9-]+))?", body)
            if not match:
                continue
            flags = set(re.findall(r"(--[a-z0-9-]+)", body))
            for array in re.findall(r"\$\{([a-z0-9_]+)\[@\]\}", body):
                flags |= _array_flags(array)
            yield name, match.group(1), match.group(2), flags

    def test_every_flag_the_launcher_passes_is_one_the_parser_knows(self) -> None:
        for name, module, subcommand, flags in self._targets():
            with self.subTest(name):
                parser = _parser_for(module, subcommand)
                self.assertIsNotNone(parser, f"{name} runs {module} {subcommand}, which has no parser")
                unknown = sorted(flags - _accepted_options(parser))
                self.assertEqual(unknown, [], f"{name} passes options {module} rejects: {unknown}")

    def test_the_flags_are_actually_being_found(self) -> None:
        """Anti-vacuity. Every reader here is a regex over a shell file, and
        one that stops matching turns the test above into a loop over
        nothing."""
        targets = list(self._targets())
        self.assertGreaterEqual(len(targets), 10)
        self.assertTrue(all(flags for _name, _module, _sub, flags in targets))
        self.assertIn("--camera-sweep", set().union(*(f for *_r, f in targets)))


class LauncherHelpTests(unittest.TestCase):
    """The usage text is the only place a person finds these names."""

    def usage_block(self) -> str:
        text = launcher_text()
        return text.split("Login commands use env vars first", 1)[0]

    def test_help_runs_clean(self) -> None:
        """`--help` printed a shell error and swallowed a word.

        The usage heredoc is `<<EOF`, unquoted, so everything in it is
        expanded -- which is wanted for `$LOGIN_PROFILE` and is not wanted for
        backticks. "The built-in local `tester` profile" ran `tester` as a
        command: an error on stderr, and the word gone from the help text,
        leaving "The built-in local  profile".
        """
        result = subprocess.run(
            [str(RUN_SH), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("tester", result.stdout)

    def test_the_usage_heredoc_expands_nothing_it_did_not_mean_to(self) -> None:
        """The class rather than the instance. The heredoc has to stay
        unquoted -- it interpolates the profile path -- so what it must not
        contain is a backtick or a `$(`."""
        usage = launcher_text().split("usage() {", 1)[1].split("\nEOF", 1)[0]
        self.assertNotIn("`", usage)
        self.assertNotIn("$(", usage)

    def test_every_arm_a_person_can_type_is_in_the_usage_text(self) -> None:
        usage = self.usage_block()
        undocumented = [
            arm
            for arm in sorted(launcher_case_arms())
            if arm not in ("help", "-h", "--help", "*")
            and not re.search(rf"^\s+{re.escape(arm)}\s", usage, re.M)
        ]
        self.assertEqual(undocumented, [], f"arms missing from ./run.sh usage: {undocumented}")


if __name__ == "__main__":
    unittest.main()
