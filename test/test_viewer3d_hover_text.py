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


class AvatarDisplayNameTests(unittest.TestCase):
    """Avatar names ride ObjectUpdate NameValues, not ObjectPropertiesFamily.

    The pairs were parsed and stored from the start but never read, so every
    avatar was anonymous in the inspector and unlabelled in the 3D view.
    """

    def test_first_and_last_name_are_joined(self) -> None:
        from vibestorm.viewer3d.scene import avatar_display_name

        self.assertEqual(
            avatar_display_name({"FirstName": "Vibestorm", "LastName": "Tester"}),
            "Vibestorm Tester",
        )

    def test_resident_last_name_is_dropped(self) -> None:
        # "Resident" is SL's placeholder for a single-name account; viewers
        # show just the first name.
        from vibestorm.viewer3d.scene import avatar_display_name

        self.assertEqual(
            avatar_display_name({"FirstName": "Someone", "LastName": "Resident"}),
            "Someone",
        )

    def test_group_title_becomes_the_line_above(self) -> None:
        from vibestorm.viewer3d.scene import avatar_display_name

        self.assertEqual(
            avatar_display_name(
                {"FirstName": "A", "LastName": "B", "Title": "Builder"}
            ),
            "Builder\nA B",
        )

    def test_empty_title_adds_no_line(self) -> None:
        # OpenSim sends Title as an empty string for an untitled avatar, which
        # must not become a blank first row on the name tag.
        from vibestorm.viewer3d.scene import avatar_display_name

        self.assertEqual(
            avatar_display_name({"FirstName": "A", "LastName": "B", "Title": ""}), "A B"
        )

    def test_missing_or_unusable_input_reports_none(self) -> None:
        from vibestorm.viewer3d.scene import avatar_display_name

        self.assertIsNone(avatar_display_name(None))
        self.assertIsNone(avatar_display_name({}))
        self.assertIsNone(avatar_display_name({"Title": "Builder"}))
        self.assertIsNone(avatar_display_name("FirstName A"))


if __name__ == "__main__":
    unittest.main()
