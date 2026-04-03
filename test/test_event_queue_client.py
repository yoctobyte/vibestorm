import unittest

from vibestorm.caps.llsd import format_xml_map, parse_xml_value


class EventQueueLlsdTests(unittest.TestCase):
    def test_format_xml_map(self) -> None:
        encoded = format_xml_map({"ack": 0, "done": False})
        text = encoded.decode("utf-8")
        self.assertIn("<key>ack</key>", text)
        self.assertIn("<integer>0</integer>", text)
        self.assertIn("<key>done</key>", text)
        self.assertIn("<boolean>false</boolean>", text)

    def test_parse_xml_value_nested(self) -> None:
        data = (
            b"<llsd><map>"
            b"<key>id</key><integer>7</integer>"
            b"<key>events</key><array>"
            b"<map><key>message</key><string>EnableSimulator</string></map>"
            b"</array>"
            b"</map></llsd>"
        )
        parsed = parse_xml_value(data)
        assert isinstance(parsed, dict)
        self.assertEqual(parsed["id"], 7)
        self.assertEqual(parsed["events"], [{"message": "EnableSimulator"}])
