"""Minimal LLSD XML support for seed capability and event-queue work."""

from __future__ import annotations

import base64
import binascii
import math
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from uuid import UUID


class LlsdError(ValueError):
    """Raised when LLSD XML cannot be serialized or parsed."""


#: How deeply one LLSD value may nest before this refuses to look further.
#:
#: `_parse_value` recurses, so a body of two thousand opened arrays is a
#: `RecursionError` -- which is not a `ValueError`, is not an `LlsdError`, and
#: so walks straight past every `except LlsdError` in `caps/client.py` and out
#: of the viewer. Real LLSD from a grid is a handful of levels deep; the
#: inventory skeleton, the deepest thing this client asks for, is six.
MAX_LLSD_DEPTH = 64

#: The longest run of digits `<integer>` will convert.
#:
#: CPython refuses `int()` on more than 4,300 digits and raises `ValueError`
#: doing it. Bounded here instead so the refusal is this module's, and at a
#: length no real integer reaches: LLSD integers are 32-bit.
MAX_LLSD_DIGITS = 32


def format_xml_string_array(values: list[str]) -> bytes:
    root = ET.Element("llsd")
    array = ET.SubElement(root, "array")
    for value in values:
        element = ET.SubElement(array, "string")
        element.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def format_xml_map(values: dict[str, object]) -> bytes:
    root = ET.Element("llsd")
    map_element = ET.SubElement(root, "map")
    for key, value in values.items():
        key_element = ET.SubElement(map_element, "key")
        key_element.text = key
        map_element.append(_format_value(value))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _root(data: bytes) -> ET.Element:
    """Parse `data`, turning the parser's own failure into this module's.

    `ET.fromstring` raises `ParseError`, which subclasses `SyntaxError` and
    is nothing this module's callers are told to expect -- so a proxy's HTML
    error page, or an empty body, left every function here as a class no
    handler upstream names. That is the fourth time this exact shape has
    been found in this client, after `DecompressionBombError` past
    `decode_j2k`, `ExpatError` past `_login_sync`, and a wire field name read
    off a renamed object. The rule it keeps proving: the exception that
    escapes is the one raised by a layer you did not write.
    """
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate()
    # The one rule this parser exists for. See `_refuse_doctype`.
    parser.StartDoctypeDeclHandler = _refuse_doctype
    parser.buffer_text = True
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(data, True)
        return builder.close()
    except expat.ExpatError as exc:
        raise LlsdError(f"LLSD body is not well-formed XML: {exc}") from exc


def _refuse_doctype(name: str, sysid: object, pubid: object, has_subset: bool) -> None:
    """Refuse a DOCTYPE, because LLSD never has one and a DOCTYPE can bite.

    `read_bounded` caps the *body*, which is no defence at all against a body
    that expands. Six nested entity declarations turn 302 bytes into a
    megabyte, eight turn it into a hundred, and the expansion happens inside
    the parse, before anything upstream gets a chance to notice:

        <!DOCTYPE llsd [
          <!ENTITY a "aaaaaaaaaa">
          <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
          ... ]><llsd><string>&f;</string></llsd>

    Refusing the declaration rather than counting expansions closes the
    external-entity route in the same rule -- a capability response that names
    `file:///etc/passwd` is not something to make a careful decision about
    each time -- and costs nothing, since no grid sends a DOCTYPE.

    This is why the tree is built on expat directly rather than through
    `ET.fromstring`: `ET.XMLParser` stopped exposing its underlying expat
    parser, so there is no other place to hang this handler.
    """
    raise LlsdError("LLSD body declares a DOCTYPE, which LLSD does not use")


def parse_xml_string_map(data: bytes) -> dict[str, str]:
    root = _root(data)
    if root.tag != "llsd" or len(root) != 1 or root[0].tag != "map":
        raise LlsdError("expected LLSD XML map root")
    return _parse_map(root[0])


def parse_xml_value(data: bytes) -> object:
    root = _root(data)
    if root.tag != "llsd" or len(root) != 1:
        raise LlsdError("expected LLSD XML root with a single child")
    return _parse_value(root[0])


def _parse_map(element: ET.Element) -> dict[str, str]:
    children = list(element)
    if len(children) % 2 != 0:
        raise LlsdError("LLSD map has uneven key/value children")

    parsed: dict[str, str] = {}
    for index in range(0, len(children), 2):
        key = children[index]
        value = children[index + 1]
        if key.tag != "key":
            raise LlsdError("LLSD map entry missing key element")
        if value.tag != "string":
            raise LlsdError(f"unsupported LLSD map value type: {value.tag}")
        parsed[key.text or ""] = value.text or ""
    return parsed


def _parse_generic_map(element: ET.Element, depth: int = 0) -> dict[str, object]:
    children = list(element)
    if len(children) % 2 != 0:
        raise LlsdError("LLSD map has uneven key/value children")

    parsed: dict[str, object] = {}
    for index in range(0, len(children), 2):
        key = children[index]
        value = children[index + 1]
        if key.tag != "key":
            raise LlsdError("LLSD map entry missing key element")
        parsed[key.text or ""] = _parse_value(value, depth)
    return parsed


def _parse_array(element: ET.Element, depth: int = 0) -> list[object]:
    return [_parse_value(child, depth) for child in list(element)]


def _parse_value(element: ET.Element, depth: int = 0) -> object:
    if element.tag in {"map", "array"}:
        if depth >= MAX_LLSD_DEPTH:
            raise LlsdError(f"LLSD nests deeper than {MAX_LLSD_DEPTH}")
        if element.tag == "map":
            return _parse_generic_map(element, depth + 1)
        return _parse_array(element, depth + 1)
    if element.tag == "string":
        return element.text or ""
    if element.tag == "integer":
        return _parse_integer(element)
    if element.tag == "boolean":
        text = (element.text or "").lower()
        return text in {"1", "true"}
    if element.tag == "uuid":
        return element.text or ""
    if element.tag == "real":
        return _parse_real(element)
    if element.tag == "binary":
        return _parse_binary(element)
    if element.tag in {"uri", "date"}:
        return element.text or ""
    if element.tag == "undef":
        return None
    raise LlsdError(f"unsupported LLSD value type: {element.tag}")


def _parse_integer(element: ET.Element) -> int:
    """`<integer>`, with the conversion's own failure turned into ours.

    `int("abc")` raises a bare `ValueError`, and `LlsdError` is a *subclass*
    of `ValueError`, so `except LlsdError` in `caps/client.py` does not catch
    it: the body arrives, the parse fails, and what leaves this module is an
    exception no handler upstream names. That is the shape `_root`'s docstring
    was written about, one function away from it.
    """
    text = (element.text or "0").strip() or "0"
    if len(text.lstrip("+-")) > MAX_LLSD_DIGITS:
        raise LlsdError("LLSD integer is longer than any integer needs to be")
    try:
        return int(text)
    except ValueError as exc:
        raise LlsdError(f"LLSD integer is not a number: {exc}") from exc


def _parse_real(element: ET.Element) -> float:
    """`<real>`, refusing what is not a number as well as what will not parse.

    The refusal of `nan` and `inf` is the terrain lesson applied one layer up.
    A non-finite float does not announce itself: every comparison against a
    NaN is false, so it disables a bounds check rather than tripping it, and
    it travels a long way from where it entered. Capability responses feed
    object costs, prim physics and mesh headers, and this client has never
    seen a grid send a non-finite real. If one ever does, that is a finding
    worth surfacing as an error rather than a value worth passing on -- and
    the error is caught and reported, because `LlsdError` is.
    """
    text = (element.text or "0").strip() or "0"
    try:
        value = float(text)
    except ValueError as exc:
        raise LlsdError(f"LLSD real is not a number: {exc}") from exc
    if not math.isfinite(value):
        raise LlsdError(f"LLSD real is not finite: {text!r}")
    return value


def _parse_binary(element: ET.Element) -> bytes:
    text = (element.text or "").strip()
    if not text:
        return b""
    encoding = (element.get("encoding") or "base64").lower()
    try:
        if encoding == "base16":
            return base64.b16decode(text, casefold=True)
        if encoding == "base85":
            return base64.b85decode(text)
        return base64.b64decode(text)
    except (ValueError, binascii.Error) as exc:
        raise LlsdError(f"invalid LLSD binary ({encoding}): {exc}") from exc


def _format_value(value: object) -> ET.Element:
    if isinstance(value, UUID):
        element = ET.Element("uuid")
        element.text = str(value)
        return element
    if isinstance(value, bool):
        element = ET.Element("boolean")
        element.text = "true" if value else "false"
        return element
    if isinstance(value, int):
        element = ET.Element("integer")
        element.text = str(value)
        return element
    if isinstance(value, str):
        element = ET.Element("string")
        element.text = value
        return element
    if value is None:
        return ET.Element("undef")
    if isinstance(value, list):
        element = ET.Element("array")
        for entry in value:
            element.append(_format_value(entry))
        return element
    if isinstance(value, tuple):
        element = ET.Element("array")
        for entry in value:
            element.append(_format_value(entry))
        return element
    if isinstance(value, dict):
        element = ET.Element("map")
        for key, entry in value.items():
            key_element = ET.SubElement(element, "key")
            key_element.text = str(key)
            element.append(_format_value(entry))
        return element
    raise LlsdError(f"unsupported LLSD serialization type: {type(value).__name__}")
