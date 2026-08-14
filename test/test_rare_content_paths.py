"""Smoke tests for code paths the live test region cannot reach.

Everything here is gated on world content the sim does not contain: a prim
with per-face texture overrides, a light, a projector, a reflection probe,
GLTF render materials, floating text, a media URL. Those branches only run
when such a prim is in view, so a field-shape mistake in one sits undetected
until someone rezzes the right object — which is exactly how the Object
Inspector's ``.items()`` call on a tuple survived.

The point is coverage of the *combination*: one synthetic prim carrying every
optional block at once, walked end to end from the compressed wire blob
through WorldView, Scene, the inspector, and the GL renderer.
"""

import struct
import unittest
from uuid import UUID

from vibestorm.udp.messages import decode_compressed_object_data
from vibestorm.world.extra_params import (
    EXTRA_PARAM_FLEXIBLE,
    EXTRA_PARAM_LIGHT,
    EXTRA_PARAM_MESH_FLAGS,
    EXTRA_PARAM_PROJECTION,
    EXTRA_PARAM_REFLECTION_PROBE,
    EXTRA_PARAM_RENDER_MATERIALS,
)

FULL_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
OWNER_ID = UUID("aaaaaaaa-0000-0000-0000-000000000002")
PROJECTOR_TEX = UUID("aaaaaaaa-0000-0000-0000-000000000003")
MATERIAL_A = UUID("aaaaaaaa-0000-0000-0000-000000000004")
DEFAULT_TEX = UUID("bbbbbbbb-0000-0000-0000-000000000001")
FACE_TEX = UUID("bbbbbbbb-0000-0000-0000-000000000002")

COMPRESSED_HAS_TEXT = 0x0004
COMPRESSED_MEDIA_URL = 0x0200


def _extra_params_blob() -> bytes:
    """Every ExtraParams block the decoders understand, in one payload."""
    blocks = [
        (EXTRA_PARAM_FLEXIBLE,
         bytes([0x82, 0x05, 120, 30]) + struct.pack("<fff", 0.1, 0.2, -0.3)),
        (EXTRA_PARAM_LIGHT, bytes([255, 128, 64, 200]) + struct.pack("<fff", 8.0, 0.5, 0.75)),
        (EXTRA_PARAM_PROJECTION, PROJECTOR_TEX.bytes + struct.pack("<fff", 1.2, 3.0, 0.4)),
        (EXTRA_PARAM_REFLECTION_PROBE, struct.pack("<ff", 0.5, 32.0) + bytes([3])),
        (EXTRA_PARAM_RENDER_MATERIALS, bytes([1]) + bytes([2]) + MATERIAL_A.bytes),
        (EXTRA_PARAM_MESH_FLAGS, struct.pack("<I", 0x00000005)),
    ]
    payload = bytes([len(blocks)])
    for param_type, data in blocks:
        # The compressed block's ExtraParams header is 6 bytes -- type u16 then
        # size u32, with no in-use byte. That extra byte only appears in the
        # ObjectUpdate/ObjectExtraParams form (PrimitiveBaseShape.ExtraParamsToBytes).
        payload += struct.pack("<H", param_type) + struct.pack("<I", len(data)) + data
    return payload


def _texture_entry_blob() -> bytes:
    """A TextureEntry with one per-face override in the texture section.

    Face masks are MSB-first 7-bit groups; 0x11 selects faces 0 and 4. Every
    later section is default-only, terminated by a zero face mask.
    """
    blob = DEFAULT_TEX.bytes + bytes([0x11]) + FACE_TEX.bytes + bytes([0x00])
    blob += bytes([255, 255, 255, 255]) + bytes([0x00])          # colour
    blob += struct.pack("<f", 1.0) + bytes([0x00])               # repeat u
    blob += struct.pack("<f", 1.0) + bytes([0x00])               # repeat v
    blob += struct.pack("<h", 0) + bytes([0x00])                 # offset u
    blob += struct.pack("<h", 0) + bytes([0x00])                 # offset v
    blob += struct.pack("<h", 0) + bytes([0x00])                 # rotation
    blob += bytes([0]) + bytes([0x00])                           # material flags
    blob += bytes([0]) + bytes([0x00])                           # media flags
    blob += bytes([0]) + bytes([0x00])                           # glow
    return blob


def _shape_block() -> bytes:
    return (
        bytes([0x10])                       # PathCurve: line
        + struct.pack("<HH", 0, 0x3FFF)     # PathBegin/End
        + bytes([100, 100, 0, 0])
        + struct.pack("<bbbbb", 0, 0, 0, 0, 0)
        + bytes([1])
        + struct.pack("<b", 0)
        + bytes([1])                        # ProfileCurve: square
        + struct.pack("<HHH", 0, 0x3FFF, 0)
    )


def _rich_prim_blob() -> bytes:
    texture_entry = _texture_entry_blob()
    return (
        FULL_ID.bytes
        + struct.pack("<I", 9001)
        + bytes([9, 0])
        + struct.pack("<I", 1)
        + bytes([3, 0])
        + struct.pack("<fff", 2.0, 2.0, 2.0)
        + struct.pack("<fff", 128.0, 128.0, 25.0)
        + struct.pack("<fff", 0.0, 0.0, 0.0)
        + struct.pack("<I", COMPRESSED_HAS_TEXT | COMPRESSED_MEDIA_URL)
        + OWNER_ID.bytes
        + b"Everything\nAt Once\x00"
        + bytes([200, 100, 50, 0x00])
        + b"http://example.invalid/media\x00"
        + _extra_params_blob()
        + _shape_block()
        + struct.pack("<HH", len(texture_entry), 0)
        + texture_entry
    )


def _decoded_entry():
    entry = decode_compressed_object_data(
        _rich_prim_blob(), region_handle=1, time_dilation=0, update_flags=0
    )
    assert entry is not None, "the rich fixture must decode at all"
    return entry


class RichPrimDecodeTests(unittest.TestCase):
    def test_every_optional_block_survives_one_blob(self) -> None:
        entry = _decoded_entry()

        self.assertEqual(entry.local_id, 9001)
        self.assertEqual(entry.hover_text, "Everything\nAt Once")
        self.assertEqual(entry.hover_text_color, (200, 100, 50, 255))
        self.assertEqual(entry.media_url, "http://example.invalid/media")
        self.assertIsNotNone(entry.shape)
        self.assertEqual(entry.shape.profile_curve, 1)
        self.assertEqual(len(entry.extra_params_entries), 6)
        self.assertEqual(entry.default_texture_id, DEFAULT_TEX)

    def test_face_override_reaches_texture_for_face(self) -> None:
        entry = _decoded_entry()

        self.assertEqual(entry.texture_entry.texture_for_face(0), FACE_TEX)
        self.assertEqual(entry.texture_entry.texture_for_face(4), FACE_TEX)
        self.assertEqual(entry.texture_entry.texture_for_face(1), DEFAULT_TEX)

    def test_all_extra_param_blocks_decode(self) -> None:
        from vibestorm.world.extra_params import decode_extra_params

        decoded = decode_extra_params(_decoded_entry().extra_params_entries)

        self.assertIsNotNone(decoded.flexible)
        self.assertIsNotNone(decoded.light)
        self.assertIsNotNone(decoded.projection)
        self.assertIsNotNone(decoded.reflection_probe)
        self.assertIsNotNone(decoded.render_materials)
        self.assertEqual(decoded.mesh_flags, 0x00000005)


class RichPrimSceneTests(unittest.TestCase):
    def _scene_entity(self):
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.models import WorldObject, WorldView

        entry = _decoded_entry()
        world_object = WorldObject(
            full_id=entry.full_id,
            local_id=entry.local_id,
            parent_id=entry.parent_id,
            pcode=entry.pcode,
            material=entry.material,
            click_action=entry.click_action,
            scale=entry.scale,
            state=entry.state,
            crc=entry.crc,
            update_flags=entry.update_flags,
            region_handle=1,
            time_dilation=0,
            object_data_size=entry.object_data_size,
            position=entry.position,
            rotation=entry.rotation,
            variant=entry.variant,
            name_values=entry.name_values,
            texture_entry_size=entry.texture_entry_size,
            texture_anim_size=entry.texture_anim_size,
            data_size=entry.data_size,
            text_size=entry.text_size,
            media_url_size=entry.media_url_size,
            ps_block_size=entry.ps_block_size,
            extra_params_size=entry.extra_params_size,
            extra_params_entries=entry.extra_params_entries,
            default_texture_id=entry.default_texture_id,
            texture_entry=entry.texture_entry,
            shape=entry.shape,
            hover_text=entry.hover_text,
            hover_text_color=entry.hover_text_color,
            media_url=entry.media_url,
        )

        view = WorldView()
        view.objects[entry.full_id] = world_object
        view.local_id_to_full_id[entry.local_id] = entry.full_id

        scene = Scene()
        scene.refresh_from_world_view(view)
        return scene, world_object

    def test_scene_carries_every_block_onto_the_entity(self) -> None:
        scene, _ = self._scene_entity()
        entity = scene.object_entities[9001]

        self.assertEqual(entity.shape, "cube")
        self.assertEqual(entity.hover_text, "Everything\nAt Once")
        self.assertEqual(entity.hover_text_color, (200, 100, 50, 255))
        self.assertIsNotNone(entity.extra_params)
        self.assertIsNotNone(entity.extra_params.light)

    def test_inspector_renders_the_whole_prim(self) -> None:
        # The path that already broke once. Every optional row at once.
        from vibestorm.viewer3d.hud import _inspector_detail_html

        scene, world_object = self._scene_entity()
        html = _inspector_detail_html(scene.object_entities[9001], world_object)

        for expected in (
            "Hover Text: Everything",
            "Hover Text: At Once",
            "Media URL",
            "Face Textures",
            "Flexible:",
            "Light:",
            "Projector:",
            "Reflection Probe:",
            "Render Materials:",
            "Mesh Flags:",
        ):
            self.assertIn(expected, html, f"inspector dropped {expected!r}")


class RichPrimRenderTests(unittest.TestCase):
    """The renderer must survive the same prim the inspector chokes on.

    Skips cleanly without GL; on this machine a standalone context works, so
    it runs.
    """

    def setUp(self) -> None:
        try:
            import moderngl
            import pygame  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"GL stack unavailable: {exc}")
        try:
            self.ctx = moderngl.create_standalone_context()
        except Exception as exc:  # noqa: BLE001 - any GL init failure is a skip
            self.skipTest(f"no GL context: {exc}")
        self.size = (128, 128)
        self._tex = self.ctx.texture(self.size, components=4)
        self._rb = self.ctx.depth_renderbuffer(self.size)
        self.fbo = self.ctx.framebuffer(
            color_attachments=[self._tex], depth_attachment=self._rb
        )
        self.fbo.use()
        self.ctx.viewport = (0, 0, *self.size)

    def tearDown(self) -> None:
        self.fbo.release()
        self._tex.release()
        self._rb.release()
        self.ctx.release()

    def test_rich_prim_renders_without_raising(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        scene, _ = RichPrimSceneTests()._scene_entity()
        scene.render_terrain = False
        scene.render_water = False
        entity = scene.object_entities[9001]
        x, y, z = entity.position

        camera = Camera3D(target=(x, y, z), eye_position=(x + 6.0, y, z + 0.5))
        camera.set_mode("free")
        camera.screen_size = self.size

        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0)
            renderer.render_gl(scene, aspect=1.0)
            data = self.fbo.read(components=3)
            pixels = [tuple(data[i : i + 3]) for i in range(0, len(data), 3)]
            self.assertTrue(
                any(pixel != (0, 0, 0) for pixel in pixels),
                "the rich prim drew nothing at all",
            )
        finally:
            renderer.clear_caches()


if __name__ == "__main__":
    unittest.main()
