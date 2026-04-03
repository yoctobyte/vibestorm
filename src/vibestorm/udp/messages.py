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


@dataclass(slots=True, frozen=True)
class SimStatEntry:
    stat_id: int
    stat_value: float


@dataclass(slots=True, frozen=True)
class SimStatsMessage:
    region_x: int
    region_y: int
    region_flags: int
    object_capacity: int
    stats: tuple[SimStatEntry, ...]
    pid: int
    region_flags_extended: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class CoarseLocation:
    x: int
    y: int
    z: int


@dataclass(slots=True, frozen=True)
class CoarseLocationUpdateMessage:
    locations: tuple[CoarseLocation, ...]
    you_index: int
    prey_index: int
    agent_ids: tuple[UUID, ...]


@dataclass(slots=True, frozen=True)
class SimulatorViewerTimeMessage:
    usec_since_start: int
    sec_per_day: int
    sec_per_year: int
    sun_direction: tuple[float, float, float]
    sun_phase: float
    sun_angular_velocity: tuple[float, float, float]


@dataclass(slots=True, frozen=True)
class ObjectUpdateSummary:
    region_handle: int
    time_dilation: int
    object_count: int


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


def parse_sim_stats(message: MessageDispatch) -> SimStatsMessage:
    if message.summary.name != "SimStats":
        raise MessageDecodeError(f"expected SimStats, got {message.summary.name}")
    if len(message.body) < 21:
        raise MessageDecodeError("SimStats body is too short")

    region_x = unpack_from("<I", message.body, 0)[0]
    region_y = unpack_from("<I", message.body, 4)[0]
    region_flags = unpack_from("<I", message.body, 8)[0]
    object_capacity = unpack_from("<I", message.body, 12)[0]
    stat_count = message.body[16]
    offset = 17
    stats: list[SimStatEntry] = []
    for _ in range(stat_count):
        if len(message.body) < offset + 8:
            raise MessageDecodeError("SimStats stat block is truncated")
        stats.append(
            SimStatEntry(
                stat_id=unpack_from("<I", message.body, offset)[0],
                stat_value=unpack_from("<f", message.body, offset + 4)[0],
            ),
        )
        offset += 8

    if len(message.body) < offset + 4:
        raise MessageDecodeError("SimStats pid block is truncated")
    pid = unpack_from("<i", message.body, offset)[0]
    offset += 4

    if len(message.body) < offset + 1:
        raise MessageDecodeError("SimStats region info count is truncated")
    region_info_count = message.body[offset]
    offset += 1
    region_flags_extended: list[int] = []
    for _ in range(region_info_count):
        if len(message.body) < offset + 8:
            raise MessageDecodeError("SimStats region info block is truncated")
        region_flags_extended.append(unpack_from("<Q", message.body, offset)[0])
        offset += 8

    return SimStatsMessage(
        region_x=region_x,
        region_y=region_y,
        region_flags=region_flags,
        object_capacity=object_capacity,
        stats=tuple(stats),
        pid=pid,
        region_flags_extended=tuple(region_flags_extended),
    )


def parse_coarse_location_update(message: MessageDispatch) -> CoarseLocationUpdateMessage:
    if message.summary.name != "CoarseLocationUpdate":
        raise MessageDecodeError(f"expected CoarseLocationUpdate, got {message.summary.name}")
    if not message.body:
        raise MessageDecodeError("CoarseLocationUpdate body is too short")

    location_count = message.body[0]
    offset = 1
    locations: list[CoarseLocation] = []
    for _ in range(location_count):
        if len(message.body) < offset + 3:
            raise MessageDecodeError("CoarseLocationUpdate location block is truncated")
        locations.append(CoarseLocation(x=message.body[offset], y=message.body[offset + 1], z=message.body[offset + 2]))
        offset += 3

    if len(message.body) < offset + 4:
        raise MessageDecodeError("CoarseLocationUpdate index block is truncated")
    you_index = unpack_from("<h", message.body, offset)[0]
    prey_index = unpack_from("<h", message.body, offset + 2)[0]
    offset += 4

    if len(message.body) < offset + 1:
        raise MessageDecodeError("CoarseLocationUpdate agent count is truncated")
    agent_count = message.body[offset]
    offset += 1
    agent_ids: list[UUID] = []
    for _ in range(agent_count):
        if len(message.body) < offset + 16:
            raise MessageDecodeError("CoarseLocationUpdate agent block is truncated")
        agent_ids.append(UUID(bytes=message.body[offset : offset + 16]))
        offset += 16

    return CoarseLocationUpdateMessage(
        locations=tuple(locations),
        you_index=you_index,
        prey_index=prey_index,
        agent_ids=tuple(agent_ids),
    )


def parse_simulator_viewer_time(message: MessageDispatch) -> SimulatorViewerTimeMessage:
    if message.summary.name != "SimulatorViewerTimeMessage":
        raise MessageDecodeError(f"expected SimulatorViewerTimeMessage, got {message.summary.name}")
    if len(message.body) != 44:
        raise MessageDecodeError("SimulatorViewerTimeMessage body must be 44 bytes")

    return SimulatorViewerTimeMessage(
        usec_since_start=unpack_from("<Q", message.body, 0)[0],
        sec_per_day=unpack_from("<I", message.body, 8)[0],
        sec_per_year=unpack_from("<I", message.body, 12)[0],
        sun_direction=tuple(unpack_from("<fff", message.body, 16)),  # type: ignore[arg-type]
        sun_phase=unpack_from("<f", message.body, 28)[0],
        sun_angular_velocity=tuple(unpack_from("<fff", message.body, 32)),  # type: ignore[arg-type]
    )


def parse_object_update_summary(message: MessageDispatch) -> ObjectUpdateSummary:
    if message.summary.name != "ObjectUpdate":
        raise MessageDecodeError(f"expected ObjectUpdate, got {message.summary.name}")
    if len(message.body) < 11:
        raise MessageDecodeError("ObjectUpdate body is too short")
    return ObjectUpdateSummary(
        region_handle=unpack_from("<Q", message.body, 0)[0],
        time_dilation=unpack_from("<H", message.body, 8)[0],
        object_count=message.body[10],
    )


def encode_complete_ping_check(ping_id: int) -> bytes:
    if not 0 <= ping_id <= 0xFF:
        raise ValueError("ping_id must fit in U8")
    return bytes([0x02, ping_id])


def encode_packet_ack(packets: tuple[int, ...]) -> bytes:
    if len(packets) > 0xFF:
        raise ValueError("packet ack list must fit in U8 count")
    return b"\xFF\xFF\xFF\xFB" + bytes([len(packets)]) + b"".join(pack("<I", packet) for packet in packets)


def encode_agent_throttle(
    agent_id: UUID,
    session_id: UUID,
    circuit_code: int,
    *,
    gen_counter: int = 0,
    throttles: tuple[float, float, float, float, float, float, float] = (
        100_000.0,
        10_000.0,
        10_000.0,
        10_000.0,
        80_000.0,
        160_000.0,
        50_000.0,
    ),
) -> bytes:
    if not 0 <= circuit_code <= 0xFFFFFFFF:
        raise ValueError("circuit_code must fit in U32")
    if not 0 <= gen_counter <= 0xFFFFFFFF:
        raise ValueError("gen_counter must fit in U32")

    throttle_bytes = pack("<fffffff", *throttles)
    return (
        b"\xFF\xFF\x00\x51"
        + agent_id.bytes
        + session_id.bytes
        + pack("<I", circuit_code)
        + pack("<I", gen_counter)
        + bytes([len(throttle_bytes)])
        + throttle_bytes
    )


def encode_object_add(
    agent_id: UUID,
    session_id: UUID,
    *,
    group_id: UUID | None = None,
    pcode: int = 9,
    material: int = 3,
    add_flags: int = 0,
    path_curve: int = 16,
    profile_curve: int = 1,
    path_begin: int = 0,
    path_end: int = 0,
    path_scale_x: int = 100,
    path_scale_y: int = 100,
    path_shear_x: int = 0,
    path_shear_y: int = 0,
    path_twist: int = 0,
    path_twist_begin: int = 0,
    path_radius_offset: int = 0,
    path_taper_x: int = 0,
    path_taper_y: int = 0,
    path_revolutions: int = 66,
    path_skew: int = 0,
    profile_begin: int = 0,
    profile_end: int = 0,
    profile_hollow: int = 0,
    bypass_raycast: int = 1,
    ray_start: tuple[float, float, float] = (128.0, 128.0, 30.0),
    ray_end: tuple[float, float, float] = (128.0, 128.0, 20.0),
    ray_target_id: UUID | None = None,
    ray_end_is_intersection: int = 0,
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    state: int = 0,
) -> bytes:
    return (
        b"\xFF\x01"
        + agent_id.bytes
        + session_id.bytes
        + (group_id.bytes if group_id is not None else UUID(int=0).bytes)
        + bytes([pcode, material])
        + pack("<I", add_flags)
        + bytes(
            [
                path_curve,
                profile_curve,
            ],
        )
        + pack("<H", path_begin)
        + pack("<H", path_end)
        + bytes([path_scale_x, path_scale_y, path_shear_x & 0xFF, path_shear_y & 0xFF])
        + pack("<b", path_twist)
        + pack("<b", path_twist_begin)
        + pack("<b", path_radius_offset)
        + pack("<b", path_taper_x)
        + pack("<b", path_taper_y)
        + bytes([path_revolutions, path_skew & 0xFF])
        + pack("<H", profile_begin)
        + pack("<H", profile_end)
        + pack("<H", profile_hollow)
        + bytes([bypass_raycast])
        + pack("<fff", *ray_start)
        + pack("<fff", *ray_end)
        + (ray_target_id.bytes if ray_target_id is not None else UUID(int=0).bytes)
        + bytes([ray_end_is_intersection])
        + pack("<fff", *scale)
        + pack("<fff", *rotation)
        + bytes([state])
    )


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
