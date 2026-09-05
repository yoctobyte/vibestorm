"""Putting a notecard inside an object.

There is no create-from-nothing message for a notecard in a prim. Scripts have
``RezScript``; notecards do not, and ``Scene.UpdateTaskInventory`` rejects a
zero item id -- an unknown one it looks up in *agent* inventory and copies in.
So the route is two hops, and both had been proven separately before this
joined them:

1. Create it in agent inventory. ``CreateInventoryItem`` makes an empty
   notecard, then ``UpdateNotecardAgentInventory`` uploads the real bytes
   against that item id.
2. Copy it into the prim with ``UpdateTaskInventory``.

``NewFileAgentInventory`` is not a shortcut for hop 1: it handles six inventory
types and notecard is not one of them, so the item is stored as asset type 0, a
texture.

One trap on hop 2: the simulator *removes* a no-copy item from agent inventory,
because copying it in is a move. Items created here are full-perm and so
unaffected, but nothing else should assume that.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass
from uuid import UUID

from vibestorm.assets.notecard import encode_notecard
from vibestorm.caps.task_inventory_upload_client import (
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)
from vibestorm.sync.task_inventory import Progress, await_object_inventory
from vibestorm.udp.world_client import WorldClient

#: Asset and inventory type for a notecard. Notecards are 7/7 -- read off the
#: grid's own library rather than assumed, because the two enumerations
#: disagree for clothing (5/18), animation (20/19) and gesture (21/20).
INVENTORY_NOTECARD = 7

#: CreateInventoryItem echoes this back so a reply can be matched to a request.
#: One counter per process is enough; a session that sent two creates with the
#: same id could not tell the replies apart.
_callback_ids = itertools.count(1000)

CREATE_TIMEOUT = 30.0


class NotecardCreateError(RuntimeError):
    """Raised when a notecard could not be created or filled in."""


@dataclass(slots=True, frozen=True)
class CreatedNotecard:
    item_id: UUID
    asset_id: UUID | None
    name: str


async def create_agent_notecard(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    folder_id: UUID,
    name: str,
    text: str,
    update_url: str,
    description: str = "Created by Vibestorm folder sync.",
    timeout: float = CREATE_TIMEOUT,
    on_progress: Progress | None = None,
) -> CreatedNotecard:
    """Hop 1: an agent-inventory notecard holding ``text``."""
    callback_id = next(_callback_ids)
    client.queue_outbound_packet(
        handle,
        session.build_create_inventory_item_packet(  # type: ignore[attr-defined]
            folder_id,
            name=name,
            description=description,
            asset_type=INVENTORY_NOTECARD,
            inv_type=INVENTORY_NOTECARD,
            callback_id=callback_id,
        ),
    )
    if on_progress is not None:
        on_progress(f"creating agent notecard {name}")

    # The reply lands in the session's own table. Polling it is enough: this
    # runs on the same loop as the session, so the value appears between
    # sleeps, and waiting on an event would mean subscribing to a bus event
    # that does not exist yet.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    created = None
    while loop.time() < deadline:
        created = session.created_inventory_items.pop(callback_id, None)  # type: ignore[attr-defined]
        if created is not None:
            break
        await asyncio.sleep(0.2)
    if created is None:
        raise NotecardCreateError(
            f"no UpdateCreateInventoryItem reply for {name!r} within {timeout:.0f}s"
        )

    try:
        await TaskInventoryUploadClient(timeout_seconds=20.0).upload_agent_notecard(
            update_url, created.item_id, encode_notecard(text)
        )
    except (TaskInventoryUploadError, OSError) as exc:
        raise NotecardCreateError(f"could not fill in {name!r}: {exc}") from exc

    return CreatedNotecard(
        item_id=created.item_id, asset_id=getattr(created, "asset_id", None), name=name
    )


async def copy_item_into_object(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    local_id: int,
    item_id: UUID,
    name: str,
    description: str = "",
    asset_type: int = INVENTORY_NOTECARD,
    inv_type: int = INVENTORY_NOTECARD,
    timeout: float = 15.0,
    on_progress: Progress | None = None,
) -> tuple[UUID, str] | None:
    """Hop 2: copy an agent inventory item into the prim.

    Returns ``(task item id, the name the object gave it)``, or None if the row
    did not appear. The task item id differs from the agent inventory one.

    The name is returned because the simulator may not use the one asked for:
    an object already holding ``foo`` receives the copy as ``foo 1``. Matching
    the new row by name therefore misses it and reports a failure for a copy
    that in fact succeeded, so the row is found by diffing item ids instead.
    """
    before = await await_object_inventory(client, local_id, timeout=timeout)
    baseline = (
        {item.item_id for item in before.items if item.item_id is not None}
        if before is not None
        else set()
    )

    client.queue_outbound_packet(
        handle,
        session.build_update_task_inventory_packet(  # type: ignore[attr-defined]
            local_id=local_id,
            item_id=item_id,
            name=name,
            description=description,
            asset_type=asset_type,
            inv_type=inv_type,
        ),
    )
    if on_progress is not None:
        on_progress(f"copying {name} into the object")

    after = await await_object_inventory(client, local_id, timeout=timeout)
    if after is None:
        return None
    added = [
        item for item in after.items if item.item_id is not None and item.item_id not in baseline
    ]
    if not added:
        return None
    # Prefer the name asked for; otherwise, if exactly one row appeared, it is
    # unambiguously ours.
    exact = next((item for item in added if item.name == name), None)
    chosen = exact or (added[0] if len(added) == 1 else None)
    if chosen is None:
        return None
    if on_progress is not None and chosen.name != name:
        on_progress(f"the object named it {chosen.name!r} instead of {name!r}")
    return chosen.item_id, chosen.name


async def create_task_notecard(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    local_id: int,
    folder_id: UUID,
    name: str,
    text: str,
    update_url: str,
    on_progress: Progress | None = None,
) -> tuple[UUID, str] | None:
    """Both hops: a notecard inside the object holding ``text``.

    Returns ``(task item id, the name the object gave it)``, or None if the row
    never appeared.
    """
    created = await create_agent_notecard(
        client,
        session,
        handle=handle,
        folder_id=folder_id,
        name=name,
        text=text,
        update_url=update_url,
        on_progress=on_progress,
    )
    return await copy_item_into_object(
        client,
        session,
        handle=handle,
        local_id=local_id,
        item_id=created.item_id,
        name=name,
        on_progress=on_progress,
    )


__all__ = [
    "CREATE_TIMEOUT",
    "INVENTORY_NOTECARD",
    "CreatedNotecard",
    "NotecardCreateError",
    "copy_item_into_object",
    "create_agent_notecard",
    "create_task_notecard",
]
