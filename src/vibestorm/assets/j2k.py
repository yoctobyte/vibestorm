"""Minimal JPEG2000 wrapper: decode for what a grid sends, encode for what
this client sends it.

Backed by Pillow with the openjpeg plugin. Pillow is treated as an optional
dependency: if it cannot be imported, both directions raise their own error
with a clear message rather than failing at import time.
"""

from __future__ import annotations

from dataclasses import dataclass


class J2KDecodeError(RuntimeError):
    """Raised when J2K decoding fails or no decoder is available."""


class J2KEncodeError(RuntimeError):
    """Raised when J2K encoding fails or no encoder is available."""


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


#: The largest texture this client will *upload*, on an edge.
#:
#: Nothing on the server side enforces this. OpenSim's `BunchOfCaps` stores
#: what the uploader sends -- `textureAsset.Data = texture_list[i].AsBinary()`
#: -- with no look at the dimensions, so the constraint is a convention among
#: the things that read the asset afterwards rather than a rule anyone
#: checks. Both grids settled on 1024, and a texture that only some consumers
#: will draw is worse than one that is smaller than the owner asked for.
MAX_UPLOAD_TEXTURE_EDGE = 1024

#: Encoder quality, as Pillow's `quality_layers` in "rates" mode: the
#: compression ratio of each layer. One layer at 40:1 is roughly what a
#: viewer's own uploader produces for a photographic texture, and the format
#: is lossy in that mode -- `irreversible=True` selects the 9/7 wavelet.
#:
#: A round trip is therefore *not* byte-exact and the tests do not ask for
#: one. What they ask is that the dimensions survive, the alpha channel
#: survives, and a flat colour comes back the colour it went in.
UPLOAD_QUALITY_LAYERS = (40.0,)


def encode_j2k(
    data: bytes,
    *,
    max_edge: int = MAX_UPLOAD_TEXTURE_EDGE,
) -> bytes:
    """Encode an ordinary image file into the JPEG2000 a grid stores.

    Takes the bytes of whatever the owner made -- a PNG, a JPEG, a TGA --
    and returns a raw J2K **codestream**, not a JP2 container. That is the
    `.j2c` a grid serves back through GetTexture, and it is the shorter of
    the two: the container adds a signature and header boxes that say what
    the codestream already says.

    Dimensions are rounded down to powers of two and capped at `max_edge`.
    Rounding *down* rather than up, because scaling a texture up invents
    detail that was never in the owner's file, and because the alternative
    to a smaller texture is not a bigger one -- it is a texture some
    consumers will not draw.
    """
    image = _open_for_encode(data)
    try:
        image = _fit_for_upload(image, max_edge)
        return _write_codestream(image)
    except OSError as exc:
        raise J2KEncodeError(f"JPEG2000 encode failed: {exc}") from exc


def _open_for_encode(data: bytes):
    """Open the owner's file, bounded the same way a decode is.

    The source here is a local file rather than something off the wire, so
    the threat is different -- but the arithmetic is not, and neither is
    `DecompressionBombError`, which comes out of `open` and subclasses
    `Exception` rather than `OSError`. A twelve-byte PNG header claiming
    60,000 by 60,000 costs the same 14 GB whoever wrote it.
    """
    try:
        import io

        from PIL import Image, UnidentifiedImageError, features
    except ImportError as exc:
        raise J2KEncodeError(
            "Pillow is required for J2K encoding (install with `pip install Pillow`)"
        ) from exc

    if not features.check("jpg_2000"):
        raise J2KEncodeError(
            "Pillow was built without JPEG2000 support; install system openjpeg "
            "and reinstall Pillow"
        )

    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError as exc:
        raise J2KEncodeError("input bytes are not an image Pillow recognises") from exc
    except Image.DecompressionBombError as exc:
        raise J2KEncodeError(f"image is too large to encode: {exc}") from exc
    except OSError as exc:
        raise J2KEncodeError(f"image could not be read: {exc}") from exc

    pixels = image.width * image.height
    if pixels > MAX_TEXTURE_PIXELS:
        raise J2KEncodeError(
            f"image is too large to encode: {image.width}x{image.height} is "
            f"{pixels:,} pixels, over the {MAX_TEXTURE_PIXELS:,} this client will take"
        )
    if image.width < 1 or image.height < 1:
        raise J2KEncodeError(f"image has no pixels: {image.width}x{image.height}")
    return image


def _fit_for_upload(image, max_edge: int):
    """Powers of two, capped, in a mode a grid's consumers expect.

    Mode first, because `RGBA` is what decides whether the texture can have
    alpha at all and a palette image carries its transparency in a place the
    resampler would drop.
    """
    from PIL import Image

    has_alpha = image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
    image = image.convert("RGBA" if has_alpha else "RGB")

    width = _power_of_two_at_most(image.width, max_edge)
    height = _power_of_two_at_most(image.height, max_edge)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.LANCZOS)
    return image


def _power_of_two_at_most(value: int, ceiling: int) -> int:
    """The largest power of two that is <= `value` and <= `ceiling`.

    Never zero: a one-pixel image is a legal texture and rounding it away
    would turn a small file into an error for no reason.
    """
    limit = min(value, ceiling)
    power = 1
    while power * 2 <= limit:
        power *= 2
    return power


def _write_codestream(image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG2000",
        # Pillow chooses the container from the *filename*, and there is no
        # filename here. `no_jp2` is the only way to ask for a bare
        # codestream when saving to a buffer -- `codec="j2k"` is not a
        # parameter this plugin reads, and passing it silently produces a
        # JP2 container instead.
        no_jp2=True,
        irreversible=True,
        quality_mode="rates",
        quality_layers=list(UPLOAD_QUALITY_LAYERS),
    )
    return buffer.getvalue()


__all__ = [
    "MAX_TEXTURE_EDGE",
    "MAX_TEXTURE_PIXELS",
    "MAX_UPLOAD_TEXTURE_EDGE",
    "UPLOAD_QUALITY_LAYERS",
    "DecodedImage",
    "J2KDecodeError",
    "J2KEncodeError",
    "decode_j2k",
    "encode_j2k",
]
