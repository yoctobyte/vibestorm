"""Minimal JPEG2000 decode wrapper.

Backed by Pillow with the openjpeg plugin. Pillow is treated as an optional
dependency: if it cannot be imported, decode raises ``J2KDecodeError`` with a
clear message rather than failing at import time.
"""

from __future__ import annotations

from dataclasses import dataclass


class J2KDecodeError(RuntimeError):
    """Raised when J2K decoding fails or no decoder is available."""


#: The largest raster this client will decode, in pixels.
#:
#: A J2K header states its own dimensions and the decoder believes them: patch
#: a four-by-four image's `ihdr` box and SIZ marker to say 8192x8192 and Pillow
#: reports 8192x8192, having read nothing but the header. What follows the
#: header decides how long the decode takes, not how much it allocates -- that
#: is `width * height * bands`, chosen by whoever sent the bytes.
#:
#: Pillow has its own guard, `Image.MAX_IMAGE_PIXELS`, and this does not lean
#: on it for two reasons. It is a mutable module global that this application
#: does not set, so its value is whatever Pillow's version defaults to and
#: whatever any other import has since done to it -- a bound nobody here owns.
#: And it is enforced by *warning* between one and two times the limit, so on
#: the version measured (89,478,485) a raster of 89 to 179 megapixels decodes
#: with a `DecompressionBombWarning` and 716 MB of RGBA behind it.
#:
#: 4096 on an edge is sixteen times the area of the largest texture either
#: grid accepts (1024x1024) and two hundred and fifty times a map tile, so
#: nothing legitimate comes near it; at four bytes a pixel it caps one decode
#: at 67 MB. The viewer shrinks anything it draws to `MAX_OBJECT_TEXTURE_EDGE`
#: afterwards, which is 512 -- but afterwards is the problem this bounds, not
#: the one it solves.
MAX_TEXTURE_EDGE = 4096
MAX_TEXTURE_PIXELS = MAX_TEXTURE_EDGE * MAX_TEXTURE_EDGE


@dataclass(slots=True, frozen=True)
class DecodedImage:
    width: int
    height: int
    mode: str  # Pillow mode string: "RGBA", "RGB", "L", "LA", etc.
    pixels: bytes  # raw pixel bytes in the given mode, row-major


def decode_j2k(data: bytes) -> DecodedImage:
    """Decode JPEG2000 bytes to a raw raster.

    Returns a ``DecodedImage`` with width, height, Pillow mode, and packed
    row-major pixel bytes. Raises ``J2KDecodeError`` if Pillow is missing,
    lacks J2K support, or the bytes are not decodable.
    """
    try:
        import io

        from PIL import Image, UnidentifiedImageError, features
    except ImportError as exc:
        raise J2KDecodeError(
            "Pillow is required for J2K decoding (install with `pip install Pillow`)"
        ) from exc

    if not features.check("jpg_2000"):
        raise J2KDecodeError(
            "Pillow was built without JPEG2000 support; install system openjpeg "
            "and reinstall Pillow"
        )

    try:
        image = Image.open(io.BytesIO(data), formats=["JPEG2000"])
    except UnidentifiedImageError as exc:
        raise J2KDecodeError("input bytes are not a JPEG2000 codestream") from exc
    except Image.DecompressionBombError as exc:
        # Pillow's own guard, which fires inside `open` and is not an
        # `OSError` -- it subclasses `Exception` directly, so the handler
        # below never saw it and it left this function as itself. Both
        # callers catch `J2KDecodeError` and nothing else, so it escaped into
        # the session's task. Kept as a second line behind the size check
        # rather than relied on: see `MAX_TEXTURE_PIXELS`.
        raise J2KDecodeError(f"JPEG2000 raster is too large to decode: {exc}") from exc
    except OSError as exc:
        raise J2KDecodeError(f"JPEG2000 decode failed: {exc}") from exc

    # Before `load()`, which is where the memory is spent. `open` has read the
    # header and nothing else, so this costs nothing and the answer is already
    # known.
    pixels = image.width * image.height
    if pixels > MAX_TEXTURE_PIXELS:
        raise J2KDecodeError(
            f"JPEG2000 raster is too large to decode: {image.width}x{image.height} "
            f"is {pixels:,} pixels, over the {MAX_TEXTURE_PIXELS:,} this client will take"
        )

    try:
        image.load()
    except Image.DecompressionBombError as exc:  # pragma: no cover - size check precedes it
        raise J2KDecodeError(f"JPEG2000 raster is too large to decode: {exc}") from exc
    except OSError as exc:
        raise J2KDecodeError(f"JPEG2000 decode failed: {exc}") from exc

    return DecodedImage(
        width=image.width,
        height=image.height,
        mode=image.mode,
        pixels=image.tobytes(),
    )
