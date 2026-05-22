"""Approximate sculpt-map geometry generation.

Sculpted prims use an image asset as a vertex-position map. This module
turns RGB pixels into a conservative unit-sized triangle mesh. It is not
yet a faithful viewer-grade sculpt tessellator, but it gives the renderer
real geometry from the sculpt texture instead of a placeholder sphere.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


class SculptDecodeError(ValueError):
    """Raised when a sculpt texture cannot be converted to geometry."""


@dataclass(slots=True, frozen=True)
class SculptMesh:
    vertices: tuple[float, ...]
    indices: tuple[int, ...]
    width: int
    height: int


SCULPT_TYPE_SPHERE = 1
SCULPT_TYPE_TORUS = 2
SCULPT_TYPE_PLANE = 3
SCULPT_TYPE_CYLINDER = 4
SCULPT_TYPE_MASK = 0x0F
SCULPT_FLAG_INVERT = 0x40
SCULPT_FLAG_MIRROR = 0x80


def sculpt_mesh_from_rgb(
    pixels: bytes,
    *,
    width: int,
    height: int,
    sculpt_type: int,
    max_samples: int = 32,
) -> SculptMesh:
    """Build a unit mesh from RGB/RGBA sculpt-map pixels.

    Each sampled pixel contributes one vertex: RGB components are mapped
    from 0..255 to -0.5..0.5. The sculpt type controls seam wrapping:
    sphere/cylinder wrap horizontally, torus wraps in both directions,
    and plane stays open.
    """
    if width < 2 or height < 2:
        raise SculptDecodeError("sculpt image needs at least 2x2 pixels")
    channels = _channels_for_pixel_bytes(pixels, width=width, height=height)
    cols = _sample_indices(width, max_samples=max_samples)
    rows = _sample_indices(height, max_samples=max_samples)
    if len(cols) < 2 or len(rows) < 2:
        raise SculptDecodeError("sculpt image sampling produced a degenerate grid")

    base_type = sculpt_type & SCULPT_TYPE_MASK
    mirror = bool(sculpt_type & SCULPT_FLAG_MIRROR)
    invert = bool(sculpt_type & SCULPT_FLAG_INVERT)
    vertices: list[float] = []
    for row in rows:
        for col in cols:
            offset = (row * width + col) * channels
            r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            x = (r / 255.0) - 0.5
            if mirror:
                x = -x
            vertices.extend((x, (g / 255.0) - 0.5, (b / 255.0) - 0.5))

    if base_type == SCULPT_TYPE_SPHERE:
        _converge_sphere_poles(vertices, width=len(cols), height=len(rows))

    wrap_u, wrap_v = _wraps_for_sculpt_type(base_type)
    indices = _grid_indices(len(cols), len(rows), wrap_u=wrap_u, wrap_v=wrap_v, invert=invert)
    if not indices:
        raise SculptDecodeError("sculpt image produced no triangles")
    return SculptMesh(
        vertices=tuple(vertices),
        indices=tuple(indices),
        width=len(cols),
        height=len(rows),
    )


def sculpt_mesh_from_rgba_words(
    words: tuple[int, ...],
    *,
    width: int,
    height: int,
    sculpt_type: int,
    max_samples: int = 32,
) -> SculptMesh:
    """Test/helper API that accepts pygame-style packed RGBA integers."""
    pixels = bytearray()
    for word in words:
        pixels.extend(struct.pack(">I", word)[0:3])
    return sculpt_mesh_from_rgb(
        bytes(pixels),
        width=width,
        height=height,
        sculpt_type=sculpt_type,
        max_samples=max_samples,
    )


def _channels_for_pixel_bytes(pixels: bytes, *, width: int, height: int) -> int:
    sample_count = width * height
    if len(pixels) == sample_count * 3:
        return 3
    if len(pixels) == sample_count * 4:
        return 4
    raise SculptDecodeError(
        f"sculpt pixel byte count {len(pixels)} does not match {width}x{height} RGB/RGBA"
    )


def _sample_indices(size: int, *, max_samples: int) -> list[int]:
    if max_samples < 2:
        raise SculptDecodeError("max_samples must be at least 2")
    if size <= max_samples:
        return list(range(size))
    return [
        round(index * (size - 1) / (max_samples - 1))
        for index in range(max_samples)
    ]


def _wraps_for_sculpt_type(sculpt_type: int) -> tuple[bool, bool]:
    if sculpt_type == SCULPT_TYPE_TORUS:
        return True, True
    if sculpt_type in (SCULPT_TYPE_SPHERE, SCULPT_TYPE_CYLINDER):
        return True, False
    return False, False


def _grid_indices(
    width: int, height: int, *, wrap_u: bool, wrap_v: bool, invert: bool = False
) -> list[int]:
    indices: list[int] = []
    col_count = width if wrap_u else width - 1
    row_count = height if wrap_v else height - 1
    for row in range(row_count):
        next_row = (row + 1) % height
        for col in range(col_count):
            next_col = (col + 1) % width
            sw = row * width + col
            se = row * width + next_col
            nw = next_row * width + col
            ne = next_row * width + next_col
            if invert:
                indices.extend((sw, ne, se, sw, nw, ne))
            else:
                indices.extend((sw, se, ne, sw, ne, nw))
    return indices


def _converge_sphere_poles(vertices: list[float], *, width: int, height: int) -> None:
    for row in (0, height - 1):
        base = row * width * 3
        x = sum(vertices[base + col * 3] for col in range(width)) / width
        y = sum(vertices[base + col * 3 + 1] for col in range(width)) / width
        z = sum(vertices[base + col * 3 + 2] for col in range(width)) / width
        for col in range(width):
            offset = base + col * 3
            vertices[offset] = x
            vertices[offset + 1] = y
            vertices[offset + 2] = z


__all__ = [
    "SCULPT_TYPE_CYLINDER",
    "SCULPT_FLAG_INVERT",
    "SCULPT_FLAG_MIRROR",
    "SCULPT_TYPE_PLANE",
    "SCULPT_TYPE_SPHERE",
    "SCULPT_TYPE_TORUS",
    "SculptDecodeError",
    "SculptMesh",
    "sculpt_mesh_from_rgb",
    "sculpt_mesh_from_rgba_words",
]
