"""Client for task inventory script and notecard upload capabilities."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.caps.llsd import parse_xml_value


class TaskInventoryUploadError(RuntimeError):
    """Raised when a task inventory upload fails."""


@dataclass(slots=True, frozen=True)
class TaskInventoryUploadPrelude:
    uploader_url: str
    state: str


@dataclass(slots=True, frozen=True)
class TaskScriptUploadResult:
    state: str
    compiled: bool
    new_asset_id: UUID | None
    errors: list[object]


@dataclass(slots=True, frozen=True)
class TaskNotecardUploadResult:
    state: str
    new_asset_id: UUID | None
    new_inventory_item_id: UUID | None


@dataclass(slots=True)
class TaskInventoryUploadClient:
    """Perform two-step task inventory updates (scripts and notecards)."""

    timeout_seconds: float = 10.0

    async def upload_task_script(
        self,
        capability_url: str,
        item_id: UUID,
        task_id: UUID,
        script_bytes: bytes,
        *,
        is_script_running: bool = True,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> TaskScriptUploadResult:
        """Upload a script update to an object's task inventory."""
        prelude = await self.request_uploader(
            capability_url,
            {"item_id": item_id, "task_id": task_id, "is_script_running": is_script_running},
            udp_listen_port=udp_listen_port,
            user_agent=user_agent,
        )
        if prelude.state != "upload":
            raise TaskInventoryUploadError(
                f"Task inventory script upload returned unexpected prelude state {prelude.state!r}"
            )
        return await self.upload_script_bytes(prelude.uploader_url, script_bytes, user_agent=user_agent)

    async def upload_task_notecard(
        self,
        capability_url: str,
        item_id: UUID,
        task_id: UUID,
        notecard_bytes: bytes,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> TaskNotecardUploadResult:
        """Upload a notecard update to an object's task inventory."""
        prelude = await self.request_uploader(
            capability_url,
            {"item_id": item_id, "task_id": task_id},
            udp_listen_port=udp_listen_port,
            user_agent=user_agent,
        )
        if prelude.state != "upload":
            raise TaskInventoryUploadError(
                f"Task inventory notecard upload returned unexpected prelude state {prelude.state!r}"
            )
        return await self.upload_notecard_bytes(prelude.uploader_url, notecard_bytes, user_agent=user_agent)

    async def request_uploader(
        self,
        capability_url: str,
        payload: dict[str, object],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> TaskInventoryUploadPrelude:
        return await asyncio.to_thread(
            self._request_uploader_sync,
            capability_url,
            payload,
            udp_listen_port,
            user_agent,
        )

    async def upload_script_bytes(
        self,
        uploader_url: str,
        script_bytes: bytes,
        *,
        user_agent: str = "Vibestorm",
    ) -> TaskScriptUploadResult:
        return await asyncio.to_thread(
            self._upload_script_bytes_sync,
            uploader_url,
            script_bytes,
            user_agent,
        )

    async def upload_notecard_bytes(
        self,
        uploader_url: str,
        notecard_bytes: bytes,
        *,
        user_agent: str = "Vibestorm",
    ) -> TaskNotecardUploadResult:
        return await asyncio.to_thread(
            self._upload_notecard_bytes_sync,
            uploader_url,
            notecard_bytes,
            user_agent,
        )

    def _request_uploader_sync(
        self,
        capability_url: str,
        payload: dict[str, object],
        udp_listen_port: int | None,
        user_agent: str,
    ) -> TaskInventoryUploadPrelude:
        client = CapabilityClient(timeout_seconds=self.timeout_seconds)
        try:
            result = client._post_capability_value_sync(
                capability_url,
                payload,
                udp_listen_port=udp_listen_port,
                user_agent=user_agent,
            )
        except CapabilityError as exc:
            raise TaskInventoryUploadError(str(exc)) from exc

        if not isinstance(result, dict):
            raise TaskInventoryUploadError("Task inventory upload prelude did not return an LLSD map")

        state = _parse_str(result.get("state"))
        uploader_url = _parse_str(result.get("uploader"))

        if state == "error":
            error_msg = _extract_error_message(result) or "Task inventory upload returned error"
            raise TaskInventoryUploadError(error_msg)

        if not uploader_url:
            raise TaskInventoryUploadError("Task inventory upload prelude did not include an uploader URL")

        return TaskInventoryUploadPrelude(
            uploader_url=uploader_url,
            state=state,
        )

    def _upload_script_bytes_sync(
        self,
        uploader_url: str,
        script_bytes: bytes,
        user_agent: str,
    ) -> TaskScriptUploadResult:
        request = urllib.request.Request(
            uploader_url,
            data=script_bytes,
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
            raise TaskInventoryUploadError(
                f"script task upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise TaskInventoryUploadError(
                f"script task upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise TaskInventoryUploadError(f"script task upload failed: {exc.reason}") from exc

        if not isinstance(payload, dict):
            raise TaskInventoryUploadError("script task upload completion did not return an LLSD map")

        state = _parse_str(payload.get("state"))
        if state not in {"complete", "upload"}:
            raise TaskInventoryUploadError(
                _extract_error_message(payload) or f"script task upload returned state {state!r}"
            )

        compiled_val = payload.get("compiled", True)
        compiled = bool(compiled_val) if isinstance(compiled_val, (bool, int)) else True

        raw_errors = payload.get("errors")
        errors: list[object] = []
        if isinstance(raw_errors, list):
            errors = list(raw_errors)

        return TaskScriptUploadResult(
            state=state,
            compiled=compiled,
            new_asset_id=_parse_uuid(payload.get("new_asset")),
            errors=errors,
        )

    def _upload_notecard_bytes_sync(
        self,
        uploader_url: str,
        notecard_bytes: bytes,
        user_agent: str,
    ) -> TaskNotecardUploadResult:
        request = urllib.request.Request(
            uploader_url,
            data=notecard_bytes,
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
            raise TaskInventoryUploadError(
                f"notecard task upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise TaskInventoryUploadError(
                f"notecard task upload timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.URLError as exc:
            raise TaskInventoryUploadError(f"notecard task upload failed: {exc.reason}") from exc

        if not isinstance(payload, dict):
            raise TaskInventoryUploadError("notecard task upload completion did not return an LLSD map")

        state = _parse_str(payload.get("state"))
        if state not in {"complete", "upload"}:
            raise TaskInventoryUploadError(
                _extract_error_message(payload) or f"notecard task upload returned state {state!r}"
            )

        return TaskNotecardUploadResult(
            state=state,
            new_asset_id=_parse_uuid(payload.get("new_asset")),
            new_inventory_item_id=_parse_uuid(payload.get("new_inventory_item")),
        )


def _parse_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
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
    "TaskInventoryUploadClient",
    "TaskInventoryUploadError",
    "TaskInventoryUploadPrelude",
    "TaskScriptUploadResult",
    "TaskNotecardUploadResult",
]
