"""The file-naming rules the two sync directions must agree on.

Pull writes ``<folder>/<item name><suffix>``; push matches a file back to the
row it came from by the same rule. If the two ever disagree, a pulled file
stops matching its own row on the way back and sync silently creates a
duplicate instead of updating -- so both directions call the same functions
here rather than each having their own idea of the mapping.

These were pure helpers inside ``viewer3d/app.py``. Nothing about them needs a
window, and the CLI sync needs them too.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

#: Asset types this project round-trips as editable text: notecard and LSL
#: source. Anything else is exported as bytes and never matched back.
TEXT_ASSET_TYPES = frozenset({7, 10})

_SUFFIX_BY_ASSET_TYPE = {
    10: ".lsl",   # LSLText
    7: ".txt",    # Notecard
    0: ".j2k",    # Texture
}

#: Suffixes that can be uploaded, mapped to (asset kind, inventory kind) as the
#: task-inventory upload capability names them.
_UPLOAD_KIND_BY_SUFFIX = {
    ".lsl": ("lsltext", "lsl"),
    ".txt": ("notecard", "notecard"),
    ".nc": ("notecard", "notecard"),
}


def safe_filename(value: str) -> str:
    """An in-world item name reduced to something safe to write to disk.

    In-world names are free text -- slashes, colons and leading dots all
    appear -- so this keeps only alphanumerics, space, dot, underscore and
    hyphen, and never returns an empty name.
    """
    cleaned = "".join(ch if ch.isalnum() or ch in " ._-" else "_" for ch in value.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "unnamed"


def asset_file_suffix(asset_type: int) -> str:
    """The file suffix a given SL asset type is written with."""
    return _SUFFIX_BY_ASSET_TYPE.get(asset_type, ".bin")


def upload_kind_for_path(path: Path) -> tuple[str, str] | None:
    """``(asset kind, inventory kind)`` for a file, or None if not uploadable."""
    return _UPLOAD_KIND_BY_SUFFIX.get(path.suffix.lower())


def file_name_for_item(item_name: str, asset_type: int) -> str:
    """The file name pull writes for an inventory row.

    Kept as one function because push has to be able to predict it exactly.
    """
    name = safe_filename(item_name or "unnamed")
    suffix = asset_file_suffix(asset_type)
    if not name.lower().endswith(suffix):
        name = f"{name}{suffix}"
    return name


_T = TypeVar("_T")


def colliding_file_names(
    rows: Iterable[_T],
    *,
    name_of: Callable[[_T], str],
    asset_type_of: Callable[[_T], int],
) -> set[str]:
    """File names that more than one inventory row would claim.

    An object may hold items called both ``notes`` and ``notes.lsl``; both
    want the file ``notes.lsl``, and a folder cannot hold two files with one
    name. Nothing stops a sim from allowing it, so the sync has to notice and
    say so -- pulling both would write one over the other, and pushing the
    result would send the survivor to whichever row happened to be found
    first.
    """
    seen: dict[str, int] = {}
    for row in rows:
        file_name = file_name_for_item(name_of(row) or "", asset_type_of(row)).lower()
        seen[file_name] = seen.get(file_name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def match_files_to_rows(
    files: list[Path],
    rows: Iterable[_T],
    *,
    name_of: Callable[[_T], str],
    asset_type_of: Callable[[_T], int],
) -> tuple[list[tuple[Path, _T]], list[Path]]:
    """Match files to inventory rows, preferring the name pull would have used.

    Three passes, most trustworthy first:

    1. The exact file name :func:`file_name_for_item` produces for the row.
       This is the round trip: whatever pull wrote, push finds again.
    2. The sanitised stem against the sanitised item name, so a file the user
       created by hand as ``greeter.lsl`` matches an item called ``Greeter``.
    3. The raw stem against the raw item name.

    Pass 1 exists because in-world item names routinely carry the suffix
    already -- an item really is called ``vibestorm-sync-88338.lsl`` -- and
    then the stem is ``vibestorm-sync-88338``, which matches nothing. A sync
    that misses like that does not fail loudly: it decides the file is new and
    creates a *second* row beside the one it came from.
    """
    by_file_name: dict[str, _T] = {}
    by_safe_stem: dict[str, _T] = {}
    by_raw_stem: dict[str, _T] = {}
    for row in rows:
        raw_name = name_of(row) or ""
        by_file_name.setdefault(
            file_name_for_item(raw_name, asset_type_of(row)).lower(), row
        )
        by_safe_stem.setdefault(safe_filename(raw_name).lower(), row)
        by_raw_stem.setdefault(raw_name.lower(), row)

    matched: list[tuple[Path, _T]] = []
    unmatched: list[Path] = []
    for file_path in sorted(files):
        stem = file_path.stem
        row = (
            by_file_name.get(file_path.name.lower())
            or by_safe_stem.get(safe_filename(stem).lower())
            or by_raw_stem.get(stem.lower())
        )
        if row is None:
            unmatched.append(file_path)
        else:
            matched.append((file_path, row))
    return matched, unmatched


__all__ = [
    "TEXT_ASSET_TYPES",
    "asset_file_suffix",
    "file_name_for_item",
    "colliding_file_names",
    "match_files_to_rows",
    "safe_filename",
    "upload_kind_for_path",
]
