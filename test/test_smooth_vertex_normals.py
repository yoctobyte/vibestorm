"""Tests for geometry-derived vertex normals.

Used wherever a surface arrives without authored normals: an SL mesh submesh
that omits its Normal array, and every decoded sculpt (a sculpt map carries
only positions). The alternative the renderer falls back to,
normalize(position), is correct only for a surface centred on the origin —
which a sculpted torus, plane or cylinder is not.
"""

import math
import unittest

from vibestorm.assets.sl_mesh import smooth_vertex_normals


class SmoothVertexNormalTests(unittest.TestCase):
    def test_a_flat_triangle_gets_its_plane_normal(self) -> None:
        # Counter-clockwise in the XY plane seen from +Z.
        positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

        normals = smooth_vertex_normals(positions, [0, 1, 2])

        for i in range(3):
            self.assertAlmostEqual(normals[i * 3], 0.0, places=6)
            self.assertAlmostEqual(normals[i * 3 + 1], 0.0, places=6)
            self.assertAlmostEqual(normals[i * 3 + 2], 1.0, places=6)

    def test_winding_decides_the_direction(self) -> None:
        positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

        flipped = smooth_vertex_normals(positions, [0, 2, 1])

        self.assertAlmostEqual(flipped[2], -1.0, places=6)

    def test_every_normal_is_unit_length(self) -> None:
        positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.5, 1.0, 1.0, 0.0]

        normals = smooth_vertex_normals(positions, [0, 1, 2, 1, 3, 2])

        for i in range(0, len(normals), 3):
            nx, ny, nz = normals[i : i + 3]
            self.assertAlmostEqual(math.sqrt(nx * nx + ny * ny + nz * nz), 1.0, places=6)

    def test_a_shared_vertex_averages_its_faces(self) -> None:
        # Two right-triangles meeting at a right angle along the 0-1 edge.
        # Triangle (0,1,2) lies in XY with normal +Z; (0,3,1) lies in XZ with
        # normal +Y. The shared vertex must land between them, not on either.
        positions = [
            0.0, 0.0, 0.0,   # 0, shared
            1.0, 0.0, 0.0,   # 1, shared
            0.0, 1.0, 0.0,   # 2, in the XY plane
            0.0, 0.0, 1.0,   # 3, in the XZ plane
        ]

        normals = smooth_vertex_normals(positions, [0, 1, 2, 0, 3, 1])

        shared = normals[0:3]
        self.assertAlmostEqual(shared[1], math.sqrt(0.5), places=5)
        self.assertAlmostEqual(shared[2], math.sqrt(0.5), places=5)
        # Vertex 2 belongs to the XY triangle alone, so it keeps that normal.
        self.assertAlmostEqual(normals[8], 1.0, places=6)

    def test_larger_triangles_pull_harder(self) -> None:
        # The cross product is accumulated unnormalized on purpose, so a big
        # face influences a shared vertex more than a sliver does. Here a tiny
        # triangle in XY (normal +Z, area ~5e-5) shares vertex 0 with a large
        # one in XZ (normal +Y, area 12.5), so the result is essentially +Y.
        positions = [
            0.0, 0.0, 0.0,    # 0, shared
            0.01, 0.0, 0.0,   # 1, tiny
            0.0, 0.01, 0.0,   # 2, tiny
            5.0, 0.0, 0.0,    # 3, large
            0.0, 0.0, 5.0,    # 4, large
        ]

        normals = smooth_vertex_normals(positions, [0, 1, 2, 0, 4, 3])

        self.assertGreater(normals[1], 0.9)
        self.assertLess(abs(normals[2]), 0.1)

    def test_degenerate_geometry_falls_back_to_up(self) -> None:
        # A zero-area triangle contributes nothing, leaving an unset vertex.
        positions = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        normals = smooth_vertex_normals(positions, [0, 1, 2])

        self.assertEqual(list(normals[0:3]), [0.0, 0.0, 1.0])

    def test_out_of_range_indices_are_skipped_not_fatal(self) -> None:
        # A partially decoded asset should render approximately, not raise.
        positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]

        normals = smooth_vertex_normals(positions, [0, 1, 2, 0, 1, 99])

        self.assertEqual(len(normals), len(positions))

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(smooth_vertex_normals([], []), [])


if __name__ == "__main__":
    unittest.main()
