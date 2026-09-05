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

from vibestorm.world.asset_types import ASSET_NAME_BY_TYPE

#: Asset types this project round-trips as editable text: notecard and LSL
#: source. Anything else can be *exported* as bytes but is never uploaded back,
#: because nothing here knows how to author one.
TEXT_ASSET_TYPES = frozenset({7, 10})

#: Suffixes for asset types whose container this tree has actually seen. Every
#: entry below is backed by bytes OpenSim wrote -- the live probe on 2026-09-05
#: for textures, the captured fixtures in ``test/fixtures/library/`` for the
#: rest -- rather than by what the extension "ought" to be.
_SUFFIX_BY_ASSET_TYPE = {
    10: ".lsl",        # LSLText: plain UTF-8 source, no container
    7: ".txt",         # Notecard
    0: ".j2k",         # Texture: JPEG2000 codestream, magic ff 4f ff 51
    5: ".wearable",    # Clothing: "LLWearable version 22" UTF-8 text
    13: ".wearable",   # Body part: the same format under a different type
    20: ".animation",  # SL's internal binary animation; no standard extension
    21: ".gesture",    # Line-based UTF-8 text
}

#: Suffixes that can be uploaded, mapped to (asset kind, inventory kind) as the
#: task-inventory upload capability names them.
_UPLOAD_KIND_BY_SUFFIX = {
    ".lsl": ("lsltext", "lsl"),
    ".txt": ("notecard", "notecard"),
    ".nc": ("notecard", "notecard"),
}

#: The asset type each uploadable suffix ends up as in world.
_ASSET_TYPE_BY_UPLOAD_KIND = {"lsltext": 10, "notecard": 7}


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
    """The file suffix a given SL asset type is written with.

    A type whose container this tree has verified gets the real extension. Any
    other known type gets its own *name* -- ``.sound``, ``.object`` -- which
    says what the bytes are without claiming to know how they are wrapped. A
    guessed extension is worse than an honest one: ``.ogg`` on a sound we have
    never opened invites a tool to fail confusingly on the day it is wrong.
    """
    suffix = _SUFFIX_BY_ASSET_TYPE.get(asset_type)
    if suffix is not None:
        return suffix
    name = ASSET_NAME_BY_TYPE.get(asset_type)
    if name:
        return f".{name}"
    return ".bin"


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

    All three passes are restricted to rows the file could actually be uploaded
    into. Passes 2 and 3 match on the *stem*, which drops the suffix and with it
    the only thing distinguishing a script from a texture: a prim holding a
    texture called ``Greeter`` would otherwise capture the user's new
    ``Greeter.lsl`` and push LSL source into a texture row. With the type
    checked, the file is correctly seen as having no row yet and a script row is
    created for it.
    """
    tables: dict[int, tuple[dict[str, _T], dict[str, _T], dict[str, _T]]] = {}
    for row in rows:
        asset_type = asset_type_of(row)
        by_file_name, by_safe_stem, by_raw_stem = tables.setdefault(
            asset_type, ({}, {}, {})
        )
        raw_name = name_of(row) or ""
        by_file_name.setdefault(file_name_for_item(raw_name, asset_type).lower(), row)
        by_safe_stem.setdefault(safe_filename(raw_name).lower(), row)
        by_raw_stem.setdefault(raw_name.lower(), row)

    matched: list[tuple[Path, _T]] = []
    unmatched: list[Path] = []
    for file_path in sorted(files):
        kind = upload_kind_for_path(file_path)
        target_type = (
            _ASSET_TYPE_BY_UPLOAD_KIND.get(kind[0]) if kind is not None else None
        )
        by_file_name, by_safe_stem, by_raw_stem = tables.get(
            target_type, ({}, {}, {})
        )
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
