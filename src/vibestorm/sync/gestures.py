"""Putting a gesture inside an object.

The same two hops as a notecard, for the same reason: there is no
create-from-nothing message for a gesture in a prim, so it is made in agent
inventory and copied in. What differs is one number and one check.

**The number.** A gesture is asset type 21 and inventory type 20, and this is
the first type this client creates where the two enumerations disagree --
notecards are 7/7, textures 0/0. The divergence is recorded in
`caps/inventory_types`, whose own docstring says libomv's ``InventoryType``
table is not in the committed OpenSim source and is deliberately left
unguessed. So `INV_TYPE_GESTURE` below is **not** pinned by a source test the
way the asset type is; it is checked against a live grid by
`tools/verify_gesture_sync.py`, which reads the inventory type back off the
row it created and off the account's existing gestures, and fails loudly
rather than quietly making a mistyped item.

**The check.** `decode_gesture` runs before the create, not only before the
update. Nothing on the far side looks at a gesture's contents, so an item
created here holding bytes nothing can play is a row an owner has to find and
delete by hand.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from vibestorm.assets.gesture import GestureDecodeError, decode_gesture
from vibestorm.sync.notecards import (
    NotecardCreateError,
    copy_item_into_object,
    create_agent_item,
)
from vibestorm.sync.task_inventory import Progress
from vibestorm.udp.world_client import WorldClient

#: Asset type, which is what a task inventory row reports and what
#: `naming.py` maps `.gesture` to.
ASSET_TYPE_GESTURE = 21

#: Inventory type, which is what `CreateInventoryItem` and
#: `UpdateTaskInventory` carry. See the module docstring: measured, not pinned.
INV_TYPE_GESTURE = 20

#: The agent-side half of the pair whose task-side half `sync/engine` uses.
#: One handler serves both -- `UpdateGestureItemAsset` -- and which inventory
#: the update lands in is decided by whether ``task_id`` is in the request.
GESTURE_AGENT_CAP_NAME = "UpdateGestureAgentInventory"


class GestureCreateError(RuntimeError):
    """Raised when a gesture could not be created, filled in, or copied."""


async def create_task_gesture(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    local_id: int,
    folder_id: UUID,
    update_url: str,
    data: bytes,
    name: str,
    path: Path | None = None,
    on_progress: Progress | None = None,
) -> tuple[UUID, str] | None:
    """Both hops: a gesture inside the object holding ``data``.

    Returns ``(task item id, the name the object gave it)``, or None if the
    row never appeared. The name is returned because a copy whose name
    collides arrives as ``foo 1`` and nothing in the reply says so.
    """
    try:
        decode_gesture(data)
    except GestureDecodeError as exc:
        where = f" ({path})" if path is not None else ""
        raise GestureCreateError(
            f"{name!r} is not a readable gesture{where}: {exc}"
        ) from exc

    try:
        created = await create_agent_item(
            client,
            session,
            handle=handle,
            folder_id=folder_id,
            name=name,
            data=data,
            asset_type=ASSET_TYPE_GESTURE,
            inv_type=INV_TYPE_GESTURE,
            update_url=update_url,
            on_progress=on_progress,
            what="gesture",
        )
    except NotecardCreateError as exc:
        raise GestureCreateError(str(exc)) from exc

    return await copy_item_into_object(
        client,
        session,
        handle=handle,
        local_id=local_id,
        item_id=created.item_id,
        name=name,
        asset_type=ASSET_TYPE_GESTURE,
        inv_type=INV_TYPE_GESTURE,
        on_progress=on_progress,
    )


__all__ = [
    "ASSET_TYPE_GESTURE",
    "GESTURE_AGENT_CAP_NAME",
    "INV_TYPE_GESTURE",
    "GestureCreateError",
    "create_task_gesture",
]
