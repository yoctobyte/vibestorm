"""Long-lived UDP session state for OpenSim and Second Life style circuits."""

from __future__ import annotations

import asyncio
import json
import random
import socket
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vibestorm.login.models import LoginBootstrap
from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH, UnknownsDatabase
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    MessageDecodeError,
    encode_agent_update,
    encode_agent_throttle,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_object_add,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_use_circuit_code,
    parse_agent_movement_complete,
    parse_chat_from_simulator,
    parse_improved_terse_object_update,
    parse_kill_object,
    parse_object_update,
    parse_object_update_summary,
    parse_packet_ack,
    parse_region_handshake,
    parse_start_ping_check,
)
from vibestorm.udp.packet import LL_RELIABLE_FLAG, build_packet, split_packet
from vibestorm.udp.template import (
    DecodedMessageNumber,
    MessageDispatch,
    MessageTemplateSummary,
    decode_message_number,
)
from vibestorm.udp.zerocode import decode_zerocode, encode_zerocode
from vibestorm.world.models import WorldView
from vibestorm.world.updater import WorldUpdater


@dataclass(slots=True, frozen=True)
class SessionConfig:
    duration_seconds: float = 60.0
    receive_timeout_seconds: float = 0.25
    agent_update_interval_seconds: float = 1.0
    spawn_test_cube: bool = False
    spawn_delay_seconds: float = 2.0
    region_handshake_reply_flags: int = 0
    max_logged_events: int = 64
    capture_dir: Path | None = None
    capture_messages: tuple[str, ...] = ()
    max_captured_per_message: int = 8
    capture_mode: str = "smart"
    unknowns_db_path: Path | None = DEFAULT_UNKNOWNS_DB_PATH


@dataclass(slots=True, frozen=True)
class SessionEvent:
    at_seconds: float
    kind: str
    detail: str


@dataclass(slots=True, frozen=True)
class SessionReport:
    elapsed_seconds: float
    total_received: int
    message_counts: dict[str, int]
    handshake_reply_sent: bool
    movement_completed: bool
    ping_requests_handled: int
    appended_acks_received: int
    packet_acks_received: int
    agent_update_count: int
    pending_reliable_sequences: tuple[int, ...]
    last_region_name: str | None
    close_reason: str | None
    world_view: WorldView
    events: tuple[SessionEvent, ...]


@dataclass(slots=True)
class LiveCircuitSession:
    bootstrap: LoginBootstrap
    dispatcher: MessageDispatcher
    config: SessionConfig = field(default_factory=SessionConfig)
    on_event: Callable[[SessionEvent], None] | None = None
    next_sequence: int = 1
    pending_reliable: dict[int, str] = field(default_factory=dict)
    seen_reliable_sequences: set[int] = field(default_factory=set)
    queued_acks: list[int] = field(default_factory=list)
    received_messages: Counter[str] = field(default_factory=Counter)
    total_received: int = 0
    handshake_reply_sent: bool = False
    ping_requests_handled: int = 0
    appended_acks_received: int = 0
    packet_acks_received: int = 0
    agent_update_count: int = 0
    last_agent_update_at: float | None = None
    last_region_name: str | None = None
    close_reason: str | None = None
    camera_center: tuple[float, float, float] = (128.0, 128.0, 25.0)
    movement_completed: bool = False
    throttle_sent: bool = False
    test_cube_spawned: bool = False
    started: bool = False
    started_at: float | None = None
    events: list[SessionEvent] = field(default_factory=list)
    world_view: WorldView = field(default_factory=WorldView)
    world_updater: WorldUpdater = field(init=False)
    captured_messages: Counter[str] = field(default_factory=Counter)
    unknowns_db: UnknownsDatabase | None = field(init=False, default=None)
    db_session_id: int | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self.world_updater = WorldUpdater(self.world_view)
        if self.config.unknowns_db_path is not None:
            self.unknowns_db = UnknownsDatabase(self.config.unknowns_db_path)

    def start(self, now: float) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        self.started_at = now
        if self.unknowns_db is not None and self.db_session_id is None:
            self.db_session_id = self.unknowns_db.begin_session(
                sim_ip=self.bootstrap.sim_ip,
                sim_port=self.bootstrap.sim_port,
                agent_id=str(self.bootstrap.agent_id),
                configured_duration_seconds=self.config.duration_seconds,
            )
        self._record_event(now, "session.started", f"sim={self.bootstrap.sim_ip}:{self.bootstrap.sim_port}")
        packets = [
            self._build_outbound_packet(
                encode_use_circuit_code(
                    self.bootstrap.circuit_code,
                    self.bootstrap.session_id,
                    self.bootstrap.agent_id,
                ),
                reliable=True,
                label="UseCircuitCode",
            ),
            self._build_outbound_packet(
                encode_complete_agent_movement(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    self.bootstrap.circuit_code,
                ),
                reliable=True,
                label="CompleteAgentMovement",
            ),
        ]
        self.last_agent_update_at = now
        return packets

    def handle_incoming(self, payload: bytes, now: float) -> list[bytes]:
        packet = decode_zerocode(payload)
        view = split_packet(packet)
        for ack in view.appended_acks:
            self.pending_reliable.pop(ack, None)
        self.appended_acks_received += len(view.appended_acks)
        if view.appended_acks:
            self._record_event(now, "transport.appended_ack", ",".join(str(ack) for ack in view.appended_acks))

        try:
            dispatched = self.dispatcher.dispatch(view.message)
        except Exception as exc:
            self._record_unknown_dispatch_failure(
                message=view.message,
                sequence=view.header.sequence,
                at_seconds=now - (self.started_at if self.started_at is not None else now),
                error_text=str(exc),
            )
            return self._flush_transport_packets(now)
        self.total_received += 1
        self.received_messages[dispatched.summary.name] += 1
        if self.unknowns_db is not None:
            self.unknowns_db.record_inbound_message(
                session_id=self.db_session_id,
                observed_at_seconds=now - (self.started_at if self.started_at is not None else now),
                message_sequence=view.header.sequence,
                message_name=dispatched.summary.name,
                frequency=dispatched.summary.frequency,
                wire_message_number=dispatched.message_number.message_number,
                body_size=len(dispatched.body),
                is_reliable=view.header.is_reliable,
                payload_preview_hex=dispatched.body[:24].hex(),
            )
        if view.header.is_reliable and view.header.sequence not in self.queued_acks:
            self.queued_acks.append(view.header.sequence)
        if view.header.is_reliable:
            if view.header.sequence in self.seen_reliable_sequences:
                self._record_event(now, "transport.reliable_duplicate", f"seq={view.header.sequence} msg={dispatched.summary.name}")
                return self._flush_transport_packets(now)
            self.seen_reliable_sequences.add(view.header.sequence)
            self._record_event(now, "transport.reliable_in", f"seq={view.header.sequence} msg={dispatched.summary.name}")

        if dispatched.summary.name == "PacketAck":
            ack_message = parse_packet_ack(dispatched)
            self.packet_acks_received += len(ack_message.packets)
            for ack in ack_message.packets:
                self.pending_reliable.pop(ack, None)
            self._record_event(now, "transport.packet_ack", ",".join(str(ack) for ack in ack_message.packets))
            return self._flush_transport_packets(now)

        if dispatched.summary.name == "CloseCircuit":
            self.close_reason = "simulator closed circuit"
            self._record_event(now, "session.closed", self.close_reason)
            return []

        if dispatched.summary.name == "ChatFromSimulator":
            chat = parse_chat_from_simulator(dispatched)
            self._record_event(
                now,
                "chat.local",
                (
                    f"from={chat.from_name!r} type={chat.chat_type} audible={chat.audible} "
                    f"pos=({chat.position[0]:.2f},{chat.position[1]:.2f},{chat.position[2]:.2f}) "
                    f"message={chat.message!r}"
                ),
            )
            if self.unknowns_db is not None:
                self.unknowns_db.record_nearby_chat(
                    session_id=self.db_session_id,
                    observed_at_seconds=now - (self.started_at if self.started_at is not None else now),
                    message_sequence=view.header.sequence,
                    from_name=chat.from_name,
                    source_id=str(chat.source_id),
                    owner_id=str(chat.owner_id),
                    source_type=chat.source_type,
                    chat_type=chat.chat_type,
                    audible=chat.audible,
                    position=chat.position,
                    message=chat.message,
                )
            return self._flush_transport_packets(now)

        if dispatched.summary.name == "StartPingCheck":
            ping = parse_start_ping_check(dispatched)
            self.ping_requests_handled += 1
            self._record_event(
                now,
                "ping.request",
                f"ping_id={ping.ping_id} oldest_unacked={ping.oldest_unacked}",
            )
            packets = [
                self._build_outbound_packet(
                    encode_complete_ping_check(ping.ping_id),
                    now=now,
                    label="CompletePingCheck",
                ),
            ]
            packets.extend(self._flush_transport_packets(now))
            return packets

        if dispatched.summary.name == "RegionHandshake":
            handshake = parse_region_handshake(dispatched)
            self.last_region_name = handshake.sim_name
            self.camera_center = (
                float(self.bootstrap.region_x) + 128.0,
                float(self.bootstrap.region_y) + 128.0,
                self.camera_center[2],
            )
            self.handshake_reply_sent = True
            world_event = self.world_updater.apply_region_handshake(
                handshake,
                region_x=self.bootstrap.region_x,
                region_y=self.bootstrap.region_y,
            )
            self._record_event(now, world_event.kind, world_event.detail)
            packets = [
                self._build_outbound_packet(
                    encode_region_handshake_reply(
                        self.bootstrap.agent_id,
                        self.bootstrap.session_id,
                        self.config.region_handshake_reply_flags,
                    ),
                    reliable=True,
                    zerocoded=True,
                    now=now,
                    label="RegionHandshakeReply",
                ),
            ]
            packets.extend(self._flush_transport_packets(now))
            return packets

        if dispatched.summary.name == "AgentMovementComplete":
            movement = parse_agent_movement_complete(dispatched)
            self.camera_center = movement.position
            self.movement_completed = True
            self._record_event(
                now,
                "movement.complete",
                f"region_handle={movement.region_handle} position={movement.position}",
            )
        else:
            world_event = self.world_updater.apply_dispatch(dispatched)
            if world_event is not None:
                self._record_event(now, world_event.kind, world_event.detail)
                self._record_object_update_observation(
                    dispatched=dispatched,
                    sequence=view.header.sequence,
                    at_seconds=now - (self.started_at if self.started_at is not None else now),
                    reason=world_event.kind,
                )
                self._record_improved_terse_observation(
                    dispatched=dispatched,
                    sequence=view.header.sequence,
                    at_seconds=now - (self.started_at if self.started_at is not None else now),
                    reason=world_event.kind,
                )
                self._record_kill_object_observation(
                    dispatched=dispatched,
                    sequence=view.header.sequence,
                    at_seconds=now - (self.started_at if self.started_at is not None else now),
                    reason=world_event.kind,
                )
                self._capture_incoming_message(
                    message_name=dispatched.summary.name,
                    sequence=view.header.sequence,
                    is_reliable=view.header.is_reliable,
                    appended_acks=view.appended_acks,
                    at_seconds=now - (self.started_at if self.started_at is not None else now),
                    message_body=dispatched.body,
                    reason=world_event.kind,
                )

        return self._flush_transport_packets(now)

    def drain_due_packets(self, now: float) -> list[bytes]:
        if self.close_reason is not None or not self.started or not self.movement_completed:
            return []
        if self.last_agent_update_at is None:
            self.last_agent_update_at = now
            return []
        if now - self.last_agent_update_at < self.config.agent_update_interval_seconds:
            packets = self._drain_throttle_packets(now)
            packets.extend(self._drain_test_cube_packets(now))
            return packets

        self.last_agent_update_at = now
        packets = self._drain_throttle_packets(now)
        packets.extend(self._drain_test_cube_packets(now))
        self.agent_update_count += 1
        packets.append(
            self._build_outbound_packet(
                encode_agent_update(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    camera_center=self.camera_center,
                ),
                zerocoded=True,
                now=now,
                label="AgentUpdate",
            ),
        )
        return packets

    def build_report(self, elapsed_seconds: float) -> SessionReport:
        return SessionReport(
            elapsed_seconds=elapsed_seconds,
            total_received=self.total_received,
            message_counts=dict(self.received_messages),
            handshake_reply_sent=self.handshake_reply_sent,
            movement_completed=self.movement_completed,
            ping_requests_handled=self.ping_requests_handled,
            appended_acks_received=self.appended_acks_received,
            packet_acks_received=self.packet_acks_received,
            agent_update_count=self.agent_update_count,
            pending_reliable_sequences=tuple(sorted(self.pending_reliable)),
            last_region_name=self.last_region_name,
            close_reason=self.close_reason,
            world_view=self.world_view,
            events=tuple(self.events),
        )

    def _build_outbound_packet(
        self,
        message: bytes,
        *,
        reliable: bool = False,
        zerocoded: bool = False,
        now: float | None = None,
        include_queued_acks: bool = False,
        label: str,
    ) -> bytes:
        sequence = self.next_sequence
        self.next_sequence += 1
        appended_acks = tuple(self._consume_queued_acks()) if include_queued_acks else ()
        packet = build_packet(
            message,
            sequence=sequence,
            flags=LL_RELIABLE_FLAG if reliable else 0,
            appended_acks=appended_acks,
        )
        if zerocoded:
            packet = encode_zerocode(packet)
        if reliable:
            self.pending_reliable[sequence] = label
        if now is not None:
            detail = f"seq={sequence}"
            if reliable:
                detail += " reliable"
            if appended_acks:
                detail += f" acks={','.join(str(ack) for ack in appended_acks)}"
            self._record_event(now, "packet.sent", f"{label} {detail}")
        return packet

    def _consume_queued_acks(self) -> list[int]:
        queued = self.queued_acks
        self.queued_acks = []
        return queued

    def _flush_transport_packets(self, now: float) -> list[bytes]:
        packets: list[bytes] = []
        if self.queued_acks:
            acked = tuple(self._consume_queued_acks())
            packets.append(
                self._build_outbound_packet(
                    encode_packet_ack(acked),
                    now=now,
                    label="PacketAck",
                ),
            )
        packets.extend(self.drain_due_packets(now))
        return packets

    def _drain_throttle_packets(self, now: float) -> list[bytes]:
        if self.throttle_sent:
            return []
        self.throttle_sent = True
        return [
            self._build_outbound_packet(
                encode_agent_throttle(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    self.bootstrap.circuit_code,
                ),
                zerocoded=True,
                now=now,
                label="AgentThrottle",
            ),
        ]

    def _drain_test_cube_packets(self, now: float) -> list[bytes]:
        if not self.config.spawn_test_cube or self.test_cube_spawned or self.started_at is None:
            return []
        if now - self.started_at < self.config.spawn_delay_seconds:
            return []

        rng = random.Random(int(now * 1000) ^ self.bootstrap.circuit_code)
        center_x = self.camera_center[0] + rng.uniform(-5.0, 5.0)
        center_y = self.camera_center[1] + rng.uniform(-5.0, 5.0)
        center_z = max(self.camera_center[2], 22.0) + rng.uniform(0.5, 2.5)
        scale = (
            rng.uniform(0.5, 2.0),
            rng.uniform(0.5, 2.0),
            rng.uniform(0.5, 2.0),
        )
        self.test_cube_spawned = True
        self._record_event(
            now,
            "world.spawn_test_cube",
            f"position=({center_x:.2f},{center_y:.2f},{center_z:.2f}) scale=({scale[0]:.2f},{scale[1]:.2f},{scale[2]:.2f})",
        )
        return [
            self._build_outbound_packet(
                encode_object_add(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    ray_start=(center_x, center_y, center_z + 5.0),
                    ray_end=(center_x, center_y, center_z),
                    scale=scale,
                ),
                zerocoded=True,
                now=now,
                label="ObjectAdd",
            ),
        ]

    def _record_event(self, now: float, kind: str, detail: str) -> None:
        started_at = self.started_at if self.started_at is not None else now
        event = SessionEvent(at_seconds=now - started_at, kind=kind, detail=detail)
        self.events.append(event)
        if len(self.events) > self.config.max_logged_events:
            self.events.pop(0)
        if self.on_event is not None:
            self.on_event(event)

    def _capture_incoming_message(
        self,
        *,
        message_name: str,
        sequence: int,
        is_reliable: bool,
        appended_acks: tuple[int, ...],
        at_seconds: float,
        message_body: bytes,
        reason: str,
    ) -> None:
        if self.config.capture_dir is None:
            return

        capture_messages = self.config.capture_messages or ("ObjectUpdate",)
        if message_name not in capture_messages:
            return

        if self.config.capture_mode == "smart" and reason == "world.object_update":
            return

        captured_count = self.captured_messages[message_name]
        if captured_count >= self.config.max_captured_per_message:
            return

        target_dir = self.config.capture_dir / message_name
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{captured_count + 1:03d}-seq{sequence:06d}"
        payload_path = target_dir / f"{stem}.body.bin"
        metadata_path = target_dir / f"{stem}.json"

        payload_path.write_bytes(message_body)
        metadata = {
            "message_name": message_name,
            "sequence": sequence,
            "is_reliable": is_reliable,
            "appended_acks": list(appended_acks),
            "at_seconds": round(at_seconds, 6),
            "body_size": len(message_body),
            "capture_reason": reason,
            "capture_mode": self.config.capture_mode,
            "source": "live_session_capture",
            "message_body": message_body,
        }
        metadata_path.write_text(
            json.dumps(
                self._build_capture_metadata(**metadata),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.captured_messages[message_name] += 1

    def _build_capture_metadata(self, **metadata: object) -> dict[str, object]:
        message_body = metadata.pop("message_body", None)
        if metadata.get("message_name") != "ObjectUpdate" or not isinstance(message_body, bytes):
            return metadata

        dispatch = MessageDispatch(
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
            body=message_body,
        )
        try:
            summary = parse_object_update_summary(dispatch)
        except Exception:
            return metadata

        object_update: dict[str, object] = {
            "region_handle": summary.region_handle,
            "time_dilation": summary.time_dilation,
            "object_count": summary.object_count,
        }
        try:
            parsed = parse_object_update(dispatch)
        except Exception as exc:
            object_update["decode_status"] = "partial"
            object_update["decode_error"] = str(exc)
        else:
            obj = parsed.objects[0]
            object_update.update(
                {
                    "decode_status": "decoded",
                    "variant": obj.variant,
                    "full_id": str(obj.full_id),
                    "local_id": obj.local_id,
                    "update_flags": obj.update_flags,
                    "name_values": obj.name_values,
                    "texture_entry_size": obj.texture_entry_size,
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
                },
            )
        metadata["object_update"] = object_update
        return metadata

    def _record_object_update_observation(
        self,
        *,
        dispatched: MessageDispatch,
        sequence: int,
        at_seconds: float,
        reason: str,
    ) -> None:
        if self.unknowns_db is None or dispatched.summary.name != "ObjectUpdate":
            return
        try:
            summary = parse_object_update_summary(dispatched)
        except MessageDecodeError as exc:
            self.unknowns_db.record_object_update_packet(
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=None,
                object_count=None,
                decode_status="malformed",
                decode_error=str(exc),
                packet_tags=["malformed"],
            )
            return
        try:
            parsed = parse_object_update(dispatched)
        except MessageDecodeError as exc:
            packet_tags = ["summary_only", reason]
            if summary.object_count > 1:
                packet_tags.append("multi_object")
            self.unknowns_db.record_object_update_packet(
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=summary.region_handle,
                object_count=summary.object_count,
                decode_status="summary_only",
                decode_error=str(exc),
                packet_tags=packet_tags,
            )
            return

        packet_tags = ["decoded", reason, f"object_count:{len(parsed.objects)}"]
        if len(parsed.objects) == 1:
            packet_tags.append("single_object")
        else:
            packet_tags.append("multi_object")
        if any(obj.interesting_payloads for obj in parsed.objects):
            packet_tags.append("interesting")
        packet_id = self.unknowns_db.record_object_update_packet(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            capture_reason=reason,
            region_handle=parsed.region_handle,
            object_count=len(parsed.objects),
            decode_status="decoded",
            decode_error=None,
            packet_tags=packet_tags,
        )
        for obj in parsed.objects:
            self.unknowns_db.record_object_update_entity(
                packet_id=packet_id,
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=parsed.region_handle,
                entry=obj,
            )

    def _record_improved_terse_observation(
        self,
        *,
        dispatched: MessageDispatch,
        sequence: int,
        at_seconds: float,
        reason: str,
    ) -> None:
        if self.unknowns_db is None or dispatched.summary.name != "ImprovedTerseObjectUpdate":
            return
        try:
            parsed = parse_improved_terse_object_update(dispatched)
        except MessageDecodeError:
            return

        packet_tags = [reason, f"object_count:{len(parsed.objects)}"]
        if any(obj.local_id is not None for obj in parsed.objects):
            packet_tags.append("has_local_id")
        if any(obj.texture_entry_size > 0 for obj in parsed.objects):
            packet_tags.append("has_texture_entry")
        packet_id = self.unknowns_db.record_improved_terse_packet(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            capture_reason=reason,
            region_handle=parsed.region_handle,
            object_count=len(parsed.objects),
            time_dilation=parsed.time_dilation,
            packet_tags=packet_tags,
        )
        for obj in parsed.objects:
            self.unknowns_db.record_improved_terse_entity(
                packet_id=packet_id,
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=parsed.region_handle,
                entry=obj,
            )

    def _record_kill_object_observation(
        self,
        *,
        dispatched: MessageDispatch,
        sequence: int,
        at_seconds: float,
        reason: str,
    ) -> None:
        if self.unknowns_db is None or dispatched.summary.name != "KillObject":
            return
        try:
            parsed = parse_kill_object(dispatched)
        except MessageDecodeError:
            return

        self.unknowns_db.record_kill_object_packet(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            capture_reason=reason,
            message=parsed,
        )

    def _record_unknown_dispatch_failure(
        self,
        *,
        message: bytes,
        sequence: int,
        at_seconds: float,
        error_text: str,
    ) -> None:
        event = SessionEvent(at_seconds=at_seconds, kind="udp.unknown", detail=error_text)
        self.events.append(event)
        if len(self.events) > self.config.max_logged_events:
            self.events.pop(0)
        if self.on_event is not None:
            self.on_event(event)
        if self.unknowns_db is None:
            return
        raw_message_number: int | None = None
        encoded_length: int | None = None
        failure_stage = "dispatch"
        try:
            decoded = decode_message_number(message)
        except Exception:
            failure_stage = "message_number_decode"
        else:
            raw_message_number = decoded.message_number
            encoded_length = decoded.encoded_length
        self.unknowns_db.record_unknown_udp_message(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            failure_stage=failure_stage,
            raw_message_number=raw_message_number,
            encoded_length=encoded_length,
            payload=message,
            error_text=error_text,
        )


async def run_live_session(
    bootstrap: LoginBootstrap,
    dispatcher: MessageDispatcher,
    *,
    config: SessionConfig | None = None,
    on_event: Callable[[SessionEvent], None] | None = None,
) -> SessionReport:
    session_config = config or SessionConfig()
    session = LiveCircuitSession(
        bootstrap=bootstrap,
        dispatcher=dispatcher,
        config=session_config,
        on_event=on_event,
    )
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    deadline = start_time + session_config.duration_seconds

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)

        for packet in session.start(start_time):
            await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

        while loop.time() < deadline and session.close_reason is None:
            now = loop.time()
            for packet in session.drain_due_packets(now):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            try:
                payload, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535),
                    timeout=min(session_config.receive_timeout_seconds, remaining),
                )
            except TimeoutError:
                continue

            for packet in session.handle_incoming(payload, loop.time()):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

    if session.close_reason is None and session.movement_completed:
        session._record_event(loop.time(), "session.completed", "duration elapsed")
    elif session.close_reason is None:
        session._record_event(loop.time(), "session.incomplete", "duration elapsed before movement complete")
    return session.build_report(loop.time() - start_time)
