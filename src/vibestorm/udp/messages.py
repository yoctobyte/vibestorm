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
class AgentWearableEntry:
    item_id: UUID
    asset_id: UUID
    wearable_type: int


@dataclass(slots=True, frozen=True)
class AgentWearablesUpdateMessage:
    agent_id: UUID
    session_id: UUID
    serial_num: int
    wearables: tuple[AgentWearableEntry, ...]


@dataclass(slots=True, frozen=True)
class WearableCacheEntry:
    cache_id: UUID
    texture_index: int


@dataclass(slots=True, frozen=True)
class AgentCachedTextureResponseEntry:
    texture_id: UUID
    texture_index: int
    host_name: str


@dataclass(slots=True, frozen=True)
class AgentCachedTextureResponseMessage:
    agent_id: UUID
    session_id: UUID
    serial_num: int
    textures: tuple[AgentCachedTextureResponseEntry, ...]


@dataclass(slots=True, frozen=True)
class AvatarAppearanceMessage:
    sender_id: UUID
    is_trial: bool
    texture_entry: bytes
    visual_params: bytes
    appearance_version: int | None
    cof_version: int | None
    appearance_flags: int | None
    hover_height: tuple[float, float, float] | None
    attachments: tuple[tuple[UUID, int], ...]


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
class ImprovedTerseObjectEntry:
    local_id: int
    state: int
    is_avatar: bool
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    acceleration: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    angular_velocity: tuple[float, float, float]
    collision_plane: tuple[float, float, float, float] | None = None
    texture_entry: bytes | None = None


@dataclass(slots=True, frozen=True)
class ImprovedTerseObjectUpdateMessage:
    region_handle: int
    time_dilation: int
    objects: tuple[ImprovedTerseObjectEntry, ...]


@dataclass(slots=True, frozen=True)
class ObjectUpdateCachedEntry:
    local_id: int
    crc: int
    update_flags: int


@dataclass(slots=True, frozen=True)
class ObjectUpdateCachedMessage:
    region_handle: int
    time_dilation: int
    objects: tuple[ObjectUpdateCachedEntry, ...]


@dataclass(slots=True, frozen=True)
class ObjectUpdateCompressedEntry:
    update_flags: int
    data: bytes


@dataclass(slots=True, frozen=True)
class ObjectUpdateCompressedMessage:
    region_handle: int
    time_dilation: int
    objects: tuple[ObjectUpdateCompressedEntry, ...]


@dataclass(slots=True, frozen=True)
class ObjectPropertiesFamilyMessage:
    request_flags: int
    object_id: UUID
    owner_id: UUID
    group_id: UUID
    base_mask: int
    owner_mask: int
    group_mask: int
    everyone_mask: int
    next_owner_mask: int
    ownership_cost: int
    sale_type: int
    sale_price: int
    category: int
    last_owner_id: UUID
    name: str
    description: str


@dataclass(slots=True, frozen=True)
class ExtraParamEntry:
    param_type: int
    param_in_use: bool
    param_data: bytes


@dataclass(slots=True, frozen=True)
class ObjectExtraParamsEntry:
    object_local_id: int
    param_type: int
    param_in_use: bool
    param_size: int
    param_data: bytes


@dataclass(slots=True, frozen=True)
class ObjectExtraParamsMessage:
    agent_id: UUID
    session_id: UUID
    objects: tuple[ObjectExtraParamsEntry, ...]


@dataclass(slots=True, frozen=True)
class ChatFromSimulatorMessage:
    from_name: str
    source_id: UUID
    owner_id: UUID
    source_type: int
    chat_type: int
    audible: int
    position: tuple[float, float, float]
    message: str


@dataclass(slots=True, frozen=True)
class ObjectUpdateSummary:
    region_handle: int
    time_dilation: int
    object_count: int


@dataclass(slots=True, frozen=True)
class ObjectUpdatePayloadSummary:
    field_name: str
    size: int
    non_zero_bytes: int
    preview_hex: str
    text_preview: str | None


@dataclass(slots=True, frozen=True)
class ObjectUpdateEntry:
    local_id: int
    state: int
    full_id: UUID
    crc: int
    pcode: int
    material: int
    click_action: int
    scale: tuple[float, float, float]
    object_data_size: int
    parent_id: int
    update_flags: int
    position: tuple[float, float, float] | None
    rotation: tuple[float, float, float, float] | None
    variant: str
    name_values: dict[str, str]
    texture_entry_size: int
    texture_anim_size: int
    data_size: int
    text_size: int
    media_url_size: int
    ps_block_size: int
    extra_params_size: int
    default_texture_id: UUID | None
    interesting_payloads: tuple[ObjectUpdatePayloadSummary, ...]
    extra_params_entries: tuple[ExtraParamEntry, ...] = ()


@dataclass(slots=True, frozen=True)
class ObjectUpdateMessage:
    region_handle: int
    time_dilation: int
    objects: tuple[ObjectUpdateEntry, ...]


def _read_variable_field(data: bytes, offset: int, length_size: int, field_name: str) -> tuple[bytes, int]:
    if len(data) < offset + length_size:
        raise MessageDecodeError(f"{field_name} length is truncated")

    length = int.from_bytes(data[offset : offset + length_size], "little")
    start = offset + length_size
    end = start + length
    if len(data) < end:
        raise MessageDecodeError(f"{field_name} payload is truncated")
    return data[start:end], end


def _read_variable_field_best_effort(
    data: bytes,
    offset: int,
    length_size: int,
    field_name: str,
) -> tuple[bytes, int]:
    if len(data) < offset + length_size:
        raise MessageDecodeError(f"{field_name} length is truncated")

    length_bytes = data[offset : offset + length_size]
    candidates = (
        int.from_bytes(length_bytes, "little"),
        int.from_bytes(length_bytes, "big"),
    )
    start = offset + length_size
    for length in candidates:
        end = start + length
        if len(data) >= end:
            return data[start:end], end

    raise MessageDecodeError(f"{field_name} payload is truncated")


def _read_utf8_fields_with_fallback(
    data: bytes,
    offset: int,
    field_names: tuple[str, ...],
    *,
    preferred_length_sizes: tuple[int, ...] = (2, 1),
) -> tuple[tuple[str, ...], int]:
    last_error: MessageDecodeError | None = None
    for length_size in preferred_length_sizes:
        try:
            strings: list[str] = []
            current = offset
            for field_name in field_names:
                payload, current = _read_variable_field(data, current, length_size, field_name)
                strings.append(payload.decode("utf-8", errors="replace").rstrip("\x00"))
            if current == len(data):
                return tuple(strings), current
        except MessageDecodeError as exc:
            last_error = exc
            continue

    for length_size in preferred_length_sizes:
        try:
            strings = []
            current = offset
            for field_name in field_names:
                payload, current = _read_variable_field(data, current, length_size, field_name)
                strings.append(payload.decode("utf-8", errors="replace").rstrip("\x00"))
            return tuple(strings), current
        except MessageDecodeError as exc:
            last_error = exc
            continue

    raise last_error or MessageDecodeError("variable UTF-8 field is truncated")


def _read_variable_field_with_length_fallback(
    data: bytes,
    offset: int,
    field_name: str,
    *,
    preferred_length_sizes: tuple[int, ...],
) -> tuple[bytes, int]:
    last_error: MessageDecodeError | None = None
    for length_size in preferred_length_sizes:
        try:
            payload, current = _read_variable_field_best_effort(data, offset, length_size, field_name)
            return payload, current
        except MessageDecodeError as exc:
            last_error = exc
            continue
    raise last_error or MessageDecodeError(f"{field_name} payload is truncated")


def parse_shape_extra_params(payload: bytes) -> tuple[ExtraParamEntry, ...]:
    if not payload:
        return ()
    if len(payload) < 1:
        raise MessageDecodeError("ExtraParams payload is truncated")

    def _parse(with_in_use: bool) -> tuple[ExtraParamEntry, ...]:
        count = payload[0]
        offset = 1
        entries: list[ExtraParamEntry] = []
        for _ in range(count):
            header_size = 7 if with_in_use else 6
            if len(payload) < offset + header_size:
                raise MessageDecodeError("ExtraParams entry header is truncated")
            param_type = unpack_from("<H", payload, offset)[0]
            offset += 2
            param_in_use = True
            if with_in_use:
                param_in_use = payload[offset] != 0
                offset += 1
            param_size = unpack_from("<I", payload, offset)[0]
            offset += 4
            if len(payload) < offset + param_size:
                raise MessageDecodeError("ExtraParams entry payload is truncated")
            param_data = payload[offset : offset + param_size]
            offset += param_size
            entries.append(
                ExtraParamEntry(
                    param_type=param_type,
                    param_in_use=param_in_use,
                    param_data=param_data,
                )
            )
        if offset != len(payload):
            raise MessageDecodeError("ExtraParams payload has trailing bytes")
        return tuple(entries)

    last_error: MessageDecodeError | None = None
    for with_in_use in (False, True):
        try:
            return _parse(with_in_use)
        except MessageDecodeError as exc:
            last_error = exc
            continue

    raise last_error or MessageDecodeError("ExtraParams payload is truncated")


def _parse_name_values(payload: bytes) -> dict[str, str]:
    text = payload.rstrip(b"\x00").decode("utf-8", errors="replace")
    values: dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            continue
        parts = line.split(" ", 4)
        if len(parts) < 5:
            values[line] = ""
            continue
        key, value_type, access, sendto, value = parts
        if value_type == "STRING" and access == "RW" and sendto == "SV":
            values[key] = value
        else:
            values[key] = " ".join(parts[1:])
    return values


def _summarize_payload(field_name: str, payload: bytes) -> ObjectUpdatePayloadSummary | None:
    if not payload:
        return None

    non_zero_bytes = sum(1 for byte in payload if byte != 0)
    if non_zero_bytes == 0:
        return None

    preview_hex = payload[:16].hex()
    text_preview: str | None = None
    if all(byte in b"\t\n\r" or 32 <= byte <= 126 for byte in payload[:64]):
        text = payload[:64].decode("utf-8", errors="replace").replace("\x00", "").strip()
        if text:
            text_preview = text.replace("\n", "\\n")

    return ObjectUpdatePayloadSummary(
        field_name=field_name,
        size=len(payload),
        non_zero_bytes=non_zero_bytes,
        preview_hex=preview_hex,
        text_preview=text_preview,
    )


def _decompress_v16(val: int, range_val: float) -> float:
    """Decompress a U16 into a float within a specific range."""
    # Scale constants from OpenSim Utils.FloatToUInt16Bytes
    if range_val == 1.0:
        return (val / 32767.5) - 1.0
    if range_val == 64.0:
        return (val / 511.9921875) - 64.0
    if range_val == 128.0:
        return (val / 255.99609375) - 128.0
    return 0.0


def _unpack_clamped_vector(data: bytes, offset: int, range_val: float) -> tuple[float, float, float]:
    vx = _decompress_v16(unpack_from("<H", data, offset)[0], range_val)
    vy = _decompress_v16(unpack_from("<H", data, offset + 2)[0], range_val)
    vz = _decompress_v16(unpack_from("<H", data, offset + 4)[0], range_val)
    return (vx, vy, vz)


def _unpack_clamped_quaternion(data: bytes, offset: int) -> tuple[float, float, float, float]:
    qx = _decompress_v16(unpack_from("<H", data, offset)[0], 1.0)
    qy = _decompress_v16(unpack_from("<H", data, offset + 2)[0], 1.0)
    qz = _decompress_v16(unpack_from("<H", data, offset + 4)[0], 1.0)
    qw = _decompress_v16(unpack_from("<H", data, offset + 6)[0], 1.0)
    return (qx, qy, qz, qw)


def infer_object_update_label(entry: ObjectUpdateEntry) -> str | None:
    if "FirstName" in entry.name_values or "LastName" in entry.name_values:
        first = entry.name_values.get("FirstName", "").strip()
        last = entry.name_values.get("LastName", "").strip()
        full_name = " ".join(part for part in (first, last) if part)
        if full_name:
            return full_name

    for key in ("Name", "Title", "Description", "TouchName", "SitName"):
        value = entry.name_values.get(key, "").strip()
        if value:
            return value

    return None


def format_object_update_interest(entry: ObjectUpdateEntry) -> str | None:
    if not entry.interesting_payloads:
        return None

    parts = [f"local_id={entry.local_id}", f"variant={entry.variant}"]
    label = infer_object_update_label(entry)
    if label is not None:
        parts.append(f"label={label!r}")

    field_parts: list[str] = []
    for payload in entry.interesting_payloads:
        field = f"{payload.field_name}:{payload.size}"
        if payload.text_preview is not None:
            field += f" text={payload.text_preview!r}"
        field += f" hex={payload.preview_hex}"
        field_parts.append(field)
    parts.append("interesting=" + ", ".join(field_parts))
    return " ".join(parts)


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


def parse_agent_wearables_update(message: MessageDispatch) -> AgentWearablesUpdateMessage:
    if message.summary.name != "AgentWearablesUpdate":
        raise MessageDecodeError(f"expected AgentWearablesUpdate, got {message.summary.name}")
    body = message.body
    if len(body) < 37:
        raise MessageDecodeError("AgentWearablesUpdate body is too short")

    offset = 0
    agent_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    session_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    serial_num = unpack_from("<I", body, offset)[0]
    offset += 4
    count = body[offset]
    offset += 1

    wearables: list[AgentWearableEntry] = []
    for _ in range(count):
        if offset + 33 > len(body):
            raise MessageDecodeError("AgentWearablesUpdate wearable block is truncated")
        wearables.append(
            AgentWearableEntry(
                item_id=UUID(bytes=body[offset : offset + 16]),
                asset_id=UUID(bytes=body[offset + 16 : offset + 32]),
                wearable_type=body[offset + 32],
            ),
        )
        offset += 33

    return AgentWearablesUpdateMessage(
        agent_id=agent_id,
        session_id=session_id,
        serial_num=serial_num,
        wearables=tuple(wearables),
    )


def parse_agent_cached_texture_response(message: MessageDispatch) -> AgentCachedTextureResponseMessage:
    if message.summary.name != "AgentCachedTextureResponse":
        raise MessageDecodeError(f"expected AgentCachedTextureResponse, got {message.summary.name}")
    body = message.body
    if len(body) < 37:
        raise MessageDecodeError("AgentCachedTextureResponse body is too short")

    offset = 0
    agent_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    session_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    serial_num = unpack_from("<i", body, offset)[0]
    offset += 4
    count = body[offset]
    offset += 1

    textures: list[AgentCachedTextureResponseEntry] = []
    for _ in range(count):
        if offset + 18 > len(body):
            raise MessageDecodeError("AgentCachedTextureResponse block is truncated")
        texture_id = UUID(bytes=body[offset : offset + 16])
        texture_index = body[offset + 16]
        host_len = body[offset + 17]
        offset += 18
        if offset + host_len > len(body):
            raise MessageDecodeError("AgentCachedTextureResponse host name is truncated")
        host_name = body[offset : offset + host_len].decode("utf-8", errors="replace")
        offset += host_len
        textures.append(
            AgentCachedTextureResponseEntry(
                texture_id=texture_id,
                texture_index=texture_index,
                host_name=host_name,
            ),
        )

    return AgentCachedTextureResponseMessage(
        agent_id=agent_id,
        session_id=session_id,
        serial_num=serial_num,
        textures=tuple(textures),
    )


def parse_avatar_appearance(message: MessageDispatch) -> AvatarAppearanceMessage:
    if message.summary.name != "AvatarAppearance":
        raise MessageDecodeError(f"expected AvatarAppearance, got {message.summary.name}")
    body = message.body
    if len(body) < 19:
        raise MessageDecodeError("AvatarAppearance body is too short")

    offset = 0
    sender_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    is_trial = bool(body[offset])
    offset += 1

    texture_len = unpack_from("<H", body, offset)[0]
    offset += 2
    if offset + texture_len > len(body):
        raise MessageDecodeError("AvatarAppearance texture entry is truncated")
    texture_entry = body[offset : offset + texture_len]
    offset += texture_len

    if offset >= len(body):
        raise MessageDecodeError("AvatarAppearance visual params count is missing")
    visual_count = body[offset]
    offset += 1
    if offset + visual_count > len(body):
        raise MessageDecodeError("AvatarAppearance visual params are truncated")
    visual_params = body[offset : offset + visual_count]
    offset += visual_count

    appearance_version: int | None = None
    cof_version: int | None = None
    appearance_flags: int | None = None
    hover_height: tuple[float, float, float] | None = None

    if offset < len(body):
        app_count = body[offset]
        offset += 1
        if app_count > 0:
            if offset + 9 > len(body):
                raise MessageDecodeError("AvatarAppearance appearance data is truncated")
            appearance_version = body[offset]
            cof_version = unpack_from("<i", body, offset + 1)[0]
            appearance_flags = unpack_from("<I", body, offset + 5)[0]
            offset += 9 * app_count

    if offset < len(body):
        hover_count = body[offset]
        offset += 1
        if hover_count > 0:
            if offset + 12 > len(body):
                raise MessageDecodeError("AvatarAppearance hover data is truncated")
            hover_height = tuple(unpack_from("<fff", body, offset))  # type: ignore[arg-type]
            offset += 12 * hover_count

    attachments: list[tuple[UUID, int]] = []
    if offset < len(body):
        attach_count = body[offset]
        offset += 1
        for _ in range(attach_count):
            if offset + 17 > len(body):
                raise MessageDecodeError("AvatarAppearance attachment block is truncated")
            attachments.append((UUID(bytes=body[offset : offset + 16]), body[offset + 16]))
            offset += 17

    return AvatarAppearanceMessage(
        sender_id=sender_id,
        is_trial=is_trial,
        texture_entry=texture_entry,
        visual_params=visual_params,
        appearance_version=appearance_version,
        cof_version=cof_version,
        appearance_flags=appearance_flags,
        hover_height=hover_height,
        attachments=tuple(attachments),
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
    sim_name = body[name_start:name_end].decode("utf-8", errors="replace").rstrip("\x00")
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


def parse_improved_terse_object_update(message: MessageDispatch) -> ImprovedTerseObjectUpdateMessage:
    if message.summary.name != "ImprovedTerseObjectUpdate":
        raise MessageDecodeError(f"expected ImprovedTerseObjectUpdate, got {message.summary.name}")
    if len(message.body) < 11:
        raise MessageDecodeError("ImprovedTerseObjectUpdate body is too short")

    region_handle = unpack_from("<Q", message.body, 0)[0]
    time_dilation = unpack_from("<H", message.body, 8)[0]
    object_count = message.body[10]
    offset = 11
    objects: list[ImprovedTerseObjectEntry] = []
    for _ in range(object_count):
        data_payload, offset = _read_variable_field(
            message.body,
            offset,
            1,
            "ImprovedTerseObjectUpdate.ObjectData.Data",
        )
        texture_entry_raw, offset = _read_variable_field(
            message.body,
            offset,
            2,
            "ImprovedTerseObjectUpdate.ObjectData.TextureEntry",
        )

        data_size = len(data_payload)
        if data_size not in (44, 60):
            # Fallback for unknown sizes or truncated data
            local_id = unpack_from("<I", data_payload, 0)[0] if data_size >= 4 else 0
            objects.append(
                ImprovedTerseObjectEntry(
                    local_id=local_id,
                    state=0,
                    is_avatar=False,
                    position=(0, 0, 0),
                    velocity=(0, 0, 0),
                    acceleration=(0, 0, 0),
                    rotation=(0, 0, 0, 1),
                    angular_velocity=(0, 0, 0),
                ),
            )
            continue

        local_id = unpack_from("<I", data_payload, 0)[0]
        state = data_payload[4]
        is_avatar = data_payload[5] == 1
        data_offset = 6

        collision_plane: tuple[float, float, float, float] | None = None
        if is_avatar:
            collision_plane = tuple(unpack_from("<ffff", data_payload, data_offset))  # type: ignore[arg-type]
            data_offset += 16

        position = tuple(unpack_from("<fff", data_payload, data_offset))
        data_offset += 12

        velocity = _unpack_clamped_vector(data_payload, data_offset, 128.0)
        data_offset += 6

        acceleration = _unpack_clamped_vector(data_payload, data_offset, 64.0)
        data_offset += 6

        rotation = _unpack_clamped_quaternion(data_payload, data_offset)
        data_offset += 8

        angular_velocity = _unpack_clamped_vector(data_payload, data_offset, 64.0)
        # data_offset += 6

        # TextureEntry in terse updates often has a custom 4-byte header in some simulators,
        # but standard Variable 2 should be handled by the outer reader.
        # OpenSim: totlen(U16), len(U16), 0,0, payload
        texture_entry: bytes | None = None
        if len(texture_entry_raw) > 4:
            # Skip the custom header if it looks like OpenSim style
            # (totlen == len + 4)
            totlen = int.from_bytes(texture_entry_raw[0:2], "little")
            sublen = int.from_bytes(texture_entry_raw[2:4], "little")
            if totlen == sublen + 4:
                texture_entry = texture_entry_raw[6:]
            else:
                texture_entry = texture_entry_raw

        objects.append(
            ImprovedTerseObjectEntry(
                local_id=local_id,
                state=state,
                is_avatar=is_avatar,
                position=position,  # type: ignore[arg-type]
                velocity=velocity,
                acceleration=acceleration,
                rotation=rotation,
                angular_velocity=angular_velocity,
                collision_plane=collision_plane,
                texture_entry=texture_entry,
            ),
        )

    return ImprovedTerseObjectUpdateMessage(
        region_handle=region_handle,
        time_dilation=time_dilation,
        objects=tuple(objects),
    )


@dataclass(slots=True, frozen=True)
class KillObjectMessage:
    local_ids: tuple[int, ...]


def parse_object_update_cached(message: MessageDispatch) -> ObjectUpdateCachedMessage:
    if message.summary.name != "ObjectUpdateCached":
        raise MessageDecodeError(f"expected ObjectUpdateCached, got {message.summary.name}")
    if len(message.body) < 11:
        raise MessageDecodeError("ObjectUpdateCached body is too short")

    region_handle = unpack_from("<Q", message.body, 0)[0]
    time_dilation = unpack_from("<H", message.body, 8)[0]
    object_count = message.body[10]
    offset = 11

    objects: list[ObjectUpdateCachedEntry] = []
    for _ in range(object_count):
        if len(message.body) < offset + 12:
            break
        local_id, crc, update_flags = unpack_from("<III", message.body, offset)
        offset += 12
        objects.append(
            ObjectUpdateCachedEntry(
                local_id=local_id,
                crc=crc,
                update_flags=update_flags,
            )
        )

    return ObjectUpdateCachedMessage(
        region_handle=region_handle,
        time_dilation=time_dilation,
        objects=tuple(objects),
    )


def parse_object_update_compressed(message: MessageDispatch) -> ObjectUpdateCompressedMessage:
    if message.summary.name != "ObjectUpdateCompressed":
        raise MessageDecodeError(f"expected ObjectUpdateCompressed, got {message.summary.name}")
    if len(message.body) < 11:
        raise MessageDecodeError("ObjectUpdateCompressed body is too short")

    region_handle = unpack_from("<Q", message.body, 0)[0]
    time_dilation = unpack_from("<H", message.body, 8)[0]
    object_count = message.body[10]
    offset = 11

    objects: list[ObjectUpdateCompressedEntry] = []
    for _ in range(object_count):
        if len(message.body) < offset + 6:
            break
        update_flags = unpack_from("<I", message.body, offset)[0]
        offset += 4
        data, offset = _read_variable_field(message.body, offset, 2, "ObjectUpdateCompressed.Data")
        objects.append(
            ObjectUpdateCompressedEntry(
                update_flags=update_flags,
                data=data,
            )
        )

    return ObjectUpdateCompressedMessage(
        region_handle=region_handle,
        time_dilation=time_dilation,
        objects=tuple(objects),
    )


def parse_object_properties_family(message: MessageDispatch) -> ObjectPropertiesFamilyMessage:
    if message.summary.name != "ObjectPropertiesFamily":
        raise MessageDecodeError(f"expected ObjectPropertiesFamily, got {message.summary.name}")
    if len(message.body) < 101:
        raise MessageDecodeError("ObjectPropertiesFamily body is too short")

    offset = 0
    request_flags = unpack_from("<I", message.body, offset)[0]
    offset += 4
    object_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16
    owner_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16
    group_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16
    base_mask = unpack_from("<I", message.body, offset)[0]
    offset += 4
    owner_mask = unpack_from("<I", message.body, offset)[0]
    offset += 4
    group_mask = unpack_from("<I", message.body, offset)[0]
    offset += 4
    everyone_mask = unpack_from("<I", message.body, offset)[0]
    offset += 4
    next_owner_mask = unpack_from("<I", message.body, offset)[0]
    offset += 4
    ownership_cost = unpack_from("<i", message.body, offset)[0]
    offset += 4
    sale_type = message.body[offset]
    offset += 1
    sale_price = unpack_from("<i", message.body, offset)[0]
    offset += 4
    category = unpack_from("<I", message.body, offset)[0]
    offset += 4
    last_owner_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16
    (name, description), _ = _read_utf8_fields_with_fallback(
        message.body,
        offset,
        ("ObjectPropertiesFamily.Name", "ObjectPropertiesFamily.Description"),
    )

    return ObjectPropertiesFamilyMessage(
        request_flags=request_flags,
        object_id=object_id,
        owner_id=owner_id,
        group_id=group_id,
        base_mask=base_mask,
        owner_mask=owner_mask,
        group_mask=group_mask,
        everyone_mask=everyone_mask,
        next_owner_mask=next_owner_mask,
        ownership_cost=ownership_cost,
        sale_type=sale_type,
        sale_price=sale_price,
        category=category,
        last_owner_id=last_owner_id,
        name=name,
        description=description,
    )


def parse_object_extra_params(message: MessageDispatch) -> ObjectExtraParamsMessage:
    if message.summary.name != "ObjectExtraParams":
        raise MessageDecodeError(f"expected ObjectExtraParams, got {message.summary.name}")
    if len(message.body) < 32:
        raise MessageDecodeError("ObjectExtraParams body is too short")

    offset = 0
    agent_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16
    session_id = UUID(bytes=message.body[offset : offset + 16])
    offset += 16

    objects: list[ObjectExtraParamsEntry] = []
    while offset < len(message.body):
        if len(message.body) < offset + 11:
            raise MessageDecodeError("ObjectExtraParams block header is truncated")
        object_local_id = unpack_from("<I", message.body, offset)[0]
        offset += 4
        param_type = unpack_from("<H", message.body, offset)[0]
        offset += 2
        param_in_use = message.body[offset] != 0
        offset += 1
        param_size = unpack_from("<I", message.body, offset)[0]
        offset += 4
        param_data, offset = _read_variable_field(
            message.body,
            offset,
            1,
            "ObjectExtraParams.ParamData",
        )
        if len(param_data) != param_size:
            raise MessageDecodeError("ObjectExtraParams ParamSize does not match ParamData length")
        objects.append(
            ObjectExtraParamsEntry(
                object_local_id=object_local_id,
                param_type=param_type,
                param_in_use=param_in_use,
                param_size=param_size,
                param_data=param_data,
            )
        )

    return ObjectExtraParamsMessage(
        agent_id=agent_id,
        session_id=session_id,
        objects=tuple(objects),
    )


def parse_kill_object(message: MessageDispatch) -> KillObjectMessage:
    if message.summary.name != "KillObject":
        raise MessageDecodeError(f"expected KillObject, got {message.summary.name}")

    if not message.body:
        raise MessageDecodeError("KillObject body is too short")

    count = message.body[0]
    expected_length = 1 + (count * 4)
    if len(message.body) != expected_length:
        raise MessageDecodeError("KillObject body length does not match block count")

    offset = 1
    local_ids = tuple(unpack_from("<I", message.body, offset + (i * 4))[0] for i in range(count))
    return KillObjectMessage(local_ids=local_ids)


def parse_chat_from_simulator(message: MessageDispatch) -> ChatFromSimulatorMessage:
    if message.summary.name != "ChatFromSimulator":
        raise MessageDecodeError(f"expected ChatFromSimulator, got {message.summary.name}")

    body = message.body
    if len(body) < 1 + 16 + 16 + 3 + 12 + 2:
        raise MessageDecodeError("ChatFromSimulator body is too short")

    from_name_length = body[0]
    offset = 1
    from_name_end = offset + from_name_length
    if len(body) < from_name_end + 16 + 16 + 3 + 12 + 2:
        raise MessageDecodeError("ChatFromSimulator FromName is truncated")
    from_name = body[offset:from_name_end].decode("utf-8", errors="replace").rstrip("\x00")
    offset = from_name_end

    source_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    owner_id = UUID(bytes=body[offset : offset + 16])
    offset += 16
    source_type = body[offset]
    chat_type = body[offset + 1]
    audible = body[offset + 2]
    offset += 3
    position = tuple(unpack_from("<fff", body, offset))
    offset += 12
    if len(body) < offset + 2:
        raise MessageDecodeError("ChatFromSimulator Message length is truncated")
    message_length = int.from_bytes(body[offset : offset + 2], "little")
    offset += 2
    message_end = offset + message_length
    if len(body) < message_end:
        raise MessageDecodeError("ChatFromSimulator Message payload is truncated")
    chat_message = body[offset:message_end].decode("utf-8", errors="replace").rstrip("\x00")
    return ChatFromSimulatorMessage(
        from_name=from_name,
        source_id=source_id,
        owner_id=owner_id,
        source_type=source_type,
        chat_type=chat_type,
        audible=audible,
        position=position,  # type: ignore[arg-type]
        message=chat_message,
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


def parse_object_update(message: MessageDispatch) -> ObjectUpdateMessage:
    if message.summary.name != "ObjectUpdate":
        raise MessageDecodeError(f"expected ObjectUpdate, got {message.summary.name}")
    if len(message.body) < 11:
        raise MessageDecodeError("ObjectUpdate body is too short")

    region_handle = unpack_from("<Q", message.body, 0)[0]
    time_dilation = unpack_from("<H", message.body, 8)[0]
    object_count = message.body[10]
    if object_count != 1:
        raise MessageDecodeError(f"unsupported ObjectUpdate object_count={object_count}")

    offset = 11
    if len(message.body) < offset + 40:
        raise MessageDecodeError("ObjectUpdate object header is truncated")

    local_id = unpack_from("<I", message.body, offset)[0]
    state = message.body[offset + 4]
    full_id = UUID(bytes=message.body[offset + 5 : offset + 21])
    crc = unpack_from("<I", message.body, offset + 21)[0]
    pcode = message.body[offset + 25]
    material = message.body[offset + 26]
    click_action = message.body[offset + 27]
    scale = tuple(unpack_from("<fff", message.body, offset + 28))
    offset += 40

    object_data, offset = _read_variable_field(message.body, offset, 1, "ObjectUpdate.ObjectData")

    if len(message.body) < offset + 8:
        raise MessageDecodeError("ObjectUpdate parent/update block is truncated")
    parent_id = unpack_from("<I", message.body, offset)[0]
    update_flags = unpack_from("<I", message.body, offset + 4)[0]

    position: tuple[float, float, float] | None = None
    rotation: tuple[float, float, float, float] | None = None
    variant = "unknown"
    name_values: dict[str, str] = {}
    texture_entry_size = 0
    texture_anim_size = 0
    data_size = 0
    text_size = 0
    media_url_size = 0
    ps_block_size = 0
    extra_params_size = 0
    default_texture_id: UUID | None = None
    extra_params_entries: tuple[ExtraParamEntry, ...] = ()
    interesting_payloads: list[ObjectUpdatePayloadSummary] = []
    trailing_payload = b""

    if pcode == 9 and len(object_data) == 60:
        position = tuple(unpack_from("<fff", object_data, 0))
        rotation = tuple(unpack_from("<ffff", object_data, 40))
        variant = "prim_basic"
        tail_offset = offset + 8 + 22
        texture_entry_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            2,
            "ObjectUpdate.TextureEntry",
        )
        texture_anim_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            1,
            "ObjectUpdate.TextureAnim",
        )
        name_value_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            2,
            "ObjectUpdate.NameValue",
        )
        data_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            2,
            "ObjectUpdate.Data",
        )
        text_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            1,
            "ObjectUpdate.Text",
        )
        if len(message.body) < tail_offset + 4:
            raise MessageDecodeError("ObjectUpdate text color is truncated")
        text_color_payload = message.body[tail_offset : tail_offset + 4]
        tail_offset += 4
        media_url_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            1,
            "ObjectUpdate.MediaURL",
        )
        ps_block_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            1,
            "ObjectUpdate.PSBlock",
        )
        extra_params_payload, tail_offset = _read_variable_field_with_length_fallback(
            message.body,
            tail_offset,
            "ObjectUpdate.ExtraParams",
            preferred_length_sizes=(2, 1),
        )
        texture_entry_size = len(texture_entry_payload)
        if texture_entry_size >= 16:
            default_texture_id = UUID(bytes=texture_entry_payload[:16])
        texture_anim_size = len(texture_anim_payload)
        data_size = len(data_payload)
        text_size = len(text_payload)
        media_url_size = len(media_url_payload)
        ps_block_size = len(ps_block_payload)
        extra_params_size = len(extra_params_payload)
        if extra_params_payload:
            try:
                extra_params_entries = parse_shape_extra_params(extra_params_payload)
            except MessageDecodeError:
                extra_params_entries = ()
        name_values = _parse_name_values(name_value_payload)
        trailing_payload = message.body[tail_offset:]
        for field_name, payload in (
            ("TextureEntry", texture_entry_payload),
            ("TextureAnim", texture_anim_payload),
            ("NameValue", name_value_payload),
            ("Data", data_payload),
            ("Text", text_payload),
            ("TextColor", text_color_payload),
            ("MediaURL", media_url_payload),
            ("PSBlock", ps_block_payload),
            ("ExtraParams", extra_params_payload),
            ("Trailing", trailing_payload),
        ):
            summary = _summarize_payload(field_name, payload)
            if summary is not None:
                interesting_payloads.append(summary)
        if extra_params_entries:
            interesting_payloads.append(
                ObjectUpdatePayloadSummary(
                    field_name="ExtraParamsDecoded",
                    size=extra_params_size,
                    non_zero_bytes=sum(1 for byte in extra_params_payload if byte != 0),
                    preview_hex=extra_params_payload[:16].hex(),
                    text_preview=(
                        f"count={len(extra_params_entries)} "
                        + ",".join(
                            f"type={entry.param_type}/size={len(entry.param_data)}/in_use={int(entry.param_in_use)}"
                            for entry in extra_params_entries[:4]
                        )
                    ),
                )
            )
    elif pcode == 47 and len(object_data) == 76:
        position = tuple(unpack_from("<fff", object_data, 16))
        variant = "avatar_basic"
        tail_offset = offset + 8 + 22
        texture_entry_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            2,
            "ObjectUpdate.TextureEntry",
        )
        texture_anim_payload, tail_offset = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            1,
            "ObjectUpdate.TextureAnim",
        )
        name_value_payload, _ = _read_variable_field_best_effort(
            message.body,
            tail_offset,
            2,
            "ObjectUpdate.NameValue",
        )
        name_values = _parse_name_values(name_value_payload)
        texture_entry_size = len(texture_entry_payload)
        texture_anim_size = len(texture_anim_payload)
        trailing_payload = message.body[tail_offset:]
        for field_name, payload in (
            ("TextureEntry", texture_entry_payload),
            ("TextureAnim", texture_anim_payload),
            ("Trailing", trailing_payload),
        ):
            summary = _summarize_payload(field_name, payload)
            if summary is not None:
                interesting_payloads.append(summary)
    else:
        raise MessageDecodeError(
            f"unsupported ObjectUpdate variant pcode={pcode} object_data_size={len(object_data)}",
        )

    return ObjectUpdateMessage(
        region_handle=region_handle,
        time_dilation=time_dilation,
        objects=(
            ObjectUpdateEntry(
                local_id=local_id,
                state=state,
                full_id=full_id,
                crc=crc,
                pcode=pcode,
                material=material,
                click_action=click_action,
                scale=scale,  # type: ignore[arg-type]
                object_data_size=len(object_data),
                parent_id=parent_id,
                update_flags=update_flags,
                position=position,  # type: ignore[arg-type]
                rotation=rotation,  # type: ignore[arg-type]
                variant=variant,
                name_values=name_values,
                texture_entry_size=texture_entry_size,
                texture_anim_size=texture_anim_size,
                data_size=data_size,
                text_size=text_size,
                media_url_size=media_url_size,
                ps_block_size=ps_block_size,
                extra_params_size=extra_params_size,
                default_texture_id=default_texture_id,
                interesting_payloads=tuple(interesting_payloads),
                extra_params_entries=extra_params_entries,
            ),
        ),
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


DEFAULT_AVATAR_TEXTURE_ID = UUID("89556747-24cb-43ed-920b-47caed15465f")
DEFAULT_AVATAR_SIZE = (0.45, 0.6, 1.9)
DEFAULT_AVATAR_BAKE_INDICES = (8, 9, 10, 11, 19, 20, 40, 41, 42, 43, 44)
DEFAULT_AVATAR_VISUAL_PARAMS = bytes(
    [
        33, 61, 85, 23, 58, 127, 63, 85, 63, 42, 0, 85, 63, 36, 85, 95, 153, 63, 34, 0, 63,
        109, 88, 132, 63, 136, 81, 85, 103, 136, 127, 0, 150, 150, 150, 127, 0, 0, 0, 0, 0, 127,
        0, 0, 255, 127, 114, 127, 99, 63, 127, 140, 127, 127, 0, 0, 0, 191, 0, 104, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 145, 216, 133, 0, 127, 0, 127, 170, 0, 0, 127, 127, 109, 85, 127, 127, 63,
        85, 42, 150, 150, 150, 150, 150, 150, 150, 25, 150, 150, 150, 0, 127, 0, 0, 144, 85, 127,
        132, 127, 85, 0, 127, 127, 127, 127, 127, 127, 59, 127, 85, 127, 127, 106, 47, 79, 127,
        127, 204, 2, 141, 66, 0, 0, 127, 127, 0, 0, 0, 0, 127, 0, 159, 0, 0, 178, 127, 36, 85,
        131, 127, 127, 127, 153, 95, 0, 140, 75, 27, 127, 127, 0, 150, 150, 198, 0, 0, 63, 30,
        127, 165, 209, 198, 127, 127, 153, 204, 51, 51, 255, 255, 255, 204, 0, 255, 150, 150, 150,
        150, 150, 150, 150, 150, 150, 150, 0, 150, 150, 150, 150, 150, 0, 127, 127, 150, 150,
        150, 150, 150, 150, 150, 150, 0, 0, 150, 51, 132, 150, 150, 150,
    ],
)
DEFAULT_AVATAR_TEXTURE_ENTRY = DEFAULT_AVATAR_TEXTURE_ID.bytes


def encode_agent_wearables_request(agent_id: UUID, session_id: UUID) -> bytes:
    return b"\xFF\xFF\x01\x7D" + agent_id.bytes + session_id.bytes


def encode_agent_is_now_wearing(
    agent_id: UUID,
    session_id: UUID,
    wearables: tuple[AgentWearableEntry, ...],
) -> bytes:
    return (
        b"\xFF\xFF\x01\x7F"
        + agent_id.bytes
        + session_id.bytes
        + bytes([len(wearables)])
        + b"".join(entry.item_id.bytes + bytes([entry.wearable_type]) for entry in wearables)
    )


def encode_agent_set_appearance(
    agent_id: UUID,
    session_id: UUID,
    *,
    serial_num: int,
    size: tuple[float, float, float] = DEFAULT_AVATAR_SIZE,
    cache_items: tuple[WearableCacheEntry, ...] = (),
    texture_entry: bytes = DEFAULT_AVATAR_TEXTURE_ENTRY,
    visual_params: bytes = DEFAULT_AVATAR_VISUAL_PARAMS,
) -> bytes:
    if not 0 <= serial_num <= 0xFFFFFFFF:
        raise ValueError("serial_num must fit in U32")
    if len(cache_items) > 0xFF:
        raise ValueError("cache item count must fit in U8")
    if len(texture_entry) > 0xFFFF:
        raise ValueError("texture entry length must fit in U16")
    if len(visual_params) > 0xFF:
        raise ValueError("visual params count must fit in U8")

    return (
        b"\xFF\xFF\x00\x54"
        + agent_id.bytes
        + session_id.bytes
        + pack("<I", serial_num)
        + pack("<fff", *size)
        + bytes([len(cache_items)])
        + b"".join(item.cache_id.bytes + bytes([item.texture_index]) for item in cache_items)
        + pack("<H", len(texture_entry))
        + texture_entry
        + bytes([len(visual_params)])
        + visual_params
    )


def encode_agent_cached_texture(
    agent_id: UUID,
    session_id: UUID,
    *,
    serial_num: int,
    cache_items: tuple[WearableCacheEntry, ...] = tuple(
        WearableCacheEntry(cache_id=UUID(int=0), texture_index=index)
        for index in DEFAULT_AVATAR_BAKE_INDICES
    ),
) -> bytes:
    if not -(1 << 31) <= serial_num < (1 << 31):
        raise ValueError("serial_num must fit in S32")
    if len(cache_items) > 0xFF:
        raise ValueError("cache item count must fit in U8")

    return (
        b"\xFF\xFF\x01\x80"
        + agent_id.bytes
        + session_id.bytes
        + pack("<i", serial_num)
        + bytes([len(cache_items)])
        + b"".join(item.cache_id.bytes + bytes([item.texture_index]) for item in cache_items)
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


def encode_logout_request(agent_id: UUID, session_id: UUID) -> bytes:
    return b"\xFF\xFF\x00\xFC" + agent_id.bytes + session_id.bytes


def encode_region_handshake_reply(agent_id: UUID, session_id: UUID, flags: int) -> bytes:
    if not 0 <= flags <= 0xFFFFFFFF:
        raise ValueError("flags must fit in U32")
    return b"\xFF\xFF\x00\x95" + agent_id.bytes + session_id.bytes + pack("<I", flags)
