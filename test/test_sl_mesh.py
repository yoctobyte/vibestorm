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


def _mesh_asset() -> bytes:
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
    submesh = _llsd_map(
        {
            "Position": _llsd_binary(positions),
            "PositionDomain": _llsd_map(
                {"Min": _vec3(-0.5, -0.5, 0.0), "Max": _vec3(0.5, 0.5, 0.0)}
            ),
            "TriangleList": _llsd_binary(triangles),
        }
    )
    lod = _llsd_array([submesh])
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


if __name__ == "__main__":
    unittest.main()
