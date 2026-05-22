"""Client for the NewFileAgentInventory upload capability."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient
from vibestorm.caps.llsd import parse_xml_value


class AssetUploadError(RuntimeError):
    """Raised when an inventory asset upload fails."""


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
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = parse_xml_value(response.read())
        except TimeoutError as exc:
            raise AssetUploadError(
                f"asset upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise AssetUploadError(f"asset upload failed: {exc.reason}") from exc
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
