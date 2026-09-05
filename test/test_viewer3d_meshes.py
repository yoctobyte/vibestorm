"""Tests for the static mesh authors (step 7a).

Pure-Python — no GL needed. Verifies counts and basic geometric
invariants (centred at origin, fits in a 1 m unit cube) for each
helper. The actual GL upload + per-shape dispatch lands in step 7b
and is tested via a real GL context.
"""

import math
import unittest


def _xyz_iter(verts: tuple[float, ...]):
    assert len(verts) % 3 == 0, "vertex tuple must be a flat run of x,y,z"
    for i in range(0, len(verts), 3):
        yield verts[i], verts[i + 1], verts[i + 2]


class CubeMeshTests(unittest.TestCase):
    def test_cube_is_flat_shaded_with_24_vertices(self) -> None:
        # Four vertices per face rather than eight shared corners: a shared
        # corner can only carry one normal, which forces smooth shading and
        # (via the renderer's normalize(position) fallback) a radial gradient.
        from vibestorm.viewer3d.meshes import cube_mesh

        verts, indices = cube_mesh()

        self.assertEqual(len(verts), 24 * 3)
        self.assertEqual(len(indices), 36)

    def test_cube_vertices_within_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import cube_mesh

        verts, _ = cube_mesh()
        for x, y, z in _xyz_iter(verts):
            self.assertLessEqual(abs(x), 0.5 + 1e-9)
            self.assertLessEqual(abs(y), 0.5 + 1e-9)
            self.assertLessEqual(abs(z), 0.5 + 1e-9)


class SphereMeshTests(unittest.TestCase):
    def test_default_count_matches_pole_band_pole_topology(self) -> None:
        from vibestorm.viewer3d.meshes import sphere_mesh

        stacks, slices = 8, 12
        verts, indices = sphere_mesh(stacks=stacks, slices=slices)

        # Two poles + (stacks-1) rings of ``slices`` verts.
        self.assertEqual(len(verts) // 3, 2 + (stacks - 1) * slices)
        # Top + bottom triangle fans = slices each; middle bands =
        # 2 * slices triangles per band, (stacks - 2) bands.
        expected_tris = slices + slices + 2 * slices * (stacks - 2)
        self.assertEqual(len(indices), expected_tris * 3)

    def test_all_vertices_within_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import sphere_mesh

        verts, _ = sphere_mesh()
        for x, y, z in _xyz_iter(verts):
            self.assertLessEqual(x * x + y * y + z * z, 0.25 + 1e-9)

    def test_north_pole_is_first_vertex(self) -> None:
        from vibestorm.viewer3d.meshes import sphere_mesh

        verts, _ = sphere_mesh()
        self.assertAlmostEqual(verts[0], 0.0, places=6)
        self.assertAlmostEqual(verts[1], 0.0, places=6)
        self.assertAlmostEqual(verts[2], 0.5, places=6)

    def test_rejects_degenerate_args(self) -> None:
        from vibestorm.viewer3d.meshes import sphere_mesh

        with self.assertRaises(ValueError):
            sphere_mesh(stacks=2, slices=12)
        with self.assertRaises(ValueError):
            sphere_mesh(stacks=8, slices=2)


class CylinderMeshTests(unittest.TestCase):
    def test_cap_and_side_rings_are_separate(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_mesh

        slices = 12
        verts, indices = cylinder_mesh(slices=slices)

        # 2 cap centres + 4 rings: a ring vertex cannot be both flat-capped
        # (normal +/-Z) and round-sided (normal radial), so each ring is
        # emitted once for the cap and once for the side.
        self.assertEqual(len(verts) // 3, 2 + 4 * slices)
        # bottom cap + top cap (slices triangles each) + side (2*slices).
        self.assertEqual(len(indices), 3 * (slices + slices + 2 * slices))

    def test_axis_along_z(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_mesh

        verts, _ = cylinder_mesh(slices=4)
        zs = [z for _, _, z in _xyz_iter(verts)]
        self.assertEqual(min(zs), -0.5)
        self.assertEqual(max(zs), 0.5)


class TorusMeshTests(unittest.TestCase):
    def test_default_count_matches_grid_topology(self) -> None:
        from vibestorm.viewer3d.meshes import torus_mesh

        rings, sides = 16, 8
        verts, indices = torus_mesh(rings=rings, sides=sides)

        self.assertEqual(len(verts) // 3, rings * sides)
        # Two triangles per quad, rings*sides quads.
        self.assertEqual(len(indices), 6 * rings * sides)

    def test_all_vertices_within_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import torus_mesh

        verts, _ = torus_mesh()
        for x, y, z in _xyz_iter(verts):
            self.assertLessEqual(abs(x), 0.5 + 1e-9)
            self.assertLessEqual(abs(y), 0.5 + 1e-9)
            self.assertLessEqual(abs(z), 0.5 + 1e-9)


class PrismMeshTests(unittest.TestCase):
    def test_flat_shaded_faces_and_eight_triangles(self) -> None:
        from vibestorm.viewer3d.meshes import prism_mesh

        verts, indices = prism_mesh()
        # Emitted per face so each carries its own normal: two triangles
        # (3 verts each) plus three side quads (4 verts each).
        self.assertEqual(len(verts) // 3, 3 + 3 + 4 * 3)
        # 1 bottom + 1 top + 3 side quads -> 1+1+6 = 8 triangles.
        self.assertEqual(len(indices), 8 * 3)

    def test_extends_full_height_along_z(self) -> None:
        from vibestorm.viewer3d.meshes import prism_mesh

        verts, _ = prism_mesh()
        zs = [z for _, _, z in _xyz_iter(verts)]
        self.assertEqual(min(zs), -0.5)
        self.assertEqual(max(zs), 0.5)

    def test_fits_within_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import prism_mesh

        verts, _ = prism_mesh()
        for x, y, _ in _xyz_iter(verts):
            self.assertLessEqual(abs(x), 0.5 + 1e-9)
            self.assertLessEqual(abs(y), 0.5 + 1e-9)


class AvatarPlaceholderMeshTests(unittest.TestCase):
    def test_avatar_placeholder_has_multiple_body_parts(self) -> None:
        from vibestorm.viewer3d.meshes import avatar_placeholder_mesh

        verts, indices = avatar_placeholder_mesh()

        self.assertGreater(len(verts) // 3, 8)
        self.assertGreater(len(indices), 36)

    def test_avatar_placeholder_facing_marker_extends_positive_x(self) -> None:
        from vibestorm.viewer3d.meshes import avatar_placeholder_mesh

        verts, _ = avatar_placeholder_mesh()
        xs = [x for x, _, _ in _xyz_iter(verts)]

        self.assertGreater(max(xs), abs(min(xs)))


class TubeAndRingMeshTests(unittest.TestCase):
    """Tube and ring are swept cross-sections, not the torus or a box.

    They aliased to torus and cube until 2026-08-14, which rendered a
    square-section tube as a round donut and a ring as a box.
    """

    def test_tube_fits_the_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import tube_mesh

        verts, _ = tube_mesh()
        for x, y, z in _xyz_iter(verts):
            self.assertLessEqual(abs(x), 0.5 + 1e-6)
            self.assertLessEqual(abs(y), 0.5 + 1e-6)
            self.assertLessEqual(abs(z), 0.5 + 1e-6)

    def test_ring_fits_the_unit_cube(self) -> None:
        from vibestorm.viewer3d.meshes import ring_mesh

        verts, _ = ring_mesh()
        for x, y, z in _xyz_iter(verts):
            self.assertLessEqual(abs(x), 0.5 + 1e-6)
            self.assertLessEqual(abs(y), 0.5 + 1e-6)
            self.assertLessEqual(abs(z), 0.5 + 1e-6)

    def test_tube_cross_section_is_square_not_a_diamond(self) -> None:
        # A bare 4-side sweep puts profile vertices at 0/90/180/270 degrees,
        # giving a diamond section that reads as a lumpy torus. The profile is
        # phased 45 degrees so its flats face outward and up.
        from vibestorm.viewer3d.meshes import tube_mesh

        verts, _ = tube_mesh()
        # Along +X the four profile vertices of the first ring must form a
        # square: two distinct |z| values, each shared by two vertices.
        first_ring = [
            (x, y, z) for x, y, z in list(_xyz_iter(verts))[:4]
        ]
        zs = sorted(round(z, 6) for _, _, z in first_ring)
        self.assertEqual(zs[0], zs[1])
        self.assertEqual(zs[2], zs[3])
        self.assertNotEqual(zs[0], zs[2])

    def test_tube_has_a_hole(self) -> None:
        # The defining property versus a cylinder: no geometry near the axis.
        from vibestorm.viewer3d.meshes import tube_mesh

        verts, _ = tube_mesh()
        radii = [(x * x + y * y) ** 0.5 for x, y, _ in _xyz_iter(verts)]

        self.assertGreater(min(radii), 0.1)

    def test_ring_profile_has_three_sides(self) -> None:
        from vibestorm.viewer3d.meshes import ring_mesh

        verts, indices = ring_mesh(rings=8)

        self.assertEqual(len(verts) // 3, 8 * 3)
        self.assertEqual(len(indices) // 3, 8 * 3 * 2)

    def test_tube_and_ring_are_not_the_same_mesh_as_torus(self) -> None:
        from vibestorm.viewer3d.meshes import ring_mesh, torus_mesh, tube_mesh

        torus_verts, _ = torus_mesh()

        self.assertNotEqual(tube_mesh()[0], torus_verts)
        self.assertNotEqual(ring_mesh()[0], torus_verts)

    def test_degenerate_parameters_are_rejected(self) -> None:
        from vibestorm.viewer3d.meshes import ring_mesh, tube_mesh

        with self.assertRaises(ValueError):
            tube_mesh(rings=2)
        with self.assertRaises(ValueError):
            ring_mesh(rings=2)


class ShapeNormalTests(unittest.TestCase):
    """Authored normals, and what they replaced.

    Until 2026-08-14 every built-in primitive fell back to the renderer's
    ``normalize(position)``. That is exact for a sphere centred on the origin
    and wrong for everything else: a torus's inner hole wall was lit as though
    it faced outward, and the avatar placeholder — whose boxes sit away from
    the origin — shaded into one smooth plank.
    """

    def _normals_of(self, shape_key):
        from vibestorm.viewer3d.meshes import shape_normals

        normals = shape_normals(shape_key)
        self.assertIsNotNone(normals, f"{shape_key} has no authored normals")
        return normals

    def _pairs(self, shape_key, author):
        verts, _ = author()
        normals = self._normals_of(shape_key)
        self.assertEqual(len(normals), len(verts))
        return [
            (verts[i : i + 3], normals[i : i + 3]) for i in range(0, len(verts), 3)
        ]

    def test_every_builtin_shape_authors_normals(self) -> None:
        from vibestorm.viewer3d.meshes import SL_FACE_COUNTS, shape_normals

        for shape_key in (*SL_FACE_COUNTS, "avatar"):
            self.assertIsNotNone(shape_normals(shape_key), shape_key)

    def test_unknown_shape_has_none(self) -> None:
        from vibestorm.viewer3d.meshes import shape_normals

        self.assertIsNone(shape_normals("not-a-shape"))

    def test_all_normals_are_unit_length(self) -> None:
        from vibestorm.viewer3d.meshes import SL_FACE_COUNTS, shape_normals

        for shape_key in (*SL_FACE_COUNTS, "avatar"):
            normals = shape_normals(shape_key)
            for i in range(0, len(normals), 3):
                nx, ny, nz = normals[i : i + 3]
                self.assertAlmostEqual(
                    (nx * nx + ny * ny + nz * nz) ** 0.5, 1.0, places=5, msg=shape_key
                )

    def test_cube_faces_are_axis_aligned_and_flat(self) -> None:
        from vibestorm.viewer3d.meshes import cube_mesh

        for position, normal in self._pairs("cube", cube_mesh):
            nonzero = [c for c in normal if abs(c) > 1e-6]
            self.assertEqual(len(nonzero), 1, f"{normal} is not a face normal")
            # The normal must point the same way as the face it belongs to.
            axis = max(range(3), key=lambda k: abs(normal[k]))
            self.assertGreater(position[axis] * normal[axis], 0.0)

    def test_cylinder_side_normals_are_horizontal_and_caps_vertical(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_normals

        normals = cylinder_normals()
        horizontal = [n for i in range(0, len(normals), 3)
                      if abs((n := normals[i : i + 3])[2]) < 1e-6]
        vertical = [n for i in range(0, len(normals), 3)
                    if abs((n := normals[i : i + 3])[2]) > 1.0 - 1e-6]

        self.assertTrue(horizontal, "cylinder has no radial side normals")
        self.assertTrue(vertical, "cylinder has no flat cap normals")

    def test_torus_normals_point_away_from_the_tube_not_the_origin(self) -> None:
        # The bug this replaced: with position-derived normals, a vertex on the
        # inner wall of the hole gets a normal pointing *outward* from the
        # world origin, so the hole lights up backwards.
        from vibestorm.viewer3d.meshes import torus_mesh

        inward_facing = 0
        for position, normal in self._pairs("torus", torus_mesh):
            x, y, _z = position
            radial = (x * x + y * y) ** 0.5
            # Dot the normal with the outward direction in the XY plane.
            if radial > 1e-8:
                outward = (x / radial, y / radial)
                if normal[0] * outward[0] + normal[1] * outward[1] < -0.5:
                    inward_facing += 1

        self.assertGreater(
            inward_facing, 0, "no torus vertex faces the hole; normals are radial"
        )

    def test_avatar_parts_do_not_share_one_radial_gradient(self) -> None:
        # The renderer's fallback normal is normalize(position), which on a
        # merged figure points every vertex away from the figure's centre and
        # smears the whole thing into one smooth plank. The tell is a normal
        # that points *back* towards the centre -- the inside of a thigh, the
        # sole of a shoe, the underside of a chin -- which the fallback can
        # never produce, for any mesh, at any vertex.
        from vibestorm.viewer3d.meshes import avatar_placeholder_mesh, avatar_placeholder_normals

        verts, _indices = avatar_placeholder_mesh()
        normals = avatar_placeholder_normals()
        self.assertEqual(len(normals), len(verts))

        inward = 0
        counted = 0
        for index in range(0, len(verts), 3):
            position = verts[index : index + 3]
            length = math.sqrt(sum(c * c for c in position))
            if length < 1e-9:
                continue
            counted += 1
            radial = sum(
                position[axis] * normals[index + axis] for axis in range(3)
            ) / length
            if radial < 0.0:
                inward += 1

        self.assertGreater(counted, 100)
        self.assertGreater(
            inward / counted,
            0.25,
            "hardly any avatar normal faces inward, which is what the "
            "position fallback would produce",
        )

    def test_prism_side_normals_point_out_of_their_faces(self) -> None:
        from vibestorm.viewer3d.meshes import prism_mesh

        for position, normal in self._pairs("prism", prism_mesh):
            if abs(normal[2]) > 1e-6:
                continue  # a cap
            x, y, _z = position
            self.assertGreater(
                x * normal[0] + y * normal[1], 0.0, "prism side normal points inward"
            )


class SLFaceMapTests(unittest.TestCase):
    """The face maps must partition a mesh and land on the right geometry.

    A texture applied to the wrong side of a prim is a silent failure — it
    still renders, just wrongly — so these pin each SL face to the plane it
    is supposed to occupy rather than only checking that a map exists.
    """

    def _face_vertices(self, verts, face_indices):
        return [
            (verts[i * 3], verts[i * 3 + 1], verts[i * 3 + 2])
            for i in face_indices
        ]

    def _assert_partitions(self, indices, face_map) -> None:
        covered = [i for face in sorted(face_map) for i in face_map[face]]
        self.assertEqual(
            len(covered), len(indices), "face map does not cover every triangle exactly once"
        )
        self.assertEqual(sorted(covered), sorted(indices))

    def test_cube_faces_partition_the_mesh(self) -> None:
        from vibestorm.viewer3d.meshes import cube_face_indices, cube_mesh

        _, indices = cube_mesh()
        self._assert_partitions(indices, cube_face_indices())

    def test_cube_sl_faces_land_on_the_expected_planes(self) -> None:
        from vibestorm.viewer3d.meshes import cube_face_indices, cube_mesh

        verts, _ = cube_mesh()
        face_map = cube_face_indices()
        # SL box numbering: 0=+X, 1=+Y, 2=-X, 3=-Y, 4=top(+Z), 5=bottom(-Z).
        expectations = {0: (0, 0.5), 1: (1, 0.5), 2: (0, -0.5), 3: (1, -0.5),
                        4: (2, 0.5), 5: (2, -0.5)}
        for face, (axis, value) in expectations.items():
            coords = {
                round(vertex[axis], 6)
                for vertex in self._face_vertices(verts, face_map[face])
            }
            self.assertEqual(
                coords, {value}, f"SL face {face} is not the axis-{axis}={value} plane"
            )

    def test_cylinder_faces_partition_the_mesh(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_face_indices, cylinder_mesh

        _, indices = cylinder_mesh()
        self._assert_partitions(indices, cylinder_face_indices())

    def test_cylinder_caps_are_flat_and_the_side_spans_both(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_face_indices, cylinder_mesh

        verts, _ = cylinder_mesh()
        face_map = cylinder_face_indices()

        top_z = {round(v[2], 6) for v in self._face_vertices(verts, face_map[1])}
        bottom_z = {round(v[2], 6) for v in self._face_vertices(verts, face_map[2])}
        side_z = {round(v[2], 6) for v in self._face_vertices(verts, face_map[0])}

        self.assertEqual(top_z, {0.5}, "SL face 1 must be the top cap")
        self.assertEqual(bottom_z, {-0.5}, "SL face 2 must be the bottom cap")
        self.assertEqual(side_z, {-0.5, 0.5}, "SL face 0 must be the curved side")

    def test_cylinder_face_map_follows_the_slice_count(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_face_indices, cylinder_mesh

        _, indices = cylinder_mesh(7)
        self._assert_partitions(indices, cylinder_face_indices(7))

    def test_prism_faces_partition_the_mesh(self) -> None:
        from vibestorm.viewer3d.meshes import prism_face_indices, prism_mesh

        _, indices = prism_mesh()
        self._assert_partitions(indices, prism_face_indices())

    def test_prism_caps_are_the_triangles(self) -> None:
        from vibestorm.viewer3d.meshes import prism_face_indices, prism_mesh

        verts, _ = prism_mesh()
        face_map = prism_face_indices()

        self.assertEqual(
            {round(v[2], 6) for v in self._face_vertices(verts, face_map[3])}, {0.5}
        )
        self.assertEqual(
            {round(v[2], 6) for v in self._face_vertices(verts, face_map[4])}, {-0.5}
        )
        for side in (0, 1, 2):
            self.assertEqual(
                {round(v[2], 6) for v in self._face_vertices(verts, face_map[side])},
                {-0.5, 0.5},
                f"SL face {side} should be a side quad",
            )

    def test_prism_face_zero_is_the_plus_x_side(self) -> None:
        # Derived, not observed live: face 0 is the side whose outward normal
        # points most toward +X, matching where the box map starts.
        from vibestorm.viewer3d.meshes import prism_face_indices, prism_mesh

        verts, _ = prism_mesh()
        face_map = prism_face_indices()
        centroids = {
            face: sum(v[0] for v in self._face_vertices(verts, face_map[face]))
            / len(face_map[face])
            for face in (0, 1, 2)
        }

        self.assertEqual(max(centroids, key=centroids.__getitem__), 0)

    def test_face_counts_match_the_maps(self) -> None:
        from vibestorm.viewer3d.meshes import SL_FACE_COUNTS, shape_face_indices

        for shape_key, count in SL_FACE_COUNTS.items():
            face_map = shape_face_indices(shape_key)
            if count == 1:
                self.assertIsNone(
                    face_map, f"{shape_key} is single-face and needs no map"
                )
            else:
                self.assertEqual(sorted(face_map), list(range(count)))

    def test_single_face_and_unknown_shapes_have_no_map(self) -> None:
        from vibestorm.viewer3d.meshes import shape_face_indices

        for shape_key in ("sphere", "torus", "avatar", "not-a-shape"):
            self.assertIsNone(shape_face_indices(shape_key))


if __name__ == "__main__":
    unittest.main()
