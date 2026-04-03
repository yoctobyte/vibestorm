"""Long-lived UDP session state for OpenSim and Second Life style circuits."""

from __future__ import annotations

import asyncio
import socket
from collections import Counter
from dataclasses import dataclass, field

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    encode_agent_update,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_region_handshake_reply,
    encode_use_circuit_code,
    parse_agent_movement_complete,
    parse_packet_ack,
    parse_region_handshake,
    parse_start_ping_check,
)
from vibestorm.udp.packet import LL_RELIABLE_FLAG, build_packet, split_packet
from vibestorm.udp.zerocode import decode_zerocode, encode_zerocode


@dataclass(slots=True, frozen=True)
class SessionConfig:
    duration_seconds: float = 60.0
    receive_timeout_seconds: float = 0.25
    agent_update_interval_seconds: float = 1.0
    region_handshake_reply_flags: int = 0


@dataclass(slots=True, frozen=True)
class SessionReport:
    elapsed_seconds: float
    total_received: int
    message_counts: dict[str, int]
    handshake_reply_sent: bool
    agent_update_count: int
    pending_reliable_sequences: tuple[int, ...]
    last_region_name: str | None
    close_reason: str | None


@dataclass(slots=True)
class LiveCircuitSession:
    bootstrap: LoginBootstrap
    dispatcher: MessageDispatcher
    config: SessionConfig = field(default_factory=SessionConfig)
    next_sequence: int = 1
    pending_reliable: dict[int, str] = field(default_factory=dict)
    queued_acks: list[int] = field(default_factory=list)
    received_messages: Counter[str] = field(default_factory=Counter)
    total_received: int = 0
    handshake_reply_sent: bool = False
    agent_update_count: int = 0
    last_agent_update_at: float | None = None
    last_region_name: str | None = None
    close_reason: str | None = None
    camera_center: tuple[float, float, float] = (128.0, 128.0, 25.0)
    movement_completed: bool = False
    started: bool = False

    def start(self, now: float) -> list[bytes]:
        if self.started:
            return []
        self.started = True
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

        dispatched = self.dispatcher.dispatch(view.message)
        self.total_received += 1
        self.received_messages[dispatched.summary.name] += 1

        if view.header.is_reliable and view.header.sequence not in self.queued_acks:
            self.queued_acks.append(view.header.sequence)

        if dispatched.summary.name == "PacketAck":
            for ack in parse_packet_ack(dispatched).packets:
                self.pending_reliable.pop(ack, None)
            return []

        if dispatched.summary.name == "CloseCircuit":
            self.close_reason = "simulator closed circuit"
            return []

        if dispatched.summary.name == "StartPingCheck":
            ping = parse_start_ping_check(dispatched)
            return [
                self._build_outbound_packet(
                    encode_complete_ping_check(ping.ping_id),
                    label="CompletePingCheck",
                ),
            ]

        if dispatched.summary.name == "RegionHandshake":
            handshake = parse_region_handshake(dispatched)
            self.last_region_name = handshake.sim_name
            self.camera_center = (
                float(self.bootstrap.region_x) + 128.0,
                float(self.bootstrap.region_y) + 128.0,
                self.camera_center[2],
            )
            self.handshake_reply_sent = True
            return [
                self._build_outbound_packet(
                    encode_region_handshake_reply(
                        self.bootstrap.agent_id,
                        self.bootstrap.session_id,
                        self.config.region_handshake_reply_flags,
                    ),
                    reliable=True,
                    zerocoded=True,
                    label="RegionHandshakeReply",
                ),
            ]

        if dispatched.summary.name == "AgentMovementComplete":
            movement = parse_agent_movement_complete(dispatched)
            self.camera_center = movement.position
            self.movement_completed = True

        return self.drain_due_packets(now)

    def drain_due_packets(self, now: float) -> list[bytes]:
        if self.close_reason is not None or not self.started or not self.movement_completed:
            return []
        if self.last_agent_update_at is None:
            self.last_agent_update_at = now
            return []
        if now - self.last_agent_update_at < self.config.agent_update_interval_seconds:
            return []

        self.last_agent_update_at = now
        self.agent_update_count += 1
        return [
            self._build_outbound_packet(
                encode_agent_update(
                    self.bootstrap.agent_id,
                    self.bootstrap.session_id,
                    camera_center=self.camera_center,
                ),
                zerocoded=True,
                label="AgentUpdate",
            ),
        ]

    def build_report(self, elapsed_seconds: float) -> SessionReport:
        return SessionReport(
            elapsed_seconds=elapsed_seconds,
            total_received=self.total_received,
            message_counts=dict(self.received_messages),
            handshake_reply_sent=self.handshake_reply_sent,
            agent_update_count=self.agent_update_count,
            pending_reliable_sequences=tuple(sorted(self.pending_reliable)),
            last_region_name=self.last_region_name,
            close_reason=self.close_reason,
        )

    def _build_outbound_packet(
        self,
        message: bytes,
        *,
        reliable: bool = False,
        zerocoded: bool = False,
        label: str,
    ) -> bytes:
        sequence = self.next_sequence
        self.next_sequence += 1
        packet = build_packet(
            message,
            sequence=sequence,
            flags=LL_RELIABLE_FLAG if reliable else 0,
            appended_acks=tuple(self._consume_queued_acks()),
        )
        if zerocoded:
            packet = encode_zerocode(packet)
        if reliable:
            self.pending_reliable[sequence] = label
        return packet

    def _consume_queued_acks(self) -> list[int]:
        queued = self.queued_acks
        self.queued_acks = []
        return queued


async def run_live_session(
    bootstrap: LoginBootstrap,
    dispatcher: MessageDispatcher,
    *,
    config: SessionConfig | None = None,
) -> SessionReport:
    session_config = config or SessionConfig()
    session = LiveCircuitSession(bootstrap=bootstrap, dispatcher=dispatcher, config=session_config)
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

    return session.build_report(loop.time() - start_time)
