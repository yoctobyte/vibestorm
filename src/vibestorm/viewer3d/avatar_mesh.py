"""A recognisable humanoid figure for avatars, built from primitives.

Nothing in this tree has the Second Life avatar mesh. It ships inside viewers,
and this project does not read viewer source, so an avatar is drawn as a figure
authored here. The bar is deliberately modest: a silhouette a person reads as a
person from across a parcel, with an unmistakable facing direction.

Two things about the space this is authored in, both easy to get wrong and both
responsible for how the previous placeholder looked:

*It is written in metres and divided down.* The renderer multiplies the mesh by
the avatar's ``ObjectUpdate`` scale, which is roughly ``0.45 x 0.60 x 1.90`` --
wildly non-uniform. A part authored as a cube in the mesh's own space therefore
comes out four times taller than it is deep. The old placeholder was written
directly in that space and had a half-metre head as a result, which is most of
why it read as a plank. Parts here are written the way anyone would measure a
body and divided by :data:`AVATAR_NOMINAL_SCALE` on the way in, so what is
written is what is drawn.

*Colour comes from a palette texture, not the instance tint.* One instanced
draw carries one tint, so a tinted avatar can only ever be a single flat
colour. Every vertex of a part instead points at one texel of a tiny palette
strip -- one extra texture, no extra draw calls -- which buys skin, hair,
shirt, trousers, shoes and eyes.

Local axes match the rest of the renderer: **+X is forward**, +Y is the
avatar's left, +Z is up, and the origin is the centre of the figure, so the
feet sit at about -0.95 m and the top of the head at about +0.95 m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

#: The ``ObjectUpdate`` scale a default-sized avatar reports, and the divisor
#: that turns the metre-space part table below into the mesh's own space. An
#: avatar reporting a different scale is stretched relative to this, which is
#: the intended behaviour: a 2.1 m avatar should be drawn taller.
AVATAR_NOMINAL_SCALE: tuple[float, float, float] = (0.45, 0.60, 1.90)

#: Palette entries, in texel order. Index is positional, so appending is safe
#: and reordering is not.
PALETTE: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("skin", (223, 184, 149)),
    ("hair", (72, 48, 36)),
    ("shirt", (84, 116, 170)),
    ("trousers", (58, 62, 76)),
    ("shoes", (38, 36, 38)),
    ("eye", (26, 28, 34)),
)

_PALETTE_INDEX: dict[str, int] = {name: index for index, (name, _rgb) in enumerate(PALETTE)}


@dataclass(frozen=True, slots=True)
class AvatarPart:
    """One body part, in metres, before the nominal scale is divided out."""

    region: str
    kind: str  # "box" | "tube" | "ellipsoid"
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    #: ``tube`` only: the bottom radius as a fraction of the top, so a limb can
    #: narrow towards the ankle or wrist the way a limb does.
    taper: float = 1.0
    sides: int = 8
    #: Which bone carries this part. Everything not on a limb rides the root.
    bone: str = "root"


#: Roughly seven and a half heads tall, which is the proportion that reads as
#: an adult rather than as a doll. Arms hang at the sides; nothing here is
#: posed, because there is no skeleton to pose it with.
AVATAR_PARTS: tuple[AvatarPart, ...] = (
    # Head and face. The nose and eyes exist to make facing unambiguous from
    # any angle -- with a symmetric head, a viewer cannot tell front from back,
    # and the previous placeholder needed a protruding box to say so.
    AvatarPart("hair", "ellipsoid", (-0.020, 0.0, 0.830), (0.200, 0.190, 0.220)),
    AvatarPart("skin", "ellipsoid", (0.000, 0.0, 0.790), (0.200, 0.185, 0.260)),
    AvatarPart("skin", "box", (0.098, 0.0, 0.780), (0.040, 0.030, 0.050)),
    AvatarPart("eye", "box", (0.096, 0.036, 0.824), (0.018, 0.028, 0.016)),
    AvatarPart("eye", "box", (0.096, -0.036, 0.824), (0.018, 0.028, 0.016)),
    AvatarPart("skin", "tube", (0.0, 0.0, 0.630), (0.110, 0.110, 0.070), sides=8),
    # Trunk. The chest is wider than the waist, so the tube tapers downward.
    AvatarPart("shirt", "tube", (0.0, 0.0, 0.330), (0.250, 0.380, 0.560), taper=0.80),
    AvatarPart("trousers", "tube", (0.0, 0.0, -0.020), (0.230, 0.330, 0.160), taper=0.95),
    # Arms, left (+Y) then right, shoulder to hand.
    AvatarPart("shirt", "tube", (0.0, 0.235, 0.420), (0.115, 0.115, 0.290), taper=0.85, bone="arm_l"),
    AvatarPart("skin", "tube", (0.0, 0.243, 0.150), (0.098, 0.098, 0.260), taper=0.85, bone="forearm_l"),
    AvatarPart("skin", "box", (0.0, 0.247, -0.040), (0.055, 0.090, 0.130), bone="forearm_l"),
    AvatarPart("shirt", "tube", (0.0, -0.235, 0.420), (0.115, 0.115, 0.290), taper=0.85, bone="arm_r"),
    AvatarPart("skin", "tube", (0.0, -0.243, 0.150), (0.098, 0.098, 0.260), taper=0.85, bone="forearm_r"),
    AvatarPart("skin", "box", (0.0, -0.247, -0.040), (0.055, 0.090, 0.130), bone="forearm_r"),
    # Legs, left then right, hip to shoe. Shoes sit forward of the ankle.
    AvatarPart("trousers", "tube", (0.0, 0.090, -0.245), (0.190, 0.190, 0.450), taper=0.78, bone="leg_l"),
    AvatarPart("trousers", "tube", (0.0, 0.090, -0.665), (0.148, 0.148, 0.390), taper=0.80, bone="shin_l"),
    AvatarPart("shoes", "box", (0.035, 0.090, -0.910), (0.230, 0.110, 0.080), bone="shin_l"),
    AvatarPart("trousers", "tube", (0.0, -0.090, -0.245), (0.190, 0.190, 0.450), taper=0.78, bone="leg_r"),
    AvatarPart("trousers", "tube", (0.0, -0.090, -0.665), (0.148, 0.148, 0.390), taper=0.80, bone="shin_r"),
    AvatarPart("shoes", "box", (0.035, -0.090, -0.910), (0.230, 0.110, 0.080), bone="shin_r"),
)


@dataclass(frozen=True, slots=True)
class AvatarBone:
    """A joint the figure can rotate about, in metres, in the figure's frame."""

    name: str
    parent: str | None
    pivot: tuple[float, float, float]


#: Nine bones, parents before children so one forward pass composes them. Each
#: pivot is the *top* of the parts it carries -- a shoulder is the top of the
#: upper arm, a knee the top of the shin -- so rotating a bone swings its limb
#: from the joint rather than about the limb's own middle.
#:
#: Deliberately shallow. There is no spine, no ankle and no wrist, because
#: nothing this client can obtain would drive them: SL animation assets are
#: keyframes against a skeleton this tree does not have, and the poses here are
#: derived from where the avatar has moved. A joint nothing can articulate is
#: cost with no picture behind it.
AVATAR_BONES: tuple[AvatarBone, ...] = (
    AvatarBone("root", None, (0.0, 0.0, 0.0)),
    AvatarBone("arm_l", "root", (0.0, 0.235, 0.565)),
    AvatarBone("forearm_l", "arm_l", (0.0, 0.243, 0.280)),
    AvatarBone("arm_r", "root", (0.0, -0.235, 0.565)),
    AvatarBone("forearm_r", "arm_r", (0.0, -0.243, 0.280)),
    AvatarBone("leg_l", "root", (0.0, 0.090, -0.020)),
    AvatarBone("shin_l", "leg_l", (0.0, 0.090, -0.470)),
    AvatarBone("leg_r", "root", (0.0, -0.090, -0.020)),
    AvatarBone("shin_r", "leg_r", (0.0, -0.090, -0.470)),
)

_BONE_BY_NAME: dict[str, AvatarBone] = {bone.name: bone for bone in AVATAR_BONES}


class _Builder:
    """Accumulates parts into one flat-shaded mesh with palette UVs."""

    def __init__(self) -> None:
        self.vertices: list[float] = []
        self.normals: list[float] = []
        self.uvs: list[float] = []
        self.indices: list[int] = []

    @property
    def next_index(self) -> int:
        return len(self.vertices) // 3

    def emit(
        self,
        position: tuple[float, float, float],
        normal: tuple[float, float, float],
        uv: tuple[float, float],
    ) -> None:
        self.vertices.extend(position)
        self.normals.extend(_unit(normal))
        self.uvs.extend(uv)

    def quad(self, first: int) -> None:
        self.indices.extend((first, first + 1, first + 2, first, first + 2, first + 3))


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-9:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def palette_uv(region: str) -> tuple[float, float]:
    """The centre of ``region``'s texel in the palette strip.

    Sampled at the texel centre and with nearest filtering on the texture, so
    neighbouring entries cannot bleed into each other however the driver
    rounds.
    """
    index = _PALETTE_INDEX[region]
    return ((index + 0.5) / len(PALETTE), 0.5)


def palette_texture() -> tuple[tuple[int, int], bytes]:
    """``((width, height), RGB bytes)`` for the palette strip."""
    data = bytearray()
    for _name, (red, green, blue) in PALETTE:
        data.extend((red, green, blue))
    return ((len(PALETTE), 1), bytes(data))


def _add_box(builder: _Builder, part: AvatarPart, uv: tuple[float, float]) -> None:
    # Borrowed rather than re-tabulated: a second copy of the cube's face
    # winding is a second thing to get wrong, and it would only ever be
    # noticed as a body part rendered inside out.
    from vibestorm.viewer3d.meshes import box_geometry

    vertices, normals, indices = box_geometry(
        part.center, part.size, base_index=builder.next_index
    )
    builder.vertices.extend(vertices)
    builder.normals.extend(normals)
    builder.uvs.extend(uv * (len(vertices) // 3))
    builder.indices.extend(indices)


def _add_tube(builder: _Builder, part: AvatarPart, uv: tuple[float, float]) -> None:
    """A flat-capped prism along Z, optionally narrower at the bottom.

    Eight sides is enough: at the size a limb occupies on screen the silhouette
    is already smooth, and a rounder limb costs vertices on every avatar in the
    region for a difference nobody can see.
    """
    cx, cy, cz = part.center
    rx, ry = part.size[0] / 2.0, part.size[1] / 2.0
    hz = part.size[2] / 2.0
    sides = max(3, part.sides)
    taper = max(0.05, part.taper)

    # Outward normals slope with the taper: over the part's height the radius
    # changes by (1 - taper) * r, so the surface leans in by that much per
    # unit of height. Ignoring it shades a tapered limb as though it were a
    # cylinder, which is visible as a hard band where two parts meet.
    slope = ((1.0 - taper) * rx) / (2.0 * hz) if hz > 1e-9 else 0.0

    def ring(z: float, scale: float) -> list[tuple[float, float, float]]:
        points = []
        for step in range(sides):
            theta = 2.0 * math.pi * step / sides
            points.append(
                (
                    cx + rx * scale * math.cos(theta),
                    cy + ry * scale * math.sin(theta),
                    z,
                )
            )
        return points

    bottom = ring(cz - hz, taper)
    top = ring(cz + hz, 1.0)

    for normal, ring_points, centre_z in (
        ((0.0, 0.0, -1.0), bottom, cz - hz),
        ((0.0, 0.0, 1.0), top, cz + hz),
    ):
        centre_index = builder.next_index
        builder.emit((cx, cy, centre_z), normal, uv)
        for point in ring_points:
            builder.emit(point, normal, uv)
        for step in range(sides):
            a = centre_index + 1 + step
            b = centre_index + 1 + (step + 1) % sides
            if normal[2] < 0.0:
                builder.indices.extend((centre_index, b, a))
            else:
                builder.indices.extend((centre_index, a, b))

    for step in range(sides):
        nxt = (step + 1) % sides
        theta = 2.0 * math.pi * (step + 0.5) / sides
        normal = (math.cos(theta), math.sin(theta), slope)
        first = builder.next_index
        builder.emit(bottom[step], normal, uv)
        builder.emit(bottom[nxt], normal, uv)
        builder.emit(top[nxt], normal, uv)
        builder.emit(top[step], normal, uv)
        builder.quad(first)


def _add_ellipsoid(
    builder: _Builder, part: AvatarPart, uv: tuple[float, float], *, stacks: int = 7, slices: int = 12
) -> None:
    cx, cy, cz = part.center
    rx, ry, rz = part.size[0] / 2.0, part.size[1] / 2.0, part.size[2] / 2.0

    def point(stack: int, slice_index: int) -> tuple[float, float, float]:
        phi = math.pi * stack / stacks
        theta = 2.0 * math.pi * slice_index / slices
        sx = math.sin(phi) * math.cos(theta)
        sy = math.sin(phi) * math.sin(theta)
        sz = math.cos(phi)
        return (cx + rx * sx, cy + ry * sy, cz + rz * sz)

    def normal_at(position: tuple[float, float, float]) -> tuple[float, float, float]:
        # Gradient of x^2/a^2 + y^2/b^2 + z^2/c^2, which is the true outward
        # normal of an ellipsoid and not the same direction as the position
        # unless the three radii are equal.
        return (
            (position[0] - cx) / (rx * rx),
            (position[1] - cy) / (ry * ry),
            (position[2] - cz) / (rz * rz),
        )

    for stack in range(stacks):
        for slice_index in range(slices):
            nxt = (slice_index + 1) % slices
            corners = (
                point(stack, slice_index),
                point(stack, nxt),
                point(stack + 1, nxt),
                point(stack + 1, slice_index),
            )
            first = builder.next_index
            for corner in corners:
                builder.emit(corner, normal_at(corner), uv)
            # Reverse winding: stacks run from the north pole downward, so a
            # naive order faces inward.
            builder.indices.extend(
                (first, first + 2, first + 1, first, first + 3, first + 2)
            )


_ADDERS = {"box": _add_box, "tube": _add_tube, "ellipsoid": _add_ellipsoid}


@dataclass(frozen=True, slots=True)
class BoneMesh:
    """One bone's geometry, in **metres, relative to that bone's pivot**.

    Relative to the pivot because that is what makes a rotation a rotation:
    ``R * v`` swings the limb about its joint. Absolute coordinates would swing
    it about the figure's navel.

    In metres because a rotation in the mesh's own non-uniformly scaled space
    is a shear, not a rotation. The scale is folded into the matrix the
    renderer hands the shader instead -- see :func:`bone_matrices`.
    """

    vertices: tuple[float, ...]
    normals: tuple[float, ...]
    uvs: tuple[float, ...]
    indices: tuple[int, ...]


@lru_cache(maxsize=1)
def avatar_bone_meshes() -> dict[str, BoneMesh]:
    """One :class:`BoneMesh` per bone that carries at least one part."""
    meshes: dict[str, BoneMesh] = {}
    for bone in AVATAR_BONES:
        parts = [part for part in AVATAR_PARTS if part.bone == bone.name]
        if not parts:
            continue
        builder = _Builder()
        for part in parts:
            _ADDERS[part.kind](builder, part, palette_uv(part.region))
        px, py, pz = bone.pivot
        relative: list[float] = []
        for index in range(0, len(builder.vertices), 3):
            relative.append(builder.vertices[index] - px)
            relative.append(builder.vertices[index + 1] - py)
            relative.append(builder.vertices[index + 2] - pz)
        meshes[bone.name] = BoneMesh(
            vertices=tuple(relative),
            normals=tuple(builder.normals),
            uvs=tuple(builder.uvs),
            indices=tuple(builder.indices),
        )
    return meshes


def multiply_4x4(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """Column-major 4x4 product ``a * b``, matching ``model_matrix``'s layout.

    Public because the renderer needs it for exactly one thing -- putting an
    avatar's model matrix in front of each of its bone matrices -- and a
    second copy over there would be a second place to get the column order
    wrong.

    Written out rather than looped. This runs per bone per avatar per frame,
    and the obvious triple loop with a generator ``sum`` measured 40 us a
    call -- a dozen avatars walking about would have cost more of the frame
    than drawing them.
    """
    a0, a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15 = a
    out = []
    for column in range(4):
        base = column * 4
        b0, b1, b2, b3 = b[base], b[base + 1], b[base + 2], b[base + 3]
        out.append(a0 * b0 + a4 * b1 + a8 * b2 + a12 * b3)
        out.append(a1 * b0 + a5 * b1 + a9 * b2 + a13 * b3)
        out.append(a2 * b0 + a6 * b1 + a10 * b2 + a14 * b3)
        out.append(a3 * b0 + a7 * b1 + a11 * b2 + a15 * b3)
    return tuple(out)


def _translation(x: float, y: float, z: float) -> tuple[float, ...]:
    return (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, x, y, z, 1.0)


def _pitch_at(offset: tuple[float, float, float], radians: float) -> tuple[float, ...]:
    """``translate(offset) * rotate_about_y(radians)``, built directly.

    Pitch is the only axis a limb here needs. +Y is the avatar's left and +X
    is forward, so a positive angle tips the *top* of a bone forward and
    swings the far end -- the foot, the hand -- back. Composing the two as a
    matrix product would be a third of the work in this module for a result
    that is just the rotation with the offset in its last column.
    """
    c, s = math.cos(radians), math.sin(radians)
    return (
        c, 0.0, -s, 0.0,
        0.0, 1.0, 0.0, 0.0,
        s, 0.0, c, 0.0,
        offset[0], offset[1], offset[2], 1.0,
    )


_NOMINAL_TO_MESH: tuple[float, ...] = (
    1.0 / AVATAR_NOMINAL_SCALE[0], 0.0, 0.0, 0.0,
    0.0, 1.0 / AVATAR_NOMINAL_SCALE[1], 0.0, 0.0,
    0.0, 0.0, 1.0 / AVATAR_NOMINAL_SCALE[2], 0.0,
    0.0, 0.0, 0.0, 1.0,
)


@lru_cache(maxsize=1)
def _ground_probes() -> tuple[tuple[str, tuple[float, float, float]], ...]:
    """The points that can touch the ground: the corners of each sole.

    Derived from the part table rather than written down, so a shoe that moves
    or changes size cannot leave a stale constant behind. Only the front and
    back edges matter -- ``y`` does not affect height under a pitch -- so this
    is four points, not a mesh.
    """
    probes: list[tuple[str, tuple[float, float, float]]] = []
    for part in AVATAR_PARTS:
        if part.region != "shoes":
            continue
        cx, cy, cz = part.center
        hx, hz = part.size[0] / 2.0, part.size[2] / 2.0
        probes.append((part.bone, (cx - hx, cy, cz - hz)))
        probes.append((part.bone, (cx + hx, cy, cz - hz)))
    return tuple(probes)


def _lowest_sole(composed: dict[str, tuple[float, ...]]) -> float:
    """Height of the lowest sole corner under a set of composed bone matrices."""
    lowest = None
    for bone_name, point in _ground_probes():
        pivot = _BONE_BY_NAME[bone_name].pivot
        x = point[0] - pivot[0]
        y = point[1] - pivot[1]
        z = point[2] - pivot[2]
        matrix = composed[bone_name]
        height = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
        lowest = height if lowest is None else min(lowest, height)
    assert lowest is not None
    return lowest


@lru_cache(maxsize=1)
def _rest_sole_height() -> float:
    """Where the soles sit with no pose applied: the height to hold them at."""
    rest = {bone.name: _translation(*bone.pivot) for bone in AVATAR_BONES}
    return _lowest_sole(rest)


#: Angles are rounded to this many decimals before the cache is consulted.
#: Three decimals is a fifth of a degree, which moves a fingertip by a tenth of
#: a millimetre; what it buys is that an avatar standing still -- the common
#: case, in any region with people in it -- recomputes nothing at all.
_POSE_PRECISION = 3


def bone_matrices(pose: dict[str, float] | None = None) -> dict[str, tuple[float, ...]]:
    """Per-bone matrices to post-multiply onto an avatar's model matrix.

    ``pose`` gives each bone a pitch in radians about its own pivot; anything
    absent is at rest. The result maps a :class:`BoneMesh`'s metre-space
    vertices into the space the renderer's model matrix expects, so the shader
    sees ``model * bone * vertex``.

    With an empty pose every bone comes back as a plain translation to its
    pivot, which reassembles exactly the figure :func:`avatar_geometry` builds
    -- that equivalence is what makes "the rig changed nothing" checkable.
    """
    pose = pose or {}
    return _bone_matrices_cached(
        tuple(
            round(pose.get(bone.name, 0.0), _POSE_PRECISION) for bone in AVATAR_BONES
        )
    )


@lru_cache(maxsize=256)
def _bone_matrices_cached(angles: tuple[float, ...]) -> dict[str, tuple[float, ...]]:
    composed: dict[str, tuple[float, ...]] = {}
    for bone, angle in zip(AVATAR_BONES, angles, strict=True):
        parent_pivot = (
            _BONE_BY_NAME[bone.parent].pivot if bone.parent is not None else (0.0, 0.0, 0.0)
        )
        local = _pitch_at(
            (
                bone.pivot[0] - parent_pivot[0],
                bone.pivot[1] - parent_pivot[1],
                bone.pivot[2] - parent_pivot[2],
            ),
            angle,
        )
        if bone.parent is None:
            composed[bone.name] = local
        else:
            composed[bone.name] = multiply_4x4(composed[bone.parent], local)

    # A leg swung forward or back reaches less far down than a straight one,
    # and a bent knee less again. Leave the hips where they were and the whole
    # figure lifts off the ground at each extreme of the stride and settles
    # back in the middle -- a bob in the wrong direction, with both feet in the
    # air at the moment one of them should be planted. Measure the rise from
    # the soles themselves rather than from an arm-length approximation: the
    # shoe reaches forward of the ankle, so how much a leg loses depends on
    # which way it swung.
    drop = _lowest_sole(composed) - _rest_sole_height()

    # Sinking the root is a pure translation, and pre-multiplying a column-
    # major matrix by one only touches its last column; likewise the metre-to-
    # mesh conversion is diagonal, so it only scales rows. Both are written out
    # rather than run through _multiply, which halves the work per bone.
    dx, dy, dz = (1.0 / value for value in AVATAR_NOMINAL_SCALE)
    return {
        name: (
            m[0] * dx, m[1] * dy, m[2] * dz, m[3],
            m[4] * dx, m[5] * dy, m[6] * dz, m[7],
            m[8] * dx, m[9] * dy, m[10] * dz, m[11],
            m[12] * dx, m[13] * dy, (m[14] - drop) * dz, m[15],
        )
        for name, m in composed.items()
    }


@lru_cache(maxsize=1)
def avatar_geometry() -> tuple[
    tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[int, ...]
]:
    """``(vertices, normals, uvs, indices)`` in the renderer's mesh space.

    Cached: the mesh is a constant, and it is asked for three times whenever a
    GL context is built.
    """
    builder = _Builder()
    for part in AVATAR_PARTS:
        uv = palette_uv(part.region)
        _ADDERS[part.kind](builder, part, uv)

    sx, sy, sz = AVATAR_NOMINAL_SCALE
    scaled: list[float] = []
    for index in range(0, len(builder.vertices), 3):
        scaled.append(builder.vertices[index] / sx)
        scaled.append(builder.vertices[index + 1] / sy)
        scaled.append(builder.vertices[index + 2] / sz)

    # A normal does not transform like a position under a non-uniform scale:
    # it goes through the inverse transpose, which for a diagonal scale means
    # *multiplying* by the same factors the positions were divided by.
    normals: list[float] = []
    for index in range(0, len(builder.normals), 3):
        normals.extend(
            _unit(
                (
                    builder.normals[index] * sx,
                    builder.normals[index + 1] * sy,
                    builder.normals[index + 2] * sz,
                )
            )
        )

    return tuple(scaled), tuple(normals), tuple(builder.uvs), tuple(builder.indices)


__all__ = [
    "AVATAR_NOMINAL_SCALE",
    "AVATAR_PARTS",
    "PALETTE",
    "AvatarBone",
    "AvatarPart",
    "BoneMesh",
    "avatar_bone_meshes",
    "avatar_geometry",
    "bone_matrices",
    "multiply_4x4",
    "palette_texture",
    "palette_uv",
]
