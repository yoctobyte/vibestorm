"""What a synced folder remembers between runs.

Kept beside the files as ``.vibestorm-sync.json``. Without it the sync cannot
tell "I edited this script" from "this is what I pulled a minute ago", so a
watch loop would either re-upload everything on every tick or miss the first
edit after a restart.

Deliberately not a lock or a merge base. The scope here is one person editing
scripts in one folder against one object; the record exists to answer "has this
file changed since we last agreed on it", and to notice when the in-world copy
moved too so the user can be told rather than quietly overwritten.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import UUID

STATE_FILENAME = ".vibestorm-sync.json"

#: Bump when the on-disk shape changes in a way older readers cannot handle.
STATE_VERSION = 1


def content_digest(data: bytes) -> str:
    """The digest recorded for a file's contents.

    Content rather than mtime: an editor that writes a file without changing it
    (or a checkout that rewrites every mtime) should not look like an edit.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class SyncedItem:
    """One inventory row and the local file standing in for it."""

    file_name: str
    item_name: str
    asset_type: int
    item_id: str | None = None
    #: Digest of the bytes both sides last agreed on -- what pull wrote, or
    #: what push last sent. A local file differing from this has been edited;
    #: an in-world asset differing from it has been edited by someone else.
    synced_digest: str | None = None
    #: Asset id the last agreed content came from, when it is known. Used to
    #: notice that the in-world copy moved on without us.
    synced_asset_id: str | None = None
    #: Set when the local file cannot faithfully represent the asset, so
    #: pushing it back would lose something. A notecard carrying embedded
    #: inventory items is the case that forces this: we can show its text, but
    #: re-encoding that text drops the items, and losing a user's attachments
    #: to save a typo is not a trade the sync gets to make on its own.
    readonly: bool = False
    readonly_reason: str | None = None


@dataclass(slots=True)
class SyncState:
    task_id: str
    version: int = STATE_VERSION
    items: dict[str, SyncedItem] = field(default_factory=dict)

    # ------------------------------------------------------------- lookup

    def by_file_name(self, file_name: str) -> SyncedItem | None:
        return self.items.get(file_name.lower())

    def record(self, item: SyncedItem) -> None:
        self.items[item.file_name.lower()] = item

    # ------------------------------------------------------------ storage

    @classmethod
    def path_for(cls, folder: Path) -> Path:
        return folder / STATE_FILENAME

    @classmethod
    def load(cls, folder: Path, *, task_id: UUID) -> SyncState:
        """Read the folder's record, or start an empty one.

        A missing, unreadable, or unrecognised file yields a fresh state rather
        than an error: the folder is still perfectly syncable, it just has no
        history, and refusing to run would be a worse answer than treating
        every file as new.
        """
        path = cls.path_for(folder)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(task_id=str(task_id))
        if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
            return cls(task_id=str(task_id))
        # A folder bound to a different object is not this object's history.
        if raw.get("task_id") != str(task_id):
            return cls(task_id=str(task_id))
        items: dict[str, SyncedItem] = {}
        for key, value in (raw.get("items") or {}).items():
            if not isinstance(value, dict):
                continue
            try:
                items[key] = SyncedItem(
                    file_name=value["file_name"],
                    item_name=value["item_name"],
                    asset_type=int(value["asset_type"]),
                    item_id=value.get("item_id"),
                    synced_digest=value.get("synced_digest"),
                    synced_asset_id=value.get("synced_asset_id"),
                    readonly=bool(value.get("readonly", False)),
                    readonly_reason=value.get("readonly_reason"),
                )
            except (KeyError, TypeError, ValueError):
                continue
        return cls(task_id=str(task_id), items=items)

    def save(self, folder: Path) -> None:
        payload = {
            "version": STATE_VERSION,
            "task_id": self.task_id,
            "items": {key: asdict(item) for key, item in sorted(self.items.items())},
        }
        path = self.path_for(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and move, so an interrupted save cannot leave
        # a half-written record that then reads as "no history".
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temp.replace(path)


__all__ = ["STATE_FILENAME", "STATE_VERSION", "SyncState", "SyncedItem", "content_digest"]
