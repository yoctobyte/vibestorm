"""Capability clients for inventory-related LLSD endpoints."""

from __future__ import annotations

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


@dataclass(slots=True, frozen=True)
class InventoryItemRequest:
    item_id: UUID


@dataclass(slots=True, frozen=True)
class InventoryCategoryEntry:
    category_id: UUID | None
    parent_id: UUID | None
    name: str
    type_default: int | None
    version: int | None


@dataclass(slots=True, frozen=True)
class InventoryItemEntry:
    item_id: UUID | None
    asset_id: UUID | None
    parent_id: UUID | None
    name: str
    description: str
    type: int | None
    inv_type: int | None
    flags: int | None

    @property
    def is_link(self) -> bool:
        return self.inv_type == 24 or self.type in {24, 25}


@dataclass(slots=True, frozen=True)
class InventoryFolderContents:
    folder_id: UUID | None
    owner_id: UUID | None
    agent_id: UUID | None
    descendents: int | None
    version: int | None
    categories: tuple[InventoryCategoryEntry, ...]
    items: tuple[InventoryItemEntry, ...]

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def link_item_count(self) -> int:
        return sum(1 for item in self.items if item.is_link)

    @property
    def inventory_types(self) -> tuple[int, ...]:
        values = sorted({item.inv_type for item in self.items if item.inv_type is not None})
        return tuple(values)

    def sample_item_names(self, limit: int = 3) -> tuple[str, ...]:
        names = [item.name for item in self.items if item.name][:limit]
        return tuple(names)


@dataclass(slots=True, frozen=True)
class InventoryFetchSnapshot:
    folders: tuple[InventoryFolderContents, ...]
    inventory_root_folder_id: UUID | None = None
    current_outfit_folder_id: UUID | None = None
    resolved_items: tuple[InventoryItemEntry, ...] = ()

    @property
    def folder_count(self) -> int:
        return len(self.folders)

    @property
    def total_item_count(self) -> int:
        return sum(folder.item_count for folder in self.folders)

    def folder_by_id(self, folder_id: UUID | None) -> InventoryFolderContents | None:
        if folder_id is None:
            return None
        for folder in self.folders:
            if folder.folder_id == folder_id:
                return folder
        return None

    @property
    def current_outfit_folder(self) -> InventoryFolderContents | None:
        return self.folder_by_id(self.current_outfit_folder_id)

    @property
    def inventory_root_folder(self) -> InventoryFolderContents | None:
        return self.folder_by_id(self.inventory_root_folder_id)

    @property
    def current_outfit_link_targets(self) -> tuple[UUID, ...]:
        cof = self.current_outfit_folder
        if cof is None:
            return ()
        # Live OpenSim data and older viewer conventions do not fully agree on
        # whether a COF link exposes the source item UUID as asset_id or item_id.
        # Query both plausible IDs and let FetchInventory2 return whichever exist.
        targets: list[UUID] = []
        seen: set[UUID] = set()
        for item in cof.items:
            if not item.is_link:
                continue
            for candidate in (item.asset_id, item.item_id):
                if candidate is None or candidate in seen:
                    continue
                seen.add(candidate)
                targets.append(candidate)
        return tuple(targets)

    @property
    def resolved_item_count(self) -> int:
        return len(self.resolved_items)

    @property
    def resolved_item_types(self) -> tuple[int, ...]:
        values = sorted({item.type for item in self.resolved_items if item.type is not None})
        return tuple(values)

    def resolved_item_names(self, limit: int = 6) -> tuple[str, ...]:
        names = [item.name for item in self.resolved_items if item.name][:limit]
        return tuple(names)


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

    async def fetch_inventory_items(
        self,
        url: str,
        items: list[InventoryItemRequest],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        try:
            return await CapabilityClient(timeout_seconds=self.timeout_seconds).post_capability_value(
                url,
                {"items": [{"item_id": item.item_id} for item in items]},
                udp_listen_port=udp_listen_port,
                user_agent=user_agent,
            )
        except CapabilityError as exc:
            raise InventoryCapabilityError(str(exc)) from exc


def parse_inventory_descendents_payload(
    payload: object,
    *,
    inventory_root_folder_id: UUID | None = None,
    current_outfit_folder_id: UUID | None = None,
) -> InventoryFetchSnapshot:
    if not isinstance(payload, dict):
        return InventoryFetchSnapshot(
            folders=(),
            inventory_root_folder_id=inventory_root_folder_id,
            current_outfit_folder_id=current_outfit_folder_id,
        )
    raw_folders = payload.get("folders")
    if not isinstance(raw_folders, list):
        return InventoryFetchSnapshot(
            folders=(),
            inventory_root_folder_id=inventory_root_folder_id,
            current_outfit_folder_id=current_outfit_folder_id,
        )
    folders: list[InventoryFolderContents] = []
    for raw_folder in raw_folders:
        if not isinstance(raw_folder, dict):
            continue
        raw_categories = raw_folder.get("categories")
        categories: list[InventoryCategoryEntry] = []
        if isinstance(raw_categories, list):
            for raw_category in raw_categories:
                if not isinstance(raw_category, dict):
                    continue
                categories.append(
                    InventoryCategoryEntry(
                        category_id=_parse_uuid(raw_category.get("category_id")),
                        parent_id=_parse_uuid(raw_category.get("parent_id")),
                        name=_parse_str(raw_category.get("name")),
                        type_default=_parse_int(raw_category.get("type_default")),
                        version=_parse_int(raw_category.get("version")),
                    )
                )
        raw_items = raw_folder.get("items")
        items: list[InventoryItemEntry] = []
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                items.append(
                    InventoryItemEntry(
                        item_id=_parse_uuid(raw_item.get("item_id")),
                        asset_id=_parse_uuid(raw_item.get("asset_id")),
                        parent_id=_parse_uuid(raw_item.get("parent_id")),
                        name=_parse_str(raw_item.get("name")),
                        description=_parse_str(raw_item.get("desc")),
                        type=_parse_int(raw_item.get("type")),
                        inv_type=_parse_int(raw_item.get("inv_type")),
                        flags=_parse_int(raw_item.get("flags")),
                    )
                )
        folders.append(
            InventoryFolderContents(
                folder_id=_parse_uuid(raw_folder.get("folder_id")),
                owner_id=_parse_uuid(raw_folder.get("owner_id")),
                agent_id=_parse_uuid(raw_folder.get("agent_id")),
                descendents=_parse_int(raw_folder.get("descendents")),
                version=_parse_int(raw_folder.get("version")),
                categories=tuple(categories),
                items=tuple(items),
            )
        )
    return InventoryFetchSnapshot(
        folders=tuple(folders),
        inventory_root_folder_id=inventory_root_folder_id,
        current_outfit_folder_id=current_outfit_folder_id,
    )


def parse_inventory_items_payload(payload: object) -> tuple[InventoryItemEntry, ...]:
    if not isinstance(payload, dict):
        return ()
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return ()
    items: list[InventoryItemEntry] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        items.append(
            InventoryItemEntry(
                item_id=_parse_uuid(raw_item.get("item_id")),
                asset_id=_parse_uuid(raw_item.get("asset_id")),
                parent_id=_parse_uuid(raw_item.get("parent_id")),
                name=_parse_str(raw_item.get("name")),
                description=_parse_str(raw_item.get("desc")),
                type=_parse_int(raw_item.get("type")),
                inv_type=_parse_int(raw_item.get("inv_type")),
                flags=_parse_int(raw_item.get("flags")),
            )
        )
    return tuple(items)


def merge_inventory_fetch_snapshots(
    base: InventoryFetchSnapshot | None,
    update: InventoryFetchSnapshot,
) -> InventoryFetchSnapshot:
    """Return a snapshot with updated folder contents merged into existing data."""

    if base is None:
        return update

    merged_by_id: dict[UUID, InventoryFolderContents] = {}
    anonymous_folders: list[InventoryFolderContents] = []
    order: list[UUID] = []
    for folder in (*base.folders, *update.folders):
        if folder.folder_id is None:
            anonymous_folders.append(folder)
            continue
        if folder.folder_id not in merged_by_id:
            order.append(folder.folder_id)
        merged_by_id[folder.folder_id] = folder

    return InventoryFetchSnapshot(
        folders=tuple(merged_by_id[folder_id] for folder_id in order) + tuple(anonymous_folders),
        inventory_root_folder_id=(
            update.inventory_root_folder_id or base.inventory_root_folder_id
        ),
        current_outfit_folder_id=(
            update.current_outfit_folder_id or base.current_outfit_folder_id
        ),
        resolved_items=base.resolved_items or update.resolved_items,
    )


def snapshot_with_loaded_empty_folder(
    snapshot: InventoryFetchSnapshot,
    *,
    folder_id: UUID,
    owner_id: UUID,
    agent_id: UUID | None = None,
) -> InventoryFetchSnapshot:
    """Ensure a successful empty folder fetch is represented as loaded."""

    if snapshot.folder_by_id(folder_id) is not None:
        return snapshot
    empty = InventoryFolderContents(
        folder_id=folder_id,
        owner_id=owner_id,
        agent_id=agent_id or owner_id,
        descendents=0,
        version=None,
        categories=(),
        items=(),
    )
    return InventoryFetchSnapshot(
        folders=(*snapshot.folders, empty),
        inventory_root_folder_id=snapshot.inventory_root_folder_id,
        current_outfit_folder_id=snapshot.current_outfit_folder_id,
        resolved_items=snapshot.resolved_items,
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
