"""Capability clients for inventory-related LLSD endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient, CapabilityError


@dataclass(slots=True, frozen=True)
class InventoryFolderRequest:
    folder_id: UUID
    owner_id: UUID
    fetch_folders: bool = True
    fetch_items: bool = True
    sort_order: int = 0


class InventoryCapabilityError(RuntimeError):
    """Raised when inventory capability requests fail."""


@dataclass(slots=True)
class InventoryCapabilityClient:
    """Client for inventory capability endpoints such as FetchInventoryDescendents2."""

    timeout_seconds: float = 10.0

    async def fetch_inventory_descendents(
        self,
        url: str,
        folders: list[InventoryFolderRequest],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        try:
            return await CapabilityClient(timeout_seconds=self.timeout_seconds).post_capability_value(
                url,
                {
                    "folders": [
                        {
                            "folder_id": folder.folder_id,
                            "owner_id": folder.owner_id,
                            "fetch_folders": folder.fetch_folders,
                            "fetch_items": folder.fetch_items,
                            "sort_order": folder.sort_order,
                        }
                        for folder in folders
                    ],
                },
                udp_listen_port=udp_listen_port,
                user_agent=user_agent,
            )
        except CapabilityError as exc:
            raise InventoryCapabilityError(str(exc)) from exc
