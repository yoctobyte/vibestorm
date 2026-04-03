"""Minimal LLSD XML support for seed capability and event-queue work."""

from __future__ import annotations

import xml.etree.ElementTree as ET


class LlsdError(ValueError):
    """Raised when LLSD XML cannot be serialized or parsed."""


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


def parse_xml_string_map(data: bytes) -> dict[str, str]:
    root = ET.fromstring(data)
    if root.tag != "llsd" or len(root) != 1 or root[0].tag != "map":
        raise LlsdError("expected LLSD XML map root")
    return _parse_map(root[0])


def parse_xml_value(data: bytes) -> object:
    root = ET.fromstring(data)
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


def _parse_generic_map(element: ET.Element) -> dict[str, object]:
    children = list(element)
    if len(children) % 2 != 0:
        raise LlsdError("LLSD map has uneven key/value children")

    parsed: dict[str, object] = {}
    for index in range(0, len(children), 2):
        key = children[index]
        value = children[index + 1]
        if key.tag != "key":
            raise LlsdError("LLSD map entry missing key element")
        parsed[key.text or ""] = _parse_value(value)
    return parsed


def _parse_array(element: ET.Element) -> list[object]:
    return [_parse_value(child) for child in list(element)]


def _parse_value(element: ET.Element) -> object:
    if element.tag == "map":
        return _parse_generic_map(element)
    if element.tag == "array":
        return _parse_array(element)
    if element.tag == "string":
        return element.text or ""
    if element.tag == "integer":
        return int(element.text or "0")
    if element.tag == "boolean":
        text = (element.text or "").lower()
        return text in {"1", "true"}
    if element.tag == "uuid":
        return element.text or ""
    if element.tag == "real":
        return float(element.text or "0")
    if element.tag == "undef":
        return None
    raise LlsdError(f"unsupported LLSD value type: {element.tag}")


def _format_value(value: object) -> ET.Element:
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
    raise LlsdError(f"unsupported LLSD serialization type: {type(value).__name__}")
