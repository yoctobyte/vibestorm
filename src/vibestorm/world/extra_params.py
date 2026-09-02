"""Decoders for prim ``ExtraParams`` blocks beyond sculpt/mesh.

An ``ExtraParams`` tail carries optional per-prim feature blocks, each a
``(type, in_use, data)`` triple. ``vibestorm.viewer3d.scene`` already reads the
sculpt block (``0x30``) for mesh identity; this module decodes the rest.

Field layouts and scaling mirror OpenSim's ``PrimitiveBaseShape``
(``ReadFlexiData`` / ``ReadLightData`` / ``ReadProjectionData`` /
``ReadReflectionProbe`` / ``ReadRenderMaterials`` /
``ReadMeshFlagsData``). The quantisation is unusual in places — flexi
softness is split across the top bits of two different bytes, and light
intensity rides in the colour's alpha channel — so the reference is worth
keeping open when changing this.

Every decoder returns ``None`` rather than raising when a block is too short.
A truncated block should cost one prim feature, not the whole update.

The ``encode_*`` functions are the inverse, and exist so this client can *set*
these features on a prim it owns rather than only read them. That is the point:
five of these blocks had never seen live data, not because the sim cannot send
them but because nothing in the region had the feature turned on. A client that
can write one can observe its own echo. See ``encode_object_extra_params``.

They are sourced from ``PrimitiveBaseShape.ExtraParamsToBytes`` -- OpenSim's own
encoder -- so the pairing is checked against the tree in both directions rather
than being one reading of the decoders inverted by eye.
"""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack, unpack_from
from uuid import UUID

# ExtraParams block type codes (PrimitiveBaseShape.ReadInUpdateExtraParam).
EXTRA_PARAM_FLEXIBLE = 0x10
EXTRA_PARAM_LIGHT = 0x20
EXTRA_PARAM_SCULPT = 0x30
EXTRA_PARAM_PROJECTION = 0x40
EXTRA_PARAM_MESH = 0x60
EXTRA_PARAM_MESH_FLAGS = 0x70
EXTRA_PARAM_RENDER_MATERIALS = 0x80
EXTRA_PARAM_REFLECTION_PROBE = 0x90


@dataclass(slots=True, frozen=True)
class FlexibleParams:
    """Flexi-prim simulation settings (``0x10``, 16 bytes).

    ``softness`` is a 0-3 level assembled from the high bit of each of the
    first two bytes; the remaining 7 bits of those bytes carry tension and
    drag. ``gravity`` is biased by -10 so it can be negative.
    """

    softness: int
    tension: float
    drag: float
    gravity: float
    wind: float
    force: tuple[float, float, float]


@dataclass(slots=True, frozen=True)
class LightParams:
    """Point-light settings (``0x20``, 16 bytes).

    The RGBA colour's alpha channel is not opacity — it carries the light's
    intensity, which is why ``color`` here is RGB only.
    """

    color: tuple[float, float, float]
    intensity: float
    radius: float
    cutoff: float
    falloff: float


@dataclass(slots=True, frozen=True)
class ProjectionParams:
    """Projector settings (``0x40``, 28 bytes)."""

    texture_id: UUID
    field_of_view: float
    focus: float
    ambiance: float


@dataclass(slots=True, frozen=True)
class RenderMaterialEntry:
    """One face's material asset (GLTF material override)."""

    face_index: int
    material_id: UUID


@dataclass(slots=True, frozen=True)
class RenderMaterialsParams:
    """Per-face GLTF material assignments (``0x80``, 1 + 17*count bytes).

    A count byte followed by ``(face_index, UUID)`` pairs. Unlike the other
    blocks this one is variable length, and OpenSim rejects it outright when
    the declared count does not fit the data rather than reading a partial
    list.
    """

    entries: tuple[RenderMaterialEntry, ...]

    def material_for_face(self, face_index: int) -> UUID | None:
        for entry in self.entries:
            if entry.face_index == face_index:
                return entry.material_id
        return None


@dataclass(slots=True, frozen=True)
class ReflectionProbeParams:
    """Reflection-probe settings (``0x90``, 9 bytes)."""

    ambiance: float
    clip_distance: float
    flags: int


def decode_flexible_params(data: bytes) -> FlexibleParams | None:
    if len(data) < 16:
        return None
    # Softness is two bits stored in the top bit of bytes 0 and 1.
    softness = ((data[0] & 0x80) >> 6) | ((data[1] & 0x80) >> 7)
    force_x, force_y, force_z = unpack_from("<fff", data, 4)
    return FlexibleParams(
        softness=softness,
        tension=(data[0] & 0x7F) / 10.0,
        drag=(data[1] & 0x7F) / 10.0,
        gravity=(data[2] / 10.0) - 10.0,
        wind=data[3] / 10.0,
        force=(force_x, force_y, force_z),
    )


def decode_light_params(data: bytes) -> LightParams | None:
    if len(data) < 16:
        return None
    radius, cutoff, falloff = unpack_from("<fff", data, 4)
    return LightParams(
        color=(data[0] / 255.0, data[1] / 255.0, data[2] / 255.0),
        intensity=data[3] / 255.0,
        radius=radius,
        cutoff=cutoff,
        falloff=falloff,
    )


def decode_projection_params(data: bytes) -> ProjectionParams | None:
    if len(data) < 28:
        return None
    field_of_view, focus, ambiance = unpack_from("<fff", data, 16)
    return ProjectionParams(
        texture_id=UUID(bytes=bytes(data[:16])),
        field_of_view=field_of_view,
        focus=focus,
        ambiance=ambiance,
    )


def decode_reflection_probe_params(data: bytes) -> ReflectionProbeParams | None:
    if len(data) < 9:
        return None
    ambiance, clip_distance = unpack_from("<ff", data, 0)
    # OpenSim clamps both on read; mirror it so a malformed prim cannot push
    # absurd values into a renderer.
    return ReflectionProbeParams(
        ambiance=min(max(ambiance, 0.0), 1.0),
        clip_distance=min(max(clip_distance, 0.0), 1024.0),
        flags=data[8],
    )


def decode_mesh_flags_params(data: bytes) -> int | None:
    """Mesh render flags (``0x70``, 4 bytes, u32 little-endian).

    Returned as a raw int: the flag meanings are viewer-side render hints and
    nothing here interprets them yet. OpenSim reads a short block as 0 rather
    than rejecting it, but ``None`` is more useful to a caller that wants to
    distinguish "absent" from "explicitly zero".
    """
    if len(data) < 4:
        return None
    return int(unpack_from("<I", data, 0)[0])


def decode_render_materials_params(data: bytes) -> RenderMaterialsParams | None:
    """Per-face GLTF material list (``0x80``).

    Mirrors OpenSim's guards: the block must exceed one entry's worth of bytes
    and must be long enough for the count it declares, or the whole list is
    rejected. A truncated list is not read partially — a half-applied material
    set would be worse than none.
    """
    if len(data) <= 17:
        return None
    count = data[0]
    if len(data) < 1 + 17 * count:
        return None
    entries = []
    offset = 1
    for _ in range(count):
        face_index = data[offset]
        material_id = UUID(bytes=bytes(data[offset + 1 : offset + 17]))
        entries.append(RenderMaterialEntry(face_index=face_index, material_id=material_id))
        offset += 17
    return RenderMaterialsParams(entries=tuple(entries))


def _zero_one_to_byte(value: float) -> int:
    """Quantise a 0..1 float to one byte, the way ``FloatZeroOneToByte`` does.

    libomv ships only as a DLL in ``opensim-source/``, so the exact rounding of
    ``Utils.FloatZeroOneToByte`` cannot be read here. It does not need to be:
    the sim stores what we send as ``byte / 255`` and re-encodes it on the way
    out, and every one of the 256 byte values survives that trip unchanged for
    both rounding and truncation. So a value we send comes back byte-identical
    regardless of which one libomv uses, and only *our* quantisation is visible.
    """
    return min(max(int(round(value * 255.0)), 0), 255)


def encode_flexible_params(params: FlexibleParams) -> bytes:
    """Encode a flexi block (``0x10``, 16 bytes).

    Softness is the awkward one: its two bits go back into the top bit of each
    of the first two bytes, which the tension and drag fields share.
    """
    softness = min(max(int(params.softness), 0), 3)
    tension = min(max(int(round(params.tension * 10.0)), 0), 0x7F)
    drag = min(max(int(round(params.drag * 10.0)), 0), 0x7F)
    gravity = min(max(int(round((params.gravity + 10.0) * 10.0)), 0), 255)
    wind = min(max(int(round(params.wind * 10.0)), 0), 255)
    return (
        bytes(
            [
                ((softness << 6) & 0x80) | tension,
                ((softness << 7) & 0x80) | drag,
                gravity,
                wind,
            ]
        )
        + pack("<fff", *params.force)
    )


def encode_light_params(params: LightParams) -> bytes:
    """Encode a point-light block (``0x20``, 16 bytes).

    Intensity rides in the colour's alpha byte -- it is not opacity.
    """
    return (
        bytes(
            [
                _zero_one_to_byte(params.color[0]),
                _zero_one_to_byte(params.color[1]),
                _zero_one_to_byte(params.color[2]),
                _zero_one_to_byte(params.intensity),
            ]
        )
        + pack("<fff", params.radius, params.cutoff, params.falloff)
    )


def encode_projection_params(params: ProjectionParams) -> bytes:
    """Encode a projector block (``0x40``, 28 bytes)."""
    return params.texture_id.bytes + pack(
        "<fff", params.field_of_view, params.focus, params.ambiance
    )


def encode_reflection_probe_params(params: ReflectionProbeParams) -> bytes:
    """Encode a reflection-probe block (``0x90``, 9 bytes).

    Clamped on the way out to the same bounds ``ReadReflectionProbe`` clamps on
    the way in, so a value that would not survive the round trip is rejected
    here rather than silently changing meaning at the sim.
    """
    return pack(
        "<ffB",
        min(max(params.ambiance, 0.0), 1.0),
        min(max(params.clip_distance, 0.0), 1024.0),
        params.flags & 0xFF,
    )


def encode_mesh_flags_params(flags: int) -> bytes:
    """Encode a mesh render-flags block (``0x70``, 4 bytes)."""
    return pack("<I", int(flags) & 0xFFFFFFFF)


def encode_render_materials_params(params: RenderMaterialsParams) -> bytes:
    """Encode a per-face GLTF material list (``0x80``, 1 + 17*count bytes).

    ``ReadRenderMaterials`` requires ``size > 17``, so a single-entry list --
    exactly 18 bytes -- is the shortest one the sim will read. An empty list is
    rejected here: the way to clear materials is ``param_in_use=False``, not a
    zero-length block, which the sim would drop on the floor instead.
    """
    if not params.entries:
        raise ValueError("render materials block needs at least one entry; clear with in_use=False")
    if len(params.entries) > 255:
        raise ValueError("at most 255 render material entries")
    return bytes([len(params.entries)]) + b"".join(
        bytes([entry.face_index & 0xFF]) + entry.material_id.bytes for entry in params.entries
    )


@dataclass(slots=True, frozen=True)
class DecodedExtraParams:
    """Every recognised block from one prim's ``ExtraParams`` tail.

    Sculpt/mesh is deliberately absent: ``viewer3d.scene.decode_sculpt_mesh_hint``
    already owns that block and returns renderer-facing shape hints.
    """

    flexible: FlexibleParams | None = None
    light: LightParams | None = None
    projection: ProjectionParams | None = None
    reflection_probe: ReflectionProbeParams | None = None
    render_materials: RenderMaterialsParams | None = None
    mesh_flags: int | None = None


_DECODERS = {
    EXTRA_PARAM_FLEXIBLE: ("flexible", decode_flexible_params),
    EXTRA_PARAM_LIGHT: ("light", decode_light_params),
    EXTRA_PARAM_PROJECTION: ("projection", decode_projection_params),
    EXTRA_PARAM_REFLECTION_PROBE: ("reflection_probe", decode_reflection_probe_params),
    EXTRA_PARAM_RENDER_MATERIALS: ("render_materials", decode_render_materials_params),
    EXTRA_PARAM_MESH_FLAGS: ("mesh_flags", decode_mesh_flags_params),
}


def decode_extra_params(entries: object) -> DecodedExtraParams:
    """Decode every recognised block from a prim's ``ExtraParams`` entries.

    ``entries`` is any iterable of objects exposing ``param_type``,
    ``param_in_use`` and ``param_data`` — both ``ExtraParamEntry`` and
    ``ObjectExtraParamsEntry`` fit. Blocks flagged not-in-use are skipped:
    that is how a sim clears a feature, so honouring it matters.
    """
    fields: dict[str, object] = {}
    for entry in entries or ():
        param_type = getattr(entry, "param_type", None)
        decoder = _DECODERS.get(param_type)
        if decoder is None:
            continue
        if not getattr(entry, "param_in_use", True):
            continue
        name, decode = decoder
        decoded = decode(getattr(entry, "param_data", b"") or b"")
        if decoded is not None:
            fields[name] = decoded
    return DecodedExtraParams(**fields)


__all__ = [
    "EXTRA_PARAM_FLEXIBLE",
    "EXTRA_PARAM_LIGHT",
    "EXTRA_PARAM_MESH",
    "EXTRA_PARAM_MESH_FLAGS",
    "EXTRA_PARAM_PROJECTION",
    "EXTRA_PARAM_REFLECTION_PROBE",
    "EXTRA_PARAM_RENDER_MATERIALS",
    "EXTRA_PARAM_SCULPT",
    "DecodedExtraParams",
    "FlexibleParams",
    "LightParams",
    "ProjectionParams",
    "ReflectionProbeParams",
    "RenderMaterialEntry",
    "RenderMaterialsParams",
    "decode_extra_params",
    "decode_flexible_params",
    "decode_light_params",
    "decode_mesh_flags_params",
    "decode_projection_params",
    "decode_reflection_probe_params",
    "decode_render_materials_params",
    "encode_flexible_params",
    "encode_light_params",
    "encode_mesh_flags_params",
    "encode_projection_params",
    "encode_reflection_probe_params",
    "encode_render_materials_params",
]
