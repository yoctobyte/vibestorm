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
    #: The **inventory item** id, despite arriving in a field named
    #: ``new_asset``. That is OpenSim's own doing, not a decode slip:
    #: ``TaskInventoryScriptUpdater`` in ``BunchOfCaps/UpdateItemAsset.cs``
    #: assigns ``uploadComplete.new_asset = m_inventoryItemID``.
    #:
    #: So a script task upload reports **no asset id at all**, and treating
    #: this one as an asset id fails in a confusing way — fetching it through
    #: ``ViewerAsset`` returns 404, which reads as "the upload did not happen"
    #: when the upload succeeded. Confirmed live 2026-08-15.
    #:
    #: To check that a write landed, re-read the object's task inventory and
    #: compare the row's asset id: the sim mints a new asset per upload. The
    #: notecard path in the same file does return a real asset id — see
    #: `TaskNotecardUploadResult`.
    new_item_id: UUID | None
    errors: list[object]


@dataclass(slots=True, frozen=True)
class TaskNotecardUploadResult:
    """Unlike the script result, both ids here mean what they are named.

    ``TaskInventoryNotecardUpdater`` sets ``new_asset`` to the asset id and
    ``new_inventory_item`` to the item id. The two updaters live in the same
    file and disagree, so neither can be inferred from the other.
    """

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

    async def upload_agent_notecard(
        self,
        capability_url: str,
        item_id: UUID,
        notecard_bytes: bytes,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> TaskNotecardUploadResult:
        """Fill in a notecard already in the agent's own inventory.

        ``UpdateNotecardAgentInventory`` and ``UpdateNotecardTaskInventory``
        are the *same handler* in OpenSim — `BunchOfCaps` registers both
        against one `UpdateNotecardItemAsset` — and it branches on whether
        ``task_id`` is present. Omitting it is what makes the update apply to
        agent inventory, so this sends ``item_id`` alone rather than a zero
        task id, which would be looked up as an object and fail.

        This is the second half of creating a notecard. The first is a
        ``CreateInventoryItem`` over UDP, which makes an item pointing at
        OpenSim's shared empty-notecard asset; without that item there is
        nothing for this to address. ``NewFileAgentInventory`` is *not* an
        alternative — it does not handle notecards and silently stores them as
        asset type 0 (texture). See `caps/asset_upload_client`.
        """
        prelude = await self.request_uploader(
            capability_url,
            {"item_id": item_id},
            udp_listen_port=udp_listen_port,
            user_agent=user_agent,
        )
        if prelude.state != "upload":
            raise TaskInventoryUploadError(
                f"notecard agent upload returned unexpected prelude state {prelude.state!r}"
            )
        return await self.upload_notecard_bytes(
            prelude.uploader_url, notecard_bytes, user_agent=user_agent
        )

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
            # Faithful to the wire; see the field's docstring for why the name
            # differs from the one OpenSim uses.
            new_item_id=_parse_uuid(payload.get("new_asset")),
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
    """The failure message, from either shape OpenSim uses.

    ``LLSDAssetUploadError`` appears two ways. The generic uploader nests it as
    ``uploadComplete.error``, so the message is at ``error.message``. But the
    item-asset updaters serialise the error object *as the whole reply* —
    ``{message, identifier}`` at the top level with no ``state`` — and reading
    only the nested form there loses the one thing that says what went wrong,
    leaving a caller with `state ''` and no reason.
    """
    error = payload.get("error")
    if isinstance(error, dict):
        message = _parse_str(error.get("message"))
        if message:
            return message
    if "state" not in payload:
        return _parse_str(payload.get("message"))
    return ""


__all__ = [
    "TaskInventoryUploadClient",
    "TaskInventoryUploadError",
    "TaskInventoryUploadPrelude",
    "TaskScriptUploadResult",
    "TaskNotecardUploadResult",
]
