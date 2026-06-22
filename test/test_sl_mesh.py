import struct
import unittest
import zlib


def _llsd_int(value: int) -> bytes:
    return b"i" + struct.pack(">i", value)


def _llsd_real(value: float) -> bytes:
    return b"r" + struct.pack(">d", value)


def _llsd_binary(value: bytes) -> bytes:
    return b"b" + struct.pack(">i", len(value)) + value


def _llsd_array(values: list[bytes]) -> bytes:
    return b"[" + struct.pack(">i", len(values)) + b"".join(values) + b"]"


def _llsd_map(values: dict[str, bytes]) -> bytes:
    body = bytearray()
    for key, value in values.items():
        encoded_key = key.encode("utf-8")
        body.extend(b"k" + struct.pack(">i", len(encoded_key)) + encoded_key)
        body.extend(value)
    return b"{" + struct.pack(">i", len(values)) + bytes(body) + b"}"


def _vec3(x: float, y: float, z: float) -> bytes:
    return _llsd_array([_llsd_real(x), _llsd_real(y), _llsd_real(z)])


def _vec2(u: float, v: float) -> bytes:
    return _llsd_array([_llsd_real(u), _llsd_real(v)])


def _triangle_submesh(*, with_normals: bool = False, with_uvs: bool = False) -> dict[str, bytes]:
    positions = struct.pack(
        "<HHHHHHHHH",
        0,
        0,
        0,
        65535,
        0,
        0,
        0,
        65535,
        0,
    )
    triangles = struct.pack("<HHH", 0, 1, 2)
    fields = {
        "Position": _llsd_binary(positions),
        "PositionDomain": _llsd_map(
            {"Min": _vec3(-0.5, -0.5, 0.0), "Max": _vec3(0.5, 0.5, 0.0)}
        ),
        "TriangleList": _llsd_binary(triangles),
    }
    if with_normals:
        # All three vertices point +Z: 65535 maps to +1 on the z axis,
        # 32767/32768 map to ~0 on x/y.
        normals = struct.pack("<HHHHHHHHH", 32767, 32767, 65535, 32767, 32767, 65535, 32767, 32767, 65535)
        fields["Normal"] = _llsd_binary(normals)
    if with_uvs:
        uvs = struct.pack("<HHHHHH", 0, 0, 65535, 0, 0, 65535)
        fields["TexCoord0"] = _llsd_binary(uvs)
    return fields


def _mesh_asset(submeshes: list[dict[str, bytes]] | None = None) -> bytes:
    if submeshes is None:
        submeshes = [_triangle_submesh()]
    lod = _llsd_array([_llsd_map(sub) for sub in submeshes])
    compressed = zlib.compress(lod)
    header = _llsd_map({"high_lod": _llsd_map({"offset": _llsd_int(0), "size": _llsd_int(len(compressed))})})
    return header + compressed


class BinaryLLSDTests(unittest.TestCase):
    def test_parse_binary_llsd_map(self) -> None:
        from vibestorm.assets.sl_mesh import parse_binary_llsd

        value, consumed = parse_binary_llsd(_llsd_map({"answer": _llsd_int(42)}))

        self.assertEqual(value, {"answer": 42})
        self.assertEqual(consumed, len(_llsd_map({"answer": _llsd_int(42)})))


class SLMeshDecodeTests(unittest.TestCase):
    def test_decode_high_lod_triangle(self) -> None:
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        decoded = decode_sl_mesh_asset(_mesh_asset())

        self.assertEqual(decoded.submesh_count, 1)
        self.assertEqual(decoded.indices, (0, 1, 2))
        self.assertEqual(len(decoded.vertices), 9)
        self.assertAlmostEqual(decoded.vertices[0], -0.5, places=5)
        self.assertAlmostEqual(decoded.vertices[1], -0.5, places=5)
        self.assertAlmostEqual(decoded.vertices[3], 0.5, places=5)
        self.assertAlmostEqual(decoded.vertices[7], 0.5, places=5)

    def test_decode_rejects_missing_lod(self) -> None:
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, decode_sl_mesh_asset

        with self.assertRaises(SLMeshDecodeError):
            decode_sl_mesh_asset(_llsd_map({}))

    def test_decode_normals_and_uvs(self) -> None:
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        decoded = decode_sl_mesh_asset(
            _mesh_asset([_triangle_submesh(with_normals=True, with_uvs=True)])
        )

        self.assertEqual(len(decoded.normals), 9)
        # Each vertex normal points +Z.
        for v in range(3):
            self.assertAlmostEqual(decoded.normals[v * 3 + 2], 1.0, places=4)
            self.assertAlmostEqual(decoded.normals[v * 3], 0.0, places=4)
        self.assertEqual(len(decoded.uvs), 6)
        self.assertAlmostEqual(decoded.uvs[0], 0.0, places=4)
        self.assertAlmostEqual(decoded.uvs[2], 1.0, places=4)

    def test_decode_computes_normals_when_absent(self) -> None:
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        decoded = decode_sl_mesh_asset(_mesh_asset())

        # Flat triangle in the z=0 plane → unit normal on z axis.
        self.assertEqual(len(decoded.normals), 9)
        for v in range(3):
            self.assertAlmostEqual(abs(decoded.normals[v * 3 + 2]), 1.0, places=4)

    def test_material_groups_track_submeshes(self) -> None:
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        decoded = decode_sl_mesh_asset(
            _mesh_asset([_triangle_submesh(), _triangle_submesh()])
        )

        self.assertEqual(decoded.submesh_count, 2)
        self.assertEqual(len(decoded.material_groups), 2)
        first, second = decoded.material_groups
        self.assertEqual((first.face_index, first.index_start, first.index_count), (0, 0, 3))
        self.assertEqual((second.face_index, second.index_start, second.index_count), (1, 3, 3))
        # Second submesh indices are rebased onto the combined vertex buffer.
        self.assertEqual(decoded.indices, (0, 1, 2, 3, 4, 5))


if __name__ == "__main__":
    unittest.main()
