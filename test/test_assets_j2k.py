import io
import struct
import unittest

from vibestorm.assets.j2k import (
    MAX_TEXTURE_EDGE,
    MAX_TEXTURE_PIXELS,
    J2KDecodeError,
    decode_j2k,
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
