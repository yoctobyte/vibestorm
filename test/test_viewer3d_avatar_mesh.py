"""The humanoid avatars are drawn as.

The properties worth pinning are the ones whose failure is quiet. A body part
built inside out still renders -- as a hole in the figure that only shows up
from one angle. A part authored a few centimetres too large still renders --
poking through the avatar's own bounding box, where the renderer's scale says
it cannot be. And a palette coordinate landing on a texel edge picks whichever
neighbour the driver rounds towards, which is a wrong-coloured hand on some
machines and not others.
"""

from __future__ import annotations

import math
import unittest

from vibestorm.viewer3d.avatar_mesh import (
    AVATAR_NOMINAL_SCALE,
    AVATAR_PARTS,
    PALETTE,
    avatar_geometry,
    palette_texture,
    palette_uv,
)


def _vertices_in_metres() -> list[tuple[float, float, float]]:
    verts, _normals, _uvs, _indices = avatar_geometry()
    scale_x, scale_y, scale_z = AVATAR_NOMINAL_SCALE
    return [
        (verts[i] * scale_x, verts[i + 1] * scale_y, verts[i + 2] * scale_z)
        for i in range(0, len(verts), 3)
    ]


def _vertices_of_region(region: str) -> list[tuple[float, float, float]]:
    """The metre-space vertices whose palette coordinate names ``region``."""
    _verts, _normals, uvs, _indices = avatar_geometry()
    (width, _height), _data = palette_texture()
    wanted = int(palette_uv(region)[0] * width)
    return [
        point
        for index, point in enumerate(_vertices_in_metres())
        if int(uvs[index * 2] * width) == wanted
    ]


class AvatarGeometryTests(unittest.TestCase):
    def test_the_attribute_arrays_line_up(self) -> None:
        verts, normals, uvs, indices = avatar_geometry()

        count = len(verts) // 3
        self.assertEqual(len(normals), count * 3)
        self.assertEqual(len(uvs), count * 2)
        self.assertEqual(len(indices) % 3, 0)
        self.assertLess(max(indices), count)

    def test_it_fits_the_unit_cube_the_renderer_scales(self) -> None:
        # The model matrix multiplies this mesh by the avatar's ObjectUpdate
        # scale, which *is* the avatar's bounding box. A part outside
        # [-0.5, 0.5] here is a part outside the avatar in world, which is
        # exactly the kind of thing that reads as a rendering glitch rather
        # than as a mesh mistake.
        verts, _normals, _uvs, _indices = avatar_geometry()

        for axis in range(3):
            extent = max(abs(verts[i]) for i in range(axis, len(verts), 3))
            self.assertLessEqual(extent, 0.5 + 1e-9, f"axis {axis} pokes out at {extent}")

    def test_it_is_about_the_size_of_a_person(self) -> None:
        # The point of authoring in metres is that the numbers in the part
        # table mean what they say once the nominal scale is divided out. If
        # that ever stops being true the figure silently becomes a plank
        # again, which is what it was.
        points = _vertices_in_metres()
        height = max(p[2] for p in points) - min(p[2] for p in points)
        width = max(p[1] for p in points) - min(p[1] for p in points)
        depth = max(p[0] for p in points) - min(p[0] for p in points)

        self.assertAlmostEqual(height, 1.89, delta=0.05)
        self.assertGreater(width, 0.45)
        self.assertLess(width, 0.75)
        self.assertGreater(depth, 0.2)
        self.assertLess(depth, 0.45)

    def test_every_triangle_is_wound_to_match_its_own_normal(self) -> None:
        # Back-face culling and lighting both read the winding. A part wound
        # the wrong way is invisible from outside and lit from within, and it
        # is only visible in a screenshot from the one angle that catches it --
        # which is how the caps of a tube and the stacks of an ellipsoid,
        # each derived by hand, would otherwise get away with being reversed.
        verts, normals, _uvs, indices = avatar_geometry()
        scale = AVATAR_NOMINAL_SCALE

        wrong = []
        for triangle in range(0, len(indices), 3):
            corners = indices[triangle : triangle + 3]
            points = [
                tuple(verts[c * 3 + axis] * scale[axis] for axis in range(3))
                for c in corners
            ]
            # Normals were authored in metres and multiplied by the scale so
            # the shader's inverse-transpose recovers them; undo that here to
            # compare against a geometric normal taken in metres.
            authored = [
                tuple(normals[c * 3 + axis] / scale[axis] for axis in range(3))
                for c in corners
            ]
            edge_a = [points[1][axis] - points[0][axis] for axis in range(3)]
            edge_b = [points[2][axis] - points[0][axis] for axis in range(3)]
            geometric = (
                edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
                edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
                edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
            )
            length = math.sqrt(sum(c * c for c in geometric))
            if length < 1e-12:
                continue  # degenerate sliver; it shades nothing either way
            average = [sum(n[axis] for n in authored) / 3.0 for axis in range(3)]
            agreement = sum(
                geometric[axis] / length * average[axis] for axis in range(3)
            )
            if agreement <= 0.0:
                wrong.append(triangle // 3)

        self.assertEqual(wrong, [], f"{len(wrong)} triangles are wound inside out")

    def test_normals_are_unit_length(self) -> None:
        _verts, normals, _uvs, _indices = avatar_geometry()

        for index in range(0, len(normals), 3):
            length = math.sqrt(sum(c * c for c in normals[index : index + 3]))
            self.assertAlmostEqual(length, 1.0, places=5)

    def test_the_face_is_on_the_front_of_the_head(self) -> None:
        # Facing has to be readable, and a bare ellipsoid head is identical
        # coming and going: a viewer cannot tell whether an avatar is walking
        # towards them or away. The eyes are the cue that breaks the tie, so
        # they belong on the front of the head and nowhere else. The extents
        # cannot say this -- the hair reaches as far back as the nose reaches
        # forward -- so ask where the eye-coloured geometry actually is.
        eyes = _vertices_of_region("eye")

        self.assertTrue(eyes, "the figure has no eyes")
        for point in eyes:
            self.assertGreater(point[0], 0.0, "an eye is behind the head's centre")
            self.assertGreater(point[2], 0.6, "an eye is not on the head")

    def test_the_hair_sits_behind_the_face(self) -> None:
        hair = _vertices_of_region("hair")
        skin = [p for p in _vertices_of_region("skin") if p[2] > 0.6]

        self.assertTrue(hair)
        self.assertTrue(skin)
        self.assertLess(
            sum(p[0] for p in hair) / len(hair),
            sum(p[0] for p in skin) / len(skin),
            "the hair is not set back from the face",
        )

    def test_it_is_cached(self) -> None:
        self.assertIs(avatar_geometry(), avatar_geometry())


class AvatarPaletteTests(unittest.TestCase):
    def test_every_part_names_a_palette_entry(self) -> None:
        known = {name for name, _rgb in PALETTE}

        for part in AVATAR_PARTS:
            self.assertIn(part.region, known)

    def test_every_part_names_a_shape_the_builder_knows(self) -> None:
        for part in AVATAR_PARTS:
            self.assertIn(part.kind, {"box", "tube", "ellipsoid"})

    def test_each_coordinate_lands_in_the_middle_of_its_own_texel(self) -> None:
        # Nearest filtering picks floor(u * width). Landing on a boundary makes
        # the choice a rounding accident, and a rounding accident here is a
        # hand painted the colour of a shirt on some drivers and not others.
        (width, _height), _data = palette_texture()

        for index, (name, _rgb) in enumerate(PALETTE):
            u, v = palette_uv(name)
            self.assertEqual(int(u * width), index)
            self.assertAlmostEqual((u * width) % 1.0, 0.5, places=6)
            self.assertAlmostEqual(v, 0.5)

    def test_the_texture_is_one_rgb_texel_per_entry(self) -> None:
        (width, height), data = palette_texture()

        self.assertEqual((width, height), (len(PALETTE), 1))
        self.assertEqual(len(data), len(PALETTE) * 3)
        for index, (_name, rgb) in enumerate(PALETTE):
            self.assertEqual(tuple(data[index * 3 : index * 3 + 3]), rgb)

    def test_the_mesh_only_ever_points_at_a_real_texel(self) -> None:
        _verts, _normals, uvs, _indices = avatar_geometry()
        (width, _height), _data = palette_texture()

        used = {int(uvs[index] * width) for index in range(0, len(uvs), 2)}

        self.assertTrue(used)
        for texel in used:
            self.assertGreaterEqual(texel, 0)
            self.assertLess(texel, width)


class MeshModuleHandoffTests(unittest.TestCase):
    """``meshes`` is what the renderer asks; this module is what answers."""

    def test_meshes_serves_the_avatar_uvs(self) -> None:
        from vibestorm.viewer3d import meshes

        verts, _indices = meshes.avatar_placeholder_mesh()
        uvs = meshes.shape_uvs("avatar")

        self.assertIsNotNone(uvs)
        assert uvs is not None
        self.assertEqual(len(uvs), (len(verts) // 3) * 2)

    def test_primitives_author_no_uvs(self) -> None:
        # They are textured by the shader's position-and-normal projection.
        # Claiming authored coordinates they do not have would switch that off
        # and map every prim face to (0, 0) -- one flat colour per texture.
        from vibestorm.viewer3d import meshes

        for shape_key in ("cube", "sphere", "cylinder", "torus", "prism"):
            self.assertIsNone(meshes.shape_uvs(shape_key))


if __name__ == "__main__":
    unittest.main()
