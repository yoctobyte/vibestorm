"""Every recursive parser in this client has to have somewhere to stop.

Two were found on the same day, in two formats, and neither was going to be
caught by the fuzz sweeps: a corpus of flipped bytes and truncations does not
produce two thousand nested containers, and a corpus of random bytes does not
get past a header.

- `caps/llsd.py` parses XML LLSD from every capability response. Two thousand
  opened `<array>` elements is a `RecursionError`, which is not a `ValueError`
  and therefore not an `LlsdError`, and so goes straight past the four
  `except LlsdError` handlers in `caps/client.py` that exist for exactly this.
- `assets/sl_mesh.py` parses the binary LLSD header of a mesh asset. Five
  bytes buy one level of nesting, so ten kilobytes of an asset buys two
  thousand -- and this one runs on the render thread, from `perspective.py`,
  on an asset any object owner chooses.

So rather than a third test about the second instance, this is the shape:
**find every function in the client that can reach itself, and require it to
carry a depth.** A recursive function whose input comes off a socket and which
has no limit is a crash waiting for someone to send the input.

The exemptions are listed with reasons rather than inferred, because "this one
is fine" is the sentence that both of the above would have been covered by.
"""

from __future__ import annotations

import ast
import unittest
from collections import defaultdict
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vibestorm"

#: Parameter names that count as a depth. A recursive function carrying one of
#: these has at least been thought about; whether the limit is right is the
#: business of that module's own tests.
DEPTH_PARAMETERS = frozenset({"depth", "remaining_depth", "max_depth", "limit"})

#: Recursive functions that legitimately have no depth, and why. A reason is
#: required: the two bugs this file exists for would both have been waved
#: through by a bare name in a list.
EXEMPT: dict[tuple[str, str], str] = {
    ("caps/llsd.py", "_format_value"): (
        "Serialises outward. Its input is a payload this client builds -- ack "
        "ids, UUIDs, a list of capability names -- never a structure echoed "
        "back from the wire, so there is no remote party who can choose its "
        "depth. Check `format_xml_map`'s callers before adding to this."
    ),
}


def recursive_functions(path: Path) -> dict[str, ast.FunctionDef]:
    """Functions in `path` that can reach themselves, directly or not.

    Mutual recursion is the case that matters: `caps/llsd.py` recurses through
    three functions, and none of them calls itself. A scan for direct
    self-calls sees none of it.

    Only two kinds of call are followed -- a bare name, and `self.<name>` --
    because anything else is a guess. Resolving `x.close()` to a module-level
    `close` finds fifteen imaginary cycles and hides the real ones in them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions: dict[tuple[str | None, str], ast.FunctionDef] = {}

    def collect(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                collect(child, child.name)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                functions[(owner, child.name)] = child
                collect(child, owner)
            else:
                collect(child, owner)

    collect(tree, None)

    edges: dict[tuple[str | None, str], set[tuple[str | None, str]]] = defaultdict(set)
    for key, node in functions.items():
        owner, _ = key
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            called = inner.func
            if isinstance(called, ast.Name):
                for candidate in ((None, called.id), (owner, called.id)):
                    if candidate in functions:
                        edges[key].add(candidate)
                        break
            elif isinstance(called, ast.Attribute):
                base = called.value
                if isinstance(base, ast.Call):
                    continue  # super().<name>() is not this class's method
                if (
                    isinstance(base, ast.Name)
                    and base.id == "self"
                    and (owner, called.attr) in functions
                ):
                    edges[key].add((owner, called.attr))

    found: dict[str, ast.FunctionDef] = {}
    for start in functions:
        seen = {start}
        stack = list(edges.get(start, ()))
        while stack:
            current = stack.pop()
            if current == start:
                found[start[1]] = functions[start]
                break
            if current in seen:
                continue
            seen.add(current)
            stack.extend(edges.get(current, ()))
    return found


def parameters(node: ast.FunctionDef) -> set[str]:
    return {argument.arg for argument in node.args.args + node.args.kwonlyargs}


class RecursionDepthTests(unittest.TestCase):
    def surveyed(self) -> list[tuple[str, str, ast.FunctionDef]]:
        rows = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            for name, node in sorted(recursive_functions(path).items()):
                rows.append((relative, name, node))
        return rows

    def test_every_recursive_function_carries_a_depth_or_a_reason(self) -> None:
        for relative, name, node in self.surveyed():
            with self.subTest(f"{relative}:{name}"):
                if (relative, name) in EXEMPT:
                    self.assertTrue(
                        EXEMPT[(relative, name)].strip(),
                        "an exemption needs a reason, not just an entry",
                    )
                    continue
                self.assertTrue(
                    parameters(node) & DEPTH_PARAMETERS,
                    f"{relative}:{node.lineno} {name}() can reach itself and takes no "
                    f"depth. If its input never comes off a socket, add it to EXEMPT "
                    f"with the reason.",
                )

    def test_the_survey_is_still_finding_the_recursion_it_knows_about(self) -> None:
        """Anti-vacuity. A resolver that stops matching calls would make this
        whole file pass by finding nothing, which is the failure mode of every
        test that searches for something."""
        found = {(relative, name) for relative, name, _ in self.surveyed()}
        for expected in (
            ("caps/llsd.py", "_parse_value"),
            ("caps/llsd.py", "_parse_array"),
            ("caps/llsd.py", "_parse_generic_map"),
            ("caps/llsd.py", "_format_value"),
            ("assets/sl_mesh.py", "_parse_value"),
        ):
            with self.subTest(expected):
                self.assertIn(expected, found)

    def test_mutual_recursion_is_seen_and_a_method_of_another_object_is_not(self) -> None:
        """The two halves of the resolver, on a module written for the purpose.

        `a`/`b` call each other and neither calls itself. `lonely` calls
        `other.lonely`, which is a different object's method and not a cycle
        -- and treating it as one is how a first draft of this found fifteen
        imaginary cycles in the viewer.
        """
        import tempfile

        source = (
            "def a(x):\n    return b(x)\n\n"
            "def b(x):\n    return a(x)\n\n"
            "def lonely(other):\n    return other.lonely()\n\n"
            "class C:\n    def m(self):\n        return self.m()\n"
            "    def n(self):\n        return self.m()\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.py"
            path.write_text(source, encoding="utf-8")
            found = set(recursive_functions(path))
        self.assertEqual(found, {"a", "b", "m"})

    def test_the_exemptions_all_name_something_that_exists(self) -> None:
        """A stale exemption is worse than none: it reads as a decision that
        was made, about code that is gone."""
        surveyed = {(relative, name) for relative, name, _ in self.surveyed()}
        for key in EXEMPT:
            with self.subTest(key):
                self.assertIn(key, surveyed)


if __name__ == "__main__":
    unittest.main()
