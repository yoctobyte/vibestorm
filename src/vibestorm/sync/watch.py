"""Watching a synced folder and pushing what changes.

The loop deliberately does a *local* check first. Calling the full push every
tick would be correct, but each push begins with a task inventory read, and
that is an Xfer round trip: polling every two seconds would mean thirty of them
a minute against a sim that has nothing to say. Comparing digests on disk to
the recorded state costs nothing and answers the same question.

Polling rather than inotify: the folder may be on a network mount or a
container bind, where filesystem events are unreliable or absent, and a missed
edit is a much worse failure than a two-second delay.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from vibestorm.sync.engine import SyncOutcome, push_folder_to_object
from vibestorm.sync.naming import upload_kind_for_path
from vibestorm.sync.state import STATE_FILENAME, SyncState, content_digest
from vibestorm.sync.task_inventory import Progress
from vibestorm.udp.world_client import WorldClient

#: Long enough not to thrash on an editor's save-write-rename dance, short
#: enough that a save-then-test cycle does not feel like waiting.
DEFAULT_POLL_SECONDS = 2.0


def changed_files(folder: Path, state: SyncState) -> list[Path]:
    """Uploadable files whose contents differ from what was last synced.

    A file with no record counts as changed: it is either new, or the state
    was lost, and in both cases the push should look at it.
    """
    changed: list[Path] = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return changed
    for path in entries:
        if not path.is_file() or path.name == STATE_FILENAME or path.name.startswith("."):
            continue
        if upload_kind_for_path(path) is None:
            continue
        record = state.by_file_name(path.name)
        try:
            digest = content_digest(path.read_bytes())
        except OSError:
            continue
        if record is None or record.synced_digest != digest:
            changed.append(path)
    return changed


async def watch_folder(
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
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    stop_event: asyncio.Event | None = None,
    on_progress: Progress | None = None,
    on_outcome: Callable[[SyncOutcome], None] | None = None,
) -> None:
    """Push the folder whenever its files change, until ``stop_event`` is set."""
    stop_event = stop_event or asyncio.Event()
    settling: set[Path] = set()

    while not stop_event.is_set():
        state = SyncState.load(folder, task_id=task_id)
        changed = set(changed_files(folder, state))
        # Only act on files that looked changed on the previous tick as well.
        # Editors write in stages, and uploading a half-written script wastes a
        # round trip to be told it does not compile.
        ready = changed & settling
        settling = changed - ready

        if ready:
            if on_progress is not None:
                on_progress(f"{len(ready)} file(s) changed")
            outcome = await push_folder_to_object(
                client,
                session,
                handle=handle,
                task_id=task_id,
                local_id=local_id,
                folder=folder,
                script_cap=script_cap,
                notecard_cap=notecard_cap,
                notecard_agent_cap=notecard_agent_cap,
                agent_folder_id=agent_folder_id,
                on_progress=on_progress,
            )
            if on_outcome is not None:
                on_outcome(outcome)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue


__all__ = ["DEFAULT_POLL_SECONDS", "changed_files", "watch_folder"]
