import io
import unittest

from vibestorm.assets.j2k import J2KDecodeError, decode_j2k


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
