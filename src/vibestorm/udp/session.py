"""Long-lived UDP session state for OpenSim and Second Life style circuits."""

from __future__ import annotations

import asyncio
import random
import socket
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    encode_agent_update,
    encode_agent_throttle,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_object_add,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_use_circuit_code,
    parse_agent_movement_complete,
    parse_coarse_location_update,
    parse_object_update_summary,
    parse_packet_ack,
    parse_region_handshake,
    parse_sim_stats,
    parse_simulator_viewer_time,
    parse_start_ping_check,
)
from vibestorm.udp.packet import LL_RELIABLE_FLAG, build_packet, split_packet
from vibestorm.udp.zerocode import decode_zerocode, encode_zerocode
from vibestorm.world.models import WorldView


@dataclass(slots=True, frozen=True)
class SessionConfig:
    duration_seconds: float = 60.0
    receive_timeout_seconds: float = 0.25
    agent_update_interval_seconds: float = 1.0
    spawn_test_cube: bool = False
    spawn_delay_seconds: float = 2.0
    region_handshake_reply_flags: int = 0
    max_logged_events: int = 64


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

    def start(self, now: float) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        self.started_at = now
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

        dispatched = self.dispatcher.dispatch(view.message)
        self.total_received += 1
        self.received_messages[dispatched.summary.name] += 1

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
            self._record_event(
                now,
                "handshake.region",
                f"sim_name={handshake.sim_name} flags={handshake.region_flags}",
            )
            self.camera_center = (
                float(self.bootstrap.region_x) + 128.0,
                float(self.bootstrap.region_y) + 128.0,
                self.camera_center[2],
            )
            self.handshake_reply_sent = True
            self.world_view.set_region(
                name=handshake.sim_name,
                grid_x=self.bootstrap.region_x // 256,
                grid_y=self.bootstrap.region_y // 256,
            )
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
        elif dispatched.summary.name == "SimStats":
            stats = parse_sim_stats(dispatched)
            self.world_view.apply_sim_stats(stats)
            self._record_event(
                now,
                "sim.stats",
                f"region=({stats.region_x},{stats.region_y}) object_capacity={stats.object_capacity} stats={len(stats.stats)} pid={stats.pid}",
            )
        elif dispatched.summary.name == "SimulatorViewerTimeMessage":
            time_info = parse_simulator_viewer_time(dispatched)
            self.world_view.apply_simulator_time(time_info)
            self._record_event(
                now,
                "sim.time",
                f"usec_since_start={time_info.usec_since_start} sec_per_day={time_info.sec_per_day} sun_phase={time_info.sun_phase:.3f}",
            )
        elif dispatched.summary.name == "CoarseLocationUpdate":
            coarse = parse_coarse_location_update(dispatched)
            self.world_view.apply_coarse_location_update(coarse)
            self._record_event(
                now,
                "world.coarse_location",
                f"locations={len(coarse.locations)} agents={len(coarse.agent_ids)} you={coarse.you_index} prey={coarse.prey_index}",
            )
        elif dispatched.summary.name == "ObjectUpdate":
            object_update = parse_object_update_summary(dispatched)
            self.world_view.apply_object_update_summary(object_update)
            self._record_event(
                now,
                "world.object_update",
                f"region_handle={object_update.region_handle} objects={object_update.object_count} dilation={object_update.time_dilation}",
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
