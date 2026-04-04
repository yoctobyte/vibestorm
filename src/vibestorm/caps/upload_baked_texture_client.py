"""Client for the OpenSim/SL UploadBakedTexture capability."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.caps.llsd import parse_xml_value


class UploadBakedTextureError(RuntimeError):
    """Raised when the UploadBakedTexture flow fails."""


@dataclass(slots=True, frozen=True)
class UploadBakedTexturePrelude:
    uploader_url: str
    state: str | None


@dataclass(slots=True, frozen=True)
class UploadBakedTextureResult:
    state: str | None
    new_asset_id: str | None
    new_inventory_item_id: str | None


@dataclass(slots=True)
class UploadBakedTextureClient:
    """Perform the two-step UploadBakedTexture capability flow."""

    timeout_seconds: float = 10.0

    async def request_uploader(
        self,
        capability_url: str,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> UploadBakedTexturePrelude:
        return await asyncio.to_thread(
            self._request_uploader_sync,
            capability_url,
            udp_listen_port,
            user_agent,
        )

    async def upload_texture_bytes(
        self,
        uploader_url: str,
        texture_bytes: bytes,
        *,
        user_agent: str = "Vibestorm",
    ) -> UploadBakedTextureResult:
        return await asyncio.to_thread(
            self._upload_texture_bytes_sync,
            uploader_url,
            texture_bytes,
            user_agent,
        )

    async def upload_via_capability(
        self,
        capability_url: str,
        texture_bytes: bytes,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> UploadBakedTextureResult:
        prelude = await self.request_uploader(
            capability_url,
            udp_listen_port=udp_listen_port,
            user_agent=user_agent,
        )
        return await self.upload_texture_bytes(
            prelude.uploader_url,
            texture_bytes,
            user_agent=user_agent,
        )

    def _request_uploader_sync(
        self,
        capability_url: str,
        udp_listen_port: int | None,
        user_agent: str,
    ) -> UploadBakedTexturePrelude:
        client = CapabilityClient(timeout_seconds=self.timeout_seconds)
        try:
            payload = client._post_capability_value_sync(
                capability_url,
                {},
                udp_listen_port=udp_listen_port,
                user_agent=user_agent,
            )
        except CapabilityError as exc:
            raise UploadBakedTextureError(str(exc)) from exc

        if not isinstance(payload, dict):
            raise UploadBakedTextureError("UploadBakedTexture prelude did not return an LLSD map")
        uploader_url = payload.get("uploader")
        if not isinstance(uploader_url, str) or not uploader_url:
            raise UploadBakedTextureError("UploadBakedTexture prelude did not include an uploader URL")
        state = payload.get("state")
        return UploadBakedTexturePrelude(
            uploader_url=uploader_url,
            state=state if isinstance(state, str) else None,
        )

    def _upload_texture_bytes_sync(
        self,
        uploader_url: str,
        texture_bytes: bytes,
        user_agent: str,
    ) -> UploadBakedTextureResult:
        request = urllib.request.Request(
            uploader_url,
            data=texture_bytes,
            headers={
                "Accept": "application/llsd+xml",
                "Connection": "keep-alive",
                "Keep-Alive": "300",
                "User-Agent": user_agent,
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = parse_xml_value(response.read())
        except TimeoutError as exc:
            raise UploadBakedTextureError(
                f"UploadBakedTexture upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise UploadBakedTextureError(
                f"UploadBakedTexture upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise UploadBakedTextureError(f"UploadBakedTexture upload failed: {exc.reason}") from exc

        if not isinstance(payload, dict):
            raise UploadBakedTextureError("UploadBakedTexture completion did not return an LLSD map")
        state = payload.get("state")
        new_asset = payload.get("new_asset")
        new_inventory_item = payload.get("new_inventory_item")
        return UploadBakedTextureResult(
            state=state if isinstance(state, str) else None,
            new_asset_id=new_asset if isinstance(new_asset, str) and new_asset else None,
            new_inventory_item_id=(
                new_inventory_item if isinstance(new_inventory_item, str) and new_inventory_item else None
            ),
        )
