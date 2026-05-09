"""Tests for the terrain bitstream reader + patch header decoder (6d-2).

The wire format is non-trivial — we use the symmetric BitPackWriter
in the same module to build deterministic test fixtures rather than
hand-pack bytes. This proves the writer/reader pair are inverses,
which is what 6d-3's IDCT decoder needs in order to test its own
golden output later.
"""

import math
import unittest


class BitPackTests(unittest.TestCase):
    def test_unpack_bits_reads_msb_first_within_byte_chunks(self) -> None:
        from vibestorm.world.terrain import BitPack

        # 0b10110100 0b00001111 = 0xB4 0x0F.
        bp = BitPack(bytes([0xB4, 0x0F]))

        self.assertEqual(bp.unpack_bits(4), 0b1011)
        self.assertEqual(bp.unpack_bits(4), 0b0100)
        self.assertEqual(bp.unpack_bits(8), 0x0F)

    def test_unpack_bits_across_byte_boundary_uses_openmetaverse_chunk_order(self) -> None:
        from vibestorm.world.terrain import BitPack

        # 0xAA 0x55 = 10101010 01010101.
        bp = BitPack(bytes([0xAA, 0x55]))
        # OpenMetaverse appends the second byte chunk above the first chunk.
        self.assertEqual(bp.unpack_bits(12), 0x5AA)
        self.assertEqual(bp.unpack_bits(4), 0b0101)

    def test_unpack_bits_matches_openmetaverse_terrain_header_prefix(self) -> None:
        from vibestorm.world.terrain import BitPack

        # OpenSim/OpenMetaverse PackBits(264, 16), PackBits(16, 8),
        # PackBits(0x4c, 8). This is the prefix observed in live LayerData.
        bp = BitPack(bytes.fromhex("0801104c"))

        self.assertEqual(bp.unpack_bits(16), 264)
        self.assertEqual(bp.unpack_bits(8), 16)
        self.assertEqual(bp.unpack_bits(8), 0x4C)

    def test_unpack_bits_matches_openmetaverse_non_aligned_multibyte_value(self) -> None:
        from vibestorm.world.terrain import BitPack

        # OpenMetaverse writes PackBits(2, 2); PackBits(0x123, 10) as 88 d0.
        # This catches coefficient magnitudes that start mid-byte.
        bp = BitPack(bytes.fromhex("88d0"))

        self.assertEqual(bp.unpack_bits(2), 2)
        self.assertEqual(bp.unpack_bits(10), 0x123)

    def test_unpack_bits_running_off_end_raises(self) -> None:
        from vibestorm.world.terrain import BitPack, TerrainDecodeError

        bp = BitPack(bytes([0xFF]))
        bp.unpack_bits(8)
        with self.assertRaises(TerrainDecodeError):
            bp.unpack_bits(1)

    def test_unpack_float_round_trip_with_writer(self) -> None:
        # Float endianness is the trickiest libomv parity bit; the writer
        # is the source of truth, the reader has to match it.
        from vibestorm.world.terrain import BitPack, BitPackWriter

        for value in (0.0, 1.0, -1.0, 22.5, math.pi, -1e6):
            w = BitPackWriter()
            w.pack_float(value)
            bp = BitPack(w.to_bytes())
            self.assertAlmostEqual(bp.unpack_float(), value, places=5)

    def test_unpack_bits_oversize_count_raises(self) -> None:
        from vibestorm.world.terrain import BitPack

        bp = BitPack(b"\x00" * 8)
        with self.assertRaises(ValueError):
            bp.unpack_bits(33)


class BitPackWriterRoundTripTests(unittest.TestCase):
    def test_writer_matches_openmetaverse_terrain_header_prefix(self) -> None:
        from vibestorm.world.terrain import BitPackWriter

        w = BitPackWriter()
        w.pack_bits(264, 16)
        w.pack_bits(16, 8)
        w.pack_bits(0x4C, 8)

        self.assertEqual(w.to_bytes(), bytes.fromhex("0801104c"))

    def test_writer_matches_openmetaverse_prefix_codes(self) -> None:
        from vibestorm.world.terrain import BitPackWriter

        eob = BitPackWriter()
        eob.pack_bits(0b10, 2)
        positive = BitPackWriter()
        positive.pack_bits(0b110, 3)
        negative = BitPackWriter()
        negative.pack_bits(0b111, 3)

        self.assertEqual(eob.to_bytes(), bytes([0x80]))
        self.assertEqual(positive.to_bytes(), bytes([0xC0]))
        self.assertEqual(negative.to_bytes(), bytes([0xE0]))

    def test_writer_matches_openmetaverse_end_of_patches_marker(self) -> None:
        from vibestorm.world.terrain import END_OF_PATCHES, BitPackWriter

        w = BitPackWriter()
        w.pack_bits(END_OF_PATCHES, 8)

        self.assertEqual(END_OF_PATCHES, 97)
        self.assertEqual(w.to_bytes(), bytes.fromhex("61"))

    def test_writer_matches_openmetaverse_non_aligned_multibyte_value(self) -> None:
        from vibestorm.world.terrain import BitPackWriter

        w = BitPackWriter()
        w.pack_bits(2, 2)
        w.pack_bits(0x123, 10)

        self.assertEqual(w.to_bytes(), bytes.fromhex("88d0"))

    def test_writer_reader_round_trip_arbitrary_widths(self) -> None:
        from vibestorm.world.terrain import BitPack, BitPackWriter

        spec = [(0b1, 1), (0b1011, 4), (0xABCD, 16), (0x12345678, 32), (0, 3)]
        w = BitPackWriter()
        for value, width in spec:
            w.pack_bits(value, width)
        bp = BitPack(w.to_bytes())
        for value, width in spec:
            self.assertEqual(bp.unpack_bits(width), value, f"failed at width={width}")


# ---------------------------------------------------------------------------
# Bitstream-level fixture helpers.
#
# These aren't 1:1 with the production decoder — they exist so tests can
# mint a synthetic LayerData payload of known shape without depending on
# a captured wire packet. Production encoding lives in libomv-side
# tooling that we don't host.


def _encode_group_header(stride: int, patch_size: int, layer_type: int):
    from vibestorm.world.terrain import BitPackWriter

    w = BitPackWriter()
    w.pack_bits(stride, 16)
    w.pack_bits(patch_size, 8)
    w.pack_bits(layer_type, 8)
    return w


def _encode_patch_header_into(
    w,
    quant_wbits: int,
    dc_offset: float,
    range_: int,
    patch_x: int,
    patch_y: int,
) -> None:
    w.pack_bits(quant_wbits, 8)
    w.pack_float(dc_offset)
    w.pack_bits(range_, 16)
    patch_ids = ((patch_x & 0x1F) << 5) | (patch_y & 0x1F)
    w.pack_bits(patch_ids, 10)


def _encode_zero_block(w, block_size: int) -> None:
    """Encode an all-zero coefficient block via the EOB shortcut.

    libomv's ZERO_EOB is the two-bit code ``10``.
    """
    del block_size
    w.pack_bits(0b10, 2)


def _encode_eod(w) -> None:
    from vibestorm.world.terrain import END_OF_PATCHES

    w.pack_bits(END_OF_PATCHES, 8)


# ---------------------------------------------------------------------------


class GroupHeaderDecodeTests(unittest.TestCase):
    def test_decode_layer_blob_yields_group_header(self) -> None:
        from vibestorm.world.terrain import (
            LAYER_TYPE_LAND,
            decode_layer_blob,
        )

        w = _encode_group_header(stride=264, patch_size=16, layer_type=LAYER_TYPE_LAND)
        _encode_eod(w)

        group, patches = decode_layer_blob(w.to_bytes())
        self.assertEqual(group.stride, 264)
        self.assertEqual(group.patch_size, 16)
        self.assertEqual(group.layer_type, LAYER_TYPE_LAND)
        self.assertEqual(patches, [])


class PatchHeaderDecodeTests(unittest.TestCase):
    def test_single_patch_header_roundtrips(self) -> None:
        from vibestorm.world.terrain import (
            LAYER_TYPE_LAND,
            decode_layer_blob,
        )

        w = _encode_group_header(stride=264, patch_size=16, layer_type=LAYER_TYPE_LAND)
        _encode_patch_header_into(
            w,
            quant_wbits=0x36,  # prequant=5 (hi nibble 3), word_bits=8 (low 6 + 2)
            dc_offset=22.5,
            range_=10,
            patch_x=3,
            patch_y=7,
        )
        _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        group, patches = decode_layer_blob(w.to_bytes())

        self.assertEqual(group.layer_type, LAYER_TYPE_LAND)
        self.assertEqual(len(patches), 1)
        h = patches[0].header
        self.assertEqual(h.quant_wbits, 0x36)
        self.assertEqual(h.range, 10)
        self.assertEqual(h.patch_x, 3)
        self.assertEqual(h.patch_y, 7)
        self.assertAlmostEqual(h.dc_offset, 22.5, places=5)
        self.assertEqual(h.word_bits, 8)
        self.assertEqual(h.prequant, 5)

    def test_multiple_patches_walk_bitstream_correctly(self) -> None:
        # Three patches with all-zero coefficient blocks, in
        # non-contiguous grid positions. The decoder must walk the
        # variable-length per-patch sections and reach EOD cleanly.
        from vibestorm.world.terrain import (
            LAYER_TYPE_LAND,
            decode_layer_blob,
        )

        positions = [(0, 0), (5, 12), (15, 15)]
        w = _encode_group_header(stride=264, patch_size=16, layer_type=LAYER_TYPE_LAND)
        for px, py in positions:
            _encode_patch_header_into(
                w, quant_wbits=0x10, dc_offset=float(px + py),
                range_=1, patch_x=px, patch_y=py,
            )
            _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        _group, patches = decode_layer_blob(w.to_bytes())

        self.assertEqual(len(patches), 3)
        for got, (px, py) in zip(patches, positions):
            self.assertEqual(got.header.patch_x, px)
            self.assertEqual(got.header.patch_y, py)
            self.assertAlmostEqual(got.header.dc_offset, float(px + py), places=5)


class CoefficientDecodeTests(unittest.TestCase):
    """The coefficient walk is the load-bearing piece — any drift here
    derails 6d-3's dequant/IDCT step."""

    def test_zero_block_yields_all_zero_coefficients(self) -> None:
        from vibestorm.world.terrain import decode_layer_blob

        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        _encode_patch_header_into(w, 0x10, 0.0, 1, 0, 0)
        _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        _group, patches = decode_layer_blob(w.to_bytes())
        self.assertEqual(len(patches), 1)
        coeffs = patches[0].coefficients
        self.assertEqual(len(coeffs), 16 * 16)
        self.assertTrue(all(c == 0 for c in coeffs))

    def test_mixed_nonzero_coefficients(self) -> None:
        from vibestorm.world.terrain import decode_layer_blob

        # Word bits = (0x10 & 0x0F) + 2 = 2. Coefficients in [-3, 3].
        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        _encode_patch_header_into(w, 0x10, 0.0, 1, 1, 1)

        # Coefficient stream: +1, -2, 0, then EOB.
        # Layout: 110 mag=01 -> +1
        #         111 mag=10 -> -2
        #         0          -> single 0
        #         10         -> EOB, rest zero
        w.pack_bits(0b110, 3)
        w.pack_bits(0b01, 2)
        w.pack_bits(0b111, 3)
        w.pack_bits(0b10, 2)
        w.pack_bits(0, 1)
        w.pack_bits(0b10, 2)
        _encode_eod(w)

        _group, patches = decode_layer_blob(w.to_bytes())
        coeffs = patches[0].coefficients

        self.assertEqual(len(coeffs), 16 * 16)
        self.assertEqual(coeffs[0], 1)
        self.assertEqual(coeffs[1], -2)
        self.assertEqual(coeffs[2], 0)
        self.assertTrue(all(c == 0 for c in coeffs[3:]))

    def test_iter_patch_headers_skips_coefficients(self) -> None:
        from vibestorm.world.terrain import iter_patch_headers

        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        for px, py in ((0, 0), (1, 2), (4, 4)):
            _encode_patch_header_into(w, 0x10, float(px), 1, px, py)
            _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        headers = list(iter_patch_headers(w.to_bytes()))
        positions = [(h.patch_x, h.patch_y) for h in headers]
        self.assertEqual(positions, [(0, 0), (1, 2), (4, 4)])


class TerrainDecompressionTests(unittest.TestCase):
    def test_dequantize_table_matches_libomv_formula(self) -> None:
        from vibestorm.world.terrain import DEQUANTIZE_TABLE16

        self.assertEqual(DEQUANTIZE_TABLE16[0], 1.0)
        self.assertEqual(DEQUANTIZE_TABLE16[1], 3.0)
        self.assertEqual(DEQUANTIZE_TABLE16[16], 3.0)
        self.assertEqual(DEQUANTIZE_TABLE16[255], 61.0)

    def test_copy_matrix_uses_libomv_diagonal_serpentine(self) -> None:
        from vibestorm.world.terrain import COPY_MATRIX16

        self.assertEqual(COPY_MATRIX16[:8], (0, 1, 5, 6, 14, 15, 27, 28))
        self.assertEqual(COPY_MATRIX16[16:24], (2, 4, 7, 13, 16, 26, 29, 43))
        self.assertEqual(sorted(COPY_MATRIX16), list(range(16 * 16)))

    def test_zero_patch_decompresses_to_addval(self) -> None:
        from vibestorm.world.terrain import decode_height_patches

        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        _encode_patch_header_into(w, 0x30, 20.0, 16, 0, 0)  # prequant=5
        _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        _group, patches = decode_height_patches(w.to_bytes())

        self.assertEqual(len(patches), 1)
        heights = patches[0].heights
        self.assertEqual(len(heights), 16 * 16)
        self.assertTrue(all(abs(value - 28.0) < 1e-6 for value in heights))

    def test_dc_only_patch_is_constant_after_idct(self) -> None:
        from vibestorm.world.terrain import decode_height_patches

        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        _encode_patch_header_into(w, 0x34, 20.0, 16, 0, 0)  # word_bits=6
        w.pack_bits(0b110, 3)
        w.pack_bits(16, 6)
        w.pack_bits(0b10, 2)
        _encode_eod(w)

        _group, patches = decode_height_patches(w.to_bytes())

        self.assertEqual(len(set(round(v, 6) for v in patches[0].heights)), 1)
        self.assertAlmostEqual(patches[0].heights[0], 28.5, places=6)

    def test_region_heightmap_accumulates_patch_at_grid_position(self) -> None:
        from vibestorm.world.terrain import RegionHeightmap

        w = _encode_group_header(stride=264, patch_size=16, layer_type=0x4C)
        _encode_patch_header_into(w, 0x30, 10.0, 4, 2, 3)
        _encode_zero_block(w, 16 * 16)
        _encode_eod(w)

        heightmap = RegionHeightmap()
        _group, patches = heightmap.apply_layer_blob(w.to_bytes())

        self.assertEqual(len(patches), 1)
        self.assertEqual(heightmap.revision, 1)
        index = (3 * 16) * 256 + (2 * 16)
        self.assertAlmostEqual(heightmap.samples[index], 12.0, places=6)
        self.assertEqual(heightmap.samples[0], 0.0)
        self.assertIsNotNone(heightmap.latest_layer_stats)
        assert heightmap.latest_layer_stats is not None
        self.assertEqual(heightmap.latest_layer_stats.patch_count, 1)
        self.assertEqual(heightmap.latest_layer_stats.positions, ((2, 3),))
        self.assertEqual(heightmap.latest_layer_stats.ranges, (4,))
        self.assertEqual(heightmap.latest_layer_stats.nonzero_coefficients, 0)
        self.assertAlmostEqual(heightmap.latest_layer_stats.height_min, 12.0, places=6)

    def test_decodes_opensim_compressed_sloped_patch_fixture(self) -> None:
        from vibestorm.world.terrain import decode_height_patches

        # Generated with OpenSimTerrainCompressor.CreatePatchFromTerrainData
        # from a patch where height = 20 + x * 0.05 + y * 0.02.
        data = bytes.fromhex(
            "0801104c8a0000a041020000399ff712ff4038e81c2c01c180381000e0400001c04261"
        )

        group, patches = decode_height_patches(data)

        self.assertEqual(group.stride, 264)
        self.assertEqual(group.patch_size, 16)
        self.assertEqual(len(patches), 1)
        heights = patches[0].heights
        self.assertAlmostEqual(heights[0], 20.0, delta=0.01)
        self.assertAlmostEqual(heights[15], 20.75, delta=0.01)
        self.assertAlmostEqual(heights[15 * 16], 20.3, delta=0.01)
        self.assertAlmostEqual(heights[15 * 16 + 15], 21.05, delta=0.01)

    def test_synthetic_heightmap_has_debug_shape_and_patch_keys(self) -> None:
        from vibestorm.world.terrain import synthetic_heightmap

        heightmap = synthetic_heightmap(width=32, height=32)

        self.assertEqual(heightmap.width, 32)
        self.assertEqual(heightmap.height, 32)
        self.assertEqual(heightmap.revision, 1)
        self.assertEqual(heightmap.patch_count, 4)
        self.assertLess(heightmap.sample_min, heightmap.sample_max)
        self.assertEqual(heightmap.first_patch_keys, ((0, 0), (0, 1), (1, 0), (1, 1)))


if __name__ == "__main__":
    unittest.main()
