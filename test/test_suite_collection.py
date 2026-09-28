"""What the runner collects, which is a claim nothing else in the suite makes.

Every test file here asserts something about the client. None of them asserted
anything about the *suite*, and that is where a fault sat from 2026-09-08 to
2026-09-28: `test_pygame_gui_agreement._AgreementCase` is an abstract base
holding two claims and no screen to check them against, guarded with

    __test__ = False

which is **pytest's** convention. `./run.sh test` runs `unittest discover`
(`run.sh`), which does not read that attribute, so the base class was
collected and run like any other case and raised `NotImplementedError` out of
its own `module()`. The suite reported `FAILED (errors=2)` for twenty days and
eight commits were pushed over it, against the one hard rule in `AGENTS.md`.

The one-line fix -- make the base a mixin, so it is not a `TestCase` and
cannot be collected -- is not what keeps this from coming back. The next
person to write an abstract case will reach for `__test__` again, because it
is what pytest documents and it *looks* like it works: the failure is two
`ERROR:` lines for an underscore-named class in a 2,958-test run, three lines
above a summary nobody reads after a green-looking scroll.

So these tests ask the runner itself rather than trusting a convention,
and one of them pins the stdlib behaviour that makes the convention wrong.

The third class here is a different fault of the same family, found in the
same pass: a default that was live rather than inert, written into the
owner's evidence database by every test that did not say otherwise.
"""

from __future__ import annotations

import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent

#: Well under the real count, which is a floor rather than a target: it is
#: here so that a `discover` that silently returned an empty suite -- an
#: import error swallowed, a start directory that moved -- cannot satisfy the
#: test above it by finding nothing to complain about.
MINIMUM_TESTS = 2000


def collected_cases() -> list[unittest.TestCase]:
    """Every test `unittest discover` finds, flattened.

    Discovery, not execution: this imports the suite's modules (already in
    `sys.modules` by the time this runs) and builds the suite objects. It does
    not run them, so there is no recursion through this file.
    """

    def walk(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from walk(item)
            else:
                yield item

    return list(walk(unittest.TestLoader().discover(str(TEST_DIR))))


class SuiteCollectionTests(unittest.TestCase):
    def test_no_abstract_base_case_is_collected(self) -> None:
        """An underscore-named class is this repo's way of saying "not a case".

        Twelve files declare one. Ten are already safe by construction -- they
        hold helpers and leave every `test_` method to their subclasses, so
        the loader finds nothing on them to run. The two that also held the
        claims themselves are the two this test is about.
        """
        collected = collected_cases()
        abstract = sorted(
            {
                f"{type(case).__module__}.{type(case).__name__}"
                for case in collected
                if type(case).__name__.startswith("_")
            }
        )
        self.assertEqual(
            abstract,
            [],
            "abstract base cases are being collected and run: "
            f"{abstract}. An underscore-named class must not subclass "
            "unittest.TestCase -- make it a mixin and have the concrete "
            "cases inherit (Mixin, unittest.TestCase).",
        )

    def test_the_suite_is_not_empty(self) -> None:
        """Anti-vacuity for the test above, which passes on an empty suite."""
        self.assertGreaterEqual(len(collected_cases()), MINIMUM_TESTS)


class WhyNotDunderTestTests(unittest.TestCase):
    """Pin the stdlib behaviour the fix depends on, rather than describing it.

    Without this, `__test__ = False` reads like something that ought to work
    and was applied wrongly, and the obvious "fix" is to reinstate it. It does
    not work, it has never worked here, and that is a fact about
    `unittest.TestLoader` rather than about how it was used.
    """

    def test_unittest_collects_a_case_that_says_it_is_not_one(self) -> None:
        class _PretendsItIsNotACase(unittest.TestCase):
            __test__ = False

            def test_this_still_runs(self) -> None:  # pragma: no cover
                raise AssertionError("collected after all")

        names = unittest.TestLoader().getTestCaseNames(_PretendsItIsNotACase)
        self.assertEqual(
            names,
            ["test_this_still_runs"],
            "unittest has started honouring __test__; the mixins in "
            "test_pygame_gui_agreement.py and test_scene_wiring.py could be "
            "simplified, and this test is how you would find that out",
        )

    def test_a_mixin_is_collected_from_its_concrete_subclass_only(self) -> None:
        """The shape the fix uses, asserted end to end."""

        class _Mixin:
            def test_the_claim(self) -> None:
                self.assertTrue(True)

        class Concrete(_Mixin, unittest.TestCase):
            pass

        loader = unittest.TestLoader()
        self.assertEqual(loader.getTestCaseNames(Concrete), ["test_the_claim"])
        # The mixin is not a TestCase, so the loader has nothing to take from
        # it -- which is the whole guarantee, and it is structural rather than
        # conventional.
        self.assertFalse(issubclass(_Mixin, unittest.TestCase))



class EvidenceDatabaseIsolationTests(unittest.TestCase):
    """No test may write to the owner's evidence database.

    `SessionConfig.unknowns_db_path` defaults to `local/unknowns.sqlite3` --
    the real forensic store, the one `projectstate.md` says to move aside
    rather than delete if it is ever polluted. It is a *relative* path,
    resolved against the working directory, and the suite runs from the
    repository root. So every test that built a session and did not say
    otherwise recorded into it.

    Measured on 2026-09-28, before the fix: 50,299 session rows of which
    **343** came from a real agent, 1,172,544 inbound-message rows of which
    180,947 did, 186 MB. About 104 rows per suite run, and the 14,650 dated
    2026-09-08 are a mutation battery -- so the instrument that found the
    `seen_sequences` leak was also the largest single polluter of the evidence
    it reasoned about. `./run.sh unknowns` with no arguments reports the
    latest session, which had meant *the last test* rather than the last live
    run.

    **This is deliberately not a check on call sites, and the first version of
    it was.** That version walked every `SessionConfig(...)` in `test/` and
    required an explicit `unknowns_db_path`; eleven took the default, they
    were fixed, and the next run still wrote 86 rows. The default is reached
    by three routes -- an explicit `SessionConfig()`, `LiveCircuitSession`'s
    own dataclass default, and `run_live_session(config=None)` -- across
    eighty-five call sites, which is well past the count at which patching
    call sites is a strategy. It is the same lesson `remote_url.py` records:
    the guarantee has to live at the one place that opens the thing, not at
    the places that ask for it.

    So `./run.sh test` exports `VIBESTORM_UNKNOWNS_DB` at a throwaway file and
    `DEFAULT_UNKNOWNS_DB_PATH` reads it at import, which puts every route in a
    tmpdir at once. This test is the floor under that: it asks where the
    default actually points, so it fails for a suite run outside the launcher
    as well as for a launcher that stopped isolating.
    """

    def test_the_suite_is_not_pointed_at_the_real_evidence_database(self) -> None:
        from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH
        from vibestorm.udp.session import SessionConfig

        real = (TEST_DIR.parent / "local" / "unknowns.sqlite3").resolve()
        for name, path in (
            ("DEFAULT_UNKNOWNS_DB_PATH", DEFAULT_UNKNOWNS_DB_PATH),
            ("SessionConfig().unknowns_db_path", SessionConfig().unknowns_db_path),
        ):
            with self.subTest(name):
                self.assertIsNotNone(path, f"{name} is None")
                self.assertNotEqual(
                    Path(path).resolve(),
                    real,
                    f"{name} points at the owner's evidence database. Run the "
                    "suite through ./run.sh test, which exports "
                    "VIBESTORM_UNKNOWNS_DB at a throwaway file; a bare "
                    "`python -m unittest` writes ~100 synthetic sessions into "
                    "local/unknowns.sqlite3.",
                )

    def test_the_two_defaults_agree(self) -> None:
        """`SessionConfig` binds the module constant at class creation.

        Which is why the override has to be read at import time and not per
        call: a later assignment to `DEFAULT_UNKNOWNS_DB_PATH` would move one
        of these and not the other, and the session would keep recording where
        the constant no longer says.
        """
        from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH
        from vibestorm.udp.session import SessionConfig

        self.assertEqual(SessionConfig().unknowns_db_path, DEFAULT_UNKNOWNS_DB_PATH)


if __name__ == "__main__":
    unittest.main()
