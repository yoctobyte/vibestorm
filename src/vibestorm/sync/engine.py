"""Running a sync between one in-world object and one local folder.

The planner in :mod:`vibestorm.sync.plan` decides what should happen; this
carries it out. Split that way because every rule worth arguing about is in
the planner and can be tested against plain data, leaving this a thin layer of
fetches, writes and uploads.

Both directions go through the same naming rules, so a file this pulls is a
file it can push back onto the row it came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from vibestorm.assets.notecard import decode_notecard, encode_notecard
from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.caps.task_inventory_upload_client import (
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)
from vibestorm.sync.naming import TEXT_ASSET_TYPES
from vibestorm.sync.notecards import NotecardCreateError, create_task_notecard
from vibestorm.sync.plan import CONFLICT, SKIP, TRANSFER, plan_pull, plan_push
from vibestorm.sync.state import SyncState, SyncedItem, content_digest
from vibestorm.sync.task_inventory import (
    Progress,
    await_object_inventory,
    create_task_script_rows,
    fetch_task_asset,
)
from vibestorm.udp.world_client import WorldClient
from vibestorm.world.asset_types import ASSET_NAME_BY_TYPE, asset_type_to_int
from vibestorm.world.object_inventory import ObjectInventorySnapshot

#: Both names OpenSim registers for updating a script in object inventory,
#: current one first. ``BunchOfCaps`` registers ``UpdateScriptTask`` and then
#: ``UpdateScriptTaskInventory`` against the same handler, with the second
#: marked ``//legacy`` in its source. Asking only for the legacy alias works
#: today but leaves the client depending on the name OpenSim has already
#: labelled as the one it keeps for compatibility. Preference, not fallback
#: ranking: a sim offering both should be talked to by the current name.
SCRIPT_TASK_CAP_NAMES = ["UpdateScriptTask", "UpdateScriptTaskInventory"]
NOTECARD_TASK_CAP_NAME = "UpdateNotecardTaskInventory"
#: Filling in a notecard that lives in *agent* inventory. Needed to create one
#: inside an object, because that route goes through agent inventory first.
NOTECARD_AGENT_CAP_NAME = "UpdateNotecardAgentInventory"

NOTECARD_ASSET_TYPE = 7
SCRIPT_ASSET_TYPE = 10


@dataclass(slots=True, frozen=True)
class SyncCaps:
    """The capabilities a push needs, and what each one unlocks."""

    script: str | None = None
    notecard: str | None = None
    notecard_agent: str | None = None

    @property
    def can_create_notecards(self) -> bool:
        return bool(self.notecard_agent)


@dataclass(slots=True, frozen=True)
class InventoryRow:
    """A task inventory row, with the asset type resolved to a number.

    Task inventory names types as strings; everything downstream wants the
    integer, and doing the conversion once here keeps it out of the planner.
    """

    name: str
    asset_type: int
    item_id: UUID | None
    asset_id: UUID | None


@dataclass(slots=True)
class SyncOutcome:
    """What a run did, in enough detail to print a useful summary."""

    transferred: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.transferred or self.created)

    def summary(self) -> str:
        parts = [
            f"{len(self.transferred)} transferred",
            f"{len(self.created)} created",
            f"{len(self.unchanged)} unchanged",
        ]
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} conflict(s)")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)


def rows_from_snapshot(snapshot: ObjectInventorySnapshot) -> list[InventoryRow]:
    """Inventory rows with numeric asset types, dropping ones we cannot name."""
    rows: list[InventoryRow] = []
    for item in snapshot.items:
        asset_type = asset_type_to_int(item.asset_type)
        if asset_type is None:
            continue
        rows.append(
            InventoryRow(
                name=item.name,
                asset_type=asset_type,
                item_id=item.item_id,
                asset_id=item.asset_id,
            )
        )
    return rows


def _report(on_progress: Progress | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)


def _decode_for_disk(data: bytes, asset_type: int) -> tuple[bytes, str | None]:
    """The bytes to write locally, and why the file cannot be pushed back.

    A notecard asset is a container: its text is what a person wants to edit,
    but it can also carry embedded inventory items that re-encoding the text
    would drop. Such a file is written for reading and marked unpushable.

    Everything outside the two text types is written byte-for-byte and marked
    unpushable too. Exporting a texture is useful; pretending we could author
    one back is not.
    """
    if asset_type not in TEXT_ASSET_TYPES:
        return data, f"{ASSET_NAME_BY_TYPE.get(asset_type, 'this')} assets are exported, not edited"
    if asset_type != NOTECARD_ASSET_TYPE:
        return data, None
    notecard = decode_notecard(data)
    if notecard.has_undecoded_items:
        return (
            notecard.text.encode("utf-8"),
            "notecard carries embedded inventory items; pushing the text back would drop them",
        )
    return notecard.text.encode("utf-8"), None


def _encode_for_upload(data: bytes, asset_type: int) -> bytes:
    """The bytes to send, given what is on disk."""
    if asset_type == NOTECARD_ASSET_TYPE:
        return encode_notecard(data.decode("utf-8", errors="replace"))
    return data


def first_resolved(caps: dict[str, str], names: Sequence[str]) -> str | None:
    """The first of ``names`` the simulator actually resolved.

    Order is preference, not fallback ranking: a sim that offers both a
    current and a legacy name for one handler should be talked to by its
    current name. An empty string does not count as resolved -- it would pass
    a truthiness-free lookup and then be POSTed to as a URL.
    """
    for name in names:
        url = caps.get(name)
        if url:
            return url
    return None


async def resolve_sync_caps(session: object, *, timeout: float = 10.0) -> SyncCaps:
    """The capabilities a sync can use against this session."""
    caps = await CapabilityClient(timeout_seconds=timeout).resolve_seed_caps(
        session.bootstrap.seed_capability,  # type: ignore[attr-defined]
        [*SCRIPT_TASK_CAP_NAMES, NOTECARD_TASK_CAP_NAME, NOTECARD_AGENT_CAP_NAME],
        udp_listen_port=session.caps_udp_listen_port,  # type: ignore[attr-defined]
        user_agent="Vibestorm",
    )
    return SyncCaps(
        script=first_resolved(caps, SCRIPT_TASK_CAP_NAMES),
        notecard=first_resolved(caps, [NOTECARD_TASK_CAP_NAME]),
        notecard_agent=first_resolved(caps, [NOTECARD_AGENT_CAP_NAME]),
    )


# ------------------------------------------------------------------- pull


async def pull_object_to_folder(
    client: WorldClient,
    *,
    task_id: UUID,
    local_id: int,
    folder: Path,
    overwrite_untracked: bool = False,
    include_binary: bool = False,
    on_progress: Progress | None = None,
) -> SyncOutcome:
    """Write the object's contents into ``folder``.

    Text assets by default; ``include_binary`` adds everything else as raw
    bytes, read-only.
    """
    outcome = SyncOutcome()
    folder.mkdir(parents=True, exist_ok=True)
    state = SyncState.load(folder, task_id=task_id)

    snapshot = await await_object_inventory(client, local_id)
    if snapshot is None:
        outcome.failed.append((str(task_id), "the object's inventory did not come back"))
        return outcome

    rows = rows_from_snapshot(snapshot)
    entries = plan_pull(
        rows,
        folder=folder,
        state=state,
        overwrite_untracked=overwrite_untracked,
        include_binary=include_binary,
    )
    by_name = {row.name: row for row in rows}

    for entry in entries:
        if entry.action == CONFLICT:
            outcome.conflicts.append((entry.file_name or entry.item_name, entry.reason))
            continue
        if entry.action == SKIP:
            outcome.skipped.append((entry.item_name, entry.reason))
            continue
        if entry.action != TRANSFER:
            outcome.unchanged.append(entry.file_name)
            continue

        row = by_name.get(entry.item_name)
        if row is None or row.asset_id is None:
            outcome.skipped.append((entry.item_name, "row has no asset id to fetch"))
            continue

        _report(on_progress, f"fetching {entry.file_name}")
        data = await fetch_task_asset(
            client,
            asset_id=row.asset_id,
            asset_type=row.asset_type,
            task_id=task_id,
            item_id=row.item_id,
        )
        if data is None:
            outcome.failed.append((entry.file_name, "the asset did not arrive"))
            continue

        try:
            body, readonly_reason = _decode_for_disk(data, row.asset_type)
            (folder / entry.file_name).write_bytes(body)
        except OSError as exc:
            outcome.failed.append((entry.file_name, f"could not write it: {exc}"))
            continue

        state.record(
            SyncedItem(
                file_name=entry.file_name,
                item_name=row.name,
                asset_type=row.asset_type,
                item_id=str(row.item_id) if row.item_id else None,
                synced_digest=content_digest(body),
                synced_asset_id=str(row.asset_id),
                readonly=readonly_reason is not None,
                readonly_reason=readonly_reason,
            )
        )
        outcome.transferred.append(entry.file_name)
        if readonly_reason is not None:
            outcome.skipped.append((entry.file_name, f"pulled read-only: {readonly_reason}"))

    state.save(folder)
    return outcome


# ------------------------------------------------------------------- push


async def push_folder_to_object(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    task_id: UUID,
    local_id: int,
    folder: Path,
    script_cap: str | None,
    notecard_cap: str | None = None,
    notecard_agent_cap: str | None = None,
    agent_folder_id: UUID | None = None,
    can_create: bool = True,
    on_progress: Progress | None = None,
) -> SyncOutcome:
    """Send the folder's changed files onto the object's rows.

    ``notecard_agent_cap`` and ``agent_folder_id`` together enable *creating* a
    notecard row: the only route in is to build one in agent inventory and copy
    it across, so without them an unmatched notecard can only be reported.
    """
    outcome = SyncOutcome()
    if not folder.is_dir():
        outcome.failed.append((str(folder), "folder not found"))
        return outcome
    state = SyncState.load(folder, task_id=task_id)

    snapshot = await await_object_inventory(client, local_id)
    if snapshot is None:
        outcome.failed.append((str(task_id), "the object's inventory did not come back"))
        return outcome
    rows = [row for row in rows_from_snapshot(snapshot) if row.asset_type in TEXT_ASSET_TYPES]

    files = [path for path in sorted(folder.iterdir()) if path.is_file() and path.name[0] != "."]
    can_create_notecards = bool(notecard_agent_cap and agent_folder_id)
    entries = plan_push(
        files,
        {row.name: row for row in rows},
        state=state,
        can_create=can_create and bool(script_cap),
        can_create_notecards=can_create and can_create_notecards,
    )

    to_create = [entry for entry in entries if entry.action == TRANSFER and entry.create]
    created_by_name: dict[str, object] = {}
    touched_notecards: list[tuple[str, str, int]] = []
    scripts_to_create = [e for e in to_create if e.asset_type == SCRIPT_ASSET_TYPE]
    notecards_to_create = [e for e in to_create if e.asset_type == NOTECARD_ASSET_TYPE]

    if scripts_to_create:
        created, create_skipped = await create_task_script_rows(
            client,
            session,
            handle=handle,
            task_id=task_id,
            local_id=local_id,
            names=[entry.item_name for entry in scripts_to_create],
            on_progress=on_progress,
        )
        created_by_name = {row.name: row for row in created}
        for name, reason in create_skipped:
            outcome.skipped.append((name, reason))

    # Notecards are made one at a time: each is a create in agent inventory,
    # an upload against it, and a copy into the object, and the copy is what
    # gives the row its content -- so unlike a script there is nothing left to
    # upload afterwards.
    notecards_done: set[str] = set()
    for entry in notecards_to_create:
        try:
            made = await create_task_notecard(
                client,
                session,
                handle=handle,
                local_id=local_id,
                folder_id=agent_folder_id,
                name=entry.item_name,
                text=entry.path.read_text(encoding="utf-8", errors="replace"),
                update_url=notecard_agent_cap,
                on_progress=on_progress,
            )
        except (NotecardCreateError, OSError) as exc:
            outcome.failed.append((entry.file_name, str(exc)))
            notecards_done.add(entry.file_name)
            continue
        if made is None:
            outcome.failed.append(
                (entry.file_name, "the notecard did not appear in the object")
            )
        else:
            _item_id, assigned_name = made
            # Record the name the object actually used, not the one asked for:
            # a copy whose name collides comes back as "foo 1", and the state
            # is what keeps the file bound to that row afterwards.
            outcome.created.append(entry.file_name)
            touched_notecards.append(
                (entry.file_name, assigned_name, NOTECARD_ASSET_TYPE)
            )
        notecards_done.add(entry.file_name)

    uploader = TaskInventoryUploadClient(timeout_seconds=20.0)
    touched: list[tuple[str, str, int]] = list(touched_notecards)

    for entry in entries:
        if entry.file_name in notecards_done:
            continue
        if entry.action == CONFLICT:
            outcome.conflicts.append((entry.file_name, entry.reason))
            continue
        if entry.action == SKIP:
            outcome.skipped.append((entry.file_name, entry.reason))
            continue
        if entry.action != TRANSFER:
            outcome.unchanged.append(entry.file_name)
            continue

        item_id: UUID | None = None
        asset_type = entry.asset_type
        if entry.create:
            created_row = created_by_name.get(entry.item_name)
            if created_row is None:
                continue  # already reported by create_skipped
            item_id = created_row.item_id  # type: ignore[attr-defined]
            asset_type = SCRIPT_ASSET_TYPE
        elif entry.item_id is not None:
            item_id = UUID(entry.item_id)
        if item_id is None or asset_type is None:
            outcome.skipped.append((entry.file_name, "no inventory row to upload onto"))
            continue

        cap = script_cap if asset_type == SCRIPT_ASSET_TYPE else notecard_cap
        if not cap:
            outcome.skipped.append((entry.file_name, "no capability for this asset type"))
            continue

        try:
            body = _encode_for_upload(entry.path.read_bytes(), asset_type)
        except OSError as exc:
            outcome.failed.append((entry.file_name, f"could not read it: {exc}"))
            continue

        _report(on_progress, f"uploading {entry.file_name}")
        try:
            if asset_type == SCRIPT_ASSET_TYPE:
                result = await uploader.upload_task_script(
                    cap,
                    item_id,
                    task_id,
                    body,
                    udp_listen_port=session.caps_udp_listen_port,  # type: ignore[attr-defined]
                )
                if not result.compiled:
                    outcome.failed.append(
                        (entry.file_name, f"did not compile: {_compile_error(result)}")
                    )
                    continue
            else:
                await uploader.upload_task_notecard(
                    cap,
                    item_id,
                    task_id,
                    body,
                    udp_listen_port=session.caps_udp_listen_port,  # type: ignore[attr-defined]
                )
        except (TaskInventoryUploadError, CapabilityError, OSError) as exc:
            outcome.failed.append((entry.file_name, str(exc)))
            continue

        if entry.create:
            outcome.created.append(entry.file_name)
        else:
            outcome.transferred.append(entry.file_name)
        touched.append((entry.file_name, entry.item_name, asset_type))

    if touched:
        await _record_uploaded(
            client,
            local_id=local_id,
            folder=folder,
            state=state,
            touched=touched,
        )
    state.save(folder)
    return outcome


async def _record_uploaded(
    client: WorldClient,
    *,
    local_id: int,
    folder: Path,
    state: SyncState,
    touched: list[tuple[str, str, int]],
) -> None:
    """Note what we just sent, with the asset ids the sim gave those rows.

    A script upload's result reports the *item* id, not the new asset id --
    OpenSim assigns ``uploadComplete.new_asset = m_inventoryItemID`` -- so the
    only way to learn the asset id a push produced is to read the inventory
    back. Without it every later pull would see an unfamiliar asset id and call
    our own upload an in-world edit.
    """
    snapshot = await await_object_inventory(client, local_id)
    rows_by_name = (
        {row.name: row for row in rows_from_snapshot(snapshot)} if snapshot is not None else {}
    )
    for file_name, item_name, asset_type in touched:
        row = rows_by_name.get(item_name)
        try:
            digest = content_digest((folder / file_name).read_bytes())
        except OSError:
            continue
        state.record(
            SyncedItem(
                file_name=file_name,
                item_name=item_name,
                asset_type=asset_type,
                item_id=str(row.item_id) if row and row.item_id else None,
                synced_digest=digest,
                synced_asset_id=str(row.asset_id) if row and row.asset_id else None,
            )
        )


def _compile_error(result: object) -> str:
    errors = getattr(result, "errors", None)
    if errors:
        return "; ".join(str(error) for error in errors)
    return "no error detail returned"


__all__ = [
    "NOTECARD_AGENT_CAP_NAME",
    "NOTECARD_TASK_CAP_NAME",
    "SCRIPT_TASK_CAP_NAMES",
    "first_resolved",
    "InventoryRow",
    "SyncCaps",
    "SyncOutcome",
    "pull_object_to_folder",
    "push_folder_to_object",
    "resolve_sync_caps",
    "rows_from_snapshot",
]
