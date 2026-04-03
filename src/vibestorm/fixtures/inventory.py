"""Structured inventory and backlog generation for captured fixtures."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from vibestorm.udp.messages import (
    MessageDecodeError,
    format_object_update_interest,
    infer_object_update_label,
    parse_object_update,
    parse_object_update_summary,
)
from vibestorm.udp.template import DecodedMessageNumber, MessageDispatch, MessageTemplateSummary


@dataclass(slots=True, frozen=True)
class FixtureBacklogItem:
    key: str
    summary: str
    count: int
    fixtures: tuple[str, ...]


def _object_update_dispatch(body: bytes) -> MessageDispatch:
    return MessageDispatch(
        summary=MessageTemplateSummary(
            name="ObjectUpdate",
            frequency="High",
            message_number=12,
            trust="Trusted",
            encoding="Zerocoded",
            deprecation=None,
        ),
        message_number=DecodedMessageNumber(
            frequency="High",
            message_number=12,
            encoded_length=1,
        ),
        body=body,
    )


def build_fixture_inventory(root: Path) -> dict[str, object]:
    captures: list[dict[str, object]] = []
    backlog: dict[str, list[str]] = {}

    for metadata_path in sorted(root.rglob("*.json")):
        if metadata_path.name == "index.json":
            continue
        body_path = metadata_path.with_suffix(".body.bin")
        if not body_path.exists():
            continue

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        entry: dict[str, object] = {
            "message_name": metadata.get("message_name", metadata_path.parent.name),
            "metadata_path": str(metadata_path.relative_to(root)),
            "body_path": str(body_path.relative_to(root)),
            "sequence": metadata.get("sequence"),
            "at_seconds": metadata.get("at_seconds"),
            "capture_reason": metadata.get("capture_reason"),
            "body_size": metadata.get("body_size", body_path.stat().st_size),
        }

        if entry["message_name"] == "ObjectUpdate":
            dispatch = _object_update_dispatch(body_path.read_bytes())
            summary = parse_object_update_summary(dispatch)
            object_update_entry: dict[str, object] = {
                "region_handle": summary.region_handle,
                "time_dilation": summary.time_dilation,
                "object_count": summary.object_count,
            }
            try:
                parsed = parse_object_update(dispatch)
            except MessageDecodeError as exc:
                object_update_entry["decode_status"] = "partial"
                object_update_entry["decode_error"] = str(exc)
                backlog.setdefault("object-update-partial", []).append(str(metadata_path.relative_to(root)))
            else:
                obj = parsed.objects[0]
                object_update_entry.update(
                    {
                        "decode_status": "decoded",
                        "variant": obj.variant,
                        "full_id": str(obj.full_id),
                        "local_id": obj.local_id,
                        "label": infer_object_update_label(obj),
                        "update_flags": obj.update_flags,
                        "position": list(obj.position) if obj.position is not None else None,
                        "rotation": list(obj.rotation) if obj.rotation is not None else None,
                        "name_values": obj.name_values,
                        "texture_entry_size": obj.texture_entry_size,
                        "default_texture_id": str(obj.default_texture_id) if obj.default_texture_id is not None else None,
                        "texture_anim_size": obj.texture_anim_size,
                        "data_size": obj.data_size,
                        "text_size": obj.text_size,
                        "media_url_size": obj.media_url_size,
                        "ps_block_size": obj.ps_block_size,
                        "extra_params_size": obj.extra_params_size,
                        "interesting_payloads": [
                            {
                                "field_name": payload.field_name,
                                "size": payload.size,
                                "non_zero_bytes": payload.non_zero_bytes,
                                "preview_hex": payload.preview_hex,
                                "text_preview": payload.text_preview,
                            }
                            for payload in obj.interesting_payloads
                        ],
                        "interest_summary": format_object_update_interest(obj),
                    },
                )
                if any(
                    (
                        obj.texture_entry_size,
                        obj.texture_anim_size,
                        obj.data_size,
                        obj.text_size,
                        obj.media_url_size,
                        obj.ps_block_size,
                        obj.extra_params_size,
                    ),
                ):
                    backlog.setdefault("object-update-rich-tail", []).append(str(metadata_path.relative_to(root)))
                if obj.interesting_payloads:
                    backlog.setdefault("object-update-interesting-unknowns", []).append(
                        str(metadata_path.relative_to(root)),
                    )
            entry["object_update"] = object_update_entry

        captures.append(entry)

    backlog_items: list[FixtureBacklogItem] = []
    if "object-update-partial" in backlog:
        fixtures = tuple(sorted(backlog["object-update-partial"]))
        backlog_items.append(
            FixtureBacklogItem(
                key="object-update-partial",
                summary="ObjectUpdate packets that still fall back to summary-only decoding.",
                count=len(fixtures),
                fixtures=fixtures,
            ),
        )
    if "object-update-rich-tail" in backlog:
        fixtures = tuple(sorted(backlog["object-update-rich-tail"]))
        backlog_items.append(
            FixtureBacklogItem(
                key="object-update-rich-tail",
                summary="Known ObjectUpdate variants with non-empty tail data such as TextureEntry or ExtraParams.",
                count=len(fixtures),
                fixtures=fixtures,
            ),
        )
    if "object-update-interesting-unknowns" in backlog:
        fixtures = tuple(sorted(backlog["object-update-interesting-unknowns"]))
        backlog_items.append(
            FixtureBacklogItem(
                key="object-update-interesting-unknowns",
                summary="ObjectUpdate captures with non-zero tail payloads worth reverse-engineering further.",
                count=len(fixtures),
                fixtures=fixtures,
            ),
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "root": str(root),
        "capture_count": len(captures),
        "captures": captures,
        "backlog": [asdict(item) for item in backlog_items],
    }


def write_fixture_inventory(root: Path) -> Path:
    inventory_path = root / "index.json"
    inventory = build_fixture_inventory(root)
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return inventory_path
