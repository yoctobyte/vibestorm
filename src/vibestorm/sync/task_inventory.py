"""Reading and writing an object's contents, without a viewer attached.

These operations started life inside ``viewer3d/app.py`` as closures over the
viewer's ``Scene``, which meant the only way to sync a folder was to open a
window. Nothing here needs one: it is task inventory reads, asset fetches and
row creation, reported through a plain callback so a CLI, a test or the viewer
can each say what they like about it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from vibestorm.bus import BusError
from vibestorm.bus.commands import RequestAssetData, RequestObjectInventory
from vibestorm.bus.events import AssetDataReady, ObjectInventorySnapshotReady
from vibestorm.udp.messages import DEFAULT_SCRIPT_ASSET_ID
from vibestorm.udp.world_client import WorldClient
from vibestorm.world.object_inventory import ObjectInventorySnapshot

#: Task inventory comes back over an Xfer round trip, not in a single packet,
#: so the wait is measured in seconds rather than milliseconds.
TASK_INVENTORY_TIMEOUT = 15.0

#: One asset fetch: the HTTP capability when available, a UDP Transfer
#: handshake otherwise.
ASSET_FETCH_TIMEOUT = 30.0

#: Reporting sink. Sync is slow enough that silence reads as a hang.
Progress = Callable[[str], None]


def _report(on_progress: Progress | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)


@dataclass(slots=True, frozen=True)
class CreatedRow:
    """A task inventory row this session brought into being."""

    name: str
    item_id: UUID
    asset_id: UUID


async def await_object_inventory(
    client: WorldClient,
    local_id: int,
    *,
    timeout: float = TASK_INVENTORY_TIMEOUT,
) -> ObjectInventorySnapshot | None:
    """Request one object's task inventory and wait for the snapshot.

    Subscribes *before* dispatching: the snapshot can arrive while this
    coroutine is still being set up, and a subscription taken afterwards would
    miss it and then wait out the full timeout.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[ObjectInventorySnapshot] = loop.create_future()

    def _on_ready(event: ObjectInventorySnapshotReady) -> None:
        if event.snapshot.local_id == local_id and not future.done():
            future.set_result(event.snapshot)

    subscription = client.bus.subscribe(ObjectInventorySnapshotReady, _on_ready)
    try:
        client.bus.dispatch(RequestObjectInventory(local_id))
        return await asyncio.wait_for(future, timeout)
    except (TimeoutError, BusError):
        return None
    finally:
        subscription.cancel()


async def fetch_task_asset(
    client: WorldClient,
    *,
    asset_id: UUID,
    asset_type: int,
    task_id: UUID | None = None,
    item_id: UUID | None = None,
    timeout: float = ASSET_FETCH_TIMEOUT,
) -> bytes | None:
    """Fetch one asset out of an object, returning its bytes.

    Subscribes before dispatching, for the same reason as the inventory read.
    Matching is on the asset id, so a fetch cannot be completed by some other
    download that happened to finish first.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()

    def _on_ready(event: AssetDataReady) -> None:
        if event.asset_id == asset_id and not future.done():
            future.set_result(event.data)

    subscription = client.bus.subscribe(AssetDataReady, _on_ready)
    try:
        client.bus.dispatch(
            RequestAssetData(
                asset_id=asset_id,
                asset_type=asset_type,
                task_id=task_id,
                item_id=item_id,
            )
        )
        return await asyncio.wait_for(future, timeout)
    except (TimeoutError, BusError):
        return None
    finally:
        subscription.cancel()


async def create_task_script_rows(
    client: WorldClient,
    session: object,
    *,
    handle: int,
    task_id: UUID,
    local_id: int,
    names: Sequence[str],
    timeout: float = TASK_INVENTORY_TIMEOUT,
    on_progress: Progress | None = None,
) -> tuple[list[CreatedRow], list[tuple[str, str]]]:
    """Create one empty script per name, then resolve the rows they became.

    Returns the rows that now exist, plus the names that did not get one and
    why.

    The rows are created in a batch and the inventory is re-read **once**. A
    read per name would be correct too, but each read is an Xfer round trip, so
    a ten-file folder would spend ten of them to learn what one tells us.

    New rows are identified by diffing item ids against a baseline rather than
    by looking for the name: an object may already hold a script called
    ``foo`` while the file ``foo.lsl`` failed to match for some other reason,
    and then matching on name would upload over the wrong row.
    """
    names = list(names)
    if not names:
        return [], []

    before = await await_object_inventory(client, local_id, timeout=timeout)
    if before is None:
        return [], [(name, "could not read object inventory before creating") for name in names]
    baseline = {item.item_id for item in before.items if item.item_id is not None}

    for name in names:
        packet = session.build_rez_script_packet(  # type: ignore[attr-defined]
            part_id=task_id,
            local_id=local_id,
            name=name,
            description="created by Vibestorm folder sync",
        )
        client.queue_outbound_packet(handle, packet)
        _report(on_progress, f"creating {name}")

    after = await await_object_inventory(client, local_id, timeout=timeout)
    if after is None:
        return [], [(name, "created, but the object inventory did not come back") for name in names]

    added = {
        item.name: item
        for item in after.items
        if item.item_id is not None and item.item_id not in baseline
    }

    created: list[CreatedRow] = []
    skipped: list[tuple[str, str]] = []
    for name in names:
        item = added.pop(name, None)
        if item is None or item.item_id is None:
            skipped.append((name, "the sim did not create a row for it"))
            continue
        created.append(
            CreatedRow(
                name=item.name,
                item_id=item.item_id,
                asset_id=item.asset_id or DEFAULT_SCRIPT_ASSET_ID,
            )
        )
    return created, skipped


__all__ = [
    "ASSET_FETCH_TIMEOUT",
    "TASK_INVENTORY_TIMEOUT",
    "CreatedRow",
    "await_object_inventory",
    "create_task_script_rows",
    "fetch_task_asset",
]
