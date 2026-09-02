"""Round-trip tests for the ``ExtraParams`` encoders.

Every block is checked by decoding what was encoded with the decoder that was
written first and independently, against OpenSim's ``Read*Data``. The two
directions were sourced separately -- the decoders from the ``Read*`` methods,
the encoders from ``ExtraParamsToBytes`` -- so agreement between them is
evidence rather than a tautology.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import encode_object_extra_params, parse_object_extra_params
from vibestorm.world.extra_params import (
    EXTRA_PARAM_LIGHT,
    EXTRA_PARAM_PROJECTION,
    EXTRA_PARAM_REFLECTION_PROBE,
    FlexibleParams,
    LightParams,
    ProjectionParams,
    ReflectionProbeParams,
    RenderMaterialEntry,
    RenderMaterialsParams,
    decode_extra_params,
    decode_flexible_params,
    decode_light_params,
    decode_mesh_flags_params,
    decode_projection_params,
    decode_reflection_probe_params,
    decode_render_materials_params,
    encode_flexible_params,
    encode_light_params,
    encode_mesh_flags_params,
    encode_projection_params,
    encode_reflection_probe_params,
    encode_render_materials_params,
)


class BlockRoundTripTests(unittest.TestCase):
    def test_light_round_trip(self) -> None:
        params = LightParams(
            color=(1.0, 0.0, 0.4980392156862745),
            intensity=0.8,
            radius=10.0,
            cutoff=0.0,
            falloff=0.75,
        )
        encoded = encode_light_params(params)
        self.assertEqual(len(encoded), 16, "ReadLightData requires 16 bytes")
        decoded = decode_light_params(encoded)
        assert decoded is not None
        # Colour and intensity are byte-quantised, so compare within one step.
        for got, want in zip(decoded.color, params.color, strict=True):
            self.assertAlmostEqual(got, want, delta=1 / 255)
        self.assertAlmostEqual(decoded.intensity, params.intensity, delta=1 / 255)
        # The three floats are not quantised at all.
        self.assertEqual(decoded.radius, params.radius)
        self.assertEqual(decoded.cutoff, params.cutoff)
        self.assertEqual(decoded.falloff, params.falloff)

    def test_light_intensity_rides_in_the_alpha_byte(self) -> None:
        """Intensity is the colour's fourth byte, not an opacity."""
        encoded = encode_light_params(
            LightParams(color=(0.0, 0.0, 0.0), intensity=1.0, radius=1.0, cutoff=0.0, falloff=0.0)
        )
        self.assertEqual(encoded[:4], bytes([0, 0, 0, 255]))

    def test_projection_round_trip(self) -> None:
        params = ProjectionParams(
            texture_id=UUID("89556747-24cb-43ed-920b-47caed15465f"),
            field_of_view=1.5,
            focus=0.25,
            ambiance=0.5,
        )
        encoded = encode_projection_params(params)
        self.assertEqual(len(encoded), 28, "ReadProjectionData requires 28 bytes")
        self.assertEqual(decode_projection_params(encoded), params)

    def test_reflection_probe_round_trip(self) -> None:
        params = ReflectionProbeParams(ambiance=0.5, clip_distance=64.0, flags=3)
        encoded = encode_reflection_probe_params(params)
        self.assertEqual(len(encoded), 9, "ReadReflectionProbe requires 9 bytes")
        self.assertEqual(decode_reflection_probe_params(encoded), params)

    def test_reflection_probe_clamps_the_way_the_sim_does(self) -> None:
        """Out-of-range values are clamped here rather than changing meaning at the sim."""
        encoded = encode_reflection_probe_params(
            ReflectionProbeParams(ambiance=5.0, clip_distance=99999.0, flags=0)
        )
        decoded = decode_reflection_probe_params(encoded)
        assert decoded is not None
        self.assertEqual(decoded.ambiance, 1.0)
        self.assertEqual(decoded.clip_distance, 1024.0)

    def test_mesh_flags_round_trip(self) -> None:
        encoded = encode_mesh_flags_params(0xDEADBEEF)
        self.assertEqual(len(encoded), 4)
        self.assertEqual(decode_mesh_flags_params(encoded), 0xDEADBEEF)

    def test_render_materials_round_trip(self) -> None:
        params = RenderMaterialsParams(
            entries=(
                RenderMaterialEntry(
                    face_index=0, material_id=UUID("11111111-2222-3333-4444-555555555555")
                ),
                RenderMaterialEntry(
                    face_index=3, material_id=UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
                ),
            )
        )
        encoded = encode_render_materials_params(params)
        self.assertEqual(len(encoded), 1 + 17 * 2)
        self.assertEqual(decode_render_materials_params(encoded), params)

    def test_single_render_material_entry_is_long_enough_to_be_read(self) -> None:
        """``ReadRenderMaterials`` requires ``size > 17``, so one entry is the minimum."""
        encoded = encode_render_materials_params(
            RenderMaterialsParams(
                entries=(
                    RenderMaterialEntry(
                        face_index=1, material_id=UUID("11111111-2222-3333-4444-555555555555")
                    ),
                )
            )
        )
        self.assertGreater(len(encoded), 17)
        self.assertIsNotNone(decode_render_materials_params(encoded))

    def test_empty_render_materials_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_render_materials_params(RenderMaterialsParams(entries=()))

    def test_flexible_round_trip_reassembles_split_softness(self) -> None:
        """Softness survives being split across the top bit of two shared bytes."""
        for softness in range(4):
            params = FlexibleParams(
                softness=softness,
                tension=1.0,
                drag=2.0,
                gravity=-3.0,
                wind=0.5,
                force=(0.0, 0.0, -1.0),
            )
            encoded = encode_flexible_params(params)
            self.assertEqual(len(encoded), 16, "ReadFlexiData requires 16 bytes")
            decoded = decode_flexible_params(encoded)
            assert decoded is not None
            self.assertEqual(decoded.softness, softness)
            self.assertAlmostEqual(decoded.tension, 1.0, places=5)
            self.assertAlmostEqual(decoded.drag, 2.0, places=5)
            self.assertAlmostEqual(decoded.gravity, -3.0, places=5)
            self.assertAlmostEqual(decoded.wind, 0.5, places=5)
            self.assertEqual(decoded.force, (0.0, 0.0, -1.0))


class ObjectExtraParamsMessageTests(unittest.TestCase):
    agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    session_id = UUID("11111111-2222-3333-4444-555555555555")

    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def test_encode_parse_round_trip(self) -> None:
        light = encode_light_params(
            LightParams(color=(1.0, 1.0, 1.0), intensity=1.0, radius=10.0, cutoff=0.0, falloff=0.5)
        )
        probe = encode_reflection_probe_params(
            ReflectionProbeParams(ambiance=0.25, clip_distance=32.0, flags=1)
        )
        packet = encode_object_extra_params(
            self.agent_id,
            self.session_id,
            [
                (176203860, EXTRA_PARAM_LIGHT, True, light),
                (176203860, EXTRA_PARAM_REFLECTION_PROBE, True, probe),
            ],
        )
        self.assertTrue(packet.startswith(b"\xFF\xFF\x00\x63"), "ObjectExtraParams is Low/99")

        parsed = parse_object_extra_params(self.dispatcher.dispatch(packet))
        self.assertEqual(parsed.agent_id, self.agent_id)
        self.assertEqual(parsed.session_id, self.session_id)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].param_type, EXTRA_PARAM_LIGHT)
        self.assertEqual(parsed.objects[0].param_data, light)
        self.assertEqual(parsed.objects[0].param_size, len(light))
        self.assertTrue(parsed.objects[0].param_in_use)
        self.assertEqual(parsed.objects[1].param_type, EXTRA_PARAM_REFLECTION_PROBE)
        self.assertEqual(parsed.objects[1].param_data, probe)

    def test_parsed_entries_feed_the_shared_block_decoder(self) -> None:
        """The entry shape is what ``decode_extra_params`` consumes, so the loop closes."""
        light = LightParams(
            color=(1.0, 0.0, 0.0), intensity=1.0, radius=8.0, cutoff=0.0, falloff=0.5
        )
        projection = ProjectionParams(
            texture_id=UUID("89556747-24cb-43ed-920b-47caed15465f"),
            field_of_view=1.0,
            focus=0.0,
            ambiance=0.0,
        )
        packet = encode_object_extra_params(
            self.agent_id,
            self.session_id,
            [
                (1, EXTRA_PARAM_LIGHT, True, encode_light_params(light)),
                (1, EXTRA_PARAM_PROJECTION, True, encode_projection_params(projection)),
            ],
        )
        parsed = parse_object_extra_params(self.dispatcher.dispatch(packet))

        decoded = decode_extra_params(parsed.objects)
        assert decoded.light is not None
        self.assertAlmostEqual(decoded.light.radius, 8.0)
        self.assertEqual(decoded.projection, projection)

    def test_not_in_use_entry_is_how_a_feature_is_cleared(self) -> None:
        packet = encode_object_extra_params(
            self.agent_id, self.session_id, [(1, EXTRA_PARAM_LIGHT, False, b"")]
        )
        parsed = parse_object_extra_params(self.dispatcher.dispatch(packet))
        self.assertFalse(parsed.objects[0].param_in_use)
        # A cleared block must not surface as a decoded feature.
        self.assertIsNone(decode_extra_params(parsed.objects).light)

    def test_empty_entries_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_object_extra_params(self.agent_id, self.session_id, [])


if __name__ == "__main__":
    unittest.main()
