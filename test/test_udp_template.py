import unittest
from pathlib import Path

from vibestorm.udp.template import load_template_summaries, template_path


class TemplateLoadingTests(unittest.TestCase):
    def test_load_template_summaries(self) -> None:
        summaries = load_template_summaries(template_path(Path.cwd()))
        self.assertIn("PacketAck", summaries)
        self.assertIn("StartPingCheck", summaries)
        self.assertIn("UseCircuitCode", summaries)
        self.assertEqual(summaries["PacketAck"].message_number, 0xFFFFFFFB)
        self.assertEqual(summaries["PacketAck"].frequency, "Fixed")
        self.assertEqual(summaries["StartPingCheck"].message_number_bytes, 1)
        self.assertEqual(summaries["UseCircuitCode"].frequency, "Low")
