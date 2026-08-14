"""Regression tests for the Object Inspector detail panel.

The panel walks whatever a live prim happens to carry, so a field shape
mistake only shows up when a prim in view actually has that field set. The
per-face texture list was read as a mapping when it is a tuple of pairs — it
would have raised AttributeError the first time anyone selected a prim with a
face override, and no prim in the test region has one.
"""

import unittest
from uuid import UUID

from vibestorm.viewer3d.hud import _inspector_detail_html
from vibestorm.world.texture_entry import TextureEntry

FACE_A = UUID("11111111-1111-1111-1111-111111111111")
FACE_B = UUID("22222222-2222-2222-2222-222222222222")
DEFAULT_TEX = UUID("33333333-3333-3333-3333-333333333333")


class _Entity:
    def __init__(self, **kwargs):
        self.local_id = 1
        self.pcode = 9
        self.kind = "prim"
        self.position = (1.0, 2.0, 3.0)
        self.scale = (1.0, 1.0, 1.0)
        self.rotation = (0.0, 0.0, 0.0, 1.0)
        self.rotation_z_radians = 0.0
        self.name = "test prim"
        self.shape = "cube"
        self.default_texture_id = DEFAULT_TEX
        self.texture_entry = None
        self.extra_params = None
        self.hover_text = None
        self.hover_text_color = None
        for key, value in kwargs.items():
            setattr(self, key, value)


class InspectorFaceTextureTests(unittest.TestCase):
    def test_per_face_overrides_render_without_raising(self) -> None:
        entity = _Entity(
            texture_entry=TextureEntry(
                default_texture_id=DEFAULT_TEX,
                face_texture_ids=((0, FACE_A), (4, FACE_B)),
            )
        )

        html = _inspector_detail_html(entity, None)

        self.assertIn("Face Textures", html)
        self.assertIn(f"0: {FACE_A}", html)
        self.assertIn(f"4: {FACE_B}", html)

    def test_prim_without_overrides_omits_the_row(self) -> None:
        entity = _Entity(texture_entry=TextureEntry(default_texture_id=DEFAULT_TEX))

        self.assertNotIn("Face Textures", _inspector_detail_html(entity, None))

    def test_prim_with_no_texture_entry_at_all_renders(self) -> None:
        self.assertIn("Identity", _inspector_detail_html(_Entity(), None))


class InspectorHoverTextTests(unittest.TestCase):
    def test_hover_text_appears_in_the_identity_block(self) -> None:
        entity = _Entity(hover_text="For sale", hover_text_color=(255, 0, 255, 255))

        html = _inspector_detail_html(entity, None)

        self.assertIn("Hover Text: For sale", html)
        self.assertIn("rgba(255, 0, 255, 255)", html)


class InspectorPrimAttributeTests(unittest.TestCase):
    """Material and click action were shown as bare integers."""

    class _World:
        def __init__(self, material=3, click_action=0):
            self.material = material
            self.click_action = click_action
            self.full_id = UUID(int=7)
            self.local_id = 1
            self.properties_family = None

    def test_material_and_click_action_are_named(self) -> None:
        html = _inspector_detail_html(_Entity(), self._World(material=1, click_action=1))

        self.assertIn("Material: metal (1)", html)
        self.assertIn("Click Action: sit (1)", html)

    def test_raw_value_is_kept_alongside_the_name(self) -> None:
        # The number stays visible: this is a protocol client, and a reader
        # comparing against a packet dump needs the byte, not only the label.
        html = _inspector_detail_html(_Entity(), self._World(material=7, click_action=6))

        self.assertIn("light (7)", html)
        self.assertIn("open media (6)", html)

    def test_no_world_object_still_renders(self) -> None:
        html = _inspector_detail_html(_Entity(), None)

        self.assertIn("Material: unknown", html)
        self.assertIn("Click Action: unknown", html)


if __name__ == "__main__":
    unittest.main()
