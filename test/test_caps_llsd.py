import unittest

from vibestorm.caps.llsd import format_xml_string_array, parse_xml_string_map


class LlsdTests(unittest.TestCase):
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
