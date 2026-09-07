"""Keeping an in-world object's contents and a local folder in step.

The viewer grew a one-shot folder-to-object upload as a closure inside
``run_viewer``, which meant it could not be reused, tested on its own, or run
in a loop. This package holds the parts that do not need a window: the naming
rules both directions have to agree on, the preparation of files that were
never in world, and the sync engine itself.
"""

from vibestorm.sync.naming import (
    TEXT_ASSET_TYPES,
    asset_file_suffix,
    match_files_to_rows,
    safe_filename,
    upload_kind_for_path,
)
from vibestorm.sync.new_assets import (
    NewAssetError,
    PreparedAsset,
    new_asset_kind_for_path,
    prepare_new_asset,
)

__all__ = [
    "TEXT_ASSET_TYPES",
    "NewAssetError",
    "PreparedAsset",
    "asset_file_suffix",
    "match_files_to_rows",
    "new_asset_kind_for_path",
    "prepare_new_asset",
    "safe_filename",
    "upload_kind_for_path",
]
