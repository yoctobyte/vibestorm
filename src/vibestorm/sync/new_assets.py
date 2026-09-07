"""Turning a file the owner made into an asset a grid will store.

This is the other half of D. Pushing an edited script back updates an asset
that already exists, and `naming.py` maps a file to the row it came from.
Uploading a texture out of a folder the owner assembled is a different
operation with a different capability: the file was never in world, there is
no row to match, and the bytes have to become a format the grid stores before
`NewFileAgentInventory` will take them.

Kept apart from `naming.py` on purpose. That module's suffix map drives the
*task inventory* upload path, and a `.png` appearing in it would be handed to
a client that can only author text -- which is worse than not supporting
textures, because it would look supported.

**What is deliberately not here.** Sounds, animations and meshes. Each needs
its own encoder or its own validator and this client has neither; a suffix
map that claimed them would create items with the owner's raw file inside,
which no consumer can read. `.j2k` and `.j2c` pass through because they are
already the format -- that is the one case where "no encoder" and "supported"
are the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibestorm.assets.j2k import J2KEncodeError, encode_j2k

#: Image suffixes Pillow reads and this client re-encodes into JPEG2000.
#:
#: Deliberately a list of what the owner's tools actually write rather than
#: everything Pillow can open: a `.pdf` or a `.pcx` opening successfully and
#: silently becoming a texture is a surprise, and the cost of the narrower
#: list is a clear "not supported" instead.
ENCODABLE_IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".gif", ".tif", ".tiff"}
)

#: Suffixes that are already a JPEG2000 codestream and are uploaded as they
#: are. `.j2k` is what this client's own export writes, so a folder pulled
#: out of world and pushed back goes through here unchanged.
PASSTHROUGH_TEXTURE_SUFFIXES: frozenset[str] = frozenset({".j2k", ".j2c"})


class NewAssetError(RuntimeError):
    """Raised when a file cannot be prepared for upload."""


@dataclass(slots=True, frozen=True)
class PreparedAsset:
    """A file, ready for `NewFileAgentInventory`."""

    #: The `asset_type` string the capability is given.
    asset_type: str
    #: The `inventory_type` string. Equal to `asset_type` for a texture; kept
    #: separate because the capability takes both and they diverge for
    #: wearables, where one inventory type covers two asset types.
    inventory_type: str
    #: The SL asset type number the item ends up with, for the caller that
    #: has to write a task-inventory row afterwards.
    numeric_asset_type: int
    #: The bytes to upload, which are not necessarily the bytes on disk.
    data: bytes
    #: True when `data` differs from what was read, so a caller can say so.
    re_encoded: bool


def new_asset_kind_for_path(path: Path) -> tuple[str, str] | None:
    """The (asset_type, inventory_type) strings for a file, or None.

    None means "this client will not upload that", which is a different
    answer from "the grid would refuse it" -- most of what returns None here
    is a format a grid stores happily and this client cannot author.
    """
    suffix = path.suffix.lower()
    if suffix in ENCODABLE_IMAGE_SUFFIXES or suffix in PASSTHROUGH_TEXTURE_SUFFIXES:
        return ("texture", "texture")
    return None


def prepare_new_asset(path: Path, data: bytes) -> PreparedAsset:
    """Encode `data` into the asset a grid stores, or pass it through.

    Raises `NewAssetError` for a suffix this client will not upload and for
    an image it cannot read. The encode failure is converted here rather than
    left as `J2KEncodeError`, so that a caller walking a folder catches one
    class per file instead of one per format.
    """
    kind = new_asset_kind_for_path(path)
    if kind is None:
        raise NewAssetError(f"{path.name}: this client cannot upload {path.suffix!r} files")

    suffix = path.suffix.lower()
    if suffix in PASSTHROUGH_TEXTURE_SUFFIXES:
        return PreparedAsset(
            asset_type=kind[0],
            inventory_type=kind[1],
            numeric_asset_type=0,
            data=data,
            re_encoded=False,
        )

    try:
        encoded = encode_j2k(data)
    except J2KEncodeError as exc:
        raise NewAssetError(f"{path.name}: {exc}") from exc
    return PreparedAsset(
        asset_type=kind[0],
        inventory_type=kind[1],
        numeric_asset_type=0,
        data=encoded,
        re_encoded=True,
    )


__all__ = [
    "ENCODABLE_IMAGE_SUFFIXES",
    "PASSTHROUGH_TEXTURE_SUFFIXES",
    "NewAssetError",
    "PreparedAsset",
    "new_asset_kind_for_path",
    "prepare_new_asset",
]
