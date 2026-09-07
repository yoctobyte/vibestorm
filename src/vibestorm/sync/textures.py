"""Getting a texture the owner made into an object, in two hops.

The same shape as `notecards.py`, and for the same reason: there is no way to
create a task-inventory row out of nothing. `UpdateTaskInventory` rejects a
zero item id before it does anything else, so the asset has to exist in
*agent* inventory first and then be copied across.

What differs is hop one. A notecard is created empty and then filled in
through `UpdateNotecardAgentInventory`, because a notecard's content is text
the simulator will happily take later. A texture arrives as a finished asset,
so it goes up through `NewFileAgentInventory`, which creates the item and its
asset together.

Hop two is `copy_item_into_object`, unchanged and shared -- it was already
general over asset type, which is why there is nothing here for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from vibestorm.caps.asset_upload_client import AssetUploadClient, NewFileInventoryRequest
from vibestorm.sync.new_assets import prepare_new_asset
from vibestorm.sync.notecards import copy_item_into_object
from vibestorm.sync.task_inventory import Progress
from vibestorm.udp.world_client import WorldClient

#: `AssetType.Texture` and `InventoryType.Texture` are both 0. See
#: `TextureUploadFallsThroughToTypeZeroTests`: this is also the value
#: `NewFileAgentInventory` lands on by taking none of its branches.
INVENTORY_TEXTURE = 0

#: The capability that creates an agent inventory item and its asset in one
#: exchange. Named here rather than in `engine.py` beside the notecard and
#: script caps because it is not a *task* capability -- it writes into the
#: agent's own inventory, and the object only sees the result second-hand.
NEW_FILE_CAP_NAME = "NewFileAgentInventory"


class TextureUploadError(RuntimeError):
    """Raised when a texture cannot be got into agent inventory."""


@dataclass(slots=True, frozen=True)
class UploadedTexture:
    """What hop one produced."""

    item_id: UUID
    asset_id: UUID | None
    #: Bytes actually sent, which is the encoded size and not the file's.
    bytes_sent: int
    #: False when the file was already a JPEG2000 codestream.
    re_encoded: bool


async def upload_agent_texture(
    path: Path,
    data: bytes,
    *,
    folder_id: UUID,
    upload_url: str,
    name: str | None = None,
    description: str = "",
    client: AssetUploadClient | None = None,
    udp_listen_port: int | None = None,
    on_progress: Progress | None = None,
) -> UploadedTexture:
    """Hop 1: the owner's file, encoded, as an item in agent inventory.

    `name` defaults to the file's stem, because a texture called
    `sunset.png` in world reads as a file rather than as a texture -- the
    suffix said which encoder to use and has nothing left to say afterwards.
    """
    prepared = prepare_new_asset(path, data)
    item_name = name if name is not None else path.stem

    request = NewFileInventoryRequest(
        folder_id=folder_id,
        name=item_name,
        description=description,
        asset_type=prepared.asset_type,
        inventory_type=prepared.inventory_type,
    )
    if on_progress is not None:
        detail = "re-encoded" if prepared.re_encoded else "already JPEG2000"
        on_progress(f"uploading {item_name} ({len(prepared.data):,} bytes, {detail})")

    uploader = client if client is not None else AssetUploadClient(timeout_seconds=30.0)
    result = await uploader.upload_new_file(
        upload_url,
        request,
        prepared.data,
        udp_listen_port=udp_listen_port,
    )
    if result.new_inventory_item_id is None:
        # The capability answered, so this is not a transport failure. It
        # means the upload completed without creating an item -- an
        # insufficient-funds refusal reads like this on a grid that charges.
        raise TextureUploadError(
            f"{item_name}: the upload returned state {result.state!r} and no inventory item"
        )
    return UploadedTexture(
        item_id=result.new_inventory_item_id,
        asset_id=result.new_asset_id,
        bytes_sent=len(prepared.data),
        re_encoded=prepared.re_encoded,
    )


async def create_task_texture(
    world: WorldClient,
    session: object,
    *,
    handle: int,
    local_id: int,
    folder_id: UUID,
    upload_url: str,
    path: Path,
    data: bytes,
    name: str | None = None,
    description: str = "",
    upload_client: AssetUploadClient | None = None,
    udp_listen_port: int | None = None,
    on_progress: Progress | None = None,
) -> tuple[UUID, str] | None:
    """Both hops: a texture inside the object, from a file on disk.

    Returns ``(task item id, the name the object gave it)``, or None if the
    row never appeared. The name can differ from the one asked for: an object
    already holding `sunset` receives the copy as `sunset 1`.
    """
    item_name = name if name is not None else path.stem
    uploaded = await upload_agent_texture(
        path,
        data,
        folder_id=folder_id,
        upload_url=upload_url,
        name=item_name,
        description=description,
        client=upload_client,
        udp_listen_port=udp_listen_port,
        on_progress=on_progress,
    )
    return await copy_item_into_object(
        world,
        session,
        handle=handle,
        local_id=local_id,
        item_id=uploaded.item_id,
        name=item_name,
        description=description,
        asset_type=INVENTORY_TEXTURE,
        inv_type=INVENTORY_TEXTURE,
        on_progress=on_progress,
    )


__all__ = [
    "INVENTORY_TEXTURE",
    "NEW_FILE_CAP_NAME",
    "TextureUploadError",
    "UploadedTexture",
    "create_task_texture",
    "upload_agent_texture",
]
