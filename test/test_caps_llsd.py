import base64
import unittest

from vibestorm.caps.llsd import (
    LlsdError,
    format_xml_string_array,
    parse_xml_string_map,
    parse_xml_value,
)


class LlsdTests(unittest.TestCase):
    def test_parse_binary_base64(self) -> None:
        raw = bytes([127, 0, 0, 1])
        b64 = base64.b64encode(raw).decode("ascii")
        data = f"<llsd><binary encoding='base64'>{b64}</binary></llsd>".encode("utf-8")

        self.assertEqual(parse_xml_value(data), raw)

    def test_parse_binary_defaults_to_base64(self) -> None:
        b64 = base64.b64encode(b"hello").decode("ascii")
        data = f"<llsd><binary>{b64}</binary></llsd>".encode("utf-8")

        self.assertEqual(parse_xml_value(data), b"hello")

    def test_parse_binary_empty(self) -> None:
        self.assertEqual(parse_xml_value(b"<llsd><binary/></llsd>"), b"")

    def test_parse_binary_invalid_raises(self) -> None:
        with self.assertRaises(LlsdError):
            parse_xml_value(b"<llsd><binary>not!base64!</binary></llsd>")

    def test_format_xml_string_array(self) -> None:
        encoded = format_xml_string_array(["EventQueueGet", "SimulatorFeatures"])
        text = encoded.decode("utf-8")
        self.assertIn("<array>", text)
        self.assertIn("<string>EventQueueGet</string>", text)
        self.assertIn("<string>SimulatorFeatures</string>", text)

    def test_parse_xml_string_map(self) -> None:
        data = (
            b"<llsd><map>"
            b"<key>EventQueueGet</key><string>http://example/eq</string>"
            b"<key>SimulatorFeatures</key><string>http://example/features</string>"
            b"</map></llsd>"
        )
        parsed = parse_xml_string_map(data)
        self.assertEqual(parsed["EventQueueGet"], "http://example/eq")
        self.assertEqual(parsed["SimulatorFeatures"], "http://example/features")
