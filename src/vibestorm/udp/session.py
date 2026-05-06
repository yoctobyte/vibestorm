"""Long-lived UDP session state for OpenSim and Second Life style circuits."""

from __future__ import annotations

import asyncio
import json
import math
import random
import socket
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.assets.j2k import J2KDecodeError, decode_j2k
from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.caps.get_texture_client import GetTextureClient, GetTextureError
from vibestorm.caps.upload_baked_texture_client import UploadBakedTextureClient, UploadBakedTextureError
from vibestorm.caps.inventory_client import (
    InventoryCapabilityClient,
    InventoryCapabilityError,
    InventoryFetchSnapshot,
    InventoryFolderRequest,
    InventoryItemRequest,
    parse_inventory_items_payload,
    parse_inventory_descendents_payload,
)
from vibestorm.event_queue.client import EventQueueClient, EventQueueError
from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH, UnknownsDatabase
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    AgentCachedTextureResponseMessage,
    AvatarAppearanceMessage,
    MessageDecodeError,
    AgentWearablesUpdateMessage,
    DEFAULT_AVATAR_SIZE,
    DEFAULT_AVATAR_TEXTURE_ENTRY,
    DEFAULT_AVATAR_VISUAL_PARAMS,
    DEFAULT_AVATAR_BAKE_INDICES,
    WearableCacheEntry,
    encode_agent_cached_texture,
    encode_agent_update,
    encode_agent_is_now_wearing,
    encode_agent_set_appearance,
    encode_agent_throttle,
    encode_agent_wearables_request,
    encode_chat_from_viewer,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_logout_request,
    encode_map_block_request,
    encode_object_add,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_request_multiple_objects,
    encode_request_object_properties_family,
    encode_teleport_location_request,
    encode_use_circuit_code,
    parse_agent_alert_message,
    parse_agent_movement_complete,
    parse_agent_cached_texture_response,
    parse_agent_wearables_update,
    parse_alert_message,
    parse_avatar_appearance,
    parse_chat_from_simulator,
    parse_improved_instant_message,
    parse_improved_terse_object_update,
    parse_kill_object,
    parse_layer_data,
    parse_map_block_reply,
    parse_object_update,
    parse_object_update_cached,
    parse_object_update_compressed,
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
    camera_sweep: bool = False
    camera_sweep_radius: float = 12.0
    camera_sweep_period_seconds: float = 24.0
    camera_sweep_height_offset: float = 3.0
    spawn_test_cube: bool = False
    spawn_delay_seconds: float = 2.0
    region_handshake_reply_flags: int = 0
    max_logged_events: int = 64
    capture_dir: Path | None = None
    capture_messages: tuple[str, ...] = ()
    max_captured_per_message: int = 8
    capture_mode: str = "smart"
    unknowns_db_path: Path | None = DEFAULT_UNKNOWNS_DB_PATH
    caps_prelude: bool = True


@dataclass(slots=True, frozen=True)
class SessionEvent:
    at_seconds: float
    kind: str
    detail: str


@dataclass(slots=True, frozen=True)
class BakedAppearanceOverride:
    """Uploaded baked textures and appearance data ready to send in AgentSetAppearance."""
    texture_entry: bytes
    wearable_cache_items: tuple[WearableCacheEntry, ...]
    visual_params: bytes
    serial_num: int
    size: tuple[float, float, float]


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
    resolved_capabilities: tuple[str, ...]
    bootstrap_packed_appearance_present: bool
    inventory_fetch: InventoryFetchSnapshot | None
    wearables_update: AgentWearablesUpdateMessage | None
    cached_texture_response: AgentCachedTextureResponseMessage | None
    avatar_appearance: AvatarAppearanceMessage | None
    self_avatar_appearance: AvatarAppearanceMessage | None
    baked_appearance_override: BakedAppearanceOverride | None
    region_map_image_id: UUID | None
    region_map_path: Path | None
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
    camera_at_axis: tuple[float, float, float] = (1.0, 0.0, 0.0)
    camera_left_axis: tuple[float, float, float] = (0.0, 1.0, 0.0)
    camera_up_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)
    base_camera_center: tuple[float, float, float] | None = None
    agent_control_flags: int = 0
    body_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    head_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    agent_state: int = 0
    movement_completed: bool = False
    throttle_sent: bool = False
    wearables_request_sent: bool = False
    cached_texture_request_sent: bool = False
    appearance_sent: bool = False
    logout_sent: bool = False
    wearables_update: AgentWearablesUpdateMessage | None = None
    latest_avatar_appearance: AvatarAppearanceMessage | None = None
    latest_self_avatar_appearance: AvatarAppearanceMessage | None = None
    latest_cached_texture_response: AgentCachedTextureResponseMessage | None = None
    latest_inventory_fetch: InventoryFetchSnapshot | None = None
    baked_appearance_override: BakedAppearanceOverride | None = None
    upload_baked_url: str | None = None
    get_texture_url: str | None = None
    map_block_request_sent: bool = False
    region_map_image_id: UUID | None = None
    region_map_fetched: bool = False
    region_map_path: Path | None = None
    # Most-recent LayerData blob per layer-type byte (0x4C 'L', 0x57 'W',
    # 0x37 '7', etc.). Patches arrive incrementally — the wire-side
    # session just keeps the latest blob; reassembly into a heightmap
    # happens in ``vibestorm.world.terrain`` when the bus consumer
    # decodes it.
    latest_layer_data: dict[int, bytes] = field(default_factory=dict)
    resolved_capabilities: tuple[str, ...] = ()
    properties_requested: set[UUID] = field(default_factory=set)
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
        if self.bootstrap.initial_packed_appearance is not None:
            packed = self.bootstrap.initial_packed_appearance
            self._record_event(
                now,
                "appearance.bootstrap",
                " ".join(
                    [
                        f"serial={packed.serial_num if packed.serial_num is not None else '-'}",
                        f"height={packed.avatar_height if packed.avatar_height is not None else '-'}",
                        f"texture={len(packed.texture_entry) if packed.texture_entry is not None else 0}",
                        f"visual={len(packed.visual_params) if packed.visual_params is not None else 0}",
                    ]
                ),
            )
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

        if dispatched.summary.name == "ImprovedInstantMessage":
            try:
                im = parse_improved_instant_message(dispatched)
            except MessageDecodeError as exc:
                self._record_event(now, "chat.im.decode_error", str(exc))
                return self._flush_transport_packets(now)
            self._record_event(
                now,
                "chat.im",
                (
                    f"from={im.from_agent_name!r} dialog={im.dialog} "
                    f"to={im.to_agent_id} message={im.message!r}"
                ),
            )
            return self._flush_transport_packets(now)

        if dispatched.summary.name == "AlertMessage":
            try:
                alert = parse_alert_message(dispatched)
            except MessageDecodeError as exc:
                self._record_event(now, "chat.alert.decode_error", str(exc))
                return self._flush_transport_packets(now)
            self._record_event(now, "chat.alert", f"message={alert.message!r}")
            return self._flush_transport_packets(now)

        if dispatched.summary.name == "AgentAlertMessage":
            try:
                alert = parse_agent_alert_message(dispatched)
            except MessageDecodeError as exc:
                self._record_event(now, "chat.agent_alert.decode_error", str(exc))
                return self._flush_transport_packets(now)
            self._record_event(
                now,
                "chat.agent_alert",
                f"modal={int(alert.modal)} message={alert.message!r}",
            )
            return self._flush_transport_packets(now)

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

        if dispatched.summary.name == "LayerData":
            try:
                layer = parse_layer_data(dispatched)
            except MessageDecodeError as exc:
                self._record_event(now, "terrain.layer_data.decode_error", str(exc))
                return self._flush_transport_packets(now)
            self.latest_layer_data[layer.layer_type] = layer.data
            self._record_event(
                now,
                "terrain.layer_data",
                f"type={layer.layer_type:#04x} bytes={len(layer.data)}",
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
            self.base_camera_center = self.camera_center
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
            if not self.map_block_request_sent:
                grid_x = self.bootstrap.region_x // 256
                grid_y = self.bootstrap.region_y // 256
                packets.append(
                    self._build_outbound_packet(
                        encode_map_block_request(
                            self.bootstrap.agent_id,
                            self.bootstrap.session_id,
                            min_x=grid_x,
                            max_x=grid_x,
                            min_y=grid_y,
                            max_y=grid_y,
                        ),
                        reliable=True,
                        now=now,
                        label="MapBlockRequest",
                    )
                )
                self.map_block_request_sent = True
                self._record_event(
                    now, "map.request", f"grid=({grid_x},{grid_y})"
                )
            packets.extend(self._flush_transport_packets(now))
            return packets

        if dispatched.summary.name == "MapBlockReply":
            try:
                reply = parse_map_block_reply(dispatched)
            except MessageDecodeError as exc:
                self._record_event(now, "map.reply.decode_error", str(exc))
                return self._flush_transport_packets(now)
            grid_x = self.bootstrap.region_x // 256
            grid_y = self.bootstrap.region_y // 256
            match = next(
                (
                    entry
                    for entry in reply.entries
                    if entry.x == grid_x and entry.y == grid_y
                ),
                None,
            )
            if match is None:
                self._record_event(
                    now,
                    "map.reply.no_match",
                    f"want=({grid_x},{grid_y}) got=[{','.join(f'({e.x},{e.y})' for e in reply.entries)}]",
                )
            elif match.map_image_id.int == 0:
                self._record_event(
                    now,
                    "map.reply.empty_image_id",
                    f"region={match.name!r} grid=({match.x},{match.y})",
                )
            else:
                self.region_map_image_id = match.map_image_id
                self._record_event(
                    now,
                    "map.reply",
                    f"region={match.name!r} grid=({match.x},{match.y}) image={match.map_image_id}",
                )
            return self._flush_transport_packets(now)

        if dispatched.summary.name == "AgentMovementComplete":
            movement = parse_agent_movement_complete(dispatched)
            self.camera_center = movement.position
            self.base_camera_center = movement.position
            self.movement_completed = True
            self._record_event(
                now,
                "movement.complete",
                f"region_handle={movement.region_handle} position={movement.position}",
            )
        elif dispatched.summary.name == "AgentWearablesUpdate":
            wearables_update = parse_agent_wearables_update(dispatched)
            self.wearables_update = wearables_update
            self._record_event(
                now,
                "appearance.wearables_update",
                f"serial={wearables_update.serial_num} count={len(wearables_update.wearables)}",
            )
        elif dispatched.summary.name == "AgentCachedTextureResponse":
            cached = parse_agent_cached_texture_response(dispatched)
            self.latest_cached_texture_response = cached
            non_zero = sum(1 for item in cached.textures if item.texture_id.int != 0)
            self._record_event(
                now,
                "appearance.cached_texture_response",
                f"serial={cached.serial_num} count={len(cached.textures)} non_zero={non_zero}",
            )
        elif dispatched.summary.name == "AvatarAppearance":
            appearance = parse_avatar_appearance(dispatched)
            self.latest_avatar_appearance = appearance
            if appearance.sender_id == self.bootstrap.agent_id:
                self.latest_self_avatar_appearance = appearance
            self._record_event(
                now,
                "appearance.avatar",
                (
                    f"sender={appearance.sender_id} texture={len(appearance.texture_entry)} "
                    f"visual={len(appearance.visual_params)} attachments={len(appearance.attachments)}"
                ),
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
                self._record_cached_observation(
                    dispatched=dispatched,
                    sequence=view.header.sequence,
                    at_seconds=now - (self.started_at if self.started_at is not None else now),
                    reason=world_event.kind,
                )
                self._record_compressed_observation(
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

        packets = self._flush_transport_packets(now)

        # When ObjectUpdateCached arrives, request full ObjectUpdate for each
        # local_id — we never have a warm cache so CacheMissType=0 always.
        if dispatched.summary.name == "ObjectUpdateCached":
            try:
                cached_msg = parse_object_update_cached(dispatched)
            except Exception:
                cached_msg = None
            if cached_msg is not None and cached_msg.objects:
                local_ids = [obj.local_id for obj in cached_msg.objects]
                for i in range(0, len(local_ids), 255):
                    chunk = local_ids[i : i + 255]
                    packets.append(
                        self._build_outbound_packet(
                            encode_request_multiple_objects(
                                self.bootstrap.agent_id,
                                self.bootstrap.session_id,
                                chunk,
                            ),
                            reliable=True,
                            zerocoded=True,
                            now=now,
                            label="RequestMultipleObjects",
                        )
                    )

        return packets

    def drain_due_packets(self, now: float) -> list[bytes]:
        if self.close_reason is not None or not self.started or not self.movement_completed:
            return []
        if self.last_agent_update_at is None:
            self.last_agent_update_at = now
            return []
        if now - self.last_agent_update_at < self.config.agent_update_interval_seconds:
            packets = self._drain_throttle_packets(now)
            packets.extend(self._drain_appearance_packets(now))
            packets.extend(self._drain_test_cube_packets(now))
            packets.extend(self._drain_properties_requests(now))
            return packets

        self.last_agent_update_at = now
        packets = self._drain_throttle_packets(now)
        packets.extend(self._drain_appearance_packets(now))
        packets.extend(self._drain_test_cube_packets(now))
        packets.extend(self._drain_properties_requests(now))
        self._update_camera_sweep(now)
        self.agent_update_count += 1
        packets.append(
            self._build_outbound_packet(
                encode_agent_update(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    body_rotation=self.body_rotation,
                    head_rotation=self.head_rotation,
                    state=self.agent_state,
                    camera_center=self.camera_center,
                    camera_at_axis=self.camera_at_axis,
                    camera_left_axis=self.camera_left_axis,
                    camera_up_axis=self.camera_up_axis,
                    control_flags=self.agent_control_flags,
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
            resolved_capabilities=self.resolved_capabilities,
            bootstrap_packed_appearance_present=self.bootstrap.initial_packed_appearance is not None,
            inventory_fetch=self.latest_inventory_fetch,
            wearables_update=self.wearables_update,
            cached_texture_response=self.latest_cached_texture_response,
            avatar_appearance=self.latest_avatar_appearance,
            self_avatar_appearance=self.latest_self_avatar_appearance,
            baked_appearance_override=self.baked_appearance_override,
            region_map_image_id=self.region_map_image_id,
            region_map_path=self.region_map_path,
            events=tuple(self.events),
        )

    def set_control_flags(self, flags: int) -> None:
        if not 0 <= int(flags) <= 0xFFFFFFFF:
            raise ValueError("control_flags must fit in U32")
        self.agent_control_flags = int(flags)

    def add_control_flags(self, flags: int) -> None:
        self.set_control_flags(self.agent_control_flags | int(flags))

    def remove_control_flags(self, flags: int) -> None:
        self.set_control_flags(self.agent_control_flags & ~int(flags))

    def clear_control_flags(self) -> None:
        self.agent_control_flags = 0

    def set_body_rotation(self, rotation: tuple[float, float, float]) -> None:
        self.body_rotation = (float(rotation[0]), float(rotation[1]), float(rotation[2]))

    def set_head_rotation(self, rotation: tuple[float, float, float]) -> None:
        self.head_rotation = (float(rotation[0]), float(rotation[1]), float(rotation[2]))

    def build_chat_packet(
        self,
        message: str,
        *,
        chat_type: int = 1,
        channel: int = 0,
        now: float | None = None,
    ) -> bytes:
        """Build a ChatFromViewer packet ready for sock_sendto.

        chat_type: 0=whisper, 1=normal, 2=shout. channel: 0=public, others
        target LSL listeners or special channels. Records a chat.outbound
        event so the line shows up alongside inbound chat in the event log.
        """
        packet = self._build_outbound_packet(
            encode_chat_from_viewer(
                self.bootstrap.agent_id,
                self.bootstrap.session_id,
                message,
                chat_type=chat_type,
                channel=channel,
            ),
            reliable=True,
            zerocoded=True,
            now=now,
            label="ChatFromViewer",
        )
        self._record_event(
            now if now is not None else (self.started_at or 0.0),
            "chat.outbound",
            f"type={chat_type} channel={channel} message={message!r}",
        )
        return packet

    def build_teleport_location_packet(
        self,
        *,
        region_handle: int,
        position: tuple[float, float, float],
        look_at: tuple[float, float, float] = (1.0, 0.0, 0.0),
        now: float | None = None,
    ) -> bytes:
        """Build a TeleportLocationRequest packet ready for sock_sendto."""
        packet = self._build_outbound_packet(
            encode_teleport_location_request(
                self.bootstrap.agent_id,
                self.bootstrap.session_id,
                region_handle=region_handle,
                position=position,
                look_at=look_at,
            ),
            reliable=True,
            now=now,
            label="TeleportLocationRequest",
        )
        self._record_event(
            now if now is not None else (self.started_at or 0.0),
            "teleport.location.requested",
            (
                f"region_handle={region_handle:#018x} "
                f"position=({position[0]:.1f},{position[1]:.1f},{position[2]:.1f})"
            ),
        )
        return packet

    def build_shutdown_packets(self, now: float) -> list[bytes]:
        if self.logout_sent or not self.started:
            return []
        self.logout_sent = True
        self._record_event(now, "session.logout_request", "client requested logout")
        return [
            self._build_outbound_packet(
                encode_logout_request(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                ),
                reliable=True,
                now=now,
                label="LogoutRequest",
            )
        ]

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

    def _drain_appearance_packets(self, now: float) -> list[bytes]:
        if not self.handshake_reply_sent or not self.movement_completed:
            return []

        if not self.wearables_request_sent:
            self.wearables_request_sent = True
            return [
                self._build_outbound_packet(
                    encode_agent_wearables_request(
                        self.bootstrap.agent_id,
                        self.bootstrap.session_id,
                    ),
                    reliable=True,
                    now=now,
                    label="AgentWearablesRequest",
                ),
            ]

        if self.wearables_update is None:
            return []

        if not self.cached_texture_request_sent:
            self.cached_texture_request_sent = True
            bootstrap_cache_by_index = {
                entry.texture_index: entry.cache_id
                for entry in self.bootstrap.initial_baked_cache_entries
                if entry.cache_id.int != 0
            }
            cache_items = tuple(
                WearableCacheEntry(
                    cache_id=bootstrap_cache_by_index.get(index, UUID(int=0)),
                    texture_index=index,
                )
                for index in DEFAULT_AVATAR_BAKE_INDICES
            )
            return [
                self._build_outbound_packet(
                    encode_agent_cached_texture(
                        self.bootstrap.agent_id,
                        self.bootstrap.session_id,
                        serial_num=self.wearables_update.serial_num,
                        cache_items=cache_items,
                    ),
                    reliable=True,
                    now=now,
                    label="AgentCachedTexture",
                ),
            ]

        if self.appearance_sent:
            return []

        # If bake uploads are pending (deferred from caps prelude), wait for them.
        if self.upload_baked_url is not None and self.baked_appearance_override is None:
            return []

        self.appearance_sent = True
        baked = self.baked_appearance_override
        if baked is not None:
            # We have freshly-uploaded baked textures — use them verbatim.
            texture_entry = baked.texture_entry
            visual_params = baked.visual_params
            serial_num = baked.serial_num
            size = baked.size
            cache_items = baked.wearable_cache_items
            self._record_event(now, "appearance.baked_override", f"serial={serial_num} te={len(texture_entry)} vp={len(visual_params)} bakes={len(cache_items)}")
        else:
            bootstrap_appearance = self.bootstrap.initial_packed_appearance
            serial_candidates = [self.wearables_update.serial_num, 1]
            if bootstrap_appearance is not None and bootstrap_appearance.serial_num is not None:
                serial_candidates.append(bootstrap_appearance.serial_num)
            serial_num = max(serial_candidates)
            source_appearance = self.latest_self_avatar_appearance
            texture_entry = (
                source_appearance.texture_entry
                if source_appearance is not None and source_appearance.texture_entry
                else (
                    bootstrap_appearance.texture_entry
                    if bootstrap_appearance is not None and bootstrap_appearance.texture_entry
                    else DEFAULT_AVATAR_TEXTURE_ENTRY
                )
            )
            visual_params = (
                source_appearance.visual_params
                if source_appearance is not None and source_appearance.visual_params
                else (
                    bootstrap_appearance.visual_params
                    if bootstrap_appearance is not None and bootstrap_appearance.visual_params
                    else DEFAULT_AVATAR_VISUAL_PARAMS
                )
            )
            if bootstrap_appearance is not None and bootstrap_appearance.avatar_height is not None:
                size = (DEFAULT_AVATAR_SIZE[0], DEFAULT_AVATAR_SIZE[1], bootstrap_appearance.avatar_height)
            else:
                size = DEFAULT_AVATAR_SIZE
            cache_items = ()
        return [
            self._build_outbound_packet(
                encode_agent_is_now_wearing(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    self.wearables_update.wearables,
                ),
                reliable=True,
                zerocoded=True,
                now=now,
                label="AgentIsNowWearing",
            ),
            self._build_outbound_packet(
                encode_agent_set_appearance(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    serial_num=serial_num,
                    size=size,
                    cache_items=cache_items,
                    texture_entry=texture_entry,
                    visual_params=visual_params,
                ),
                reliable=True,
                zerocoded=True,
                now=now,
                label="AgentSetAppearance",
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

    def _drain_properties_requests(self, now: float) -> list[bytes]:
        """Send RequestObjectPropertiesFamily for tracked objects not yet requested.

        Caps at 10 per drain cycle to avoid flooding the sim.
        """
        if not self.movement_completed:
            return []
        pending = [
            full_id
            for full_id in self.world_view.objects
            if full_id not in self.properties_requested
        ]
        if not pending:
            return []
        batch = pending[:10]
        packets = []
        for full_id in batch:
            self.properties_requested.add(full_id)
            packets.append(
                self._build_outbound_packet(
                    encode_request_object_properties_family(
                        self.bootstrap.agent_id,
                        self.bootstrap.session_id,
                        full_id,
                    ),
                    reliable=True,
                    zerocoded=True,
                    now=now,
                    label="RequestObjectPropertiesFamily",
                )
            )
        return packets

    def _update_camera_sweep(self, now: float) -> None:
        if not self.config.camera_sweep or self.started_at is None:
            return
        base_center = self.base_camera_center or self.camera_center
        period = max(self.config.camera_sweep_period_seconds, 1.0)
        phase = ((now - self.started_at) % period) / period
        theta = phase * (2.0 * math.pi)
        radius = max(self.config.camera_sweep_radius, 0.0)
        center_x = base_center[0] + math.cos(theta) * radius
        center_y = base_center[1] + math.sin(theta) * radius
        center_z = max(base_center[2], 22.0) + self.config.camera_sweep_height_offset + math.sin(theta * 0.5) * 1.5
        look_x = base_center[0] - center_x
        look_y = base_center[1] - center_y
        look_z = (base_center[2] + 1.5) - center_z
        magnitude = math.sqrt((look_x * look_x) + (look_y * look_y) + (look_z * look_z))
        if magnitude <= 0.0001:
            return
        at_axis = (look_x / magnitude, look_y / magnitude, look_z / magnitude)
        left_axis = (-at_axis[1], at_axis[0], 0.0)
        left_magnitude = math.sqrt((left_axis[0] * left_axis[0]) + (left_axis[1] * left_axis[1]) + (left_axis[2] * left_axis[2]))
        if left_magnitude <= 0.0001:
            left_axis = (0.0, 1.0, 0.0)
        else:
            left_axis = (
                left_axis[0] / left_magnitude,
                left_axis[1] / left_magnitude,
                left_axis[2] / left_magnitude,
            )
        up_axis = (
            (at_axis[1] * left_axis[2]) - (at_axis[2] * left_axis[1]),
            (at_axis[2] * left_axis[0]) - (at_axis[0] * left_axis[2]),
            (at_axis[0] * left_axis[1]) - (at_axis[1] * left_axis[0]),
        )
        self.camera_center = (center_x, center_y, center_z)
        self.camera_at_axis = at_axis
        self.camera_left_axis = left_axis
        self.camera_up_axis = up_axis

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
        if parsed.objects:
            packet_tags.append("has_local_id")
        if any(obj.texture_entry is not None for obj in parsed.objects):
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

    def _record_cached_observation(
        self,
        *,
        dispatched: MessageDispatch,
        sequence: int,
        at_seconds: float,
        reason: str,
    ) -> None:
        if self.unknowns_db is None or dispatched.summary.name != "ObjectUpdateCached":
            return
        try:
            parsed = parse_object_update_cached(dispatched)
        except MessageDecodeError:
            return

        packet_tags = [reason, f"object_count:{len(parsed.objects)}"]
        packet_id = self.unknowns_db.record_cached_packet(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            capture_reason=reason,
            region_handle=parsed.region_handle,
            time_dilation=parsed.time_dilation,
            packet_tags=packet_tags,
        )
        for obj in parsed.objects:
            self.unknowns_db.record_cached_entity(
                packet_id=packet_id,
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=parsed.region_handle,
                entry=obj,
            )

    def _record_compressed_observation(
        self,
        *,
        dispatched: MessageDispatch,
        sequence: int,
        at_seconds: float,
        reason: str,
    ) -> None:
        if self.unknowns_db is None or dispatched.summary.name != "ObjectUpdateCompressed":
            return
        try:
            parsed = parse_object_update_compressed(dispatched)
        except MessageDecodeError:
            return

        packet_tags = [reason, f"object_count:{len(parsed.objects)}"]
        packet_id = self.unknowns_db.record_compressed_packet(
            session_id=self.db_session_id,
            observed_at_seconds=at_seconds,
            message_sequence=sequence,
            capture_reason=reason,
            region_handle=parsed.region_handle,
            time_dilation=parsed.time_dilation,
            packet_tags=packet_tags,
        )
        for obj in parsed.objects:
            self.unknowns_db.record_compressed_entity(
                packet_id=packet_id,
                session_id=self.db_session_id,
                observed_at_seconds=at_seconds,
                message_sequence=sequence,
                capture_reason=reason,
                region_handle=parsed.region_handle,
                entry=obj,
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
    world_view: WorldView | None = None,
    stop_event: asyncio.Event | None = None,
    world_client: "WorldClient | None" = None,
) -> SessionReport:
    from vibestorm.udp.world_client import WorldClient

    session_config = config or SessionConfig()
    init_kwargs: dict = dict(
        bootstrap=bootstrap,
        dispatcher=dispatcher,
        config=session_config,
        on_event=on_event,
    )
    if world_view is not None:
        init_kwargs["world_view"] = world_view
    session = LiveCircuitSession(**init_kwargs)
    client = world_client if world_client is not None else WorldClient()
    session_handle = client.add_circuit(session, make_current=True)
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    deadline = start_time + session_config.duration_seconds

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.bind(("0.0.0.0", 0))

        if session_config.caps_prelude:
            await _run_caps_prelude(session, sock, start_time)

        for packet in session.start(start_time):
            await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

        def _should_continue() -> bool:
            if session.close_reason is not None:
                return False
            if stop_event is not None and stop_event.is_set():
                return False
            if stop_event is None and loop.time() >= deadline:
                return False
            return True

        while _should_continue():
            now = loop.time()
            for _, packet in client.drain_outbound_packets(session_handle):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))
            for packet in session.drain_due_packets(now):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

            if stop_event is None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                recv_timeout = min(session_config.receive_timeout_seconds, remaining)
            else:
                recv_timeout = session_config.receive_timeout_seconds

            try:
                payload, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535),
                    timeout=recv_timeout,
                )
            except TimeoutError:
                continue

            for packet in session.handle_incoming(payload, loop.time()):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))
            for _, packet in client.drain_outbound_packets(session_handle):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

            # Deferred bake upload: trigger after AgentCachedTextureResponse arrives,
            # right before AgentSetAppearance, so WeakRefs stay alive through cache lookup.
            if (
                session.upload_baked_url is not None
                and session.latest_cached_texture_response is not None
                and not session.appearance_sent
                and session.baked_appearance_override is None
            ):
                local_port = int(sock.getsockname()[1])
                _now = loop.time()
                override = await _load_and_upload_baked_textures(session, session.upload_baked_url, local_port, _now)
                session.upload_baked_url = None
                if override is not None:
                    session.baked_appearance_override = override
                for packet in session.drain_due_packets(loop.time()):
                    await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

            # Deferred map tile fetch: triggered once per session after
            # MapBlockReply has been parsed and the GetTexture CAP is known.
            if (
                session.get_texture_url is not None
                and session.region_map_image_id is not None
                and not session.region_map_fetched
            ):
                session.region_map_fetched = True
                cached = await _fetch_and_cache_region_map(
                    session,
                    session.get_texture_url,
                    session.region_map_image_id,
                    _MAP_CACHE_DIR,
                    loop.time(),
                )
                if cached is not None:
                    session.region_map_path = cached

        for packet in session.build_shutdown_packets(loop.time()):
            await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

        logout_deadline = loop.time() + 0.75
        while loop.time() < logout_deadline:
            remaining = logout_deadline - loop.time()
            if remaining <= 0:
                break
            try:
                payload, _ = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 65535),
                    timeout=min(session_config.receive_timeout_seconds, remaining),
                )
            except TimeoutError:
                break

            for packet in session.handle_incoming(payload, loop.time()):
                await loop.sock_sendto(sock, packet, (bootstrap.sim_ip, bootstrap.sim_port))

    if session.close_reason is None and session.movement_completed:
        session._record_event(loop.time(), "session.completed", "duration elapsed")
    elif session.close_reason is None:
        session._record_event(loop.time(), "session.incomplete", "duration elapsed before movement complete")
    return session.build_report(loop.time() - start_time)


_BAKED_CACHE_DIR = Path(__file__).parent.parent.parent.parent / "local" / "baked-cache"
_APPEARANCE_FIXTURE = _BAKED_CACHE_DIR / "appearance-fixture.json"
_MAP_CACHE_DIR = Path(__file__).parent.parent.parent.parent / "local" / "map-cache"


async def _fetch_and_cache_region_map(
    session: LiveCircuitSession,
    cap_url: str,
    image_id: UUID,
    cache_dir: Path,
    now: float,
) -> Path | None:
    """Fetch the region map J2K via GetTexture, decode, and write a PNG.

    Returns the cache path on success or None on failure (errors are
    recorded as session events). Saves under cache_dir as ``<image_id>.png``.
    """
    client = GetTextureClient(timeout_seconds=10.0)
    try:
        fetched = await client.fetch(cap_url, image_id)
    except GetTextureError as exc:
        session._record_event(now, "map.fetch.error", str(exc))
        return None
    session._record_event(
        now,
        "map.fetch.ok",
        f"image={image_id} bytes={len(fetched.data)} content_type={fetched.content_type}",
    )

    try:
        decoded = decode_j2k(fetched.data)
    except J2KDecodeError as exc:
        session._record_event(now, "map.decode.error", str(exc))
        return None

    cache_dir.mkdir(parents=True, exist_ok=True)
    output_path = cache_dir / f"{image_id}.png"

    def _write_png() -> None:
        from PIL import Image

        Image.frombytes(decoded.mode, (decoded.width, decoded.height), decoded.pixels).save(
            output_path, format="PNG"
        )

    try:
        await asyncio.to_thread(_write_png)
    except (OSError, ImportError) as exc:
        session._record_event(now, "map.cache.error", str(exc))
        return None

    session._record_event(
        now,
        "map.cache.ok",
        f"path={output_path} size={decoded.width}x{decoded.height} mode={decoded.mode}",
    )
    return output_path


def _encode_face_mask(face_index: int) -> bytes:
    """Encode a single face index as a face bitmask for SL TextureEntry.

    SL TextureEntry uses MSB-first 7-bit group encoding (not standard LEB128):
    bytes are read most-significant-group first, accumulated as
    faceBits = (faceBits << 7) | (b & 0x7F), with bit 7 as a continuation flag.
    So face N (mask = 1 << N) must be encoded MSB-first.
    """
    mask = 1 << face_index
    # Split into 7-bit groups LSB first, then reverse for MSB-first output
    groups = []
    temp = mask
    while True:
        groups.append(temp & 0x7F)
        temp >>= 7
        if temp == 0:
            break
    groups.reverse()
    out = []
    for i, g in enumerate(groups):
        if i < len(groups) - 1:
            out.append(g | 0x80)  # continuation bit
        else:
            out.append(g)
    return bytes(out)


def _build_bake_texture_entry(face_uuids: dict[int, UUID], te_suffix: bytes) -> bytes:
    """Build a SL TextureEntry binary.

    face_uuids maps face slot index → UUID (must all be the same or distinct per slot).
    te_suffix is the raw bytes for the remaining TE sections (RGBA, scale, offset, etc.)
    following the texture-UUID section null terminator — copied verbatim from a reference TE.

    Layout: [default_uuid(16)] ([face_mask][uuid])... [0x00] [te_suffix...]
    """
    # SL default avatar texture UUID (the well-known null-stand-in for TE slots)
    DEFAULT_AVATAR_TEXTURE = UUID("8dcd4a48-2d37-4909-9f78-f7a9eb4ef05d")
    out = bytearray()
    out.extend(DEFAULT_AVATAR_TEXTURE.bytes)
    for face_idx, uuid in sorted(face_uuids.items()):
        out.extend(_encode_face_mask(face_idx))
        out.extend(uuid.bytes)
    out.append(0x00)  # null terminator for texture-UUID section
    out.extend(te_suffix)
    return bytes(out)


async def _load_and_upload_baked_textures(
    session: LiveCircuitSession,
    upload_baked_url: str,
    local_port: int,
    now: float,
) -> BakedAppearanceOverride | None:
    """Load J2K blobs from local/baked-cache, upload via UploadBakedTexture CAP, build override.

    Uploads each blob and assigns the returned new_asset UUID to the wearable_data texture_index
    at the same position (blob 0 → wearable_data[0].texture_index, etc.).  Builds a fresh TE
    that places those UUIDs at the correct bake face slots (8, 9, 10, 11, 20 etc.) so that
    OpenSim's UpdateBakedTextureCache finds them in its local asset cache.
    """
    if not _APPEARANCE_FIXTURE.exists():
        session._record_event(now, "bake.skip", "appearance-fixture.json not found")
        return None

    with _APPEARANCE_FIXTURE.open() as fh:
        fixture = json.load(fh)

    # The fixture TE hex contains a reference TE from Firestorm.  The texture-UUID section ends
    # at the first 0x00-valued face-mask byte.  Everything after that (RGBA, scale, etc.) is
    # reused verbatim as te_suffix so our TE has sane defaults for the remaining sections.
    raw_te = bytes.fromhex(fixture["te_hex"])
    te_suffix = _extract_te_suffix(raw_te)

    wearable_data = fixture["wearable_data"]
    visual_params = bytes(fixture["visual_params"])
    serial_num = fixture["serial_num"]
    sz = fixture["size_vec"]
    size: tuple[float, float, float] = (float(sz[0]), float(sz[1]), float(sz[2]))

    blob_count = len(fixture["blob_files"])
    client = UploadBakedTextureClient(timeout_seconds=30.0)
    face_uuids: dict[int, UUID] = {}
    cache_item_list: list[WearableCacheEntry] = []
    uploaded_count = 0

    for blob_index in range(blob_count):
        blob_path = _BAKED_CACHE_DIR / f"bake-{blob_index}.j2k"
        if not blob_path.exists():
            session._record_event(now, "bake.skip", f"blob {blob_index} missing: {blob_path.name}")
            continue
        if blob_index >= len(wearable_data):
            session._record_event(now, "bake.skip", f"blob {blob_index} has no wearable_data entry")
            continue
        texture_bytes = blob_path.read_bytes()
        try:
            result = await client.upload_via_capability(
                upload_baked_url,
                texture_bytes,
                udp_listen_port=local_port,
            )
        except UploadBakedTextureError as exc:
            session._record_event(now, "bake.upload_error", f"blob={blob_index} err={exc}")
            continue

        new_asset_id = result.new_asset_id
        if new_asset_id is None:
            session._record_event(now, "bake.upload_no_asset", f"blob={blob_index} state={result.state}")
            continue

        wd = wearable_data[blob_index]
        face_slot = wd["texture_index"]
        asset_uuid = UUID(new_asset_id)
        face_uuids[face_slot] = asset_uuid
        cache_item_list.append(WearableCacheEntry(
            texture_index=face_slot,
            cache_id=UUID(wd["cache_id"]),
        ))
        session._record_event(
            now, "bake.uploaded",
            f"blob={blob_index} face={face_slot} asset={new_asset_id}",
        )
        uploaded_count += 1

    if uploaded_count == 0:
        session._record_event(now, "bake.override_skipped", "no blobs uploaded successfully")
        return None

    texture_entry = _build_bake_texture_entry(face_uuids, te_suffix)
    return BakedAppearanceOverride(
        texture_entry=texture_entry,
        wearable_cache_items=tuple(cache_item_list),
        visual_params=visual_params,
        serial_num=serial_num,
        size=size,
    )


def _extract_te_suffix(te_bytes: bytes) -> bytes:
    """Return the bytes after the texture-UUID section's null terminator in a TE blob.

    Parses the LEB128 face-mask + UUID entries until a zero-valued face mask is found,
    then returns everything from the byte after the null terminator to the end.
    """
    pos = 16  # skip default UUID
    while pos < len(te_bytes):
        # Read LEB128 face mask
        mask = 0
        shift = 0
        while pos < len(te_bytes):
            b = te_bytes[pos]
            pos += 1
            mask |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        if mask == 0:
            # null terminator — everything from here onward is the suffix
            return te_bytes[pos:]
        pos += 16  # skip the 16-byte UUID for this entry
    return b""


async def _run_caps_prelude(session: LiveCircuitSession, sock: socket.socket, now: float) -> None:
    local_port = int(sock.getsockname()[1])
    capability_client = CapabilityClient(timeout_seconds=5.0)
    event_queue_client = EventQueueClient(timeout_seconds=5.0)
    inventory_client = InventoryCapabilityClient(timeout_seconds=5.0)
    requested_caps = [
        "EventQueueGet",
        "SimulatorFeatures",
        "FetchInventoryDescendents2",
        "FetchInventory2",
        "UploadBakedTexture",
        "ViewerAsset",
        "GetTexture",
    ]

    session._record_event(now, "caps.seed.start", f"udp_port={local_port}")
    try:
        resolved = await capability_client.resolve_seed_caps(
            session.bootstrap.seed_capability,
            requested_caps,
            udp_listen_port=local_port,
        )
    except CapabilityError as exc:
        session._record_event(now, "caps.seed.error", str(exc))
        return

    session._record_event(
        now,
        "caps.seed.ok",
        ",".join(name for name in requested_caps if name in resolved) or "none",
    )
    session.resolved_capabilities = tuple(name for name in requested_caps if name in resolved)

    event_queue_url = resolved.get("EventQueueGet")
    if event_queue_url:
        try:
            poll_result = await event_queue_client.poll_once(
                event_queue_url,
                udp_listen_port=local_port,
            )
        except EventQueueError as exc:
            session._record_event(now, "caps.event_queue.error", str(exc))
        else:
            session._record_event(now, "caps.event_queue", poll_result.status)

    simulator_features_url = resolved.get("SimulatorFeatures")
    if simulator_features_url:
        try:
            payload = await capability_client.fetch_capability_value(
                simulator_features_url,
                udp_listen_port=local_port,
            )
        except CapabilityError as exc:
            session._record_event(now, "caps.simulator_features.error", str(exc))
        else:
            feature_count = len(payload) if isinstance(payload, dict) else 0
            session._record_event(now, "caps.simulator_features", f"keys={feature_count}")

    inventory_url = resolved.get("FetchInventoryDescendents2")
    inventory_requests: list[InventoryFolderRequest] = []
    if inventory_url and session.bootstrap.inventory_root_folder_id is not None:
        inventory_requests.append(
            InventoryFolderRequest(
                folder_id=session.bootstrap.inventory_root_folder_id,
                owner_id=session.bootstrap.agent_id,
            )
        )
    if (
        inventory_url
        and session.bootstrap.current_outfit_folder_id is not None
        and session.bootstrap.current_outfit_folder_id != session.bootstrap.inventory_root_folder_id
    ):
        inventory_requests.append(
            InventoryFolderRequest(
                folder_id=session.bootstrap.current_outfit_folder_id,
                owner_id=session.bootstrap.agent_id,
            )
        )
    if inventory_url and inventory_requests:
        try:
            payload = await inventory_client.fetch_inventory_descendents(
                inventory_url,
                inventory_requests,
                udp_listen_port=local_port,
            )
        except InventoryCapabilityError as exc:
            session._record_event(now, "caps.inventory.error", str(exc))
        else:
            session.latest_inventory_fetch = parse_inventory_descendents_payload(
                payload,
                inventory_root_folder_id=session.bootstrap.inventory_root_folder_id,
                current_outfit_folder_id=session.bootstrap.current_outfit_folder_id,
            )
            fetch_inventory2_url = resolved.get("FetchInventory2")
            link_targets = session.latest_inventory_fetch.current_outfit_link_targets
            if fetch_inventory2_url and link_targets:
                try:
                    items_payload = await inventory_client.fetch_inventory_items(
                        fetch_inventory2_url,
                        [InventoryItemRequest(item_id=item_id) for item_id in link_targets],
                        udp_listen_port=local_port,
                    )
                except InventoryCapabilityError as exc:
                    session._record_event(now, "caps.inventory_items.error", str(exc))
                else:
                    session.latest_inventory_fetch = InventoryFetchSnapshot(
                        folders=session.latest_inventory_fetch.folders,
                        inventory_root_folder_id=session.latest_inventory_fetch.inventory_root_folder_id,
                        current_outfit_folder_id=session.latest_inventory_fetch.current_outfit_folder_id,
                        resolved_items=parse_inventory_items_payload(items_payload),
                    )
            session._record_event(now, "caps.inventory", _summarize_inventory_snapshot(session.latest_inventory_fetch))

    upload_baked_url = resolved.get("UploadBakedTexture")
    if upload_baked_url:
        session.upload_baked_url = upload_baked_url
        session._record_event(now, "bake.url_ready", "upload deferred until post-AgentCachedTextureResponse")
    else:
        session._record_event(now, "bake.skip", "UploadBakedTexture CAP not resolved")

    get_texture_url = resolved.get("GetTexture")
    if get_texture_url:
        session.get_texture_url = get_texture_url
        session._record_event(now, "map.get_texture_url_ready", "tile fetch deferred until MapBlockReply")
    else:
        session._record_event(now, "map.skip", "GetTexture CAP not resolved")


def _summarize_inventory_snapshot(snapshot: InventoryFetchSnapshot) -> str:
    if snapshot.folder_count == 0:
        return "folders=0"
    parts = [f"folders={snapshot.folder_count}", f"items={snapshot.total_item_count}"]
    cof = snapshot.current_outfit_folder
    if cof is not None:
        sample_names = ",".join(cof.sample_item_names(limit=3)) or "-"
        inv_types = ",".join(str(value) for value in cof.inventory_types) or "-"
        parts.append(
            f"cof_items={cof.item_count} cof_links={cof.link_item_count} "
            f"cof_inv_types={inv_types} cof_sample={sample_names}"
        )
    if snapshot.resolved_items:
        resolved_names = ",".join(snapshot.resolved_item_names(limit=4)) or "-"
        resolved_types = ",".join(str(value) for value in snapshot.resolved_item_types) or "-"
        parts.append(
            f"resolved_items={snapshot.resolved_item_count} "
            f"resolved_types={resolved_types} resolved_sample={resolved_names}"
        )
    root = snapshot.inventory_root_folder
    if root is not None:
        parts.append(f"root_items={root.item_count} root_categories={len(root.categories)}")
    return " ".join(parts)
