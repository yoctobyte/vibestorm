"""SQLite-backed collection of reverse-engineering evidence."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vibestorm.udp.messages import (
    ObjectUpdateEntry,
    format_object_update_interest,
    infer_object_update_label,
)


DEFAULT_UNKNOWNS_DB_PATH = Path("local/unknowns.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_utc TEXT NOT NULL,
    sim_ip TEXT,
    sim_port INTEGER,
    agent_id TEXT,
    configured_duration_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_sessions_started_at
ON sessions(started_at_utc);

CREATE TABLE IF NOT EXISTS object_update_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    capture_reason TEXT NOT NULL,
    region_handle INTEGER,
    object_count INTEGER,
    decode_status TEXT NOT NULL,
    decode_error TEXT,
    packet_tags_json TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_object_update_packets_decode_status
ON object_update_packets(decode_status);

CREATE INDEX IF NOT EXISTS idx_object_update_packets_sequence
ON object_update_packets(message_sequence);

CREATE TABLE IF NOT EXISTS object_update_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    packet_id INTEGER NOT NULL,
    session_id INTEGER,
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
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_packet_id
ON object_update_entities(packet_id);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_fingerprint
ON object_update_entities(payload_fingerprint);

CREATE INDEX IF NOT EXISTS idx_object_update_entities_full_id
ON object_update_entities(full_id);

CREATE TABLE IF NOT EXISTS improved_terse_packets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    capture_reason TEXT NOT NULL,
    region_handle INTEGER NOT NULL,
    object_count INTEGER NOT NULL,
    time_dilation INTEGER NOT NULL,
    packet_tags_json TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_improved_terse_packets_sequence
ON improved_terse_packets(message_sequence);

CREATE TABLE IF NOT EXISTS improved_terse_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    packet_id INTEGER NOT NULL,
    session_id INTEGER,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    capture_reason TEXT NOT NULL,
    region_handle INTEGER NOT NULL,
    local_id INTEGER,
    data_size INTEGER NOT NULL,
    texture_entry_size INTEGER NOT NULL,
    has_texture_entry INTEGER NOT NULL,
    data_preview_hex TEXT NOT NULL,
    texture_entry_preview_hex TEXT NOT NULL,
    entity_tags_json TEXT NOT NULL,
    FOREIGN KEY(packet_id) REFERENCES improved_terse_packets(id),
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_improved_terse_entities_packet_id
ON improved_terse_entities(packet_id);

CREATE INDEX IF NOT EXISTS idx_improved_terse_entities_local_id
ON improved_terse_entities(local_id);

CREATE TABLE IF NOT EXISTS nearby_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
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
    message TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_nearby_chat_messages_observed_at
ON nearby_chat_messages(observed_at_seconds);

CREATE TABLE IF NOT EXISTS unknown_udp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    failure_stage TEXT NOT NULL,
    raw_message_number INTEGER,
    encoded_length INTEGER,
    payload_size INTEGER NOT NULL,
    preview_hex TEXT NOT NULL,
    error_text TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_unknown_udp_messages_observed_at
ON unknown_udp_messages(observed_at_seconds);

CREATE TABLE IF NOT EXISTS inbound_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    observed_at_seconds REAL NOT NULL,
    message_sequence INTEGER NOT NULL,
    message_name TEXT NOT NULL,
    frequency TEXT NOT NULL,
    wire_message_number INTEGER NOT NULL,
    body_size INTEGER NOT NULL,
    is_reliable INTEGER NOT NULL,
    payload_preview_hex TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_messages_name
ON inbound_messages(message_name);

CREATE INDEX IF NOT EXISTS idx_inbound_messages_observed_at
ON inbound_messages(observed_at_seconds);

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
    terse_packet_count: int
    terse_entity_count: int
    terse_distinct_local_ids: int
    terse_rich_entities: int
    unknown_udp_messages: int
    inbound_messages: int


@dataclass(slots=True, frozen=True)
class SessionInfo:
    session_id: int
    started_at_utc: str
    sim_ip: str | None
    sim_port: int | None
    agent_id: str | None
    configured_duration_seconds: float | None


class UnknownsDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._ensure_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(SCHEMA)
        self._ensure_column(connection, "object_update_packets", "session_id", "INTEGER")
        self._ensure_column(connection, "object_update_entities", "session_id", "INTEGER")
        self._ensure_column(connection, "improved_terse_packets", "session_id", "INTEGER")
        self._ensure_column(connection, "improved_terse_entities", "session_id", "INTEGER")
        self._ensure_column(connection, "nearby_chat_messages", "session_id", "INTEGER")
        self._ensure_column(connection, "unknown_udp_messages", "session_id", "INTEGER")
        self._ensure_column(connection, "inbound_messages", "session_id", "INTEGER")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_object_update_packets_session_id ON object_update_packets(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_object_update_entities_session_id ON object_update_entities(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_improved_terse_packets_session_id ON improved_terse_packets(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_improved_terse_entities_session_id ON improved_terse_entities(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_nearby_chat_messages_session_id ON nearby_chat_messages(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_unknown_udp_messages_session_id ON unknown_udp_messages(session_id)",
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbound_messages_session_id ON inbound_messages(session_id)",
        )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_type_sql: str,
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type_sql}",
        )

    def begin_session(
        self,
        *,
        sim_ip: str | None,
        sim_port: int | None,
        agent_id: str | None,
        configured_duration_seconds: float | None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (
                    started_at_utc,
                    sim_ip,
                    sim_port,
                    agent_id,
                    configured_duration_seconds
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    sim_ip,
                    sim_port,
                    agent_id,
                    configured_duration_seconds,
                ),
            )
            return int(cursor.lastrowid)

    def latest_session(self) -> SessionInfo | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    started_at_utc,
                    sim_ip,
                    sim_port,
                    agent_id,
                    configured_duration_seconds
                FROM sessions
                ORDER BY id DESC
                LIMIT 1
                """,
            ).fetchone()
        return self._row_to_session_info(row)

    def get_session(self, session_id: int) -> SessionInfo | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    started_at_utc,
                    sim_ip,
                    sim_port,
                    agent_id,
                    configured_duration_seconds
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._row_to_session_info(row)

    def _row_to_session_info(self, row: sqlite3.Row | None) -> SessionInfo | None:
        if row is None:
            return None
        return SessionInfo(
            session_id=int(row["id"]),
            started_at_utc=str(row["started_at_utc"]),
            sim_ip=None if row["sim_ip"] is None else str(row["sim_ip"]),
            sim_port=None if row["sim_port"] is None else int(row["sim_port"]),
            agent_id=None if row["agent_id"] is None else str(row["agent_id"]),
            configured_duration_seconds=(
                None
                if row["configured_duration_seconds"] is None
                else float(row["configured_duration_seconds"])
            ),
        )

    def _scope_clause(self, session_id: int | None) -> tuple[str, tuple[object, ...]]:
        if session_id is None:
            return "", ()
        return " WHERE session_id = ?", (session_id,)

    def record_object_update_packet(
        self,
        *,
        session_id: int | None,
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
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    object_count,
                    decode_status,
                    decode_error,
                    packet_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
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
        session_id: int | None,
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
                    session_id,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet_id,
                    session_id,
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

    def record_improved_terse_packet(
        self,
        *,
        session_id: int | None,
        observed_at_seconds: float,
        message_sequence: int,
        capture_reason: str,
        region_handle: int,
        object_count: int,
        time_dilation: int,
        packet_tags: list[str],
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO improved_terse_packets (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    object_count,
                    time_dilation,
                    packet_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    object_count,
                    time_dilation,
                    json.dumps(sorted(packet_tags)),
                ),
            )
            return int(cursor.lastrowid)

    def record_improved_terse_entity(
        self,
        *,
        packet_id: int,
        session_id: int | None,
        observed_at_seconds: float,
        message_sequence: int,
        capture_reason: str,
        region_handle: int,
        local_id: int | None,
        data_size: int,
        texture_entry_size: int,
        data_preview_hex: str,
        texture_entry_preview_hex: str,
    ) -> None:
        entity_tags: list[str] = []
        if local_id is not None:
            entity_tags.append("has_local_id")
        if texture_entry_size > 0:
            entity_tags.append("has_texture_entry")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO improved_terse_entities (
                    packet_id,
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    local_id,
                    data_size,
                    texture_entry_size,
                    has_texture_entry,
                    data_preview_hex,
                    texture_entry_preview_hex,
                    entity_tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet_id,
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    capture_reason,
                    region_handle,
                    local_id,
                    data_size,
                    texture_entry_size,
                    int(texture_entry_size > 0),
                    data_preview_hex,
                    texture_entry_preview_hex,
                    json.dumps(sorted(entity_tags)),
                ),
            )

    def read_stats(self, *, session_id: int | None = None) -> UnknownStats:
        packet_clause, packet_params = self._scope_clause(session_id)
        entity_clause, entity_params = self._scope_clause(session_id)
        terse_packet_clause, terse_packet_params = self._scope_clause(session_id)
        terse_entity_clause, terse_entity_params = self._scope_clause(session_id)
        unknown_clause, unknown_params = self._scope_clause(session_id)
        inbound_clause, inbound_params = self._scope_clause(session_id)
        with self._connect() as connection:
            packet_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS packet_count,
                    SUM(CASE WHEN COALESCE(object_count, 0) > 1 THEN 1 ELSE 0 END) AS multi_object_packets,
                    SUM(CASE WHEN decode_status != 'decoded' THEN 1 ELSE 0 END) AS partial_packets
                FROM object_update_packets
                {packet_clause}
                """,
                packet_params,
            ).fetchone()
            entity_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS entity_count,
                    COUNT(DISTINCT full_id) AS distinct_objects,
                    COUNT(DISTINCT CASE WHEN has_interesting = 1 THEN payload_fingerprint END) AS distinct_fingerprints,
                    SUM(CASE WHEN has_interesting = 1 THEN 1 ELSE 0 END) AS rich_entities
                FROM object_update_entities
                {entity_clause}
                """,
                entity_params,
            ).fetchone()
            terse_packet_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS packet_count
                FROM improved_terse_packets
                {terse_packet_clause}
                """,
                terse_packet_params,
            ).fetchone()
            terse_entity_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS entity_count,
                    COUNT(DISTINCT local_id) AS distinct_local_ids,
                    SUM(CASE WHEN has_texture_entry = 1 THEN 1 ELSE 0 END) AS rich_entities
                FROM improved_terse_entities
                {terse_entity_clause}
                """,
                terse_entity_params,
            ).fetchone()
        assert packet_row is not None
        assert entity_row is not None
        assert terse_packet_row is not None
        assert terse_entity_row is not None
        return UnknownStats(
            packet_count=int(packet_row["packet_count"] or 0),
            entity_count=int(entity_row["entity_count"] or 0),
            distinct_objects=int(entity_row["distinct_objects"] or 0),
            distinct_fingerprints=int(entity_row["distinct_fingerprints"] or 0),
            multi_object_packets=int(packet_row["multi_object_packets"] or 0),
            partial_packets=int(packet_row["partial_packets"] or 0),
            rich_entities=int(entity_row["rich_entities"] or 0),
            terse_packet_count=int(terse_packet_row["packet_count"] or 0),
            terse_entity_count=int(terse_entity_row["entity_count"] or 0),
            terse_distinct_local_ids=int(terse_entity_row["distinct_local_ids"] or 0),
            terse_rich_entities=int(terse_entity_row["rich_entities"] or 0),
            unknown_udp_messages=int(
                self._scalar(
                    f"SELECT COUNT(*) FROM unknown_udp_messages{unknown_clause}",
                    unknown_params,
                ),
            ),
            inbound_messages=int(
                self._scalar(
                    f"SELECT COUNT(*) FROM inbound_messages{inbound_clause}",
                    inbound_params,
                ),
            ),
        )

    def _scalar(self, sql: str, params: tuple[object, ...] = ()) -> int:
        with self._connect() as connection:
            row = connection.execute(sql, params).fetchone()
        assert row is not None
        return int(row[0] or 0)

    def summarize_object_update_packets(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
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
                {clause}
                GROUP BY decode_status, capture_reason
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (*params, limit),
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

    def summarize_payload_fingerprints(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
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
                {"AND session_id = ?" if session_id is not None else ""}
                GROUP BY payload_fingerprint, variant, label
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (*params, limit),
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

    def summarize_improved_terse_packets(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    capture_reason,
                    COUNT(*) AS seen_count,
                    SUM(object_count) AS total_objects,
                    MIN(observed_at_seconds) AS first_seen_at_seconds,
                    MAX(observed_at_seconds) AS last_seen_at_seconds,
                    MIN(packet_tags_json) AS sample_packet_tags_json
                FROM improved_terse_packets
                {clause}
                GROUP BY capture_reason
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [
            {
                "capture_reason": row["capture_reason"],
                "seen_count": int(row["seen_count"]),
                "total_objects": int(row["total_objects"] or 0),
                "first_seen_at_seconds": float(row["first_seen_at_seconds"]),
                "last_seen_at_seconds": float(row["last_seen_at_seconds"]),
                "sample_packet_tags": json.loads(str(row["sample_packet_tags_json"])),
            }
            for row in rows
        ]

    def summarize_improved_terse_local_ids(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        where_parts = ["local_id IS NOT NULL"]
        if clause:
            where_parts.append(clause.removeprefix(" WHERE "))
        where_sql = " WHERE " + " AND ".join(where_parts)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    local_id,
                    COUNT(*) AS seen_count,
                    MIN(observed_at_seconds) AS first_seen_at_seconds,
                    MAX(observed_at_seconds) AS last_seen_at_seconds,
                    MAX(texture_entry_size) AS max_texture_entry_size,
                    MIN(data_preview_hex) AS sample_data_preview_hex
                FROM improved_terse_entities
                {where_sql}
                GROUP BY local_id
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [
            {
                "local_id": int(row["local_id"]),
                "seen_count": int(row["seen_count"]),
                "first_seen_at_seconds": float(row["first_seen_at_seconds"]),
                "last_seen_at_seconds": float(row["last_seen_at_seconds"]),
                "max_texture_entry_size": int(row["max_texture_entry_size"] or 0),
                "sample_data_preview_hex": row["sample_data_preview_hex"],
            }
            for row in rows
        ]

    def record_nearby_chat(
        self,
        *,
        session_id: int | None,
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
                    session_id,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
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

    def record_unknown_udp_message(
        self,
        *,
        session_id: int | None,
        observed_at_seconds: float,
        message_sequence: int,
        failure_stage: str,
        raw_message_number: int | None,
        encoded_length: int | None,
        payload: bytes,
        error_text: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO unknown_udp_messages (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    failure_stage,
                    raw_message_number,
                    encoded_length,
                    payload_size,
                    preview_hex,
                    error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    failure_stage,
                    raw_message_number,
                    encoded_length,
                    len(payload),
                    payload[:24].hex(),
                    error_text,
                ),
            )

    def record_inbound_message(
        self,
        *,
        session_id: int | None,
        observed_at_seconds: float,
        message_sequence: int,
        message_name: str,
        frequency: str,
        wire_message_number: int,
        body_size: int,
        is_reliable: bool,
        payload_preview_hex: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO inbound_messages (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    message_name,
                    frequency,
                    wire_message_number,
                    body_size,
                    is_reliable,
                    payload_preview_hex
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    observed_at_seconds,
                    message_sequence,
                    message_name,
                    frequency,
                    wire_message_number,
                    body_size,
                    int(is_reliable),
                    payload_preview_hex,
                ),
            )

    def recent_nearby_chat(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
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
                {clause}
                ORDER BY observed_at_seconds DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
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

    def recent_unknown_udp_messages(
        self,
        *,
        limit: int = 20,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    observed_at_seconds,
                    message_sequence,
                    failure_stage,
                    raw_message_number,
                    encoded_length,
                    payload_size,
                    preview_hex,
                    error_text
                FROM unknown_udp_messages
                {clause}
                ORDER BY observed_at_seconds DESC, id DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [
            {
                "observed_at_seconds": float(row["observed_at_seconds"]),
                "message_sequence": int(row["message_sequence"]),
                "failure_stage": row["failure_stage"],
                "raw_message_number": None if row["raw_message_number"] is None else int(row["raw_message_number"]),
                "encoded_length": None if row["encoded_length"] is None else int(row["encoded_length"]),
                "payload_size": int(row["payload_size"]),
                "preview_hex": row["preview_hex"],
                "error_text": row["error_text"],
            }
            for row in rows
        ]

    def summarize_inbound_messages(
        self,
        *,
        limit: int = 30,
        session_id: int | None = None,
    ) -> list[dict[str, object]]:
        clause, params = self._scope_clause(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    message_name,
                    frequency,
                    wire_message_number,
                    COUNT(*) AS seen_count,
                    MIN(observed_at_seconds) AS first_seen_at_seconds,
                    MAX(observed_at_seconds) AS last_seen_at_seconds,
                    MIN(body_size) AS min_body_size,
                    MAX(body_size) AS max_body_size,
                    SUM(is_reliable) AS reliable_count,
                    MIN(payload_preview_hex) AS sample_payload_preview_hex
                FROM inbound_messages
                {clause}
                GROUP BY message_name, frequency, wire_message_number
                ORDER BY seen_count DESC, last_seen_at_seconds DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [
            {
                "message_name": row["message_name"],
                "frequency": row["frequency"],
                "wire_message_number": int(row["wire_message_number"]),
                "seen_count": int(row["seen_count"]),
                "first_seen_at_seconds": float(row["first_seen_at_seconds"]),
                "last_seen_at_seconds": float(row["last_seen_at_seconds"]),
                "min_body_size": int(row["min_body_size"]),
                "max_body_size": int(row["max_body_size"]),
                "reliable_count": int(row["reliable_count"] or 0),
                "sample_payload_preview_hex": row["sample_payload_preview_hex"],
            }
            for row in rows
        ]
