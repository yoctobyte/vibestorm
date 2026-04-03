"""Typed handlers for the first UDP message semantics."""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack, unpack_from
from uuid import UUID

from vibestorm.udp.template import MessageDispatch


class MessageDecodeError(ValueError):
    """Raised when a decoded message body does not match the expected shape."""


@dataclass(slots=True, frozen=True)
class StartPingCheckMessage:
    ping_id: int
    oldest_unacked: int


@dataclass(slots=True, frozen=True)
class CompletePingCheckMessage:
    ping_id: int


@dataclass(slots=True, frozen=True)
class PacketAckMessage:
    packets: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class UseCircuitCodeMessage:
    code: int
    session_id: UUID
    agent_id: UUID


@dataclass(slots=True, frozen=True)
class CompleteAgentMovementMessage:
    agent_id: UUID
    session_id: UUID
    circuit_code: int


@dataclass(slots=True, frozen=True)
class AgentMovementCompleteMessage:
    agent_id: UUID
    session_id: UUID
    position: tuple[float, float, float]
    look_at: tuple[float, float, float]
    region_handle: int
    timestamp: int
    channel_version: str


@dataclass(slots=True, frozen=True)
class RegionHandshakeMessage:
    region_flags: int
    sim_access: int
    sim_name: str
    sim_owner: UUID
    is_estate_manager: bool
    water_height: float
    billable_factor: float
    cache_id: UUID
    region_id: UUID


def parse_packet_ack(message: MessageDispatch) -> PacketAckMessage:
    if message.summary.name != "PacketAck":
        raise MessageDecodeError(f"expected PacketAck, got {message.summary.name}")
    if not message.body:
        raise MessageDecodeError("PacketAck body must include a packet count")

    count = message.body[0]
    expected_length = 1 + (count * 4)
    if len(message.body) != expected_length:
        raise MessageDecodeError("PacketAck body length does not match packet count")

    packets = tuple(unpack_from("<I", message.body, 1 + (index * 4))[0] for index in range(count))
    return PacketAckMessage(packets=packets)


def parse_start_ping_check(message: MessageDispatch) -> StartPingCheckMessage:
    if message.summary.name != "StartPingCheck":
        raise MessageDecodeError(f"expected StartPingCheck, got {message.summary.name}")
    if len(message.body) != 5:
        raise MessageDecodeError("StartPingCheck body must be 5 bytes")
    ping_id = message.body[0]
    oldest_unacked = unpack_from("<I", message.body, 1)[0]
    return StartPingCheckMessage(ping_id=ping_id, oldest_unacked=oldest_unacked)


def parse_complete_ping_check(message: MessageDispatch) -> CompletePingCheckMessage:
    if message.summary.name != "CompletePingCheck":
        raise MessageDecodeError(f"expected CompletePingCheck, got {message.summary.name}")
    if len(message.body) != 1:
        raise MessageDecodeError("CompletePingCheck body must be 1 byte")
    return CompletePingCheckMessage(ping_id=message.body[0])


def parse_use_circuit_code(message: MessageDispatch) -> UseCircuitCodeMessage:
    if message.summary.name != "UseCircuitCode":
        raise MessageDecodeError(f"expected UseCircuitCode, got {message.summary.name}")
    if len(message.body) != 36:
        raise MessageDecodeError("UseCircuitCode body must be 36 bytes")
    code = unpack_from("<I", message.body, 0)[0]
    session_id = UUID(bytes=message.body[4:20])
    agent_id = UUID(bytes=message.body[20:36])
    return UseCircuitCodeMessage(code=code, session_id=session_id, agent_id=agent_id)


def parse_agent_movement_complete(message: MessageDispatch) -> AgentMovementCompleteMessage:
    if message.summary.name != "AgentMovementComplete":
        raise MessageDecodeError(f"expected AgentMovementComplete, got {message.summary.name}")
    if len(message.body) < 62:
        raise MessageDecodeError("AgentMovementComplete body is too short")

    agent_id = UUID(bytes=message.body[0:16])
    session_id = UUID(bytes=message.body[16:32])
    position = tuple(unpack_from("<fff", message.body, 32))
    look_at = tuple(unpack_from("<fff", message.body, 44))
    region_handle = unpack_from("<Q", message.body, 56)[0]
    timestamp = unpack_from("<I", message.body, 64)[0]
    channel_length = unpack_from("<H", message.body, 68)[0]
    start = 70
    end = start + channel_length
    if len(message.body) < end:
        raise MessageDecodeError("AgentMovementComplete channel version is truncated")
    channel_version = message.body[start:end].decode("utf-8", errors="replace")
    return AgentMovementCompleteMessage(
        agent_id=agent_id,
        session_id=session_id,
        position=position,  # type: ignore[arg-type]
        look_at=look_at,  # type: ignore[arg-type]
        region_handle=region_handle,
        timestamp=timestamp,
        channel_version=channel_version,
    )


def parse_region_handshake(message: MessageDispatch) -> RegionHandshakeMessage:
    if message.summary.name != "RegionHandshake":
        raise MessageDecodeError(f"expected RegionHandshake, got {message.summary.name}")

    body = message.body
    if len(body) < 123:
        raise MessageDecodeError("RegionHandshake body is too short")

    region_flags = unpack_from("<I", body, 0)[0]
    sim_access = body[4]
    name_length = body[5]
    name_start = 6
    name_end = name_start + name_length
    sim_name = body[name_start:name_end].decode("utf-8", errors="replace")
    offset = name_end
    sim_owner = UUID(bytes=body[offset : offset + 16])
    offset += 16
    is_estate_manager = bool(body[offset])
    offset += 1
    water_height, billable_factor = unpack_from("<ff", body, offset)
    offset += 8
    cache_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    offset += 16 * 8
    offset += 4 * 8
    region_id = UUID(bytes=body[offset : offset + 16])
    return RegionHandshakeMessage(
        region_flags=region_flags,
        sim_access=sim_access,
        sim_name=sim_name,
        sim_owner=sim_owner,
        is_estate_manager=is_estate_manager,
        water_height=water_height,
        billable_factor=billable_factor,
        cache_id=cache_id,
        region_id=region_id,
    )


def encode_complete_ping_check(ping_id: int) -> bytes:
    if not 0 <= ping_id <= 0xFF:
        raise ValueError("ping_id must fit in U8")
    return bytes([0x02, ping_id])


def encode_packet_ack(packets: tuple[int, ...]) -> bytes:
    if len(packets) > 0xFF:
        raise ValueError("packet ack list must fit in U8 count")
    return b"\xFF\xFF\xFF\xFB" + bytes([len(packets)]) + b"".join(pack("<I", packet) for packet in packets)


def encode_agent_update(
    agent_id: UUID,
    session_id: UUID,
    *,
    body_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    head_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    state: int = 0,
    camera_center: tuple[float, float, float] = (128.0, 128.0, 25.0),
    camera_at_axis: tuple[float, float, float] = (1.0, 0.0, 0.0),
    camera_left_axis: tuple[float, float, float] = (0.0, 1.0, 0.0),
    camera_up_axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    far: float = 512.0,
    control_flags: int = 0,
    flags: int = 0,
) -> bytes:
    if not 0 <= state <= 0xFF:
        raise ValueError("state must fit in U8")
    if not 0 <= control_flags <= 0xFFFFFFFF:
        raise ValueError("control_flags must fit in U32")
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags must fit in U8")

    return (
        b"\x04"
        + agent_id.bytes
        + session_id.bytes
        + pack("<fff", *body_rotation)
        + pack("<fff", *head_rotation)
        + bytes([state])
        + pack("<fff", *camera_center)
        + pack("<fff", *camera_at_axis)
        + pack("<fff", *camera_left_axis)
        + pack("<fff", *camera_up_axis)
        + pack("<f", far)
        + pack("<I", control_flags)
        + bytes([flags])
    )


def encode_use_circuit_code(code: int, session_id: UUID, agent_id: UUID) -> bytes:
    if not 0 <= code <= 0xFFFFFFFF:
        raise ValueError("code must fit in U32")
    return b"\xFF\xFF\x00\x03" + pack("<I", code) + session_id.bytes + agent_id.bytes


def encode_complete_agent_movement(agent_id: UUID, session_id: UUID, circuit_code: int) -> bytes:
    if not 0 <= circuit_code <= 0xFFFFFFFF:
        raise ValueError("circuit_code must fit in U32")
    return b"\xFF\xFF\x00\xF9" + agent_id.bytes + session_id.bytes + pack("<I", circuit_code)


def encode_region_handshake_reply(agent_id: UUID, session_id: UUID, flags: int) -> bytes:
    if not 0 <= flags <= 0xFFFFFFFF:
        raise ValueError("flags must fit in U32")
    return b"\xFF\xFF\x00\x95" + agent_id.bytes + session_id.bytes + pack("<I", flags)
