"""Tests for the static mesh authors (step 7a).

Pure-Python — no GL needed. Verifies counts and basic geometric
invariants (centred at origin, fits in a 1 m unit cube) for each
helper. The actual GL upload + per-shape dispatch lands in step 7b
and is tested via a real GL context.
"""

import unittest


def _xyz_iter(verts: tuple[float, ...]):
    assert len(verts) % 3 == 0, "vertex tuple must be a flat run of x,y,z"
    for i in range(0, len(verts), 3):
        yield verts[i], verts[i + 1], verts[i + 2]


class CubeMeshTests(unittest.TestCase):
    def test_cube_has_8_vertices_and_36_indices(self) -> None:
        from vibestorm.viewer3d.meshes import cube_mesh

        verts, indices = cube_mesh()

        self.assertEqual(len(verts), 8 * 3)
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
    def test_default_count_matches_capped_cylinder(self) -> None:
        from vibestorm.viewer3d.meshes import cylinder_mesh

        slices = 12
        verts, indices = cylinder_mesh(slices=slices)

        # 2 cap centres + 2 rings of ``slices`` verts.
        self.assertEqual(len(verts) // 3, 2 + 2 * slices)
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
    def test_six_vertices_eight_triangles(self) -> None:
        from vibestorm.viewer3d.meshes import prism_mesh

        verts, indices = prism_mesh()
        self.assertEqual(len(verts) // 3, 6)
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


if __name__ == "__main__":
    unittest.main()
