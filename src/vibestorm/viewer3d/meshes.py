"""Static unit-mesh authors for the primitive library (step 7).

Each helper returns ``(vertices_xyz, indices_uint)``: a flat tuple of
x,y,z floats and a flat tuple of triangle indices. No UV/normal
attributes — those land in step 8 with lighting.

Sizing convention: every primitive is sized to fit a 1 m unit cube
(max extent ±0.5 on every axis), so per-entity ``scale`` from the
``SceneEntity`` corresponds 1:1 to "metres along each local axis".
A sphere with ``scale=(2, 2, 2)`` is therefore a 2 m diameter sphere.

Cylinders, prisms, and tori use Z as their axis of revolution. SL's
``ObjectUpdate`` quaternion is applied at draw time, so the renderer
does not need to know the per-prim "up" axis.
"""

from __future__ import annotations

import math

# ---- cube --------------------------------------------------------------------

CUBE_VERTICES: tuple[float, ...] = (
    -0.5, -0.5, -0.5,  # 0
     0.5, -0.5, -0.5,  # 1
     0.5,  0.5, -0.5,  # 2
    -0.5,  0.5, -0.5,  # 3
    -0.5, -0.5,  0.5,  # 4
     0.5, -0.5,  0.5,  # 5
     0.5,  0.5,  0.5,  # 6
    -0.5,  0.5,  0.5,  # 7
)

CUBE_INDICES: tuple[int, ...] = (
    0, 2, 1,  0, 3, 2,
    4, 5, 6,  4, 6, 7,
    3, 7, 6,  3, 6, 2,
    0, 1, 5,  0, 5, 4,
    1, 2, 6,  1, 6, 5,
    0, 4, 7,  0, 7, 3,
)


def cube_mesh() -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Unit cube centred at origin, side length 1."""
    return CUBE_VERTICES, CUBE_INDICES


# ---- sphere ------------------------------------------------------------------


def sphere_mesh(stacks: int = 8, slices: int = 12) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """UV-sphere of radius 0.5 centred at origin, axis along Z.

    ``stacks`` is the number of latitude bands (poles + interior rings);
    ``slices`` is the number of longitude divisions. (8, 12) yields 96
    quads = 192 triangles, plenty for v1.
    """
    if stacks < 3 or slices < 3:
        raise ValueError(f"stacks={stacks}, slices={slices} too small (need >=3)")

    vertices: list[float] = [0.0, 0.0, 0.5]  # north pole
    for i in range(1, stacks):
        phi = math.pi * i / stacks
        z = 0.5 * math.cos(phi)
        r = 0.5 * math.sin(phi)
        for j in range(slices):
            theta = 2.0 * math.pi * j / slices
            vertices.extend((r * math.cos(theta), r * math.sin(theta), z))
    vertices.extend((0.0, 0.0, -0.5))  # south pole

    north = 0
    south = 1 + (stacks - 1) * slices

    indices: list[int] = []
    for j in range(slices):
        j1 = (j + 1) % slices
        indices.extend((north, 1 + j, 1 + j1))

    for i in range(stacks - 2):
        ring_a = 1 + i * slices
        ring_b = 1 + (i + 1) * slices
        for j in range(slices):
            j1 = (j + 1) % slices
            indices.extend((ring_a + j, ring_b + j, ring_b + j1))
            indices.extend((ring_a + j, ring_b + j1, ring_a + j1))

    last_ring = 1 + (stacks - 2) * slices
    for j in range(slices):
        j1 = (j + 1) % slices
        indices.extend((last_ring + j, south, last_ring + j1))

    return tuple(vertices), tuple(indices)


# ---- cylinder ----------------------------------------------------------------


def cylinder_mesh(slices: int = 12) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Capped cylinder, radius 0.5, height 1, axis along Z, centred at origin."""
    if slices < 3:
        raise ValueError(f"slices={slices} too small (need >=3)")

    vertices: list[float] = [
        0.0, 0.0, -0.5,  # 0: bottom centre
        0.0, 0.0,  0.5,  # 1: top centre
    ]
    bottom_ring = 2
    for j in range(slices):
        theta = 2.0 * math.pi * j / slices
        vertices.extend((0.5 * math.cos(theta), 0.5 * math.sin(theta), -0.5))
    top_ring = 2 + slices
    for j in range(slices):
        theta = 2.0 * math.pi * j / slices
        vertices.extend((0.5 * math.cos(theta), 0.5 * math.sin(theta), 0.5))

    indices: list[int] = []
    for j in range(slices):
        j1 = (j + 1) % slices
        # Bottom cap (CCW viewed from below = wind 0,j1,j)
        indices.extend((0, bottom_ring + j1, bottom_ring + j))
        # Top cap (CCW viewed from above)
        indices.extend((1, top_ring + j, top_ring + j1))
        # Side quad
        b = bottom_ring + j
        b1 = bottom_ring + j1
        t = top_ring + j
        t1 = top_ring + j1
        indices.extend((b, b1, t1))
        indices.extend((b, t1, t))

    return tuple(vertices), tuple(indices)


# ---- torus -------------------------------------------------------------------


def torus_mesh(
    rings: int = 16,
    sides: int = 8,
    ring_radius: float = 0.4,
    tube_radius: float = 0.1,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Torus in the XY plane, centred at origin, axis along Z.

    ``ring_radius`` is the distance from the centre of the torus to the
    centre of the tube; ``tube_radius`` is the radius of the cross-section.
    Defaults sum to 0.5 so the torus fits inside a 1 m unit cube.
    """
    if rings < 3 or sides < 3:
        raise ValueError(f"rings={rings}, sides={sides} too small (need >=3)")

    vertices: list[float] = []
    for i in range(rings):
        phi = 2.0 * math.pi * i / rings
        cphi, sphi = math.cos(phi), math.sin(phi)
        for j in range(sides):
            theta = 2.0 * math.pi * j / sides
            ctheta, stheta = math.cos(theta), math.sin(theta)
            x = (ring_radius + tube_radius * ctheta) * cphi
            y = (ring_radius + tube_radius * ctheta) * sphi
            z = tube_radius * stheta
            vertices.extend((x, y, z))

    indices: list[int] = []
    for i in range(rings):
        i1 = (i + 1) % rings
        for j in range(sides):
            j1 = (j + 1) % sides
            a = i * sides + j
            b = i * sides + j1
            c = i1 * sides + j
            d = i1 * sides + j1
            indices.extend((a, c, d))
            indices.extend((a, d, b))

    return tuple(vertices), tuple(indices)


# ---- prism -------------------------------------------------------------------


def prism_mesh() -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Equilateral triangular prism, length 1 along Z, centred at origin.

    Triangle circumradius is 0.5 (so the prism fits within a unit cube).
    The cross-section sits in the XY plane at z=±0.5 with one vertex
    pointing toward +Y.
    """
    r = 0.5
    angles = [math.pi / 2.0 + 2.0 * math.pi * i / 3.0 for i in range(3)]

    vertices: list[float] = []
    for ang in angles:
        vertices.extend((r * math.cos(ang), r * math.sin(ang), -0.5))
    for ang in angles:
        vertices.extend((r * math.cos(ang), r * math.sin(ang), 0.5))

    # Bottom face (CCW from below): 0,2,1
    # Top face (CCW from above): 3,4,5
    # Three side quads.
    indices: tuple[int, ...] = (
        0, 2, 1,
        3, 4, 5,
        0, 1, 4,  0, 4, 3,
        1, 2, 5,  1, 5, 4,
        2, 0, 3,  2, 3, 5,
    )

    return tuple(vertices), indices


# ---- SL prim face maps -------------------------------------------------------
#
# A prim's ``TextureEntry`` addresses faces by SL's numbering, which is not the
# order these authors happen to emit triangles in. The maps below translate:
# they give, for one SL face index, the triangle indices of that face.
#
# SL numbers faces by walking the profile's side segments first and appending
# the caps last, top (+Z) before bottom (-Z). For a box that gives the familiar
# 0=+X, 1=+Y, 2=-X, 3=-Y, 4=top, 5=bottom; for a cylinder 0=side, 1=top,
# 2=bottom. Sphere and torus are single-face prims and need no map — their
# whole mesh is face 0.

SL_FACE_COUNTS: dict[str, int] = {
    "cube": 6,
    "cylinder": 3,
    "prism": 5,
    "sphere": 1,
    "torus": 1,
}


def cube_face_indices() -> dict[int, tuple[int, ...]]:
    """Map SL box face indices onto ``CUBE_INDICES``' six triangle pairs.

    ``CUBE_INDICES`` is authored in geometric slot order
    (-Z, +Z, +Y, -Y, +X, -X), which is *not* SL's order. Texturing by slot
    puts each face's texture on the wrong side of the box.
    """
    slot_for_sl_face = {0: 4, 1: 2, 2: 5, 3: 3, 4: 1, 5: 0}
    return {
        face: CUBE_INDICES[slot * 6 : (slot + 1) * 6]
        for face, slot in slot_for_sl_face.items()
    }


def cylinder_face_indices(slices: int = 12) -> dict[int, tuple[int, ...]]:
    """Map SL cylinder faces (0=side, 1=top, 2=bottom) onto ``cylinder_mesh``.

    ``cylinder_mesh`` interleaves bottom-cap, top-cap and side triangles per
    slice, so each face gathers a stride rather than a contiguous slice.
    """
    _, indices = cylinder_mesh(slices)
    bottom: list[int] = []
    top: list[int] = []
    side: list[int] = []
    for j in range(slices):
        base = j * 12
        bottom.extend(indices[base : base + 3])
        top.extend(indices[base + 3 : base + 6])
        side.extend(indices[base + 6 : base + 12])
    return {0: tuple(side), 1: tuple(top), 2: tuple(bottom)}


def prism_face_indices() -> dict[int, tuple[int, ...]]:
    """Map SL prism faces onto ``prism_mesh``.

    Derived from the profile-then-caps rule rather than observed: faces 0-2
    are the three side quads walking counter-clockwise from the +X-facing one
    (the same starting point the box map uses), then 3 = top, 4 = bottom.

    ``prism_mesh`` emits its side quads spanning 90->210 deg, 210->330 deg and
    330->90 deg, so the +X-facing side is the *last* of the three. The caps are
    the confident half of this map; which side quad is SL's face 0 has never
    been checked against a textured in-world prism, and a wrong guess there
    rotates the three side textures among themselves.
    """
    _, indices = prism_mesh()
    return {
        0: indices[18:24],
        1: indices[6:12],
        2: indices[12:18],
        3: indices[3:6],
        4: indices[0:3],
    }


def shape_face_indices(shape_key: str) -> dict[int, tuple[int, ...]] | None:
    """Per-SL-face triangle indices for a primitive, or ``None`` if it has one face.

    Single-face prims (sphere, torus) and shapes with no face model at all
    (the avatar placeholder) return ``None``; callers draw those in one pass.
    """
    if shape_key == "cube":
        return cube_face_indices()
    if shape_key == "cylinder":
        return cylinder_face_indices()
    if shape_key == "prism":
        return prism_face_indices()
    return None


def avatar_placeholder_mesh() -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Simple human-like avatar placeholder facing local +X.

    Built from merged box parts in a unit-ish local frame. The renderer
    applies the avatar ObjectUpdate scale/rotation, so this only needs a
    recognizable silhouette and a clear facing direction.
    """
    vertices: list[float] = []
    indices: list[int] = []

    def add_box(
        center: tuple[float, float, float],
        size: tuple[float, float, float],
    ) -> None:
        base = len(vertices) // 3
        cx, cy, cz = center
        sx, sy, sz = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0)
        for x, y, z in (
            (-sx, -sy, -sz),
            (sx, -sy, -sz),
            (sx, sy, -sz),
            (-sx, sy, -sz),
            (-sx, -sy, sz),
            (sx, -sy, sz),
            (sx, sy, sz),
            (-sx, sy, sz),
        ):
            vertices.extend((cx + x, cy + y, cz + z))
        indices.extend(base + index for index in CUBE_INDICES)

    # Body coordinates are normalized to roughly fit inside [-0.5, 0.5].
    add_box((0.0, 0.0, 0.05), (0.34, 0.46, 0.56))      # torso
    add_box((0.12, 0.0, 0.43), (0.30, 0.30, 0.26))     # head, slightly forward
    add_box((0.0, -0.33, 0.02), (0.18, 0.16, 0.52))    # left arm
    add_box((0.0, 0.33, 0.02), (0.18, 0.16, 0.52))     # right arm
    add_box((0.0, -0.12, -0.42), (0.18, 0.17, 0.48))   # left leg
    add_box((0.0, 0.12, -0.42), (0.18, 0.17, 0.48))    # right leg
    add_box((0.31, 0.0, 0.44), (0.08, 0.12, 0.08))     # nose/facing marker
    return tuple(vertices), tuple(indices)


__all__ = [
    "CUBE_INDICES",
    "CUBE_VERTICES",
    "avatar_placeholder_mesh",
    "cube_mesh",
    "cylinder_mesh",
    "prism_mesh",
    "sphere_mesh",
    "torus_mesh",
]
