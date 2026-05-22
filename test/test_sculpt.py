import unittest


def _rgb_grid(width: int, height: int) -> bytes:
    pixels = bytearray()
    for row in range(height):
        for col in range(width):
            pixels.extend((col * 255 // (width - 1), row * 255 // (height - 1), 128))
    return bytes(pixels)


class SculptMeshTests(unittest.TestCase):
    def test_plane_mesh_uses_open_grid(self) -> None:
        from vibestorm.assets.sculpt import SCULPT_TYPE_PLANE, sculpt_mesh_from_rgb

        mesh = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_PLANE,
        )

        self.assertEqual(len(mesh.vertices), 3 * 3 * 3)
        self.assertEqual(len(mesh.indices), 2 * 2 * 6)
        self.assertAlmostEqual(mesh.vertices[0], -0.5, places=5)
        self.assertAlmostEqual(mesh.vertices[1], -0.5, places=5)

    def test_torus_wraps_both_axes(self) -> None:
        from vibestorm.assets.sculpt import SCULPT_TYPE_TORUS, sculpt_mesh_from_rgb

        mesh = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_TORUS,
        )

        self.assertEqual(len(mesh.indices), 3 * 3 * 6)

    def test_sphere_wraps_only_horizontal_axis(self) -> None:
        from vibestorm.assets.sculpt import SCULPT_TYPE_SPHERE, sculpt_mesh_from_rgb

        mesh = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_SPHERE,
        )

        self.assertEqual(len(mesh.indices), 3 * 2 * 6)

    def test_sphere_converges_top_and_bottom_rows(self) -> None:
        from vibestorm.assets.sculpt import SCULPT_TYPE_SPHERE, sculpt_mesh_from_rgb

        mesh = sculpt_mesh_from_rgb(
            _rgb_grid(4, 4),
            width=4,
            height=4,
            sculpt_type=SCULPT_TYPE_SPHERE,
        )

        top_vertices = [mesh.vertices[col * 3 : col * 3 + 3] for col in range(4)]
        bottom_base = (4 * 3) * 3
        bottom_vertices = [
            mesh.vertices[bottom_base + col * 3 : bottom_base + col * 3 + 3]
            for col in range(4)
        ]
        self.assertEqual(len(set(top_vertices)), 1)
        self.assertEqual(len(set(bottom_vertices)), 1)

    def test_mirror_flag_flips_x_axis(self) -> None:
        from vibestorm.assets.sculpt import (
            SCULPT_FLAG_MIRROR,
            SCULPT_TYPE_PLANE,
            sculpt_mesh_from_rgb,
        )

        normal = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_PLANE,
        )
        mirrored = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_PLANE | SCULPT_FLAG_MIRROR,
        )

        self.assertAlmostEqual(mirrored.vertices[0], -normal.vertices[0], places=5)
        self.assertAlmostEqual(mirrored.vertices[3], -normal.vertices[3], places=5)

    def test_invert_flag_reverses_triangle_winding(self) -> None:
        from vibestorm.assets.sculpt import (
            SCULPT_FLAG_INVERT,
            SCULPT_TYPE_PLANE,
            sculpt_mesh_from_rgb,
        )

        normal = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_PLANE,
        )
        inverted = sculpt_mesh_from_rgb(
            _rgb_grid(3, 3),
            width=3,
            height=3,
            sculpt_type=SCULPT_TYPE_PLANE | SCULPT_FLAG_INVERT,
        )

        self.assertEqual(normal.indices[:6], (0, 1, 4, 0, 4, 3))
        self.assertEqual(inverted.indices[:6], (0, 4, 1, 0, 3, 4))

    def test_sampling_caps_large_maps(self) -> None:
        from vibestorm.assets.sculpt import SCULPT_TYPE_PLANE, sculpt_mesh_from_rgb

        mesh = sculpt_mesh_from_rgb(
            _rgb_grid(8, 8),
            width=8,
            height=8,
            sculpt_type=SCULPT_TYPE_PLANE,
            max_samples=4,
        )

        self.assertEqual(mesh.width, 4)
        self.assertEqual(mesh.height, 4)
        self.assertEqual(len(mesh.vertices), 4 * 4 * 3)

    def test_rejects_mismatched_pixel_bytes(self) -> None:
        from vibestorm.assets.sculpt import (
            SCULPT_TYPE_PLANE,
            SculptDecodeError,
            sculpt_mesh_from_rgb,
        )

        with self.assertRaises(SculptDecodeError):
            sculpt_mesh_from_rgb(b"\x00\x00\x00", width=3, height=3, sculpt_type=SCULPT_TYPE_PLANE)


if __name__ == "__main__":
    unittest.main()
