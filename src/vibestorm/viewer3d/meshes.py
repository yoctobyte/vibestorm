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
#
# Boxes are authored *flat shaded*: each face carries its own four vertices so
# it can carry its own normal. Sharing eight corner vertices is cheaper, but
# then the only normal a vertex can have is the average of three faces, and the
# renderer's fallback (normalize(position)) turns that into a radial gradient.
# On a single cube that merely looks soft; on the avatar placeholder, whose
# boxes sit *away* from the origin, it smears every part into one plank.
#
# Face order is (-Z, +Z, +Y, -Y, +X, -X) — the slot order ``cube_face_indices``
# maps onto SL's numbering. Corner order within each face is counter-clockwise
# seen from outside, so winding matches the outward normal.

_BOX_FACES: tuple[tuple[tuple[float, float, float], tuple[tuple[int, int, int], ...]], ...] = (
    ((0.0, 0.0, -1.0), ((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1))),
    ((0.0, 0.0, 1.0), ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))),
    ((0.0, 1.0, 0.0), ((-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1))),
    ((0.0, -1.0, 0.0), ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1))),
    ((1.0, 0.0, 0.0), ((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1))),
    ((-1.0, 0.0, 0.0), ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1))),
)


def box_geometry(
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    size: tuple[float, float, float] = (1.0, 1.0, 1.0),
    base_index: int = 0,
) -> tuple[list[float], list[float], list[int]]:
    """Flat-shaded box: ``(vertices, normals, indices)``, 24 verts / 12 tris.

    ``base_index`` offsets the emitted indices so several boxes can be merged
    into one mesh.
    """
    cx, cy, cz = center
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    vertices: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for normal, corners in _BOX_FACES:
        first = base_index + len(vertices) // 3
        for sx, sy, sz in corners:
            vertices.extend((cx + sx * hx, cy + sy * hy, cz + sz * hz))
            normals.extend(normal)
        indices.extend((first, first + 1, first + 2, first, first + 2, first + 3))
    return vertices, normals, indices


_CUBE_VERTS, _CUBE_NORMALS, _CUBE_INDICES = box_geometry()
CUBE_VERTICES: tuple[float, ...] = tuple(_CUBE_VERTS)
CUBE_NORMALS: tuple[float, ...] = tuple(_CUBE_NORMALS)
CUBE_INDICES: tuple[int, ...] = tuple(_CUBE_INDICES)


def cube_mesh() -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Unit cube centred at origin, side length 1."""
    return CUBE_VERTICES, CUBE_INDICES


def cube_normals() -> tuple[float, ...]:
    return CUBE_NORMALS


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


def sphere_normals(stacks: int = 8, slices: int = 12) -> tuple[float, ...]:
    """A sphere centred on the origin is the one shape whose surface normal
    *is* its normalized position, so this is exact rather than approximate."""
    verts, _ = sphere_mesh(stacks, slices)
    return _normalized(verts)


def _normalized(vertices: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    out: list[float] = []
    for i in range(0, len(vertices), 3):
        x, y, z = vertices[i], vertices[i + 1], vertices[i + 2]
        length = math.sqrt(x * x + y * y + z * z)
        if length <= 1e-8:
            out.extend((0.0, 0.0, 1.0))
        else:
            out.extend((x / length, y / length, z / length))
    return tuple(out)


# ---- cylinder ----------------------------------------------------------------


def cylinder_mesh(slices: int = 12) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Capped cylinder, radius 0.5, height 1, axis along Z, centred at origin."""
    if slices < 3:
        raise ValueError(f"slices={slices} too small (need >=3)")

    verts, _normals, indices = _cylinder_geometry(slices)
    return verts, indices


def cylinder_normals(slices: int = 12) -> tuple[float, ...]:
    return _cylinder_geometry(slices)[1]


def _cylinder_geometry(
    slices: int,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...]]:
    """Cylinder with the cap rings split from the side rings.

    A ring vertex cannot be both flat-capped (normal +/-Z) and round-sided
    (normal radial), so each ring is emitted twice. Index emission order per
    slice is unchanged — bottom triangle, top triangle, then the side quad —
    because ``cylinder_face_indices`` walks a 12-index stride over it.
    """
    vertices: list[float] = [0.0, 0.0, -0.5, 0.0, 0.0, 0.5]
    normals: list[float] = [0.0, 0.0, -1.0, 0.0, 0.0, 1.0]

    def ring(z: float, normal_mode: str) -> int:
        start = len(vertices) // 3
        for j in range(slices):
            theta = 2.0 * math.pi * j / slices
            cx, cy = math.cos(theta), math.sin(theta)
            vertices.extend((0.5 * cx, 0.5 * cy, z))
            if normal_mode == "cap":
                normals.extend((0.0, 0.0, 1.0 if z > 0 else -1.0))
            else:
                normals.extend((cx, cy, 0.0))
        return start

    bottom_cap = ring(-0.5, "cap")
    top_cap = ring(0.5, "cap")
    side_bottom = ring(-0.5, "side")
    side_top = ring(0.5, "side")

    indices: list[int] = []
    for j in range(slices):
        j1 = (j + 1) % slices
        indices.extend((0, bottom_cap + j1, bottom_cap + j))
        indices.extend((1, top_cap + j, top_cap + j1))
        b, b1 = side_bottom + j, side_bottom + j1
        t, t1 = side_top + j, side_top + j1
        indices.extend((b, b1, t1))
        indices.extend((b, t1, t))

    return tuple(vertices), tuple(normals), tuple(indices)


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

    profile = _regular_polygon_profile(sides, tube_radius, 0.0)
    verts, _normals, indices = _swept_ring_geometry(rings, profile, ring_radius)
    return verts, indices


def torus_normals(
    rings: int = 16,
    sides: int = 8,
    ring_radius: float = 0.4,
    tube_radius: float = 0.1,
) -> tuple[float, ...]:
    profile = _regular_polygon_profile(sides, tube_radius, 0.0)
    return _swept_ring_geometry(rings, profile, ring_radius)[1]


def _swept_ring_mesh(
    rings: int,
    profile_points: tuple[tuple[float, float], ...],
    ring_radius: float,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Sweep a closed 2D cross-section around the Z axis.

    ``profile_points`` are ``(radial_offset, z)`` pairs describing the
    cross-section relative to the centre of the tube; the sweep places each at
    ``ring_radius + radial_offset`` from the origin. This is the shared body
    behind torus, tube and ring, which differ only in that cross-section.
    """
    verts, _normals, indices = _swept_ring_geometry(rings, profile_points, ring_radius)
    return verts, indices


def _swept_ring_geometry(
    rings: int,
    profile_points: tuple[tuple[float, float], ...],
    ring_radius: float,
) -> tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...]]:
    """Sweep body shared by torus, tube and ring, with analytic normals.

    The surface normal at a swept vertex points away from the *centre of the
    tube* — the nearest point on the ring circle — not away from the world
    origin. Using the origin (the renderer's position fallback) lights the
    inside wall of the hole as though it faced outward, which is the single
    most visible normal error in the primitive set.
    """
    if rings < 3 or len(profile_points) < 3:
        raise ValueError(f"rings={rings}, profile={len(profile_points)} too small (need >=3)")

    sides = len(profile_points)
    vertices: list[float] = []
    normals: list[float] = []
    for i in range(rings):
        phi = 2.0 * math.pi * i / rings
        cphi, sphi = math.cos(phi), math.sin(phi)
        for radial, z in profile_points:
            radius = ring_radius + radial
            vertices.extend((radius * cphi, radius * sphi, z))
            # Offset from the ring-circle point at this phi, in world space.
            length = math.sqrt(radial * radial + z * z)
            if length <= 1e-8:
                normals.extend((cphi, sphi, 0.0))
            else:
                nr, nz = radial / length, z / length
                normals.extend((nr * cphi, nr * sphi, nz))

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
    return tuple(vertices), tuple(normals), tuple(indices)


def _regular_polygon_profile(
    sides: int, radius: float, phase: float
) -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            radius * math.cos(phase + 2.0 * math.pi * j / sides),
            radius * math.sin(phase + 2.0 * math.pi * j / sides),
        )
        for j in range(sides)
    )


# ---- tube --------------------------------------------------------------------


def tube_mesh(
    rings: int = 16,
    ring_radius: float = 0.4,
    half_width: float = 0.1,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Square-section torus: SL's "tube" prim, a square profile on a circular path.

    The profile is a 4-gon phased by 45 degrees so its flats face outward and
    up rather than its corners — a bare 4-side sweep produces a diamond
    cross-section, which reads as a lumpy torus instead of a tube. Its
    circumradius is therefore ``half_width * sqrt(2)``.
    """
    profile = _regular_polygon_profile(4, half_width * math.sqrt(2.0), math.pi / 4.0)
    return _swept_ring_mesh(rings, profile, ring_radius)


def tube_normals(
    rings: int = 16,
    ring_radius: float = 0.4,
    half_width: float = 0.1,
) -> tuple[float, ...]:
    profile = _regular_polygon_profile(4, half_width * math.sqrt(2.0), math.pi / 4.0)
    return _swept_ring_geometry(rings, profile, ring_radius)[1]


# ---- ring --------------------------------------------------------------------


def ring_mesh(
    rings: int = 16,
    ring_radius: float = 0.4,
    profile_radius: float = 0.1,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Triangle-section torus: SL's "ring" prim.

    Phased so one profile vertex points outward from the ring centre, matching
    how ``prism_mesh`` orients its cross-section.
    """
    profile = _regular_polygon_profile(3, profile_radius, 0.0)
    return _swept_ring_mesh(rings, profile, ring_radius)


def ring_normals(
    rings: int = 16,
    ring_radius: float = 0.4,
    profile_radius: float = 0.1,
) -> tuple[float, ...]:
    profile = _regular_polygon_profile(3, profile_radius, 0.0)
    return _swept_ring_geometry(rings, profile, ring_radius)[1]


# ---- prism -------------------------------------------------------------------


def prism_mesh() -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Equilateral triangular prism, length 1 along Z, centred at origin.

    Triangle circumradius is 0.5 (so the prism fits within a unit cube).
    The cross-section sits in the XY plane at z=±0.5 with one vertex
    pointing toward +Y.
    """
    verts, _normals, indices = _prism_geometry()
    return verts, indices


def prism_normals() -> tuple[float, ...]:
    return _prism_geometry()[1]


def _prism_geometry() -> tuple[tuple[float, ...], tuple[float, ...], tuple[int, ...]]:
    """Flat-shaded prism.

    Emitted per face rather than per corner so each face carries its own
    normal, and in the same order the face map slices: bottom triangle, top
    triangle, then the three side quads walking the profile.
    """
    r = 0.5
    angles = [math.pi / 2.0 + 2.0 * math.pi * i / 3.0 for i in range(3)]
    corners = [(r * math.cos(a), r * math.sin(a)) for a in angles]

    vertices: list[float] = []
    normals: list[float] = []
    indices: list[int] = []

    def emit(points: list[tuple[float, float, float]], normal: tuple[float, float, float]) -> None:
        first = len(vertices) // 3
        for x, y, z in points:
            vertices.extend((x, y, z))
            normals.extend(normal)
        # Fan the polygon: triangles (0,1,2), (0,2,3), ...
        for k in range(1, len(points) - 1):
            indices.extend((first, first + k, first + k + 1))

    # Bottom (CCW seen from below) then top (CCW seen from above).
    emit([(x, y, -0.5) for x, y in (corners[0], corners[2], corners[1])], (0.0, 0.0, -1.0))
    emit([(x, y, 0.5) for x, y in corners], (0.0, 0.0, 1.0))

    for i in range(3):
        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 3]
        edge_x, edge_y = bx - ax, by - ay
        # Outward normal of a CCW profile edge is the edge rotated -90 degrees.
        nx, ny = edge_y, -edge_x
        length = math.sqrt(nx * nx + ny * ny) or 1.0
        emit(
            [(ax, ay, -0.5), (bx, by, -0.5), (bx, by, 0.5), (ax, ay, 0.5)],
            (nx / length, ny / length, 0.0),
        )

    return tuple(vertices), tuple(normals), tuple(indices)


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
    "tube": 1,
    "ring": 1,
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
    """The humanoid figure avatars are drawn as, facing local +X.

    The geometry lives in :mod:`vibestorm.viewer3d.avatar_mesh`, which is
    imported lazily because that module borrows :func:`box_geometry` from this
    one. The renderer applies the avatar's ``ObjectUpdate`` scale on top.
    """
    verts, _normals, _uvs, indices = _avatar_geometry()
    return verts, indices


def avatar_placeholder_normals() -> tuple[float, ...]:
    return _avatar_geometry()[1]


def avatar_placeholder_uvs() -> tuple[float, ...]:
    """Per-vertex palette coordinates; see ``avatar_mesh.palette_texture``."""
    return _avatar_geometry()[2]


def _avatar_geometry() -> tuple[
    tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[int, ...]
]:
    from vibestorm.viewer3d.avatar_mesh import avatar_geometry

    return avatar_geometry()


def shape_uvs(shape_key: str) -> tuple[float, ...] | None:
    """Authored texture coordinates for a built-in shape, or ``None``.

    Only the avatar has any: primitives are textured through the fragment
    shader's position-and-normal projection, which is what SL's own face
    mapping approximates, while the avatar needs each body part to land on its
    own palette texel and so has to say where.
    """
    if shape_key == "avatar":
        return avatar_placeholder_uvs()
    return None


def shape_normals(shape_key: str) -> tuple[float, ...] | None:
    """Authored normals for a built-in primitive, or ``None`` if unknown.

    Without these the renderer falls back to ``normalize(position)``, which is
    exact only for a sphere centred on the origin and wrong for everything
    else.
    """
    authors = {
        "cube": cube_normals,
        "sphere": sphere_normals,
        "cylinder": cylinder_normals,
        "torus": torus_normals,
        "tube": tube_normals,
        "ring": ring_normals,
        "prism": prism_normals,
        "avatar": avatar_placeholder_normals,
    }
    author = authors.get(shape_key)
    return author() if author is not None else None


__all__ = [
    "CUBE_INDICES",
    "CUBE_NORMALS",
    "CUBE_VERTICES",
    "SL_FACE_COUNTS",
    "avatar_placeholder_mesh",
    "avatar_placeholder_normals",
    "avatar_placeholder_uvs",
    "box_geometry",
    "cube_face_indices",
    "cube_mesh",
    "cube_normals",
    "cylinder_face_indices",
    "cylinder_mesh",
    "cylinder_normals",
    "prism_face_indices",
    "prism_mesh",
    "prism_normals",
    "ring_mesh",
    "ring_normals",
    "shape_face_indices",
    "shape_normals",
    "shape_uvs",
    "sphere_mesh",
    "sphere_normals",
    "torus_mesh",
    "torus_normals",
    "tube_mesh",
    "tube_normals",
]
