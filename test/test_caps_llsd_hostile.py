"""A capability response that is not trying to be helpful.

Every LLSD body this client parses came off a socket. `caps/client.py` catches
`LlsdError` around each parse and turns it into a `CapabilityError` -- and the
comment there says why, in four places: "`LlsdError` is caught nowhere in this
client, so letting it through would only rename the exception that escapes".

Four things got past that. Three raise something that is not an `LlsdError`,
so the handler does not see them; the fourth raises nothing at all and is the
worse one.

- `int()` and `float()` raise a bare `ValueError`. `LlsdError` is a *subclass*
  of `ValueError`, so `except LlsdError` does not catch its parent, and
  `<integer>abc</integer>` leaves this module as an exception nothing names.
- `_parse_value` recurses. Two thousand opened arrays is a `RecursionError`,
  which is not a `ValueError` at all.
- A `<real>` of `nan` or `inf` parses fine and goes on into object costs, prim
  physics and mesh headers. The terrain decoder taught this project what a
  non-finite float does downstream: every comparison against a NaN is false,
  so it switches a bounds check off rather than tripping it.
- **A DOCTYPE.** `read_bounded` caps the body, which is no defence against a
  body that *expands*: 302 bytes of nested entity declarations became a
  megabyte inside the parser, and two more levels would make it ten gigabytes.
  The cap is on what arrives, and the expansion happens after it arrives.

The last one is the reason the tree is built on expat directly now:
`ET.XMLParser` no longer exposes its underlying parser, so there was nowhere
else to hang a handler that refuses the declaration.
"""

from __future__ import annotations

import unittest

from vibestorm.caps.llsd import (
    MAX_LLSD_DEPTH,
    LlsdError,
    parse_xml_string_map,
    parse_xml_value,
)

#: 302 bytes in, one million characters out. Six levels; each further level
#: multiplies by ten.
ENTITY_BOMB = b"""<!DOCTYPE llsd [
<!ENTITY a "aaaaaaaaaa">
<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
<!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">
<!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">
]><llsd><string>&f;</string></llsd>"""

EXTERNAL_ENTITY = (
    b'<!DOCTYPE llsd [<!ENTITY x SYSTEM "file:///etc/passwd">]><llsd><string>&x;</string></llsd>'
)


def nested(depth: int, inner: bytes = b"<integer>3</integer>") -> bytes:
    return b"<llsd>" + b"<array>" * depth + inner + b"</array>" * depth + b"</llsd>"


def nested_maps(depth: int, inner: bytes = b"<integer>3</integer>") -> bytes:
    """The same shape through the other recursive branch.

    Arrays and maps recurse through separate functions, and a depth that is
    passed on without being incremented in one of them is a limit that does
    not apply to that half. A mutation battery found exactly that: the map
    branch was unbounded while every test here nested arrays.
    """
    opened = b"<map><key>k</key>" * depth
    return b"<llsd>" + opened + inner + b"</map>" * depth + b"</llsd>"


class EverythingHostileIsAnLlsdError(unittest.TestCase):
    """The one property the callers depend on, stated once over every shape.

    `caps/client.py` catches `LlsdError` and nothing else. So the contract is
    not "these bodies are rejected", it is "these bodies are rejected *as an
    `LlsdError`*" -- a body that raises the right sort of complaint through
    the wrong sort of exception is not handled at all.
    """

    BODIES = {
        "an integer that is not one": b"<llsd><integer>abc</integer></llsd>",
        "an integer no integer needs": b"<llsd><integer>" + b"9" * 100_000 + b"</integer></llsd>",
        "a real that is not one": b"<llsd><real>abc</real></llsd>",
        "a real that is not a number": b"<llsd><real>nan</real></llsd>",
        "a real that is infinite": b"<llsd><real>-inf</real></llsd>",
        "an array nested past the limit": nested(MAX_LLSD_DEPTH + 1),
        "an array nested far past it": nested(2000),
        "a map nested past the limit": nested_maps(MAX_LLSD_DEPTH + 1),
        "a map nested far past it": nested_maps(2000),
        "maps and arrays alternating past it": b"<llsd>"
        + b"<array><map><key>k</key>" * 2000
        + b"<integer>3</integer>"
        + b"</map></array>" * 2000
        + b"</llsd>",
        "an entity bomb": ENTITY_BOMB,
        "an external entity": EXTERNAL_ENTITY,
        "an empty body": b"",
        "a proxy's error page": b"<html><body>502 Bad Gateway</body></html>",
        "a body cut in half": b"<llsd><map><key>a</key><stri",
        "a value type nobody sends": b"<llsd><widget>3</widget></llsd>",
    }

    def test_every_hostile_body_is_an_llsd_error(self) -> None:
        for label, body in self.BODIES.items():
            with self.subTest(label):
                with self.assertRaises(LlsdError):
                    parse_xml_value(body)

    def test_the_string_map_entry_point_is_no_softer(self) -> None:
        """It has its own `_root` call and its own callers in `caps/client.py`."""
        for label, body in self.BODIES.items():
            with self.subTest(label):
                with self.assertRaises(LlsdError):
                    parse_xml_string_map(body)


class ExpansionTests(unittest.TestCase):
    """The one that costs memory rather than raising."""

    def test_a_body_that_would_expand_is_refused_before_it_does(self) -> None:
        self.assertLess(len(ENTITY_BOMB), 400, "the point is that the body is small")
        with self.assertRaises(LlsdError) as caught:
            parse_xml_value(ENTITY_BOMB)
        self.assertIn("DOCTYPE", str(caught.exception))

    def test_an_external_entity_is_refused_by_the_same_rule(self) -> None:
        """One rule, not a careful decision per entity: a capability response
        that names a local file is not a thing to weigh up each time."""
        with self.assertRaises(LlsdError) as caught:
            parse_xml_value(EXTERNAL_ENTITY)
        self.assertIn("DOCTYPE", str(caught.exception))

    def test_a_harmless_doctype_is_refused_too(self) -> None:
        """No grid sends one, so there is nothing to weigh against the rule."""
        with self.assertRaises(LlsdError):
            parse_xml_value(b"<!DOCTYPE llsd><llsd><string>x</string></llsd>")


class IntegerLengthTests(unittest.TestCase):
    """Why there is a digit cap when `int()` already refuses long strings.

    CPython refuses `int()` on more than 4,300 digits -- but that limit is a
    *setting*, `sys.set_int_max_str_digits`, and an embedder or a future
    default can raise or disable it. With it disabled, an unguarded
    `int("9" * 1_000_000)` does not raise at all: it succeeds, slowly, and
    hands a million-digit integer to whatever asked for an LLSD field.

    So the cap is not a duplicate of the interpreter's. It is the one that
    still holds when the interpreter's is gone.
    """

    def test_a_million_digit_integer_is_refused_with_the_interpreter_s_limit_off(
        self,
    ) -> None:
        import sys

        previous = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(0)  # 0 disables the limit entirely
        self.addCleanup(sys.set_int_max_str_digits, previous)
        body = b"<llsd><integer>" + b"9" * 1_000_000 + b"</integer></llsd>"
        with self.assertRaises(LlsdError):
            parse_xml_value(body)


class StillParsesRealLlsdTests(unittest.TestCase):
    """The control. A guard that rejects everything is not a guard."""

    def test_the_shapes_a_grid_actually_sends(self) -> None:
        cases = {
            b"<llsd><map><key>a</key><integer>7</integer></map></llsd>": {"a": 7},
            b"<llsd><real>1.5</real></llsd>": 1.5,
            b"<llsd><real>-0.0</real></llsd>": -0.0,
            b"<llsd><integer>-2147483648</integer></llsd>": -2147483648,
            b"<llsd><string>hello &amp; goodbye</string></llsd>": "hello & goodbye",
            b"<?xml version='1.0' encoding='UTF-8'?><llsd><string>x</string></llsd>": "x",
            b"<llsd><undef /></llsd>": None,
        }
        for body, expected in cases.items():
            with self.subTest(body[:40]):
                self.assertEqual(parse_xml_value(body), expected)

    def test_nesting_up_to_the_limit_is_fine(self) -> None:
        value = parse_xml_value(nested(MAX_LLSD_DEPTH))
        for _ in range(MAX_LLSD_DEPTH):
            value = value[0]
        self.assertEqual(value, 3)

    def test_maps_nested_up_to_the_limit_are_fine_too(self) -> None:
        value = parse_xml_value(nested_maps(MAX_LLSD_DEPTH))
        for _ in range(MAX_LLSD_DEPTH):
            value = value["k"]
        self.assertEqual(value, 3)

    def test_the_limit_is_deeper_than_anything_a_grid_sends(self) -> None:
        """The inventory skeleton is the deepest thing this client asks for,
        and it is six."""
        self.assertGreaterEqual(MAX_LLSD_DEPTH, 32)

    def test_a_seed_capability_map_still_reads(self) -> None:
        body = (
            b"<llsd><map>"
            b"<key>GetTexture</key><string>http://sim/cap/1</string>"
            b"<key>GetMesh2</key><string>http://sim/cap/2</string>"
            b"</map></llsd>"
        )
        self.assertEqual(
            parse_xml_string_map(body),
            {"GetTexture": "http://sim/cap/1", "GetMesh2": "http://sim/cap/2"},
        )


if __name__ == "__main__":
    unittest.main()
