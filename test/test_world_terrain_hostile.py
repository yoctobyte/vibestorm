"""Terrain that decodes perfectly and means something impossible.

The patch header carries a 32-bit **IEEE float** straight off the wire as the
patch's DC offset, and every height in that patch is `coefficient * mult +
dc_offset`. A NaN or an infinity is one bit pattern away at all times, and it
does not stop at the decoder: `RegionHeightmap.apply_patch` writes into the
heightmap the session keeps, so one packet poisons a 16x16 metre square of
the region's ground **for the rest of the session**.

Not a crash, which is worth being exact about. `height_at` answers NaN over
that square, the terrain mesh takes NaN vertices and draws nothing there, and
`eye_clear_of_the_ground` quietly stops working -- every comparison against a
NaN is false, so the camera is never "blocked" by that hill and is left
standing inside it. One packet, and a piece of the ground is gone and the
camera walks through it, for as long as the session lasts.

Found by fuzzing `decode_height_patches` with random payloads: of twenty
thousand, ninety-one decoded and 1,280 of their heights came back non-finite,
every one of them from a header whose DC offset was NaN. Nothing on the local
test sim sends one and Linden Lab's simulators will not either -- this is the
same class as the fourteen crashing messages in the receive loop, which the
same treatment found and which were equally unreachable from the one sim this
client talks to. The owner's first priority begins "without crashes".

The refusal is on the *header* rather than on the heights, and that is a
claim this file has to earn: the arithmetic between them cannot reach a
non-finite value from a finite offset, so the header is the only way in.
`EveryDecodedHeightIsFiniteTests` is where that is checked rather than
asserted.
"""

from __future__ import annotations

import math
import random
import struct
import unittest

from vibestorm.viewer3d.camera import eye_clear_of_the_ground
from vibestorm.world.terrain import (
    END_OF_PATCHES,
    BitPackWriter,
    RegionHeightmap,
    TerrainDecodeError,
    decode_height_patches,
)

NAN = float("nan")
INF = float("inf")


def layer_blob(
    dc_offset: float,
    *,
    quant_wbits: int = 0x60,
    range_: int = 1024,
    patch_ids: int = 0,
    patch_size: int = 16,
) -> bytes:
    """One flat patch, with the header fields the caller wants to choose.

    Built with the project's own `BitPackWriter`, which the round-trip tests
    in `test_world_terrain.py` already treat as the source of truth for this
    format -- so what is fed in here is a payload the reader accepts, not a
    guess at one. `ZERO_EOB` makes every coefficient zero, which leaves the
    DC offset as the only thing deciding the heights.
    """
    writer = BitPackWriter()
    writer.pack_bits(patch_size * patch_size, 16)  # stride
    writer.pack_bits(patch_size, 8)
    writer.pack_bits(0x4C, 8)  # layer type: land
    writer.pack_bits(quant_wbits, 8)
    writer.pack_float(dc_offset)
    writer.pack_bits(range_, 16)
    writer.pack_bits(patch_ids, 10)
    writer.pack_bits(0b10, 2)  # ZERO_EOB: the rest of the block is zero
    writer.pack_bits(END_OF_PATCHES, 8)
    return writer.to_bytes()


class NonFiniteDcOffsetTests(unittest.TestCase):
    """The bit patterns that used to get through."""

    def test_a_nan_offset_is_refused(self) -> None:
        with self.assertRaises(TerrainDecodeError):
            decode_height_patches(layer_blob(NAN))

    def test_an_infinite_offset_is_refused(self) -> None:
        for value in (INF, -INF):
            with self.subTest(value):
                with self.assertRaises(TerrainDecodeError):
                    decode_height_patches(layer_blob(value))

    def test_the_message_says_which_field(self) -> None:
        """A terrain payload carries up to 256 patches. "malformed" would send
        the reader through all of them."""
        with self.assertRaisesRegex(TerrainDecodeError, "DC offset"):
            decode_height_patches(layer_blob(NAN))

    def test_an_ordinary_offset_still_decodes(self) -> None:
        """The control. A guard that refused everything would pass every test
        above and leave the client unable to draw ground at all."""
        _group, patches = decode_height_patches(layer_blob(21.5))
        self.assertEqual(len(patches), 1)
        self.assertTrue(all(math.isfinite(h) for h in patches[0].heights))

    def test_offsets_at_the_edge_of_the_float_still_decode(self) -> None:
        """Refused for being non-finite, not for being large. A region really
        can be a long way down -- and a client that rejected a legal height
        would draw a hole where the ground is."""
        for value in (-1e30, -10000.0, 0.0, 4096.0, 1e30):
            with self.subTest(value):
                _group, patches = decode_height_patches(layer_blob(value))
                self.assertTrue(all(math.isfinite(h) for h in patches[0].heights))

    def test_the_heightmap_is_never_reached(self) -> None:
        """What the guard is protecting. `apply_patch` writes into the map the
        session keeps, so a patch that got this far would still be poisoning
        the ground an hour later."""
        heightmap = RegionHeightmap()
        _group, good = decode_height_patches(layer_blob(21.5))
        heightmap.apply_patch(good[0])
        before = list(heightmap.samples)
        with self.assertRaises(TerrainDecodeError):
            decode_height_patches(layer_blob(NAN))
        self.assertEqual(heightmap.samples, before)
        self.assertTrue(math.isfinite(heightmap.height_at(8.0, 8.0)))


class WhatAPoisonedPatchWouldDoTests(unittest.TestCase):
    """The consequence, pinned separately from the guard.

    Written against the camera helper rather than against the terrain,
    because this is a fact about NaN and comparison rather than about
    terrain: it is the reason the guard is worth having, and it would go on
    being true if the guard were removed.
    """

    INSIDE_A_HILL = (10.0, 10.0, 5.0)
    TARGET = (10.0, 10.0, 6.0)

    def test_a_real_hill_lifts_the_camera_out_of_it(self) -> None:
        eye = eye_clear_of_the_ground(self.INSIDE_A_HILL, self.TARGET, lambda _x, _y: 20.0)
        self.assertGreater(eye[2], 20.0)

    def test_a_nan_hill_leaves_the_camera_inside_it(self) -> None:
        """Silently: nothing raises, nothing is logged, and the check that
        exists to stop this is still being called on every frame."""
        eye = eye_clear_of_the_ground(self.INSIDE_A_HILL, self.TARGET, lambda _x, _y: NAN)
        self.assertEqual(eye, self.INSIDE_A_HILL)


class EveryDecodedHeightIsFiniteTests(unittest.TestCase):
    """The property the renderer depends on, and the reason one guard is enough.

    The claim being earned: a finite DC offset cannot produce a non-finite
    height. The coefficients are integers of bounded width, the dequantisation
    table is finite, and the multiplier is a 16-bit range over a power of two
    -- so the header is the only way a NaN or an infinity gets in. Rather than
    assert that, this sweeps the header fields that feed the arithmetic.
    """

    def test_no_combination_of_header_fields_produces_a_non_finite_height(self) -> None:
        for quant_wbits in (0x00, 0x0F, 0x60, 0x6F, 0xF0, 0xFF):
            for range_ in (0, 1, 1024, 0xFFFF):
                for dc_offset in (-1e38, -1.0, 0.0, 1.0, 1e38):
                    with self.subTest(quant_wbits=quant_wbits, range=range_, dc=dc_offset):
                        blob = layer_blob(dc_offset, quant_wbits=quant_wbits, range_=range_)
                        try:
                            _group, patches = decode_height_patches(blob)
                        except TerrainDecodeError:
                            continue
                        for patch in patches:
                            self.assertTrue(all(math.isfinite(h) for h in patch.heights))

    def test_a_random_bit_pattern_in_the_offset_is_refused_or_finite(self) -> None:
        """Every value the four bytes can hold, sampled. There is no third
        outcome: a decode error the session records, or a usable patch."""
        rng = random.Random(20260908)
        refused = 0
        for _ in range(400):
            raw = rng.getrandbits(32)
            (dc_offset,) = struct.unpack("<f", struct.pack("<I", raw))
            try:
                _group, patches = decode_height_patches(layer_blob(dc_offset))
            except TerrainDecodeError:
                refused += 1
                continue
            for patch in patches:
                self.assertTrue(
                    all(math.isfinite(h) for h in patch.heights),
                    f"non-finite height from dc_offset {dc_offset!r}",
                )
        # Anti-vacuity in both directions: a guard that refused everything, or
        # one that refused nothing, would pass the loop above in silence.
        self.assertGreater(refused, 0)
        self.assertLess(refused, 400)


if __name__ == "__main__":
    unittest.main()
