"""Deciding what a sync should do, without doing any of it.

Every interesting question here -- is this file edited, did the in-world copy
move, is this a conflict -- is answerable from an inventory listing, a folder
listing and the last recorded state. Keeping that separate from the fetching
and uploading means the rules can be tested exhaustively against ordinary data
structures, and the async half stays a thin executor.

Scope, deliberately: no deletes, no merges, no recursive folders. What this
does add over a one-shot copy is *refusing to clobber*. A sync that silently
overwrites an edit is worse than one that stops and says which file it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vibestorm.sync.naming import (
    TEXT_ASSET_TYPES,
    colliding_file_names,
    file_name_for_item,
    safe_filename,
    upload_kind_for_path,
)
from vibestorm.sync.state import SyncState, content_digest

# ---- what a plan entry can say -------------------------------------------

#: Nothing to do; the two sides already agree.
UNCHANGED = "unchanged"
#: Transfer it.
TRANSFER = "transfer"
#: Both sides changed since they last agreed. Reported, never resolved.
CONFLICT = "conflict"
#: Cannot act on it at all (unsupported type, missing id, no capability).
SKIP = "skip"


@dataclass(slots=True, frozen=True)
class PullEntry:
    """One inventory row, and what pulling it should do."""

    item_name: str
    file_name: str
    asset_type: int
    item_id: str | None
    asset_id: str | None
    action: str
    reason: str


@dataclass(slots=True, frozen=True)
class PushEntry:
    """One local file, and what pushing it should do."""

    path: Path
    file_name: str
    item_name: str
    action: str
    reason: str
    #: None when the row does not exist yet and has to be created.
    item_id: str | None = None
    asset_type: int | None = None
    create: bool = False


def _read_digest(path: Path) -> str | None:
    try:
        return content_digest(path.read_bytes())
    except OSError:
        return None


def plan_pull(
    items,
    *,
    folder: Path,
    state: SyncState,
    overwrite_untracked: bool = False,
) -> list[PullEntry]:
    """What to fetch from the object into ``folder``.

    ``items`` is an iterable of objects with ``name``, ``item_id``,
    ``asset_id`` and a numeric ``asset_type``.
    """
    items = list(items)
    collisions = _colliding_pull_names(items, state)
    tracked_names = {
        record.item_id: record.file_name
        for record in state.items.values()
        if record.item_id
    }
    entries: list[PullEntry] = []
    for item in items:
        asset_type = item.asset_type
        name = item.name or ""
        if asset_type not in TEXT_ASSET_TYPES:
            entries.append(
                PullEntry(
                    item_name=name,
                    file_name="",
                    asset_type=asset_type,
                    item_id=_str_or_none(item.item_id),
                    asset_id=_str_or_none(item.asset_id),
                    action=SKIP,
                    reason="not a text asset",
                )
            )
            continue

        # Once a file and a row are bound, the binding outranks the name. An
        # item renamed in world -- which the simulator does on its own when a
        # copied name collides, turning "foo" into "foo 1" -- must keep
        # updating the file it came from rather than start a second one.
        file_name = tracked_names.get(_str_or_none(item.item_id) or "") or file_name_for_item(
            name, asset_type
        )
        path = folder / file_name

        if file_name.lower() in collisions:
            entries.append(
                PullEntry(
                    item_name=name,
                    file_name=file_name,
                    asset_type=asset_type,
                    item_id=_str_or_none(item.item_id),
                    asset_id=_str_or_none(item.asset_id),
                    action=CONFLICT,
                    reason=f"another item also wants the file name {file_name}",
                )
            )
            continue
        asset_id = _str_or_none(item.asset_id)
        record = state.by_file_name(file_name)
        local_digest = _read_digest(path)

        def entry(action: str, reason: str) -> PullEntry:
            return PullEntry(
                item_name=name,
                file_name=file_name,
                asset_type=asset_type,
                item_id=_str_or_none(item.item_id),
                asset_id=asset_id,
                action=action,
                reason=reason,
            )

        if local_digest is None:
            entries.append(entry(TRANSFER, "not present locally"))
            continue

        if record is None:
            # A file we have never synced. It may be exactly what is in world,
            # or someone's work that predates the binding; we cannot tell which
            # without fetching, so say so rather than overwrite.
            entries.append(
                entry(TRANSFER, "untracked local file, overwriting on request")
                if overwrite_untracked
                else entry(CONFLICT, "local file is not tracked by this folder's sync state")
            )
            continue

        locally_edited = local_digest != record.synced_digest
        remotely_moved = asset_id is not None and asset_id != record.synced_asset_id

        if locally_edited and remotely_moved:
            entries.append(entry(CONFLICT, "edited here and in world since the last sync"))
        elif locally_edited:
            entries.append(entry(UNCHANGED, "local copy is ahead; push it or discard it"))
        elif remotely_moved:
            entries.append(entry(TRANSFER, "in-world copy changed"))
        else:
            entries.append(entry(UNCHANGED, "already in step"))
    return entries


def plan_push(
    files: list[Path],
    rows_by_name: dict[str, object],
    *,
    state: SyncState,
    can_create: bool = True,
    can_create_notecards: bool = False,
) -> list[PushEntry]:
    """What to send from the folder into the object.

    ``rows_by_name`` maps an in-world item name to a row carrying ``item_id``,
    ``asset_id`` and a numeric ``asset_type``.
    """
    from vibestorm.sync.naming import match_files_to_rows

    uploadable = [path for path in files if upload_kind_for_path(path) is not None]
    ignored = [path for path in files if upload_kind_for_path(path) is None]
    rows = list(rows_by_name.values())
    rows_by_item_id = {
        str(getattr(row, "item_id", None)): row
        for row in rows
        if getattr(row, "item_id", None) is not None
    }

    # A file already bound to a row goes to that row, whatever either is
    # called now. Name matching only ever needs to *establish* a binding.
    matched: list[tuple[Path, object]] = []
    unbound: list[Path] = []
    bound_rows: set[int] = set()
    for path in sorted(uploadable):
        record = state.by_file_name(path.name)
        row = rows_by_item_id.get(record.item_id) if record and record.item_id else None
        if row is None:
            unbound.append(path)
            continue
        matched.append((path, row))
        bound_rows.add(id(row))

    by_name_matched, unmatched = match_files_to_rows(
        unbound,
        [row for row in rows if id(row) not in bound_rows],
        name_of=lambda row: getattr(row, "name", "") or "",
        asset_type_of=lambda row: getattr(row, "asset_type", 0) or 0,
    )
    matched.extend(by_name_matched)

    entries: list[PushEntry] = []
    for path in sorted(ignored):
        entries.append(
            PushEntry(
                path=path,
                file_name=path.name,
                item_name=path.stem,
                action=SKIP,
                reason=f"{path.suffix or 'no suffix'} is not an uploadable type",
            )
        )

    collisions = colliding_file_names(
        rows_by_name.values(),
        name_of=lambda row: getattr(row, "name", "") or "",
        asset_type_of=lambda row: getattr(row, "asset_type", 0) or 0,
    )
    for path, row in matched:
        file_name = path.name
        record_for_path = state.by_file_name(file_name)
        already_bound = bool(
            record_for_path
            and record_for_path.item_id
            and record_for_path.item_id == _str_or_none(getattr(row, "item_id", None))
        )
        if not already_bound and file_name.lower() in collisions:
            entries.append(
                PushEntry(
                    path=path,
                    file_name=file_name,
                    item_name=getattr(row, "name", None) or path.stem,
                    action=CONFLICT,
                    reason=f"more than one inventory item is called {file_name}",
                )
            )
            continue
        record = state.by_file_name(file_name)
        local_digest = _read_digest(path)
        asset_id = _str_or_none(getattr(row, "asset_id", None))
        item_id = _str_or_none(getattr(row, "item_id", None))
        item_name = getattr(row, "name", None) or path.stem

        def entry(action: str, reason: str) -> PushEntry:
            return PushEntry(
                path=path,
                file_name=file_name,
                item_name=item_name,
                action=action,
                reason=reason,
                item_id=item_id,
                asset_type=getattr(row, "asset_type", None),
            )

        if local_digest is None:
            entries.append(entry(SKIP, "cannot read the file"))
            continue
        if item_id is None:
            entries.append(entry(SKIP, "inventory row has no item id"))
            continue
        if record is not None and record.readonly:
            entries.append(
                entry(SKIP, record.readonly_reason or "not safe to push from this file")
            )
            continue
        if record is None:
            entries.append(entry(TRANSFER, "not tracked yet"))
            continue

        locally_edited = local_digest != record.synced_digest
        remotely_moved = asset_id is not None and asset_id != record.synced_asset_id
        if locally_edited and remotely_moved:
            entries.append(entry(CONFLICT, "edited here and in world since the last sync"))
        elif locally_edited:
            entries.append(entry(TRANSFER, "edited locally"))
        else:
            entries.append(entry(UNCHANGED, "already in step"))

    for path in sorted(unmatched):
        kind = upload_kind_for_path(path)
        asset_kind = kind[0] if kind is not None else None
        # A script row is made with RezScript; a notecard has no
        # create-from-nothing message and has to be built in agent inventory
        # and copied in, which needs a capability the caller may not have.
        allowed = (asset_kind == "lsltext" and can_create) or (
            asset_kind == "notecard" and can_create_notecards
        )
        if allowed:
            entries.append(
                PushEntry(
                    path=path,
                    file_name=path.name,
                    item_name=safe_filename(path.stem),
                    action=TRANSFER,
                    reason="no row yet; creating one",
                    create=True,
                    asset_type=10 if asset_kind == "lsltext" else 7,
                )
            )
            continue
        if asset_kind == "lsltext":
            reason = "no matching inventory item and creating script rows is disabled"
        elif asset_kind == "notecard":
            reason = "no matching inventory item and no capability to create a notecard"
        else:
            reason = "no matching inventory item, and this type cannot be created"
        entries.append(
            PushEntry(
                path=path,
                file_name=path.name,
                item_name=safe_filename(path.stem),
                action=SKIP,
                reason=reason,
            )
        )
    return entries


def _colliding_pull_names(items, state: SyncState) -> set[str]:
    """File names more than one row would write, honouring existing bindings.

    Computed from the names pull will *actually* use, so a row already bound to
    a file is not accused of colliding with the name it would otherwise have
    been given.
    """
    tracked = {record.item_id: record.file_name for record in state.items.values() if record.item_id}
    seen: dict[str, int] = {}
    for item in items:
        if item.asset_type not in TEXT_ASSET_TYPES:
            continue
        item_id = _str_or_none(item.item_id) or ""
        name = tracked.get(item_id) or file_name_for_item(item.name or "", item.asset_type)
        key = name.lower()
        seen[key] = seen.get(key, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def _str_or_none(value) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "CONFLICT",
    "SKIP",
    "TRANSFER",
    "UNCHANGED",
    "PullEntry",
    "PushEntry",
    "plan_pull",
    "plan_push",
]
