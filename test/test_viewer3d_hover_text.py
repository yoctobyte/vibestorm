"""Hover text (prim floating text) from wire to inspector row.

Floating text is the most visible thing an ObjectUpdate carries that the
client used to record only as a byte count. These cover the two halves that
can silently go wrong: the inverted alpha byte, and multi-line text getting
squashed into one inspector row.
"""

import unittest


class HoverTextInspectorLineTests(unittest.TestCase):
    class _Entity:
        def __init__(self, hover_text=None, hover_text_color=None):
            self.hover_text = hover_text
            self.hover_text_color = hover_text_color

    def test_no_hover_text_adds_no_rows(self) -> None:
        from vibestorm.viewer3d.hud import _hover_text_lines

        self.assertEqual(_hover_text_lines(self._Entity()), [])

    def test_single_line_renders_text_and_colour(self) -> None:
        from vibestorm.viewer3d.hud import _hover_text_lines

        lines = _hover_text_lines(
            self._Entity(hover_text="For sale", hover_text_color=(255, 128, 0, 255))
        )

        self.assertEqual(
            lines, ["Hover Text: For sale", "Hover Text Color: rgba(255, 128, 0, 255)"]
        )

    def test_each_wire_newline_becomes_its_own_row(self) -> None:
        from vibestorm.viewer3d.hud import _hover_text_lines

        lines = _hover_text_lines(self._Entity(hover_text="Vendor\nPrice: 250 L$"))

        self.assertEqual(lines, ["Hover Text: Vendor", "Hover Text: Price: 250 L$"])

    def test_markup_in_hover_text_is_escaped(self) -> None:
        # Hover text is authored by other residents and rendered into the
        # inspector's HTML text box.
        from vibestorm.viewer3d.hud import _hover_text_lines

        lines = _hover_text_lines(self._Entity(hover_text="<b>bold</b>"))

        self.assertNotIn("<b>", lines[0])


class HoverTextWireDecodeTests(unittest.TestCase):
    def test_empty_and_all_nul_payloads_report_no_text(self) -> None:
        from vibestorm.udp.messages import _decode_object_string

        self.assertIsNone(_decode_object_string(b""))
        self.assertIsNone(_decode_object_string(b"\x00\x00\x00"))

    def test_trailing_nul_is_stripped(self) -> None:
        from vibestorm.udp.messages import _decode_object_string

        self.assertEqual(_decode_object_string(b"hello\x00"), "hello")

    def test_invalid_utf8_is_replaced_rather_than_raising(self) -> None:
        # A malformed string should cost the text, not the whole update.
        from vibestorm.udp.messages import _decode_object_string

        self.assertIsNotNone(_decode_object_string(b"caf\xff\x00"))

    def test_short_colour_payload_reports_none(self) -> None:
        from vibestorm.udp.messages import _decode_text_color

        self.assertIsNone(_decode_text_color(b"\x01\x02\x03"))


if __name__ == "__main__":
    unittest.main()
