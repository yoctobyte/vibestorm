"""Terrain heightmap decoder for SL/OpenSim ``LayerData`` packets.

The wire payload is a bit-packed stream of 16x16 m elevation patches,
each compressed with a quantised 2D DCT. This module owns the decode
pipeline:

- :class:`BitPack` reads the MSB-first bitstream that libomv produces.
- :func:`decode_layer_blob` walks the stream patch-by-patch, yielding
  one :class:`PatchHeader` per patch alongside its 256 raw quantised
  coefficients (in the original zigzag/copy-matrix order).

Step 6d-2 stops after coefficient *extraction*: the dequantisation +
IDCT (to recover the actual elevation grid) lands in step 6d-3 along
with the region heightmap accumulator, and the renderer-side
tessellated mesh lands in 6d-4.

Wire format reference: libomv ``OpenMetaverse.TerrainCompressor``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

# libomv terrain layer-type bytes (also exported from udp.messages, but
# decoders here may be invoked directly from logging/replay paths so we
# keep a local copy to avoid a hard dep on the wire layer).
LAYER_TYPE_LAND: int = 0x4C
LAYER_TYPE_LAND_EXTENDED: int = 0x4D
LAYER_TYPE_WIND: int = 0x57
LAYER_TYPE_WIND_EXTENDED: int = 0x39
LAYER_TYPE_CLOUD: int = 0x37
LAYER_TYPE_CLOUD_EXTENDED: int = 0x38

# End-of-data marker. When the next 8 bits at a patch boundary == 0x97,
# no more patches follow. (libomv: ``END_OF_PATCHES``.)
END_OF_PATCHES: int = 0x97

# Patch sizes by layer-type byte. Land/Wind/Cloud use 16x16 patches in
# a 16x16 patch grid (256x256 m total). The ``*_EXTENDED`` variants
# bump to 32x32 patches in a 16x16 patch grid for variable-region sims
# — the wire still says "32 m per patch" via the GroupHeader.
DEFAULT_PATCH_SIZE: int = 16


class TerrainDecodeError(ValueError):
    """Raised when the bitstream is malformed or runs out of data."""


@dataclass(slots=True, frozen=True)
class GroupHeader:
    """First-bytes header that prefixes every patch group in a blob.

    ``stride`` is the row stride libomv uses for its IDCT temporary
    matrix; we don't use it directly but preserve it for parity.
    ``patch_size`` is the number of samples per patch side (16 for
    Land, 32 for LandExtended). ``layer_type`` should equal the
    ``Type`` byte from the parent ``LayerData`` packet.
    """

    stride: int
    patch_size: int
    layer_type: int


@dataclass(slots=True, frozen=True)
class PatchHeader:
    """Per-patch header decoded from the bitstream.

    - ``quant_wbits``: 8-bit field encoding both the dequantisation
      scale (high 4 bits, the *prequant* exponent) and the
      *coefficient word width* (low 4 bits + 2).
    - ``dc_offset``: 32-bit IEEE float, the patch's DC offset
      (added to every sample after IDCT).
    - ``range``: 16-bit unsigned, the patch's value range used in
      the dequantisation multiplier.
    - ``patch_x`` / ``patch_y``: 5-bit patch grid coordinates
      (0..31, packed as a single 10-bit value).
    """

    quant_wbits: int
    dc_offset: float
    range: int
    patch_x: int
    patch_y: int

    @property
    def word_bits(self) -> int:
        """Bit-width of each coefficient word (low 4 of quant_wbits + 2)."""
        return (self.quant_wbits & 0x0F) + 2

    @property
    def prequant(self) -> int:
        """Pre-quantisation scale exponent (high 4 of quant_wbits + 2)."""
        return (self.quant_wbits >> 4) + 2


@dataclass(slots=True, frozen=True)
class DecodedPatch:
    """A single decoded patch: header + raw quantised coefficients.

    ``coefficients`` is a tuple of ``patch_size**2`` signed ints in
    libomv's stored order (zigzag/copy-matrix). The caller is
    expected to dequantise and inverse-DCT them — that lives in step
    6d-3, not here, so this layer is testable without the
    floating-point tables.
    """

    header: PatchHeader
    coefficients: tuple[int, ...]


# ---- bit reader ------------------------------------------------------------


class BitPack:
    """libomv-compatible bitstream reader.

    Bits are packed MSB-first within each byte: the first bit yielded by
    :meth:`unpack_bits` is bit 7 of byte 0, then bit 6, etc. This
    matches libomv's ``BitPack.UnpackBits`` exactly.
    """

    __slots__ = ("_data", "_byte_pos", "_bit_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._byte_pos = 0
        # _bit_pos counts from MSB: 0 means "next read takes bit 7".
        self._bit_pos = 0

    def unpack_bits(self, count: int) -> int:
        """Read ``count`` bits as an unsigned int (MSB-first)."""
        if count <= 0:
            return 0
        if count > 32:
            raise ValueError(f"unpack_bits supports up to 32 bits, got {count}")
        value = 0
        for _ in range(count):
            if self._byte_pos >= len(self._data):
                raise TerrainDecodeError("BitPack ran past end of bitstream")
            byte = self._data[self._byte_pos]
            bit = (byte >> (7 - self._bit_pos)) & 1
            value = (value << 1) | bit
            self._bit_pos += 1
            if self._bit_pos == 8:
                self._bit_pos = 0
                self._byte_pos += 1
        return value

    def unpack_float(self) -> float:
        """Read 32 bits and reinterpret as an IEEE 754 little-endian float.

        libomv reads 4 bytes via UnpackBitsArray, then byte-reverses
        on little-endian host before ``BitConverter.ToSingle``. The
        net effect is: the 4 bytes coming off the bitstream are the
        IEEE float in *little-endian* byte order.
        """
        raw = bytes(self.unpack_bits(8) for _ in range(4))
        return struct.unpack("<f", raw)[0]

    def has_more(self) -> bool:
        return self._byte_pos < len(self._data)


# ---- bit writer (used by tests; symmetric with BitPack) --------------------


class BitPackWriter:
    """libomv-compatible bitstream writer.

    Symmetric with :class:`BitPack`: bits are written MSB-first within
    each output byte. Used by tests to build deterministic decode
    fixtures without depending on a captured wire packet, but kept in
    the production module so any future encoder (e.g., for testing
    against an OpenSim fork) can share the implementation.
    """

    __slots__ = ("_buf", "_cur_byte", "_bit_pos")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._cur_byte = 0
        self._bit_pos = 0  # next write goes to bit (7 - _bit_pos)

    def pack_bits(self, value: int, count: int) -> None:
        if count <= 0:
            return
        if count > 32:
            raise ValueError(f"pack_bits supports up to 32 bits, got {count}")
        for shift in range(count - 1, -1, -1):
            bit = (value >> shift) & 1
            self._cur_byte |= bit << (7 - self._bit_pos)
            self._bit_pos += 1
            if self._bit_pos == 8:
                self._buf.append(self._cur_byte)
                self._cur_byte = 0
                self._bit_pos = 0

    def pack_float(self, value: float) -> None:
        raw = struct.pack("<f", value)
        for byte in raw:
            self.pack_bits(byte, 8)

    def to_bytes(self) -> bytes:
        if self._bit_pos == 0:
            return bytes(self._buf)
        # Flush partial byte; trailing zero bits are harmless if the
        # consumer's UnpackBits doesn't read past the EOD marker.
        return bytes(self._buf) + bytes([self._cur_byte])


# ---- decoder ----------------------------------------------------------------


def _decode_group_header(bp: BitPack) -> GroupHeader:
    """Read the first GroupHeader at the start of the blob."""
    stride = bp.unpack_bits(16)
    patch_size = bp.unpack_bits(8)
    layer_type = bp.unpack_bits(8)
    return GroupHeader(stride=stride, patch_size=patch_size, layer_type=layer_type)


def _decode_patch_header(bp: BitPack) -> PatchHeader | None:
    """Read one patch header. Returns ``None`` on the END_OF_PATCHES marker."""
    quant_wbits = bp.unpack_bits(8)
    if quant_wbits == END_OF_PATCHES:
        return None
    dc_offset = bp.unpack_float()
    range_ = bp.unpack_bits(16)
    patch_ids = bp.unpack_bits(10)
    # libomv packs PatchIDs as: high 5 bits = X, low 5 bits = Y.
    patch_x = (patch_ids >> 5) & 0x1F
    patch_y = patch_ids & 0x1F
    return PatchHeader(
        quant_wbits=quant_wbits,
        dc_offset=dc_offset,
        range=range_,
        patch_x=patch_x,
        patch_y=patch_y,
    )


def _decode_patch_coefficients(
    bp: BitPack, header: PatchHeader, patch_size: int
) -> tuple[int, ...]:
    """Read the variable-bit coefficient stream for one patch."""
    block_size = patch_size * patch_size
    word_bits = header.word_bits
    out: list[int] = []
    j = 0
    while j < block_size:
        if bp.unpack_bits(1) == 1:
            # Non-zero coefficient: sign bit + ``word_bits`` magnitude.
            sign = bp.unpack_bits(1)
            magnitude = bp.unpack_bits(word_bits)
            out.append(-magnitude if sign else magnitude)
            j += 1
        else:
            # Either a single zero (next bit 0) or end-of-block (next bit 1).
            if bp.unpack_bits(1) == 1:
                out.extend([0] * (block_size - j))
                break
            out.append(0)
            j += 1
    if len(out) != block_size:
        raise TerrainDecodeError(
            f"decoded {len(out)} coefficients, expected {block_size}"
        )
    return tuple(out)


def decode_layer_blob(data: bytes) -> tuple[GroupHeader, list[DecodedPatch]]:
    """Walk a complete LayerData payload and yield headers + coefficients.

    The payload is the ``Data`` block from a parsed ``LayerDataMessage``
    — i.e., the bytes after the U16 length prefix on the wire. The
    return value is the GroupHeader followed by a list of
    :class:`DecodedPatch` records, one per patch, in the order they
    appear on the wire.
    """
    bp = BitPack(data)
    group = _decode_group_header(bp)

    patches: list[DecodedPatch] = []
    while True:
        header = _decode_patch_header(bp)
        if header is None:
            break
        coefficients = _decode_patch_coefficients(bp, header, group.patch_size)
        patches.append(DecodedPatch(header=header, coefficients=coefficients))

    return group, patches


def iter_patch_headers(data: bytes) -> Iterator[PatchHeader]:
    """Convenience: yield only the patch headers, dropping coefficients.

    Useful for log/replay paths that want to know "which 16x16 m
    patches arrived" without paying the coefficient walk cost. The
    implementation still has to walk the coefficient bits to reach
    the next patch, so the cost saving is in not allocating a
    coefficient tuple per patch — not in skipping the bit work.
    """
    bp = BitPack(data)
    group = _decode_group_header(bp)
    while True:
        header = _decode_patch_header(bp)
        if header is None:
            return
        # Walk past coefficients without keeping them.
        block_size = group.patch_size * group.patch_size
        word_bits = header.word_bits
        j = 0
        while j < block_size:
            if bp.unpack_bits(1) == 1:
                bp.unpack_bits(1 + word_bits)
                j += 1
            else:
                if bp.unpack_bits(1) == 1:
                    break
                j += 1
        yield header


__all__ = [
    "BitPack",
    "BitPackWriter",
    "DEFAULT_PATCH_SIZE",
    "DecodedPatch",
    "END_OF_PATCHES",
    "GroupHeader",
    "LAYER_TYPE_CLOUD",
    "LAYER_TYPE_CLOUD_EXTENDED",
    "LAYER_TYPE_LAND",
    "LAYER_TYPE_LAND_EXTENDED",
    "LAYER_TYPE_WIND",
    "LAYER_TYPE_WIND_EXTENDED",
    "PatchHeader",
    "TerrainDecodeError",
    "decode_layer_blob",
    "iter_patch_headers",
]
