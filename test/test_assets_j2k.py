import io
import struct
import unittest

from vibestorm.assets.j2k import (
    MAX_TEXTURE_EDGE,
    MAX_TEXTURE_PIXELS,
    MAX_UPLOAD_TEXTURE_EDGE,
    J2KDecodeError,
    J2KEncodeError,
    decode_j2k,
    encode_j2k,
)


def _make_j2k_bytes(width: int, height: int, mode: str = "RGB") -> bytes:
    """Build a small JPEG2000 codestream via Pillow for round-trip testing."""
    from PIL import Image

    if mode == "RGB":
        pixels = bytes(((x * 3) % 256 for x in range(width * height * 3)))
    elif mode == "L":
        pixels = bytes((x % 256 for x in range(width * height)))
    elif mode == "RGBA":
        pixels = bytes(((x * 5) % 256 for x in range(width * height * 4)))
    else:
        raise ValueError(mode)

    image = Image.frombytes(mode, (width, height), pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG2000")
    return buffer.getvalue()


class DecodeJ2KTests(unittest.TestCase):
    def test_decode_round_trip_rgb(self) -> None:
        encoded = _make_j2k_bytes(32, 16, "RGB")
        decoded = decode_j2k(encoded)
        self.assertEqual(decoded.width, 32)
        self.assertEqual(decoded.height, 16)
        self.assertEqual(decoded.mode, "RGB")
        self.assertEqual(len(decoded.pixels), 32 * 16 * 3)

    def test_decode_round_trip_grayscale(self) -> None:
        encoded = _make_j2k_bytes(8, 8, "L")
        decoded = decode_j2k(encoded)
        self.assertEqual(decoded.width, 8)
        self.assertEqual(decoded.height, 8)
        self.assertEqual(decoded.mode, "L")
        self.assertEqual(len(decoded.pixels), 64)

    def test_decode_rejects_garbage_bytes(self) -> None:
        with self.assertRaises(J2KDecodeError):
            decode_j2k(b"not a jpeg2000 codestream at all")


if __name__ == "__main__":
    unittest.main()


def _claiming(width: int, height: int) -> bytes:
    """A four-by-four image whose header says it is `width` by `height`.

    The point of the whole exercise is that a decoder believes a header, and
    a header is cheap to write. Both places have to be patched: the JP2
    container's `ihdr` box, which is what Pillow reports as the size, and the
    codestream's SIZ marker, which is what the decoder would work from. Four
    by four of real pixels follows, so nothing here allocates anything --
    which is exactly the asymmetry being tested.
    """
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buffer, format="JPEG2000")
    data = bytearray(buffer.getvalue())
    header = data.find(b"ihdr")
    assert header > 0, "no ihdr box in Pillow's JPEG2000 output"
    struct.pack_into(">II", data, header + 4, height, width)  # HEIGHT then WIDTH
    siz = data.find(b"\xff\x51")
    assert siz > 0, "no SIZ marker in Pillow's JPEG2000 output"
    struct.pack_into(">II", data, siz + 6, width, height)  # Xsiz then Ysiz
    return bytes(data)


class OversizedRasterTests(unittest.TestCase):
    """A texture arrives from the grid, and states its own size.

    `Image.open` reads the header and stops, so the size is known before a
    byte is decoded -- and `load()` then allocates `width * height * bands`
    of whatever that header claimed. Four bytes a pixel makes an 8192-square
    header worth 268 MB, written by whoever sent it.
    """

    def test_a_header_claiming_more_than_the_budget_is_refused(self) -> None:
        with self.assertRaises(J2KDecodeError) as caught:
            decode_j2k(_claiming(8192, 8192))
        self.assertIn("too large", str(caught.exception))
        # Named in the message, because a reader who hits this needs to know
        # whether the texture is absurd or the budget is too tight.
        self.assertIn("8192x8192", str(caught.exception))

    def test_the_edge_of_the_budget_is_not_refused(self) -> None:
        """The control, and the reason the check is `>` and not `>=`.

        A raster of exactly the budget is allowed through the size check. It
        still fails, because these four pixels are not an 4096-square image --
        but it fails as a broken stream, which is the decoder's answer and not
        this guard's.
        """
        with self.assertRaises(J2KDecodeError) as caught:
            decode_j2k(_claiming(MAX_TEXTURE_EDGE, MAX_TEXTURE_EDGE))
        self.assertNotIn("too large", str(caught.exception))

    def test_and_one_edge_over_it_is(self) -> None:
        with self.assertRaises(J2KDecodeError) as caught:
            decode_j2k(_claiming(MAX_TEXTURE_EDGE + 1, MAX_TEXTURE_EDGE))
        self.assertIn("too large", str(caught.exception))

    def test_the_boundary_is_a_pixel_and_not_an_edge(self) -> None:
        """One pixel either side of the budget, on a raster one pixel tall.

        A square fixture cannot say this: 4097 by 4096 is four thousand pixels
        past the limit, so a check written a few thousand pixels loose passes
        it. A mutation battery found exactly that -- `> MAX_TEXTURE_PIXELS + 1`
        survived the square pair and nothing else in the file noticed. The
        constant is a count of pixels, so the test that fixes its edge has to
        be counted in pixels too.
        """
        with self.assertRaises(J2KDecodeError) as caught:
            decode_j2k(_claiming(MAX_TEXTURE_PIXELS + 1, 1))
        self.assertIn("too large", str(caught.exception))

        with self.assertRaises(J2KDecodeError) as allowed:
            decode_j2k(_claiming(MAX_TEXTURE_PIXELS, 1))
        self.assertNotIn("too large", str(allowed.exception))

    def test_a_bomb_pillow_stops_first_still_leaves_as_a_decode_error(self) -> None:
        """The failure this was found through, and the reason for the second catch.

        Above `Image.MAX_IMAGE_PIXELS` Pillow raises `DecompressionBombError`
        from inside `open` itself -- before this module's own size check gets
        a look. That exception subclasses `Exception` and not `OSError`, so
        the handler here did not catch it and it left `decode_j2k` as itself.
        Both callers in `session.py` catch `J2KDecodeError` and nothing else,
        so it went on into the session's task.
        """
        with self.assertRaises(J2KDecodeError):
            decode_j2k(_claiming(30_000, 30_000))

    def test_nothing_legitimate_is_anywhere_near_the_budget(self) -> None:
        """Why 4096 rather than something snug.

        The largest texture either grid accepts is 1024 square and a map tile
        is 256. The budget is sixteen times the area of the first, so a real
        asset never meets it -- a guard that fires on ordinary content gets
        raised until it fires on nothing, and then it is not a guard.
        """
        self.assertGreaterEqual(MAX_TEXTURE_PIXELS, 16 * 1024 * 1024)
        self.assertEqual(MAX_TEXTURE_PIXELS, MAX_TEXTURE_EDGE * MAX_TEXTURE_EDGE)
        decoded = decode_j2k(_make_j2k_bytes(64, 64, "RGB"))
        self.assertEqual((decoded.width, decoded.height), (64, 64))

    def test_the_budget_does_not_depend_on_a_global_somebody_else_owns(self) -> None:
        """`Image.MAX_IMAGE_PIXELS` is a mutable module global this app never sets.

        Anything in the process can raise it or set it to `None`, and its
        default has changed between Pillow versions. A bound that matters is
        not left in a variable nobody here writes, so the check above is made
        against this module's own constant -- and this is the test that says
        so, by disabling Pillow's guard entirely and finding the answer
        unchanged.
        """
        from PIL import Image

        original = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            with self.assertRaises(J2KDecodeError) as caught:
                decode_j2k(_claiming(30_000, 30_000))
            self.assertIn("too large", str(caught.exception))
        finally:
            Image.MAX_IMAGE_PIXELS = original


def _png(size: tuple[int, int], mode: str = "RGB", colour=(200, 100, 50)) -> bytes:
    """What the owner actually hands us: an ordinary image file."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


class EncodeJ2KTests(unittest.TestCase):
    """Encoding, which is the half D needs.

    Uploading a texture into an object is not the same operation as pushing
    an edited script back: the file is one the owner made outside the world,
    in whatever their paint program writes, and it has to become the format a
    grid stores before `NewFileAgentInventory` will take it.
    """

    def test_the_output_is_a_bare_codestream_not_a_container(self) -> None:
        """`ff4f ff51` is SOC followed by SIZ. A JP2 container starts with a
        twelve-byte signature box instead, and while both decode here, the
        `.j2c` a grid serves through GetTexture is the bare one -- and it is
        smaller, since the container's boxes restate what SIZ already says.

        Pillow picks the container from the *filename*, and there is no
        filename when saving to a buffer, so this is `no_jp2` doing its job.
        Passing `codec="j2k"` instead looks right, is not a parameter the
        plugin reads, and silently produces a container.
        """
        encoded = encode_j2k(_png((64, 64)))
        self.assertEqual(encoded[:4], b"\xff\x4f\xff\x51")

    def test_our_own_decoder_reads_what_our_encoder_writes(self) -> None:
        decoded = decode_j2k(encode_j2k(_png((64, 32))))
        self.assertEqual((decoded.width, decoded.height), (64, 32))
        self.assertEqual(decoded.mode, "RGB")

    def test_a_flat_colour_survives_the_round_trip(self) -> None:
        """The encode is lossy -- `irreversible=True` is the 9/7 wavelet --
        so this asks for the colour back within a tolerance rather than
        byte-exactly. A flat field is the one case where a wavelet codec has
        nothing to lose, so the tolerance is small on purpose: a wide one
        would pass even if the channels were transposed.
        """
        decoded = decode_j2k(encode_j2k(_png((64, 64), colour=(200, 100, 50))))
        red, green, blue = decoded.pixels[0], decoded.pixels[1], decoded.pixels[2]
        self.assertAlmostEqual(red, 200, delta=4)
        self.assertAlmostEqual(green, 100, delta=4)
        self.assertAlmostEqual(blue, 50, delta=4)

    def test_alpha_survives(self) -> None:
        """A texture with no alpha where the owner drew one is a texture with
        a black or white halo everywhere they expected transparency."""
        decoded = decode_j2k(encode_j2k(_png((64, 64), "RGBA", (10, 20, 30, 128))))
        self.assertEqual(decoded.mode, "RGBA")
        self.assertAlmostEqual(decoded.pixels[3], 128, delta=4)

    def test_a_palette_image_with_transparency_keeps_it(self) -> None:
        """Transparency on a palette image lives in `info`, not in the mode,
        and converting to RGB rather than RGBA drops it silently. A GIF or an
        indexed PNG is an ordinary thing to find in a folder."""
        from PIL import Image

        image = Image.new("P", (32, 32))
        image.info["transparency"] = 0
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", transparency=0)
        self.assertEqual(decode_j2k(encode_j2k(buffer.getvalue())).mode, "RGBA")

    SIZES = (
        ("already a power of two", (64, 64), (64, 64)),
        ("rounded down, not up", (100, 100), (64, 64)),
        ("each edge on its own", (100, 32), (64, 32)),
        ("capped at the ceiling", (3000, 3000), (1024, 1024)),
        ("capped on one edge only", (3000, 16), (1024, 16)),
        ("one pixel stays one pixel", (1, 1), (1, 1)),
        ("three rounds down to two", (3, 3), (2, 2)),
    )

    def test_dimensions_become_powers_of_two_within_the_cap(self) -> None:
        for name, given, expected in self.SIZES:
            with self.subTest(name):
                decoded = decode_j2k(encode_j2k(_png(given)))
                self.assertEqual((decoded.width, decoded.height), expected)

    def test_rounding_never_reaches_zero(self) -> None:
        """The floor under the rule above. A 1x1 image is a legal texture and
        the obvious implementations of "largest power of two below" return 0
        for it, which turns a small file into an error for no reason."""
        self.assertEqual(decode_j2k(encode_j2k(_png((1, 1)))).width, 1)

    def test_the_cap_can_be_lowered_for_a_caller_that_wants_smaller(self) -> None:
        decoded = decode_j2k(encode_j2k(_png((512, 512)), max_edge=128))
        self.assertEqual((decoded.width, decoded.height), (128, 128))

    def test_bytes_that_are_not_an_image_are_an_encode_error(self) -> None:
        for name, payload in (
            ("empty", b""),
            ("text", b"this is not an image"),
            ("a truncated png", _png((32, 32))[:20]),
        ):
            with self.subTest(name):
                with self.assertRaises(J2KEncodeError):
                    encode_j2k(payload)

    @staticmethod
    def _png_claiming(width: int, height: int) -> bytes:
        """A sixteen-pixel PNG whose IHDR says otherwise.

        Width and height sit at offsets 16 and 20, inside the IHDR chunk, and
        the chunk's CRC at 29 has to be recomputed -- Pillow *does* check it,
        and a wrong one makes the file unrecognisable rather than oversized.
        The first version of this helper skipped that step, so the test
        passed on "not an image" while believing it had proved a size bound.

        With the CRC right, `open` reads the header, believes it, and
        `load()` is where the memory would go.
        """
        import zlib

        header = bytearray(_png((16, 16)))
        header[16:20] = struct.pack(">I", width)
        header[20:24] = struct.pack(">I", height)
        header[29:33] = struct.pack(">I", zlib.crc32(bytes(header[12:29])))
        return bytes(header)

    def test_an_oversized_source_is_refused_before_it_is_loaded(self) -> None:
        """The same bound as the decode, for the same reason.

        The source is a local file rather than something off the wire, so the
        threat model is different -- but the arithmetic is identical.

        The size is chosen to sit **between** this client's limit and
        Pillow's own: 25 megapixels is over `MAX_TEXTURE_PIXELS` and under
        the 178 at which `DecompressionBombError` fires. The first version of
        this test claimed 40,000 on each edge, which is over both -- so it
        passed through Pillow's guard and went on passing with this client's
        removed. A test that can be satisfied by somebody else's check is not
        testing ours.
        """
        with self.assertRaisesRegex(J2KEncodeError, "too large"):
            encode_j2k(self._png_claiming(5000, 5000))

    def test_the_bound_holds_with_pillow_s_own_guard_switched_off(self) -> None:
        """`Image.MAX_IMAGE_PIXELS` is a mutable module global this
        application does not set, so its value is whatever Pillow defaults to
        and whatever any other import has since done to it. The answer must
        not depend on it."""
        from PIL import Image

        original = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = None
        try:
            with self.assertRaisesRegex(J2KEncodeError, "too large"):
                encode_j2k(self._png_claiming(5000, 5000))
        finally:
            Image.MAX_IMAGE_PIXELS = original

    def test_an_image_that_already_fits_is_not_resampled(self) -> None:
        """Resampling a 1024x1024 texture to 1024x1024 is not free, and the
        guard against it is invisible in the output -- a LANCZOS resize to
        the same size is very nearly the identity, and the encode is lossy
        enough to hide the difference. So the assertion is on the work done
        rather than on the result, which is the only place it shows.
        """
        from PIL import Image

        calls = []
        original = Image.Image.resize

        def counting(self, size, *args, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(size)
            return original(self, size, *args, **kwargs)

        Image.Image.resize = counting
        try:
            encode_j2k(_png((64, 64)))
            self.assertEqual(calls, [])
            encode_j2k(_png((100, 100)))
            self.assertEqual(calls, [(64, 64)])
        finally:
            Image.Image.resize = original

    def test_the_upload_cap_is_the_size_both_grids_settled_on(self) -> None:
        """A floor and a ceiling. Nothing server-side enforces this --
        OpenSim's `BunchOfCaps` stores the bytes without looking at them --
        so it is a convention, and a constant with only a floor passes every
        test above while being 2**60."""
        self.assertGreaterEqual(MAX_UPLOAD_TEXTURE_EDGE, 512)
        self.assertLessEqual(MAX_UPLOAD_TEXTURE_EDGE, MAX_TEXTURE_EDGE)
        self.assertEqual(MAX_UPLOAD_TEXTURE_EDGE & (MAX_UPLOAD_TEXTURE_EDGE - 1), 0)

