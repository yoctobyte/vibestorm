"""Client for the NewFileAgentInventory upload capability."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient
from vibestorm.caps.llsd import LlsdError, parse_xml_value
from vibestorm.util.http_body import MAX_LLSD_BODY_BYTES, read_bounded
from vibestorm.util.remote_url import open_remote, require_remote_http_url


class AssetUploadError(RuntimeError):
    """Raised when an inventory asset upload fails."""


#: The only ``inventory_type`` values ``NewFileAgentInventory`` understands.
#:
#: OpenSim's ``BunchOfCaps.UploadCompleteHandler`` starts with
#: ``sbyte assType = 0; sbyte inType = 0;`` and then branches on exactly these
#: strings. Anything else falls through every branch and the item is created
#: with **both types left at 0** — asset type 0 is *texture*. The upload
#: reports success and the resulting item is mistyped, which is how a notecard
#: uploaded by this client reads back as a texture.
#:
#: Notecards and scripts are not uploaded through this capability at all: a
#: viewer creates them with ``CreateInventoryItem`` and then fills them in
#: through ``UpdateNotecardAgentInventory`` / ``UpdateScriptAgent``.
NEW_FILE_INVENTORY_TYPES: frozenset[str] = frozenset(
    {"sound", "snapshot", "animation", "animset", "wearable", "object"}
)

#: The one type the fall-through gets *right*.
#:
#: ``"texture"`` matches none of the branches above, so it keeps
#: ``assType = 0`` and ``inType = 0`` — and ``AssetType.Texture`` and
#: ``InventoryType.Texture`` are both 0, so the item lands correctly typed.
#: The upload works, and works for a reason nobody chose.
#:
#: Which is why it is pinned rather than trusted: "no branch matches" is a
#: claim that rots without anything failing, so
#: ``TextureUploadFallsThroughToTypeZeroTests`` reads the branch list out of
#: the committed OpenSim source and fails if ``texture`` ever appears in it.
NEW_FILE_FALLTHROUGH_TYPES: frozenset[str] = frozenset({"texture"})


def new_file_inventory_type_warning(inventory_type: str) -> str | None:
    """Warn when ``NewFileAgentInventory`` will silently mistype the item.

    Returns None for a type the capability handles, and for ``texture``,
    where falling through every branch is how the right answer is reached.
    This does not raise: the upload really does create an item, so refusing
    would be wrong — but reporting success without saying the item is
    mistyped would be worse.
    """
    if inventory_type in NEW_FILE_INVENTORY_TYPES:
        return None
    if inventory_type in NEW_FILE_FALLTHROUGH_TYPES:
        return None
    supported = ", ".join(sorted(NEW_FILE_INVENTORY_TYPES | NEW_FILE_FALLTHROUGH_TYPES))
    return (
        f"NewFileAgentInventory does not handle inventory_type={inventory_type!r}; "
        f"OpenSim will store the item with asset type 0 (texture) and inventory "
        f"type 0. Supported: {supported}."
    )


@dataclass(slots=True, frozen=True)
class NewFileInventoryRequest:
    folder_id: UUID
    name: str
    description: str = ""
    asset_type: str = "notecard"
    inventory_type: str = "notecard"
    next_owner_mask: int = 0x7FFFFFFF
    group_mask: int = 0
    everyone_mask: int = 0


@dataclass(slots=True, frozen=True)
class AssetUploadPrelude:
    uploader_url: str
    state: str
    upload_price: int | None = None


@dataclass(slots=True, frozen=True)
class AssetUploadResult:
    state: str
    new_asset_id: UUID | None
    new_inventory_item_id: UUID | None
    new_next_owner_mask: int | None = None
    new_group_mask: int | None = None
    new_everyone_mask: int | None = None
    inventory_item_flags: int | None = None


@dataclass(slots=True)
class AssetUploadClient:
    """Perform a two-step NewFileAgentInventory upload."""

    timeout_seconds: float = 10.0

    async def request_new_file_uploader(
        self,
        url: str,
        request: NewFileInventoryRequest,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> AssetUploadPrelude:
        return await asyncio.to_thread(
            self._request_new_file_uploader_sync,
            url,
            request,
            udp_listen_port,
            user_agent,
        )

    async def upload_bytes(
        self,
        uploader_url: str,
        data: bytes,
        *,
        user_agent: str = "Vibestorm",
    ) -> AssetUploadResult:
        return await asyncio.to_thread(self._upload_bytes_sync, uploader_url, data, user_agent)

    async def upload_new_file(
        self,
        url: str,
        request: NewFileInventoryRequest,
        data: bytes,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> AssetUploadResult:
        prelude = await self.request_new_file_uploader(
            url,
            request,
            udp_listen_port=udp_listen_port,
            user_agent=user_agent,
        )
        if prelude.state != "upload":
            raise AssetUploadError(
                f"NewFileAgentInventory returned unexpected state {prelude.state!r}"
            )
        return await self.upload_bytes(prelude.uploader_url, data, user_agent=user_agent)

    def _request_new_file_uploader_sync(
        self,
        url: str,
        request: NewFileInventoryRequest,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> AssetUploadPrelude:
        payload = {
            "asset_type": request.asset_type,
            "description": request.description,
            "folder_id": request.folder_id,
            "inventory_type": request.inventory_type,
            "name": request.name,
            "next_owner_mask": request.next_owner_mask,
            "group_mask": request.group_mask,
            "everyone_mask": request.everyone_mask,
        }
        try:
            capability_client = CapabilityClient(timeout_seconds=self.timeout_seconds)
            result = capability_client._post_capability_value_sync(
                url,
                payload,
                udp_listen_port,
                user_agent,
            )
        except Exception as exc:
            raise AssetUploadError(str(exc)) from exc
        if not isinstance(result, dict):
            raise AssetUploadError("NewFileAgentInventory prelude did not return an LLSD map")
        state = _parse_str(result.get("state"))
        uploader_url = _parse_str(result.get("uploader"))
        if state == "error":
            raise AssetUploadError(
                _extract_error_message(result) or "NewFileAgentInventory returned error"
            )
        if not uploader_url:
            raise AssetUploadError("NewFileAgentInventory prelude did not include an uploader URL")
        return AssetUploadPrelude(
            uploader_url=uploader_url,
            state=state,
            upload_price=_parse_int(result.get("upload_price")),
        )

    def _upload_bytes_sync(
        self,
        uploader_url: str,
        data: bytes,
        user_agent: str = "Vibestorm",
    ) -> AssetUploadResult:
        # Before the `Request`, which raises a bare `ValueError` on a URL with
        # no scheme at all -- and that is not AssetUploadError.
        require_remote_http_url(uploader_url, what="the asset upload capability", error=AssetUploadError)
        request = urllib.request.Request(
            uploader_url,
            data=data,
            headers={
                "Accept": "application/llsd+xml",
                "Content-Type": "application/octet-stream",
                "User-Agent": user_agent,
            },
            method="POST",
        )
        try:
            with open_remote(
                request,
                timeout=self.timeout_seconds,
                what="the asset upload capability",
                error=AssetUploadError,
            ) as response:
                payload = parse_xml_value(
                    read_bounded(
                        response,
                        max_bytes=MAX_LLSD_BODY_BYTES,
                        what="asset upload response",
                        error=AssetUploadError,
                    )
                )
        except TimeoutError as exc:
            raise AssetUploadError(
                f"asset upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise AssetUploadError(f"asset upload failed: {exc.reason}") from exc
        except LlsdError as exc:
            # The body arrived and was not LLSD -- a proxy's error page, a
            # truncated response. Converted here rather than left to the
            # caller: `LlsdError` is caught nowhere in this client, so
            # letting it through would only rename the exception that
            # escapes.
            raise AssetUploadError(f"asset upload response was not valid LLSD: {exc}") from exc
        if not isinstance(payload, dict):
            raise AssetUploadError("asset upload completion did not return an LLSD map")
        state = _parse_str(payload.get("state"))
        if state not in {"complete", "upload"}:
            raise AssetUploadError(
                _extract_error_message(payload) or f"asset upload returned state {state!r}"
            )
        return AssetUploadResult(
            state=state,
            new_asset_id=_parse_uuid(payload.get("new_asset")),
            new_inventory_item_id=_parse_uuid(payload.get("new_inventory_item")),
            new_next_owner_mask=_parse_int(payload.get("new_next_owner_mask")),
            new_group_mask=_parse_int(payload.get("new_group_mask")),
            new_everyone_mask=_parse_int(payload.get("new_everyone_mask")),
            inventory_item_flags=_parse_int(payload.get("inventory_item_flags")),
        )


def _parse_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _extract_error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return _parse_str(error.get("message"))
    return ""


__all__ = [
    "AssetUploadClient",
    "AssetUploadError",
    "AssetUploadPrelude",
    "AssetUploadResult",
    "NewFileInventoryRequest",
]
