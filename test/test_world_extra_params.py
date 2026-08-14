"""Tests for prim ExtraParams block decoding.

Expected values are derived from OpenSim's PrimitiveBaseShape read/write pair,
not from this module's own arithmetic — several fields use non-obvious
quantisation (flexi softness split across two bytes' high bits, light intensity
carried in the colour's alpha channel).
"""

import struct
import unittest
from dataclasses import dataclass
from uuid import UUID

from vibestorm.world.extra_params import (
    EXTRA_PARAM_FLEXIBLE,
    EXTRA_PARAM_LIGHT,
    EXTRA_PARAM_MESH_FLAGS,
    EXTRA_PARAM_PROJECTION,
    EXTRA_PARAM_REFLECTION_PROBE,
    EXTRA_PARAM_RENDER_MATERIALS,
    EXTRA_PARAM_SCULPT,
    decode_extra_params,
    decode_flexible_params,
    decode_light_params,
    decode_mesh_flags_params,
    decode_projection_params,
    decode_reflection_probe_params,
    decode_render_materials_params,
)


@dataclass(frozen=True)
class _Entry:
    """Stands in for ExtraParamEntry / ObjectExtraParamsEntry."""

    param_type: int
    param_in_use: bool
    param_data: bytes


def _flexi_bytes(
    *,
    softness: int,
    tension: float,
    drag: float,
    gravity: float,
    wind: float,
    force: tuple[float, float, float],
) -> bytes:
    # Mirrors PrimitiveBaseShape's writer: softness bit 1 rides the top bit of
    # byte 0, softness bit 0 the top bit of byte 1.
    byte0 = ((softness & 2) << 6) | (int(tension * 10.01) & 0x7F)
    byte1 = ((softness & 1) << 7) | (int(drag * 10.01) & 0x7F)
    byte2 = int((gravity + 10.0) * 10.01)
    byte3 = int(wind * 10.01)
    return bytes([byte0, byte1, byte2, byte3]) + struct.pack("<fff", *force)


class FlexibleParamsTests(unittest.TestCase):
    def test_decodes_round_trip_values(self) -> None:
        data = _flexi_bytes(
            softness=3,
            tension=1.5,
            drag=0.7,
            gravity=-3.0,
            wind=2.1,
            force=(0.25, -0.5, 1.0),
        )

        params = decode_flexible_params(data)

        self.assertIsNotNone(params)
        self.assertEqual(params.softness, 3)
        self.assertAlmostEqual(params.tension, 1.5, places=5)
        self.assertAlmostEqual(params.drag, 0.7, places=5)
        self.assertAlmostEqual(params.gravity, -3.0, places=5)
        self.assertAlmostEqual(params.wind, 2.1, places=5)
        self.assertEqual(params.force, (0.25, -0.5, 1.0))

    def test_softness_bits_come_from_two_different_bytes(self) -> None:
        # softness=1 sets only byte 1's top bit; softness=2 only byte 0's.
        one = decode_flexible_params(
            _flexi_bytes(
                softness=1, tension=0.0, drag=0.0, gravity=0.0, wind=0.0,
                force=(0.0, 0.0, 0.0),
            )
        )
        two = decode_flexible_params(
            _flexi_bytes(
                softness=2, tension=0.0, drag=0.0, gravity=0.0, wind=0.0,
                force=(0.0, 0.0, 0.0),
            )
        )

        self.assertEqual(one.softness, 1)
        self.assertEqual(two.softness, 2)

    def test_softness_bits_do_not_leak_into_tension_or_drag(self) -> None:
        # The top bit is softness; the low 7 bits are the value. A high
        # tension must survive softness being set.
        params = decode_flexible_params(
            _flexi_bytes(
                softness=3, tension=12.0, drag=12.0, gravity=0.0, wind=0.0,
                force=(0.0, 0.0, 0.0),
            )
        )

        self.assertEqual(params.softness, 3)
        self.assertAlmostEqual(params.tension, 12.0, places=5)
        self.assertAlmostEqual(params.drag, 12.0, places=5)

    def test_short_block_returns_none(self) -> None:
        self.assertIsNone(decode_flexible_params(b"\x00" * 15))


class LightParamsTests(unittest.TestCase):
    def test_intensity_comes_from_the_alpha_channel(self) -> None:
        data = bytes([255, 128, 0, 64]) + struct.pack("<fff", 10.0, 0.5, 0.75)

        params = decode_light_params(data)

        self.assertIsNotNone(params)
        self.assertAlmostEqual(params.color[0], 1.0, places=3)
        self.assertAlmostEqual(params.color[1], 128 / 255.0, places=5)
        self.assertAlmostEqual(params.color[2], 0.0, places=5)
        # Alpha is intensity, not opacity.
        self.assertAlmostEqual(params.intensity, 64 / 255.0, places=5)
        self.assertAlmostEqual(params.radius, 10.0, places=5)
        self.assertAlmostEqual(params.cutoff, 0.5, places=5)
        self.assertAlmostEqual(params.falloff, 0.75, places=5)

    def test_short_block_returns_none(self) -> None:
        self.assertIsNone(decode_light_params(b"\x00" * 15))


class ProjectionParamsTests(unittest.TestCase):
    def test_decodes_texture_and_floats(self) -> None:
        texture_id = UUID("12345678-1234-5678-1234-567812345678")
        data = texture_id.bytes + struct.pack("<fff", 1.2, 3.4, 0.6)

        params = decode_projection_params(data)

        self.assertIsNotNone(params)
        self.assertEqual(params.texture_id, texture_id)
        self.assertAlmostEqual(params.field_of_view, 1.2, places=5)
        self.assertAlmostEqual(params.focus, 3.4, places=5)
        self.assertAlmostEqual(params.ambiance, 0.6, places=5)

    def test_short_block_returns_none(self) -> None:
        self.assertIsNone(decode_projection_params(b"\x00" * 27))


class ReflectionProbeParamsTests(unittest.TestCase):
    def test_decodes_and_clamps(self) -> None:
        data = struct.pack("<ff", 5.0, 99999.0) + bytes([3])

        params = decode_reflection_probe_params(data)

        self.assertIsNotNone(params)
        self.assertEqual(params.ambiance, 1.0)          # clamped to 0..1
        self.assertEqual(params.clip_distance, 1024.0)  # clamped to 0..1024
        self.assertEqual(params.flags, 3)

    def test_short_block_returns_none(self) -> None:
        self.assertIsNone(decode_reflection_probe_params(b"\x00" * 8))


class RenderMaterialsParamsTests(unittest.TestCase):
    def _block(self, entries: list[tuple[int, UUID]]) -> bytes:
        data = bytes([len(entries)])
        for face_index, material_id in entries:
            data += bytes([face_index]) + material_id.bytes
        return data

    def test_decodes_per_face_material_list(self) -> None:
        first = UUID(int=1)
        second = UUID(int=2)

        params = decode_render_materials_params(self._block([(0, first), (3, second)]))

        self.assertIsNotNone(params)
        self.assertEqual(len(params.entries), 2)
        self.assertEqual(params.entries[0].face_index, 0)
        self.assertEqual(params.entries[0].material_id, first)
        self.assertEqual(params.material_for_face(3), second)
        self.assertIsNone(params.material_for_face(5))

    def test_declared_count_longer_than_data_rejects_whole_list(self) -> None:
        # A half-applied material set would be worse than none, so OpenSim
        # rejects rather than reading a partial list. Mirror that.
        data = self._block([(0, UUID(int=1)), (1, UUID(int=2))])
        truncated = bytes([3]) + data[1:]  # claims 3 entries, carries 2

        self.assertIsNone(decode_render_materials_params(truncated))

    def test_block_of_one_entry_or_shorter_is_rejected(self) -> None:
        # OpenSim guards on size > 17; one entry is 18 bytes with its count.
        self.assertIsNone(decode_render_materials_params(b"\x00" * 17))
        self.assertIsNotNone(
            decode_render_materials_params(self._block([(0, UUID(int=1))]))
        )


class MeshFlagsParamsTests(unittest.TestCase):
    def test_decodes_u32_little_endian(self) -> None:
        self.assertEqual(decode_mesh_flags_params(struct.pack("<I", 0x0A0B0C0D)), 0x0A0B0C0D)

    def test_explicit_zero_is_not_confused_with_absent(self) -> None:
        # 0 is a real value; only a short block is "absent".
        self.assertEqual(decode_mesh_flags_params(struct.pack("<I", 0)), 0)
        self.assertIsNone(decode_mesh_flags_params(b"\x00" * 3))


class DecodeExtraParamsTests(unittest.TestCase):
    def _light_entry(self, *, in_use: bool = True) -> _Entry:
        return _Entry(
            param_type=EXTRA_PARAM_LIGHT,
            param_in_use=in_use,
            param_data=bytes([255, 255, 255, 255]) + struct.pack("<fff", 5.0, 0.0, 1.0),
        )

    def test_decodes_multiple_blocks(self) -> None:
        entries = [
            self._light_entry(),
            _Entry(
                param_type=EXTRA_PARAM_FLEXIBLE,
                param_in_use=True,
                param_data=_flexi_bytes(
                    softness=2, tension=1.0, drag=0.0, gravity=0.0, wind=0.0,
                    force=(0.0, 0.0, 0.0),
                ),
            ),
        ]

        decoded = decode_extra_params(entries)

        self.assertIsNotNone(decoded.light)
        self.assertIsNotNone(decoded.flexible)
        self.assertEqual(decoded.flexible.softness, 2)
        self.assertIsNone(decoded.projection)

    def test_not_in_use_blocks_are_skipped(self) -> None:
        # in_use=False is how a sim clears a feature, so it must not decode.
        decoded = decode_extra_params([self._light_entry(in_use=False)])

        self.assertIsNone(decoded.light)

    def test_sculpt_block_is_left_to_the_scene_decoder(self) -> None:
        entries = [
            _Entry(
                param_type=EXTRA_PARAM_SCULPT,
                param_in_use=True,
                param_data=UUID(int=1).bytes + bytes([1]),
            )
        ]

        decoded = decode_extra_params(entries)

        self.assertEqual(decoded, decode_extra_params([]))

    def test_unknown_and_truncated_blocks_are_ignored(self) -> None:
        entries = [
            _Entry(param_type=0xAB, param_in_use=True, param_data=b"\xff" * 32),
            _Entry(
                param_type=EXTRA_PARAM_REFLECTION_PROBE,
                param_in_use=True,
                param_data=b"\x00" * 4,  # too short
            ),
            self._light_entry(),
        ]

        decoded = decode_extra_params(entries)

        # A bad block costs its own feature, not the whole tail.
        self.assertIsNone(decoded.reflection_probe)
        self.assertIsNotNone(decoded.light)

    def test_zero_mesh_flags_survives_the_none_filter(self) -> None:
        # mesh_flags 0 is falsy but valid; a truthiness check would drop it.
        decoded = decode_extra_params(
            [
                _Entry(
                    param_type=EXTRA_PARAM_MESH_FLAGS,
                    param_in_use=True,
                    param_data=struct.pack("<I", 0),
                )
            ]
        )

        self.assertEqual(decoded.mesh_flags, 0)

    def test_render_materials_reach_the_aggregate(self) -> None:
        decoded = decode_extra_params(
            [
                _Entry(
                    param_type=EXTRA_PARAM_RENDER_MATERIALS,
                    param_in_use=True,
                    param_data=bytes([1, 2]) + UUID(int=9).bytes,
                )
            ]
        )

        self.assertIsNotNone(decoded.render_materials)
        self.assertEqual(decoded.render_materials.material_for_face(2), UUID(int=9))

    def test_empty_input_is_all_none(self) -> None:
        decoded = decode_extra_params(None)

        self.assertIsNone(decoded.flexible)
        self.assertIsNone(decoded.light)
        self.assertIsNone(decoded.projection)
        self.assertIsNone(decoded.reflection_probe)
        self.assertIsNone(decoded.render_materials)
        self.assertIsNone(decoded.mesh_flags)


class InspectorRenderingTests(unittest.TestCase):
    """The decoded blocks must reach a human — otherwise this is dead code."""

    def test_only_present_blocks_produce_rows(self) -> None:
        from vibestorm.viewer3d.hud import _extra_param_lines

        decoded = decode_extra_params(
            [
                _Entry(
                    param_type=EXTRA_PARAM_LIGHT,
                    param_in_use=True,
                    param_data=bytes([255, 0, 0, 128])
                    + struct.pack("<fff", 8.0, 0.0, 0.5),
                )
            ]
        )

        lines = _extra_param_lines(decoded)

        self.assertEqual(len(lines), 1)
        self.assertIn("Light:", lines[0])
        self.assertIn("rgb(255, 0, 0)", lines[0])

    def test_render_materials_and_mesh_flags_render(self) -> None:
        from vibestorm.viewer3d.hud import _extra_param_lines

        decoded = decode_extra_params(
            [
                _Entry(
                    param_type=EXTRA_PARAM_RENDER_MATERIALS,
                    param_in_use=True,
                    param_data=bytes([1, 4]) + UUID(int=7).bytes,
                ),
                _Entry(
                    param_type=EXTRA_PARAM_MESH_FLAGS,
                    param_in_use=True,
                    param_data=struct.pack("<I", 0),
                ),
            ]
        )

        lines = _extra_param_lines(decoded)

        self.assertEqual(len(lines), 2)
        self.assertIn("Render Materials:", lines[0])
        self.assertIn("4:", lines[0])
        # Zero flags still render — the block was present.
        self.assertIn("Mesh Flags: 0x00000000", lines[1])

    def test_ordinary_prim_adds_no_rows(self) -> None:
        from vibestorm.viewer3d.hud import _extra_param_lines

        self.assertEqual(_extra_param_lines(decode_extra_params([])), [])
        self.assertEqual(_extra_param_lines(None), [])

    def test_scene_entity_carries_decoded_extra_params(self) -> None:
        # The inspector reads SceneEntity.extra_params, so the field has to be
        # populated where entities are built.
        from vibestorm.viewer3d.scene import SceneEntity

        decoded = decode_extra_params(
            [
                _Entry(
                    param_type=EXTRA_PARAM_PROJECTION,
                    param_in_use=True,
                    param_data=UUID(int=5).bytes + struct.pack("<fff", 1.0, 2.0, 3.0),
                )
            ]
        )
        entity = SceneEntity(
            local_id=1,
            pcode=9,
            kind="prim",
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=None,
            rotation_z_radians=0.0,
            extra_params=decoded,
        )

        from vibestorm.viewer3d.hud import _extra_param_lines

        lines = _extra_param_lines(entity.extra_params)
        self.assertEqual(len(lines), 1)
        self.assertIn("Projector:", lines[0])


if __name__ == "__main__":
    unittest.main()
