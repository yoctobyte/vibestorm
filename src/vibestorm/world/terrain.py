"""Terrain heightmap decoder for SL/OpenSim ``LayerData`` packets.

The wire payload is a bit-packed stream of 16x16 m elevation patches,
each compressed with a quantised 2D DCT. This module owns the decode
pipeline:

- :class:`BitPack` reads the OpenMetaverse bitstream shape.
- :func:`decode_layer_blob` walks the stream patch-by-patch, yielding
  one :class:`PatchHeader` per patch alongside its 256 raw quantised
  coefficients (in the original zigzag/copy-matrix order).

Wire format reference: libomv ``OpenMetaverse.TerrainCompressor``.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

# libomv terrain layer-type bytes (also exported from udp.messages, but
# decoders here may be invoked directly from logging/replay paths so we
# keep a local copy to avoid a hard dep on the wire layer).
LAYER_TYPE_LAND: int = 0x4C
LAYER_TYPE_LAND_EXTENDED: int = 0x4D
LAYER_TYPE_WIND: int = 0x57
LAYER_TYPE_WIND_EXTENDED: int = 0x39
LAYER_TYPE_CLOUD: int = 0x37
LAYER_TYPE_CLOUD_EXTENDED: int = 0x38

# End-of-data marker. When the next 8 bits at a patch boundary == 97 decimal,
# no more patches follow. (libomv: ``END_OF_PATCHES``.)
END_OF_PATCHES: int = 97

# Patch sizes by layer-type byte. Land/Wind/Cloud use 16x16 patches in
# a 16x16 patch grid (256x256 m total). The ``*_EXTENDED`` variants
# bump to 32x32 patches in a 16x16 patch grid for variable-region sims
# — the wire still says "32 m per patch" via the GroupHeader.
DEFAULT_PATCH_SIZE: int = 16
REGION_SIZE_METERS: int = 256
PATCHES_PER_EDGE: int = 16
OO_SQRT2: float = 0.7071067811865476


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


@dataclass(slots=True, frozen=True)
class HeightPatch:
    """A decompressed 16x16 patch in row-major SL terrain sample order."""

    header: PatchHeader
    heights: tuple[float, ...]


@dataclass(slots=True, frozen=True)
class LayerDecodeStats:
    """Compact forensic summary for one decoded LayerData blob."""

    patch_count: int
    positions: tuple[tuple[int, int], ...]
    ranges: tuple[int, ...]
    dc_offsets: tuple[float, ...]
    prequants: tuple[int, ...]
    nonzero_coefficients: int
    coefficient_abs_max: int
    height_min: float | None
    height_max: float | None
    height_mean: float | None


@dataclass(slots=True)
class RegionHeightmap:
    """Accumulated 256x256 terrain samples for one region.

    LayerData arrives patch-by-patch in arbitrary order. This object
    keeps the latest decoded land samples in a full-region row-major
    array so renderers can rebuild a terrain mesh when ``revision``
    changes.
    """

    width: int = REGION_SIZE_METERS
    height: int = REGION_SIZE_METERS
    samples: list[float] = field(
        default_factory=lambda: [0.0] * (REGION_SIZE_METERS * REGION_SIZE_METERS)
    )
    patch_keys: set[tuple[int, int]] = field(default_factory=set)
    revision: int = 0
    latest_layer_stats: LayerDecodeStats | None = None

    def apply_patch(self, patch: HeightPatch) -> None:
        patch_size = int(math.sqrt(len(patch.heights)))
        if patch_size * patch_size != len(patch.heights):
            raise TerrainDecodeError("height patch is not square")
        x0 = patch.header.patch_x * patch_size
        y0 = patch.header.patch_y * patch_size
        if x0 + patch_size > self.width or y0 + patch_size > self.height:
            raise TerrainDecodeError(
                f"patch ({patch.header.patch_x}, {patch.header.patch_y}) "
                f"size {patch_size} exceeds {self.width}x{self.height} heightmap"
            )
        for row in range(patch_size):
            dst = (y0 + row) * self.width + x0
            src = row * patch_size
            self.samples[dst:dst + patch_size] = patch.heights[src:src + patch_size]
        self.patch_keys.add((patch.header.patch_x, patch.header.patch_y))
        self.revision += 1

    @property
    def patch_count(self) -> int:
        return len(self.patch_keys)

    @property
    def sample_min(self) -> float | None:
        return min(self.samples) if self.samples else None

    @property
    def sample_max(self) -> float | None:
        return max(self.samples) if self.samples else None

    @property
    def sample_mean(self) -> float | None:
        return (sum(self.samples) / float(len(self.samples))) if self.samples else None

    @property
    def first_patch_keys(self) -> tuple[tuple[int, int], ...]:
        return tuple(sorted(self.patch_keys)[:8])

    def apply_layer_blob(self, data: bytes) -> tuple[GroupHeader, list[HeightPatch]]:
        group, patches = decode_layer_blob(data)
        if group.layer_type not in (LAYER_TYPE_LAND, LAYER_TYPE_LAND_EXTENDED):
            return group, []
        height_patches = [decompress_patch(patch, group) for patch in patches]
        self.latest_layer_stats = layer_decode_stats(patches, height_patches)
        for patch in height_patches:
            self.apply_patch(patch)
        return group, height_patches


def layer_decode_stats(
    decoded_patches: list[DecodedPatch] | tuple[DecodedPatch, ...],
    height_patches: list[HeightPatch] | tuple[HeightPatch, ...],
) -> LayerDecodeStats:
    coefficients = [
        coefficient
        for patch in decoded_patches
        for coefficient in patch.coefficients
    ]
    heights = [height for patch in height_patches for height in patch.heights]
    return LayerDecodeStats(
        patch_count=len(decoded_patches),
        positions=tuple(
            (patch.header.patch_x, patch.header.patch_y)
            for patch in decoded_patches[:8]
        ),
        ranges=tuple(patch.header.range for patch in decoded_patches[:8]),
        dc_offsets=tuple(round(patch.header.dc_offset, 3) for patch in decoded_patches[:8]),
        prequants=tuple(patch.header.prequant for patch in decoded_patches[:8]),
        nonzero_coefficients=sum(1 for coefficient in coefficients if coefficient != 0),
        coefficient_abs_max=max((abs(coefficient) for coefficient in coefficients), default=0),
        height_min=min(heights) if heights else None,
        height_max=max(heights) if heights else None,
        height_mean=(sum(heights) / float(len(heights))) if heights else None,
    )


def synthetic_heightmap(
    *,
    width: int = REGION_SIZE_METERS,
    height: int = REGION_SIZE_METERS,
    base_height: float = 18.0,
    amplitude: float = 18.0,
) -> RegionHeightmap:
    """Build a deterministic debug heightmap independent of LayerData.

    The shape combines a central hill, a valley, and gentle sine ripples
    so camera/rendering mistakes are visually obvious.
    """
    samples: list[float] = []
    cx = (width - 1) * 0.5
    cy = (height - 1) * 0.5
    for y in range(height):
        dy = (float(y) - cy) / max(1.0, cy)
        for x in range(width):
            dx = (float(x) - cx) / max(1.0, cx)
            r2 = dx * dx + dy * dy
            hill = math.exp(-r2 * 5.0)
            valley = math.exp(-((dx + 0.45) ** 2 + (dy - 0.35) ** 2) * 18.0)
            ripple = math.sin(float(x) * 0.14) * math.cos(float(y) * 0.11)
            samples.append(
                base_height + amplitude * hill - amplitude * 0.45 * valley + ripple * 1.5
            )

    heightmap = RegionHeightmap(width=width, height=height, samples=samples, revision=1)
    patch_size = DEFAULT_PATCH_SIZE
    for py in range(max(1, height // patch_size)):
        for px in range(max(1, width // patch_size)):
            heightmap.patch_keys.add((px, py))
    return heightmap


# ---- bit reader ------------------------------------------------------------


class BitPack:
    """libomv-compatible bitstream reader.

    OpenMetaverse treats the integer as little-endian bytes; each complete
    byte is serialized MSB-first, and a final partial byte serializes only its
    low significant bits MSB-first. For example, ``PackBits(264, 16)`` yields
    ``08 01`` on the wire, while ``PackBits(0x6, 3)`` yields leading bits
    ``110``.
    """

    __slots__ = ("_data", "_byte_pos", "_bit_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._byte_pos = 0
        # _bit_pos counts from MSB: 0 means "next read takes bit 7".
        self._bit_pos = 0

    def unpack_bits(self, count: int) -> int:
        """Read ``count`` bits as an unsigned int."""
        if count <= 0:
            return 0
        if count > 32:
            raise ValueError(f"unpack_bits supports up to 32 bits, got {count}")
        bit_count = count
        bits = [self._unpack_one_bit() for _ in range(bit_count)]
        value = 0
        cursor = 0
        shift = 0
        while cursor < bit_count:
            take = min(8, bit_count - cursor)
            byte_value = 0
            for bit in bits[cursor:cursor + take]:
                byte_value = (byte_value << 1) | bit
            value |= byte_value << shift
            shift += 8
            cursor += take
        return value

    def _unpack_one_bit(self) -> int:
        if self._byte_pos >= len(self._data):
            raise TerrainDecodeError("BitPack ran past end of bitstream")
        byte = self._data[self._byte_pos]
        bit = (byte >> (7 - self._bit_pos)) & 1
        self._bit_pos += 1
        if self._bit_pos == 8:
            self._bit_pos = 0
            self._byte_pos += 1
        return bit

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

    Symmetric with :class:`BitPack`: values are split into little-endian bytes,
    each byte's significant bits are emitted MSB-first, and output bytes are
    filled MSB-first. Used by tests to build deterministic decode fixtures
    without depending on a captured wire packet, but kept in the production
    module so any future encoder can share the implementation.
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
        bits: list[int] = []
        remaining = count
        shift = 0
        while remaining > 0:
            take = min(8, remaining)
            byte_value = (value >> shift) & ((1 << take) - 1)
            for bit_index in range(take - 1, -1, -1):
                bits.append((byte_value >> bit_index) & 1)
            remaining -= take
            shift += 8
        for bit in bits:
            self._pack_one_bit(bit)

    def _pack_one_bit(self, bit: int) -> None:
        self._cur_byte |= (bit & 1) << (7 - self._bit_pos)
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
        if bp.unpack_bits(1) == 0:
            # ZERO_CODE: one zero coefficient.
            out.append(0)
            j += 1
        else:
            if bp.unpack_bits(1) == 0:
                # ZERO_EOB: the rest of the block is zero.
                out.extend([0] * (block_size - j))
                break
            # POSITIVE_VALUE / NEGATIVE_VALUE: sign bit + magnitude.
            sign = bp.unpack_bits(1)
            magnitude = bp.unpack_bits(word_bits)
            out.append(-magnitude if sign else magnitude)
            j += 1
    if len(out) != block_size:
        raise TerrainDecodeError(
            f"decoded {len(out)} coefficients, expected {block_size}"
        )
    return tuple(out)


def _build_dequantize_table16() -> tuple[float, ...]:
    return tuple(1.0 + 2.0 * float(i + j) for j in range(16) for i in range(16))


def _build_copy_matrix16() -> tuple[int, ...]:
    copy = [0] * (16 * 16)
    diag = False
    right = True
    i = 0
    j = 0
    count = 0
    while i < 16 and j < 16:
        copy[j * 16 + i] = count
        count += 1
        if not diag:
            if right:
                if i < 15:
                    i += 1
                else:
                    j += 1
                right = False
                diag = True
            else:
                if j < 15:
                    j += 1
                else:
                    i += 1
                right = True
                diag = True
        elif right:
            i += 1
            j -= 1
            if i == 15 or j == 0:
                diag = False
        else:
            i -= 1
            j += 1
            if j == 15 or i == 0:
                diag = False
    return tuple(copy)


def _setup_cosines16() -> tuple[float, ...]:
    hposz = math.pi * 0.5 / 16.0
    return tuple(
        math.cos((2.0 * float(n) + 1.0) * float(u) * hposz)
        for u in range(16)
        for n in range(16)
    )


DEQUANTIZE_TABLE16: tuple[float, ...] = _build_dequantize_table16()
COPY_MATRIX16: tuple[int, ...] = _build_copy_matrix16()
COSINE_TABLE16: tuple[float, ...] = _setup_cosines16()


def idct_patch16(coefficients: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    """Run libomv's 16x16 inverse DCT over dequantized coefficients."""
    if len(coefficients) != 16 * 16:
        raise TerrainDecodeError("IDCTPatch16 requires exactly 256 coefficients")

    temp = [0.0] * (16 * 16)
    out = [0.0] * (16 * 16)

    for column in range(16):
        for n in range(16):
            total = OO_SQRT2 * coefficients[column]
            for u in range(1, 16):
                total += coefficients[u * 16 + column] * COSINE_TABLE16[u * 16 + n]
            temp[16 * n + column] = total

    for line in range(16):
        line_size = line * 16
        for n in range(16):
            total = OO_SQRT2 * temp[line_size]
            for u in range(1, 16):
                total += temp[line_size + u] * COSINE_TABLE16[u * 16 + n]
            out[line_size + n] = total * (2.0 / 16.0)

    return tuple(out)


def decompress_patch(patch: DecodedPatch, group: GroupHeader) -> HeightPatch:
    """Dequantize + IDCT one decoded patch into elevation samples."""
    if group.patch_size != 16:
        raise TerrainDecodeError(
            f"only 16x16 terrain patches are supported, got {group.patch_size}"
        )

    block = [
        float(patch.coefficients[COPY_MATRIX16[n]]) * DEQUANTIZE_TABLE16[n]
        for n in range(16 * 16)
    ]
    idct = idct_patch16(block)

    prequant = patch.header.prequant
    mult = (1.0 / float(1 << prequant)) * float(patch.header.range)
    addval = mult * float(1 << (prequant - 1)) + patch.header.dc_offset
    heights = tuple(value * mult + addval for value in idct)
    return HeightPatch(header=patch.header, heights=heights)


def decode_height_patches(data: bytes) -> tuple[GroupHeader, list[HeightPatch]]:
    """Decode a LayerData payload all the way to terrain heights."""
    group, patches = decode_layer_blob(data)
    return group, [decompress_patch(patch, group) for patch in patches]


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
            if bp.unpack_bits(1) == 0:
                j += 1
            elif bp.unpack_bits(1) == 0:
                break
            else:
                bp.unpack_bits(1 + word_bits)
                j += 1
        yield header


__all__ = [
    "BitPack",
    "BitPackWriter",
    "COPY_MATRIX16",
    "COSINE_TABLE16",
    "DEFAULT_PATCH_SIZE",
    "DEQUANTIZE_TABLE16",
    "DecodedPatch",
    "END_OF_PATCHES",
    "GroupHeader",
    "HeightPatch",
    "LayerDecodeStats",
    "LAYER_TYPE_CLOUD",
    "LAYER_TYPE_CLOUD_EXTENDED",
    "LAYER_TYPE_LAND",
    "LAYER_TYPE_LAND_EXTENDED",
    "LAYER_TYPE_WIND",
    "LAYER_TYPE_WIND_EXTENDED",
    "PatchHeader",
    "RegionHeightmap",
    "TerrainDecodeError",
    "decode_height_patches",
    "decode_layer_blob",
    "decompress_patch",
    "idct_patch16",
    "iter_patch_headers",
    "layer_decode_stats",
    "synthetic_heightmap",
]
