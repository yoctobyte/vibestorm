"""Minimal JPEG2000 decode wrapper.

Backed by Pillow with the openjpeg plugin. Pillow is treated as an optional
dependency: if it cannot be imported, decode raises ``J2KDecodeError`` with a
clear message rather than failing at import time.
"""

from __future__ import annotations

from dataclasses import dataclass


class J2KDecodeError(RuntimeError):
    """Raised when J2K decoding fails or no decoder is available."""


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
        image.load()
    except UnidentifiedImageError as exc:
        raise J2KDecodeError("input bytes are not a JPEG2000 codestream") from exc
    except OSError as exc:
        raise J2KDecodeError(f"JPEG2000 decode failed: {exc}") from exc

    return DecodedImage(
        width=image.width,
        height=image.height,
        mode=image.mode,
        pixels=image.tobytes(),
    )
