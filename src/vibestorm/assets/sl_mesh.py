"""Minimal Second Life mesh asset decoder.

The goal here is renderable high-LOD geometry, not a complete importer.
SL mesh assets are a binary LLSD header followed by compressed LLSD
blocks. We parse the header, inflate the high_lod block, and combine
submesh Position/TriangleList arrays into one indexed mesh.
"""

from __future__ import annotations

import gzip
import math
import struct
import zlib
from dataclasses import dataclass
from uuid import UUID


class SLMeshDecodeError(ValueError):
    """Raised when an SL mesh asset cannot be decoded."""


@dataclass(slots=True, frozen=True)
class MeshMaterialGroup:
    """One submesh's slice of the combined index buffer.

    SL mesh submeshes map 1:1 to prim faces, so ``face_index`` is the slot
    that the prim's ``TextureEntry`` indexes for material/texture assignment.
    """

    face_index: int
    index_start: int
    index_count: int


@dataclass(slots=True, frozen=True)
class DecodedSLMesh:
    vertices: tuple[float, ...]
    indices: tuple[int, ...]
    submesh_count: int
    normals: tuple[float, ...] = ()
    uvs: tuple[float, ...] = ()
    material_groups: tuple[MeshMaterialGroup, ...] = ()


_BINARY_PREFIX = b"<? LLSD/Binary ?>\n"
_DEFAULT_POSITION_MIN = (-0.5, -0.5, -0.5)
_DEFAULT_POSITION_MAX = (0.5, 0.5, 0.5)
_NORMAL_MIN = (-1.0, -1.0, -1.0)
_NORMAL_MAX = (1.0, 1.0, 1.0)
_DEFAULT_TEXCOORD_MIN = (0.0, 0.0)
_DEFAULT_TEXCOORD_MAX = (1.0, 1.0)


def decode_sl_mesh_asset(data: bytes, *, lod: str = "high_lod") -> DecodedSLMesh:
    """Decode one LOD from an SL mesh asset into flat xyz vertices + indices."""
    header, header_end = parse_binary_llsd(data)
    if not isinstance(header, dict):
        raise SLMeshDecodeError("mesh header is not an LLSD map")
    lod_info = header.get(lod)
    if not isinstance(lod_info, dict):
        raise SLMeshDecodeError(f"mesh asset has no {lod} block")
    try:
        offset = int(lod_info["offset"])
        size = int(lod_info["size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SLMeshDecodeError(f"{lod} block is missing offset/size") from exc
    start = header_end + offset
    end = start + size
    if start < header_end or end > len(data) or size <= 0:
        raise SLMeshDecodeError(f"{lod} block points outside asset")

    block_bytes = _decompress_mesh_block(data[start:end])
    block, consumed = parse_binary_llsd(block_bytes)
    if consumed != len(block_bytes):
        # Some producers tolerate trailing bytes, but for render geometry
        # this usually means the inflate or LLSD parse went wrong.
        raise SLMeshDecodeError(f"{lod} block has trailing bytes")
    if not isinstance(block, list):
        raise SLMeshDecodeError(f"{lod} block is not an LLSD array")

    vertices: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    material_groups: list[MeshMaterialGroup] = []
    submesh_count = 0
    for face_index, submesh in enumerate(block):
        if not isinstance(submesh, dict):
            raise SLMeshDecodeError(f"{lod} submesh is not an LLSD map")
        if bool(submesh.get("NoGeometry")):
            continue
        sub_vertices = _decode_positions(submesh)
        vertex_count = len(sub_vertices) // 3
        sub_indices = _decode_triangle_list(submesh, vertex_count)
        base = len(vertices) // 3
        index_start = len(indices)
        vertices.extend(sub_vertices)
        normals.extend(_decode_normals(submesh, vertex_count))
        uvs.extend(_decode_texcoords(submesh, vertex_count))
        indices.extend(base + index for index in sub_indices)
        material_groups.append(
            MeshMaterialGroup(
                face_index=face_index,
                index_start=index_start,
                index_count=len(sub_indices),
            )
        )
        submesh_count += 1

    if not vertices or not indices:
        raise SLMeshDecodeError(f"{lod} block contains no renderable geometry")
    return DecodedSLMesh(
        vertices=tuple(vertices),
        indices=tuple(indices),
        submesh_count=submesh_count,
        normals=tuple(normals),
        uvs=tuple(uvs),
        material_groups=tuple(material_groups),
    )


def parse_binary_llsd(data: bytes, offset: int = 0) -> tuple[object, int]:
    """Parse one binary LLSD value and return ``(value, next_offset)``."""
    if data.startswith(_BINARY_PREFIX, offset):
        offset += len(_BINARY_PREFIX)
    value, offset = _parse_value(data, offset)
    return value, offset


def _parse_value(data: bytes, offset: int) -> tuple[object, int]:
    if offset >= len(data):
        raise SLMeshDecodeError("LLSD value is truncated")
    tag = data[offset : offset + 1]
    offset += 1
    if tag == b"!":
        return None, offset
    if tag == b"1":
        return True, offset
    if tag == b"0":
        return False, offset
    if tag == b"i":
        return _read_i32(data, offset)
    if tag == b"r":
        return _read_f64(data, offset)
    if tag == b"u":
        _need(data, offset, 16, "LLSD UUID")
        return UUID(bytes=data[offset : offset + 16]), offset + 16
    if tag == b"b":
        length, offset = _read_i32(data, offset)
        _need(data, offset, length, "LLSD binary")
        return data[offset : offset + length], offset + length
    if tag in (b"s", b"l"):
        length, offset = _read_i32(data, offset)
        _need(data, offset, length, "LLSD string")
        return data[offset : offset + length].decode("utf-8", errors="replace"), offset + length
    if tag == b"d":
        return _read_f64(data, offset)
    if tag == b"[":
        count, offset = _read_i32(data, offset)
        values: list[object] = []
        for _ in range(count):
            value, offset = _parse_value(data, offset)
            values.append(value)
        _need_tag(data, offset, b"]", "LLSD array terminator")
        return values, offset + 1
    if tag == b"{":
        count, offset = _read_i32(data, offset)
        values: dict[str, object] = {}
        for _ in range(count):
            key, offset = _parse_key(data, offset)
            value, offset = _parse_value(data, offset)
            values[key] = value
        _need_tag(data, offset, b"}", "LLSD map terminator")
        return values, offset + 1
    raise SLMeshDecodeError(f"unsupported LLSD binary tag {tag!r}")


def _parse_key(data: bytes, offset: int) -> tuple[str, int]:
    _need_tag(data, offset, b"k", "LLSD map key")
    length, offset = _read_i32(data, offset + 1)
    _need(data, offset, length, "LLSD map key")
    return data[offset : offset + length].decode("utf-8", errors="replace"), offset + length


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    _need(data, offset, 4, "LLSD integer")
    return struct.unpack_from(">i", data, offset)[0], offset + 4


def _read_f64(data: bytes, offset: int) -> tuple[float, int]:
    _need(data, offset, 8, "LLSD real")
    return struct.unpack_from(">d", data, offset)[0], offset + 8


def _need(data: bytes, offset: int, size: int, what: str) -> None:
    if size < 0 or len(data) < offset + size:
        raise SLMeshDecodeError(f"{what} is truncated")


def _need_tag(data: bytes, offset: int, tag: bytes, what: str) -> None:
    _need(data, offset, 1, what)
    if data[offset : offset + 1] != tag:
        raise SLMeshDecodeError(f"missing {what}")


def _decompress_mesh_block(data: bytes) -> bytes:
    errors: list[str] = []
    for label, func in (
        ("gzip", gzip.decompress),
        ("zlib", zlib.decompress),
        ("raw-deflate-after-zlib-header", lambda blob: zlib.decompress(blob[2:], -zlib.MAX_WBITS)),
    ):
        try:
            return func(data)
        except (OSError, zlib.error) as exc:
            errors.append(f"{label}: {exc}")
    raise SLMeshDecodeError("mesh block decompression failed: " + "; ".join(errors))


def _decode_positions(submesh: dict[str, object]) -> list[float]:
    raw = submesh.get("Position")
    if not isinstance(raw, (bytes, bytearray)):
        raise SLMeshDecodeError("submesh is missing Position bytes")
    if len(raw) % 6 != 0:
        raise SLMeshDecodeError("Position byte count is not divisible by 6")
    domain_min, domain_max = _position_domain(submesh)
    vertices: list[float] = []
    for offset in range(0, len(raw), 6):
        for axis in range(3):
            q = struct.unpack_from("<H", raw, offset + axis * 2)[0]
            lo = domain_min[axis]
            hi = domain_max[axis]
            vertices.append(lo + (hi - lo) * (q / 65535.0))
    return vertices


def _decode_triangle_list(submesh: dict[str, object], vertex_count: int) -> list[int]:
    raw = submesh.get("TriangleList")
    if not isinstance(raw, (bytes, bytearray)):
        raise SLMeshDecodeError("submesh is missing TriangleList bytes")
    if len(raw) % 6 != 0:
        raise SLMeshDecodeError("TriangleList byte count is not divisible by 6")
    indices: list[int] = []
    for offset in range(0, len(raw), 2):
        index = struct.unpack_from("<H", raw, offset)[0]
        if index >= vertex_count:
            raise SLMeshDecodeError("TriangleList references a missing vertex")
        indices.append(index)
    return indices


def _decode_normals(submesh: dict[str, object], vertex_count: int) -> list[float]:
    raw = submesh.get("Normal")
    if not isinstance(raw, (bytes, bytearray)):
        return _compute_normals(submesh, vertex_count)
    if len(raw) != vertex_count * 6:
        raise SLMeshDecodeError("Normal byte count does not match vertex count")
    normals: list[float] = []
    for offset in range(0, len(raw), 6):
        vec = [0.0, 0.0, 0.0]
        for axis in range(3):
            q = struct.unpack_from("<H", raw, offset + axis * 2)[0]
            lo = _NORMAL_MIN[axis]
            hi = _NORMAL_MAX[axis]
            vec[axis] = lo + (hi - lo) * (q / 65535.0)
        normals.extend(_normalized(vec))
    return normals


def _decode_texcoords(submesh: dict[str, object], vertex_count: int) -> list[float]:
    raw = submesh.get("TexCoord0")
    if not isinstance(raw, (bytes, bytearray)):
        return [0.0] * (vertex_count * 2)
    if len(raw) != vertex_count * 4:
        raise SLMeshDecodeError("TexCoord0 byte count does not match vertex count")
    domain_min, domain_max = _texcoord_domain(submesh)
    uvs: list[float] = []
    for offset in range(0, len(raw), 4):
        for axis in range(2):
            q = struct.unpack_from("<H", raw, offset + axis * 2)[0]
            lo = domain_min[axis]
            hi = domain_max[axis]
            uvs.append(lo + (hi - lo) * (q / 65535.0))
    return uvs


def _compute_normals(submesh: dict[str, object], vertex_count: int) -> list[float]:
    """Build smooth per-vertex normals from the submesh triangles."""
    positions = _decode_positions(submesh)
    indices = _decode_triangle_list(submesh, vertex_count)
    accum = [0.0] * (vertex_count * 3)
    for tri in range(0, len(indices) - 2, 3):
        ia, ib, ic = indices[tri], indices[tri + 1], indices[tri + 2]
        a = positions[ia * 3 : ia * 3 + 3]
        b = positions[ib * 3 : ib * 3 + 3]
        c = positions[ic * 3 : ic * 3 + 3]
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        nx = u[1] * v[2] - u[2] * v[1]
        ny = u[2] * v[0] - u[0] * v[2]
        nz = u[0] * v[1] - u[1] * v[0]
        for idx in (ia, ib, ic):
            accum[idx * 3] += nx
            accum[idx * 3 + 1] += ny
            accum[idx * 3 + 2] += nz
    normals: list[float] = []
    for v_index in range(vertex_count):
        normals.extend(_normalized(accum[v_index * 3 : v_index * 3 + 3]))
    return normals


def _normalized(vec: list[float]) -> tuple[float, float, float]:
    length = math.sqrt(vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2])
    if length <= 1e-12:
        return (0.0, 0.0, 1.0)
    return (vec[0] / length, vec[1] / length, vec[2] / length)


def _texcoord_domain(submesh: dict[str, object]) -> tuple[tuple[float, float], tuple[float, float]]:
    domain = submesh.get("TexCoord0Domain")
    if not isinstance(domain, dict):
        return _DEFAULT_TEXCOORD_MIN, _DEFAULT_TEXCOORD_MAX
    min_value = _as_vec2(domain.get("Min"), fallback=_DEFAULT_TEXCOORD_MIN)
    max_value = _as_vec2(domain.get("Max"), fallback=_DEFAULT_TEXCOORD_MAX)
    return min_value, max_value


def _as_vec2(value: object, *, fallback: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return fallback
    try:
        result = (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return fallback
    if not all(math.isfinite(component) for component in result):
        return fallback
    return result


def _position_domain(submesh: dict[str, object]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    domain = submesh.get("PositionDomain")
    if not isinstance(domain, dict):
        return _DEFAULT_POSITION_MIN, _DEFAULT_POSITION_MAX
    min_value = _as_vec3(domain.get("Min"), fallback=_DEFAULT_POSITION_MIN)
    max_value = _as_vec3(domain.get("Max"), fallback=_DEFAULT_POSITION_MAX)
    return min_value, max_value


def _as_vec3(value: object, *, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return fallback
    try:
        result = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return fallback
    if not all(math.isfinite(component) for component in result):
        return fallback
    return result


__all__ = [
    "DecodedSLMesh",
    "MeshMaterialGroup",
    "SLMeshDecodeError",
    "decode_sl_mesh_asset",
    "parse_binary_llsd",
]
