"""Task/object inventory snapshots decoded from simulator xfer text."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True, frozen=True)
class ObjectInventoryItem:
    item_id: UUID | None
    asset_id: UUID | None
    parent_id: UUID | None
    name: str
    description: str
    asset_type: str
    inventory_type: str
    raw_fields: dict[str, str]


@dataclass(slots=True, frozen=True)
class ObjectInventorySnapshot:
    local_id: int
    task_id: UUID | None
    serial: int
    filename: str
    items: tuple[ObjectInventoryItem, ...]
    raw_text: str

    @property
    def item_count(self) -> int:
        return len(self.items)


def parse_task_inventory_text(
    data: bytes | str,
    *,
    local_id: int,
    task_id: UUID | None,
    serial: int,
    filename: str,
) -> ObjectInventorySnapshot:
    """Parse the common Linden/OpenSim task-inventory text file format.

    The file is a loose brace-delimited text format. We only need a
    conservative item listing for now, so unknown fields stay in ``raw_fields``.
    """

    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    items: list[ObjectInventoryItem] = []
    lines = [line.strip() for line in text.splitlines()]
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("inv_item"):
            fields, index = _read_inventory_block(lines, index + 1)
            items.append(_item_from_fields(fields))
            continue
        index += 1

    return ObjectInventorySnapshot(
        local_id=int(local_id),
        task_id=task_id,
        serial=int(serial),
        filename=filename,
        items=tuple(items),
        raw_text=text,
    )


def _read_inventory_block(lines: list[str], index: int) -> tuple[dict[str, str], int]:
    fields: dict[str, str] = {}
    depth = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line:
            continue
        if line == "{":
            depth += 1
            continue
        if line == "}":
            if depth <= 1:
                return fields, index
            depth -= 1
            continue
        if depth < 1:
            continue
        key, value = _split_field(line)
        if key:
            fields[key] = value
    return fields, index


def _split_field(line: str) -> tuple[str, str]:
    parts = line.split(None, 1)
    if not parts:
        return "", ""
    key = parts[0]
    value = parts[1].strip() if len(parts) > 1 else ""
    if value.endswith("|"):
        value = value[:-1]
    return key, value


def _item_from_fields(fields: dict[str, str]) -> ObjectInventoryItem:
    return ObjectInventoryItem(
        item_id=_parse_uuid(fields.get("item_id")),
        asset_id=_parse_uuid(fields.get("asset_id")),
        parent_id=_parse_uuid(fields.get("parent_id")),
        name=fields.get("name", ""),
        description=fields.get("desc", ""),
        asset_type=fields.get("type", ""),
        inventory_type=fields.get("inv_type", ""),
        raw_fields=dict(fields),
    )


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None
