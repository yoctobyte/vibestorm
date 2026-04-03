"""SQLite-backed collection of reverse-engineering evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vibestorm.udp.messages import (
    ObjectUpdateEntry,
    format_object_update_interest,
    infer_object_update_label,
)


DEFAULT_UNKNOWNS_DB_PATH = Path("local/unknowns.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS object_update_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    capture_reason TEXT NOT NULL,
    region_handle INTEGER,
    object_count INTEGER,
    decode_status TEXT NOT NULL,
    decode_error TEXT,
    packet_tags_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_object_update_packets_decode_status
ON object_update_packets(decode_status);

CREATE INDEX IF NOT EXISTS idx_object_update_packets_sequence
ON object_update_packets(message_sequence);

CREATE TABLE IF NOT EXISTS object_update_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    packet_id INTEGER NOT NULL,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    capture_reason TEXT NOT NULL,
    region_handle INTEGER,
    full_id TEXT,
    local_id INTEGER,
    variant TEXT,
    label TEXT,
    update_flags INTEGER,
    position_x REAL,
    position_y REAL,
    position_z REAL,
    has_interesting INTEGER NOT NULL,
    interest_summary TEXT,
    payload_count INTEGER NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    payloads_json TEXT NOT NULL,
    entity_tags_json TEXT NOT NULL,
    FOREIGN KEY(packet_id) REFERENCES object_update_packets(id)
);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_packet_id
ON object_update_entities(packet_id);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_fingerprint
ON object_update_entities(payload_fingerprint);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_full_id
ON object_update_entities(full_id);

CREATE TABLE IF NOT EXISTS nearby_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    from_name TEXT NOT NULL,
    source_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    source_type INTEGER NOT NULL,
    chat_type INTEGER NOT NULL,
    audible INTEGER NOT NULL,
    position_x REAL NOT NULL,
    position_y REAL NOT NULL,
    position_z REAL NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nearby_chat_messages_observed_at
ON nearby_chat_messages(observed_at_seconds);
"""


@dataclass(slots=True, frozen=True)
class UnknownStats:
    packet_count: int
    entity_count: int
    distinct_objects: int
    distinct_fingerprints: int
    multi_object_packets: int
    partial_packets: int
    rich_entities: int


class UnknownsDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def record_object_update_packet(
        self,
        *,
        observed_at_seconds: float,
        message_sequence: int,
        capture_reason: str,
        region_handle: int | None,
        object_count: int | None,
        decode_status: str,
        decode_error: str | None,
        packet_tags: list[str],
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO object_update_packets (
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    object_count,
                    decode_status,
                    decode_error,
                    packet_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    object_count,
                    decode_status,
                    decode_error,
                    json.dumps(sorted(packet_tags)),
                ),
            )
            return int(cursor.lastrowid)

    def record_object_update_entity(
        self,
        *,
        packet_id: int,
        observed_at_seconds: float,
        message_sequence: int,
        capture_reason: str,
        region_handle: int,
        entry: ObjectUpdateEntry,
    ) -> None:
        payloads = [
            {
                "field_name": payload.field_name,
                "size": payload.size,
                "non_zero_bytes": payload.non_zero_bytes,
                "preview_hex": payload.preview_hex,
                "text_preview": payload.text_preview,
            }
            for payload in entry.interesting_payloads
        ]
        payload_fingerprint = "|".join(
            f"{payload['field_name']}:{payload['size']}:{payload['preview_hex']}"
            for payload in payloads
        )
        entity_tags = [entry.variant]
        label = infer_object_update_label(entry)
        if label is not None:
            entity_tags.append("has_label")
        if entry.position is not None:
            entity_tags.append("has_position")
        if entry.interesting_payloads:
            entity_tags.append("interesting")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO object_update_entities (
                    packet_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    full_id,
                    local_id,
                    variant,
                    label,
                    update_flags,
                    position_x,
                    position_y,
                    position_z,
                    has_interesting,
                    interest_summary,
                    payload_count,
                    payload_fingerprint,
                    payloads_json,
                    entity_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    str(entry.full_id),
                    entry.local_id,
                    entry.variant,
                    label,
                    entry.update_flags,
                    entry.position[0] if entry.position is not None else None,
                    entry.position[1] if entry.position is not None else None,
                    entry.position[2] if entry.position is not None else None,
                    int(bool(entry.interesting_payloads)),
                    format_object_update_interest(entry),
                    len(payloads),
                    payload_fingerprint,
                    json.dumps(payloads, sort_keys=True),
                    json.dumps(sorted(entity_tags)),
                ),
            )

    def read_stats(self) -> UnknownStats:
        with self._connect() as connection:
            packet_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS packet_count,
                    SUM(CASE WHEN COALESCE(object_count, 0) > 1 THEN 1 ELSE 0 END) AS multi_object_packets,
                    SUM(CASE WHEN decode_status != 'decoded' THEN 1 ELSE 0 END) AS partial_packets
                FROM object_update_packets
                """,
            ).fetchone()
            entity_row = connection.execute(
                """
                SELECT
                    COUNT(*) AS entity_count,
                    COUNT(DISTINCT full_id) AS distinct_objects,
                    COUNT(DISTINCT CASE WHEN has_interesting = 1 THEN payload_fingerprint END) AS distinct_fingerprints,
                    SUM(CASE WHEN has_interesting = 1 THEN 1 ELSE 0 END) AS rich_entities
                FROM object_update_entities
                """,
            ).fetchone()
        assert packet_row is not None
        assert entity_row is not None
        return UnknownStats(
            packet_count=int(packet_row["packet_count"] or 0),
            entity_count=int(entity_row["entity_count"] or 0),
            distinct_objects=int(entity_row["distinct_objects"] or 0),
            distinct_fingerprints=int(entity_row["distinct_fingerprints"] or 0),
            multi_object_packets=int(packet_row["multi_object_packets"] or 0),
            partial_packets=int(packet_row["partial_packets"] or 0),
            rich_entities=int(entity_row["rich_entities"] or 0),
        )

    def summarize_object_update_packets(self, *, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    decode_status,
                    capture_reason,
                    COUNT(*) AS seen_count,
                    SUM(CASE WHEN COALESCE(object_count, 0) > 1 THEN 1 ELSE 0 END) AS multi_object_count,
                    MIN(observed_at_seconds) AS first_seen_at_seconds,
                    MAX(observed_at_seconds) AS last_seen_at_seconds,
                    MIN(decode_error) AS sample_decode_error,
                    MIN(packet_tags_json) AS sample_packet_tags_json
                FROM object_update_packets
                GROUP BY decode_status, capture_reason
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "decode_status": row["decode_status"],
                "capture_reason": row["capture_reason"],
                "seen_count": int(row["seen_count"]),
                "multi_object_count": int(row["multi_object_count"] or 0),
                "first_seen_at_seconds": float(row["first_seen_at_seconds"]),
                "last_seen_at_seconds": float(row["last_seen_at_seconds"]),
                "sample_decode_error": row["sample_decode_error"],
                "sample_packet_tags": json.loads(str(row["sample_packet_tags_json"])),
            }
            for row in rows
        ]

    def summarize_payload_fingerprints(self, *, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    payload_fingerprint,
                    variant,
                    label,
                    COUNT(*) AS seen_count,
                    MIN(observed_at_seconds) AS first_seen_at_seconds,
                    MAX(observed_at_seconds) AS last_seen_at_seconds,
                    MIN(full_id) AS sample_full_id,
                    MIN(interest_summary) AS sample_interest_summary,
                    MIN(payloads_json) AS sample_payloads_json
                FROM object_update_entities
                WHERE has_interesting = 1
                GROUP BY payload_fingerprint, variant, label
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "payload_fingerprint": row["payload_fingerprint"],
                "variant": row["variant"],
                "label": row["label"],
                "seen_count": int(row["seen_count"]),
                "first_seen_at_seconds": float(row["first_seen_at_seconds"]),
                "last_seen_at_seconds": float(row["last_seen_at_seconds"]),
                "sample_full_id": row["sample_full_id"],
                "sample_interest_summary": row["sample_interest_summary"],
                "sample_payloads": json.loads(str(row["sample_payloads_json"])),
            }
            for row in rows
        ]

    def record_nearby_chat(
        self,
        *,
        observed_at_seconds: float,
        message_sequence: int,
        from_name: str,
        source_id: str,
        owner_id: str,
        source_type: int,
        chat_type: int,
        audible: int,
        position: tuple[float, float, float],
        message: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO nearby_chat_messages (
                    observed_at_seconds,
                    message_sequence,
                    from_name,
                    source_id,
                    owner_id,
                    source_type,
                    chat_type,
                    audible,
                    position_x,
                    position_y,
                    position_z,
                    message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observed_at_seconds,
                    message_sequence,
                    from_name,
                    source_id,
                    owner_id,
                    source_type,
                    chat_type,
                    audible,
                    position[0],
                    position[1],
                    position[2],
                    message,
                ),
            )

    def recent_nearby_chat(self, *, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    observed_at_seconds,
                    message_sequence,
                    from_name,
                    source_id,
                    owner_id,
                    source_type,
                    chat_type,
                    audible,
                    position_x,
                    position_y,
                    position_z,
                    message
                FROM nearby_chat_messages
                ORDER BY observed_at_seconds DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "observed_at_seconds": float(row["observed_at_seconds"]),
                "message_sequence": int(row["message_sequence"]),
                "from_name": row["from_name"],
                "source_id": row["source_id"],
                "owner_id": row["owner_id"],
                "source_type": int(row["source_type"]),
                "chat_type": int(row["chat_type"]),
                "audible": int(row["audible"]),
                "position": [
                    float(row["position_x"]),
                    float(row["position_y"]),
                    float(row["position_z"]),
                ],
                "message": row["message"],
            }
            for row in rows
        ]
