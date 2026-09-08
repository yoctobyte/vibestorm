import gzip
import math
import struct
import unittest
import zlib

from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

NAN = float("nan")
INF = float("inf")


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

    def test_has_authored_uvs_distinguishes_missing_from_zero(self) -> None:
        # uvs is zero-filled when TexCoord0 is absent, so length alone cannot
        # tell an authored (0, 0) from a missing array. Renderers need the
        # difference: sampling a whole mesh at one texel looks far worse than
        # falling back to generated coordinates.
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        authored = decode_sl_mesh_asset(
            _mesh_asset([_triangle_submesh(with_uvs=True)])
        )
        missing = decode_sl_mesh_asset(_mesh_asset([_triangle_submesh()]))

        self.assertTrue(authored.has_authored_uvs)
        self.assertFalse(missing.has_authored_uvs)
        # Both still expose a full-length uvs array.
        self.assertEqual(len(authored.uvs), 6)
        self.assertEqual(len(missing.uvs), 6)

    def test_has_authored_uvs_requires_every_submesh(self) -> None:
        # A partially textured mesh cannot be trusted to the authored path.
        from vibestorm.assets.sl_mesh import decode_sl_mesh_asset

        decoded = decode_sl_mesh_asset(
            _mesh_asset([_triangle_submesh(with_uvs=True), _triangle_submesh()])
        )

        self.assertFalse(decoded.has_authored_uvs)

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


class MeshBombTests(unittest.TestCase):
    """A mesh block states nothing about how big it will be until it is.

    `decode_sl_mesh_asset` bounds the *compressed* block -- it has to lie
    inside the asset -- and nothing bounded what came out of it. Deflate's
    ceiling is about 1,029 to 1, measured on this machine: 510 kB of
    compressible bytes inflate to 524 MB in 2.3 seconds, and five megabytes
    to five gigabytes, on the render thread, from an asset any object owner
    chooses. There is no upstream guard to fall back on here the way the J2K
    path has Pillow's -- `zlib.decompress` has no output bound at all.
    """

    @staticmethod
    def _bomb(uncompressed_bytes: int) -> bytes:
        """A block that inflates to `uncompressed_bytes` of zeros."""
        return zlib.compress(b"\x00" * uncompressed_bytes, 9)

    def test_a_block_that_inflates_past_the_bound_is_refused(self) -> None:
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        with self.assertRaises(SLMeshDecodeError) as caught:
            _decompress_mesh_block(self._bomb(4096), max_bytes=1024)
        self.assertIn("inflates past", str(caught.exception))

    def test_a_block_that_fits_is_not(self) -> None:
        """The control, and it has to be near the bound to mean anything."""
        from vibestorm.assets.sl_mesh import _decompress_mesh_block

        self.assertEqual(len(_decompress_mesh_block(self._bomb(1024), max_bytes=1024)), 1024)

    def test_the_bound_is_on_the_output_and_not_the_input(self) -> None:
        """Which is the whole point: 26 bytes in, four kilobytes out.

        A guard on the compressed size would have passed this, and a check on
        `len(result)` afterwards would have allocated it before looking.
        """
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        blob = self._bomb(4096)
        self.assertLess(len(blob), 100)
        with self.assertRaises(SLMeshDecodeError):
            _decompress_mesh_block(blob, max_bytes=1024)

    def test_no_framing_is_tried_after_the_bound_fires(self) -> None:
        """Falling through would inflate the same bomb again by another route.

        Three framings are tried in turn, and a block that fails on a *header*
        costs nothing to reject -- these bytes carry zlib framing, so the gzip
        attempt is thrown out before a byte is inflated and the zlib attempt
        is the one that hits the bound. What must not happen is the third
        attempt: raw-deflate-after-a-zlib-header would inflate the very same
        payload a second time, doubling the damage done by a block already
        refused.

        Which is why this counts *which* framings were reached rather than how
        many. The first version of it asserted one call and failed on two,
        because a header rejection is not an inflation and the assertion had
        confused the two.
        """
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        reached = []
        real = zlib.decompressobj

        def counting(wbits=zlib.MAX_WBITS, *args, **kwargs):
            reached.append(wbits)
            return real(wbits, *args, **kwargs)

        zlib.decompressobj = counting
        try:
            with self.assertRaises(SLMeshDecodeError) as caught:
                _decompress_mesh_block(self._bomb(4096), max_bytes=1024)
        finally:
            zlib.decompressobj = real
        self.assertIn("inflates past", str(caught.exception))
        self.assertIn(zlib.MAX_WBITS, reached, "the zlib framing was never tried")
        self.assertNotIn(
            -zlib.MAX_WBITS, reached, "kept going and inflated the bomb a second time"
        )

    def test_garbage_still_reports_every_framing_it_tried(self) -> None:
        """The pair to it: a block that is not compressed at all is not a bomb."""
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        with self.assertRaises(SLMeshDecodeError) as caught:
            _decompress_mesh_block(b"not compressed at all, by any framing")
        message = str(caught.exception)
        self.assertIn("decompression failed", message)
        self.assertIn("gzip", message)
        self.assertIn("zlib", message)

    def test_the_real_bound_is_far_above_any_real_mesh_and_still_a_bound(self) -> None:
        """Both halves, because either one alone is satisfied by a bad answer.

        A guard that fires on ordinary content gets raised until it fires on
        nothing, so the floor is here. And a "bound" of a billion gigabytes
        passes any floor while bounding nothing, so the ceiling is here too --
        a mutation battery raised this to 2^60 and only the floor was checked,
        which the battery survived.
        """
        from vibestorm.assets.sl_mesh import MAX_MESH_BLOCK_BYTES, decode_sl_mesh_asset

        self.assertGreaterEqual(MAX_MESH_BLOCK_BYTES, 16 * 1024 * 1024)
        self.assertLessEqual(MAX_MESH_BLOCK_BYTES, 256 * 1024 * 1024)
        # And the ordinary path still goes through it untouched.
        decoded = decode_sl_mesh_asset(_mesh_asset())
        self.assertEqual(len(decoded.vertices), 9)

    def test_every_framing_the_decoder_offers_actually_works(self) -> None:
        """Three framings were accepted and one of them was tested.

        The battery found it: swapping gzip's window bits for zlib's, and
        dropping the two-byte skip from the raw-deflate case, both survived
        the whole file. A branch nothing exercises is a branch that will be
        wrong the first time somebody's mesh needs it -- and which framing a
        given uploader used is not this client's choice.
        """
        from vibestorm.assets.sl_mesh import _decompress_mesh_block

        body = b"mesh bytes, framed three ways" * 8

        gzipped = gzip.compress(body)
        self.assertEqual(_decompress_mesh_block(gzipped), body)

        zlibbed = zlib.compress(body)
        self.assertEqual(_decompress_mesh_block(zlibbed), body)

        # Raw deflate behind two bytes that are not a zlib header, so the
        # zlib attempt above it fails and this branch is the one that answers.
        # A real `\x78\x9c` prefix would not test it at all -- that is a
        # valid zlib stream and the previous framing swallows it, which is how
        # the first version of this test passed while the branch stayed dead.
        raw = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        headered = b"\x00\x00" + raw.compress(body) + raw.flush()
        with self.assertRaises(Exception):
            zlib.decompress(headered)
        self.assertEqual(_decompress_mesh_block(headered), body)

    def test_the_bound_holds_for_every_framing_and_not_just_zlib(self) -> None:
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        for label, blob in (
            ("gzip", gzip.compress(b"\x00" * 4096)),
            ("zlib", zlib.compress(b"\x00" * 4096, 9)),
        ):
            with self.subTest(framing=label):
                with self.assertRaises(SLMeshDecodeError) as caught:
                    _decompress_mesh_block(blob, max_bytes=1024)
                self.assertIn("inflates past", str(caught.exception))

    def test_what_comes_back_never_exceeds_the_bound(self) -> None:
        """The property, over five orders of magnitude of expansion.

        The tail check is what enforces the limit today. This says the thing
        that actually matters, which is about the answer rather than about the
        mechanism: whatever zlib does internally, nothing longer than the
        bound is ever returned.
        """
        from vibestorm.assets.sl_mesh import SLMeshDecodeError, _decompress_mesh_block

        for size in (512, 4096, 65_536, 1_048_576, 16_777_216):
            for bound in (1024, 100_000):
                with self.subTest(size=size, bound=bound):
                    blob = zlib.compress(b"\x00" * size, 9)
                    try:
                        out = _decompress_mesh_block(blob, max_bytes=bound)
                    except SLMeshDecodeError:
                        continue
                    self.assertLessEqual(len(out), bound)
                    self.assertEqual(len(out), size)


class NonFiniteDomainTests(unittest.TestCase):
    """A bounding box that is not a box.

    `PositionDomain` and `TexCoord0Domain` are LLSD reals off the wire, and
    every vertex in the submesh is decoded by scaling a `u16` between those
    two corners -- so a NaN corner is not one bad vertex, it is every vertex
    in the mesh, and the mesh goes to the graphics card. `_as_vec3` and
    `_as_vec2` fall back to the default box instead, which draws the object
    at the wrong size rather than nowhere at all.

    Pinned here rather than left to the mutation corpus in
    `test_asset_decoder_fuzz.py`: that corpus flips bytes, and the eight
    bytes of a domain corner are a small target. Both guards survived it.
    """

    def _mesh_with_domain(self, corner: bytes, *, key: str = "PositionDomain") -> bytes:
        submesh = _triangle_submesh(with_uvs=True)
        submesh[key] = _llsd_map({"Min": corner, "Max": corner})
        return _mesh_asset([submesh])

    def _all_finite(self, values) -> bool:
        return all(math.isfinite(float(value)) for value in values)

    def test_a_nan_position_domain_does_not_reach_the_vertices(self) -> None:
        mesh = decode_sl_mesh_asset(self._mesh_with_domain(_vec3(NAN, NAN, NAN)))
        self.assertTrue(self._all_finite(mesh.vertices))

    def test_an_infinite_position_domain_does_not_either(self) -> None:
        mesh = decode_sl_mesh_asset(self._mesh_with_domain(_vec3(INF, -INF, INF)))
        self.assertTrue(self._all_finite(mesh.vertices))

    def test_a_nan_texcoord_domain_does_not_reach_the_uvs(self) -> None:
        mesh = decode_sl_mesh_asset(
            self._mesh_with_domain(_vec2(NAN, NAN), key="TexCoord0Domain")
        )
        self.assertTrue(self._all_finite(mesh.uvs))

    def test_a_real_domain_is_still_used(self) -> None:
        """The control. A fallback that ignored the domain would pass all
        three tests above and put every mesh in the world in a unit box."""
        submesh = _triangle_submesh()
        submesh["PositionDomain"] = _llsd_map(
            {"Min": _vec3(-4.0, -4.0, -4.0), "Max": _vec3(4.0, 4.0, 4.0)}
        )
        mesh = decode_sl_mesh_asset(_mesh_asset([submesh]))
        self.assertEqual(min(mesh.vertices), -4.0)
        self.assertEqual(max(mesh.vertices), 4.0)
