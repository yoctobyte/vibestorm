"""Typed decoders for EventQueueGet LLSD events.

EventQueueGet returns an LLSD map ``{"id": <int>, "events": [{"message":
<name>, "body": {...}}, ...]}``. The body shapes mirror OpenSim's
``EventQueueGetHandlers.cs``. Note OpenSim's LLSD encoder emits ``uint`` and
``ulong`` as big-endian ``binary`` blobs (not ``<integer>``), so region
handles and sizes arrive as bytes; ``int`` fields arrive as ``<integer>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from vibestorm.udp.messages import ParcelPropertiesMessage

EVENT_ENABLE_SIMULATOR = "EnableSimulator"
EVENT_ESTABLISH_AGENT_COMMUNICATION = "EstablishAgentCommunication"
EVENT_TELEPORT_FINISH = "TeleportFinish"
EVENT_CROSSED_REGION = "CrossedRegion"
EVENT_SCRIPT_RUNNING_REPLY = "ScriptRunningReply"
EVENT_OBJECT_PHYSICS_PROPERTIES = "ObjectPhysicsProperties"
EVENT_AGENT_GROUP_DATA_UPDATE = "AgentGroupDataUpdate"
EVENT_PARCEL_PROPERTIES = "ParcelProperties"


class EventQueueDecodeError(ValueError):
    """Raised when an EventQueueGet payload cannot be decoded."""


@dataclass(slots=True, frozen=True)
class EnableSimulatorEvent:
    handle: int
    ip: str
    port: int
    region_size_x: int
    region_size_y: int


@dataclass(slots=True, frozen=True)
class EstablishAgentCommunicationEvent:
    agent_id: str
    sim_ip_and_port: str
    seed_capability: str


@dataclass(slots=True, frozen=True)
class TeleportFinishEvent:
    agent_id: str
    location_id: int
    sim_ip: str
    sim_port: int
    region_handle: int
    seed_capability: str
    sim_access: int
    teleport_flags: int
    region_size_x: int
    region_size_y: int


@dataclass(slots=True, frozen=True)
class CrossedRegionEvent:
    agent_id: str
    session_id: str
    look_at: tuple[float, float, float]
    position: tuple[float, float, float]
    region_handle: int
    seed_capability: str
    sim_ip: str
    sim_port: int
    region_size_x: int
    region_size_y: int


@dataclass(slots=True, frozen=True)
class ScriptRunningReplyEvent:
    object_id: str
    item_id: str
    running: bool
    mono: bool


@dataclass(slots=True, frozen=True)
class ObjectPhysicsPropertiesEvent:
    local_id: int
    density: float
    friction: float
    gravity_multiplier: float
    restitution: float
    physics_shape_type: int


@dataclass(slots=True, frozen=True)
class GroupMembership:
    group_id: str
    group_powers: int
    accept_notices: bool
    group_insignia_id: str
    contribution: int
    group_name: str
    list_in_profile: bool


@dataclass(slots=True, frozen=True)
class AgentGroupDataUpdateEvent:
    agent_id: str
    groups: tuple[GroupMembership, ...]


@dataclass(slots=True, frozen=True)
class ParcelPropertiesEvent:
    """Parcel identity delivered over the event queue.

    OpenSim sends ``ParcelProperties`` **only** here — ``LLClientView.
    SendLandProperties`` builds an EQG event and there is no UDP send path at
    all (no ``ParcelPropertiesPacket`` anywhere in ``LLClientView.cs``). The
    UDP ``ParcelProperties`` message stays in the template as
    ``UDPDeprecated``, so ``udp.messages.parse_parcel_properties`` never fires
    against OpenSim.

    The payload is decoded into the same ``ParcelPropertiesMessage`` the UDP
    parser produces, so bus consumers do not care which transport delivered it.
    """

    properties: ParcelPropertiesMessage


@dataclass(slots=True, frozen=True)
class UnknownEvent:
    message: str
    body: object


@dataclass(slots=True, frozen=True)
class EventQueueBatch:
    """One EventQueueGet poll result."""

    ack_id: int | None
    events: tuple[object, ...] = field(default_factory=tuple)


def decode_event_queue_payload(payload: object) -> EventQueueBatch:
    """Decode a parsed EventQueueGet LLSD payload into typed events.

    Unknown event names are preserved as ``UnknownEvent`` so the caller can
    still ack the queue and log them.
    """
    if not isinstance(payload, dict):
        raise EventQueueDecodeError("EventQueueGet payload is not an LLSD map")

    raw_events = payload.get("events")
    if raw_events is None:
        raw_events = []
    if not isinstance(raw_events, list):
        raise EventQueueDecodeError("EventQueueGet 'events' is not an array")

    ack_id = payload.get("id")
    ack_id = ack_id if isinstance(ack_id, int) else None

    decoded: list[object] = []
    for entry in raw_events:
        if not isinstance(entry, dict):
            raise EventQueueDecodeError("EventQueueGet event entry is not a map")
        name = entry.get("message")
        body = entry.get("body")
        if not isinstance(name, str):
            raise EventQueueDecodeError("EventQueueGet event missing 'message'")
        decoded.append(_decode_one(name, body))

    return EventQueueBatch(ack_id=ack_id, events=tuple(decoded))


def _decode_one(name: str, body: object) -> object:
    if name == EVENT_ENABLE_SIMULATOR:
        info = _first_block(body, "SimulatorInfo")
        return EnableSimulatorEvent(
            handle=_as_int(info.get("Handle")),
            ip=_as_ip(info.get("IP")),
            port=_as_int(info.get("Port")),
            region_size_x=_as_int(info.get("RegionSizeX")),
            region_size_y=_as_int(info.get("RegionSizeY")),
        )
    if name == EVENT_ESTABLISH_AGENT_COMMUNICATION:
        b = _as_map(body)
        return EstablishAgentCommunicationEvent(
            agent_id=str(b.get("agent-id", "")),
            sim_ip_and_port=str(b.get("sim-ip-and-port", "")),
            seed_capability=str(b.get("seed-capability", "")),
        )
    if name == EVENT_TELEPORT_FINISH:
        info = _first_block(body, "Info")
        return TeleportFinishEvent(
            agent_id=str(info.get("AgentID", "")),
            location_id=_as_int(info.get("LocationID")),
            sim_ip=_as_ip(info.get("SimIP")),
            sim_port=_as_int(info.get("SimPort")),
            region_handle=_as_int(info.get("RegionHandle")),
            seed_capability=str(info.get("SeedCapability", "")),
            sim_access=_as_int(info.get("SimAccess")),
            teleport_flags=_as_int(info.get("TeleportFlags")),
            region_size_x=_as_int(info.get("RegionSizeX")),
            region_size_y=_as_int(info.get("RegionSizeY")),
        )
    if name == EVENT_CROSSED_REGION:
        agent = _first_block(body, "AgentData")
        info = _first_block(body, "Info")
        region = _first_block(body, "RegionData")
        return CrossedRegionEvent(
            agent_id=str(agent.get("AgentID", "")),
            session_id=str(agent.get("SessionID", "")),
            look_at=_as_vec3(info.get("LookAt")),
            position=_as_vec3(info.get("Position")),
            region_handle=_as_int(region.get("RegionHandle")),
            seed_capability=str(region.get("SeedCapability", "")),
            sim_ip=_as_ip(region.get("SimIP")),
            sim_port=_as_int(region.get("SimPort")),
            region_size_x=_as_int(region.get("RegionSizeX")),
            region_size_y=_as_int(region.get("RegionSizeY")),
        )
    if name == EVENT_SCRIPT_RUNNING_REPLY:
        script = _first_block(body, "Script")
        return ScriptRunningReplyEvent(
            object_id=str(script.get("ObjectID", "")),
            item_id=str(script.get("ItemID", "")),
            running=bool(script.get("Running", False)),
            mono=bool(script.get("Mono", False)),
        )
    if name == EVENT_OBJECT_PHYSICS_PROPERTIES:
        obj = _first_block(body, "ObjectData")
        return ObjectPhysicsPropertiesEvent(
            local_id=_as_int(obj.get("LocalID")),
            density=_as_float(obj.get("Density")),
            friction=_as_float(obj.get("Friction")),
            gravity_multiplier=_as_float(obj.get("GravityMultiplier")),
            restitution=_as_float(obj.get("Restitution")),
            physics_shape_type=_as_int(obj.get("PhysicsShapeType")),
        )
    if name == EVENT_AGENT_GROUP_DATA_UPDATE:
        b = _as_map(body)
        agent = _first_block(body, "AgentData")
        group_data = b.get("GroupData")
        group_rows = group_data if isinstance(group_data, list) else []
        new_data = b.get("NewGroupData")
        new_rows = new_data if isinstance(new_data, list) else []
        groups: list[GroupMembership] = []
        for index, row in enumerate(group_rows):
            row = _as_map(row)
            list_in_profile = False
            if index < len(new_rows) and isinstance(new_rows[index], dict):
                list_in_profile = bool(new_rows[index].get("ListInProfile", False))
            groups.append(
                GroupMembership(
                    group_id=str(row.get("GroupID", "")),
                    group_powers=_as_int(row.get("GroupPowers")),
                    accept_notices=bool(row.get("AcceptNotices", False)),
                    group_insignia_id=str(row.get("GroupInsigniaID", "")),
                    contribution=_as_int(row.get("Contribution")),
                    group_name=str(row.get("GroupName", "")),
                    list_in_profile=list_in_profile,
                )
            )
        return AgentGroupDataUpdateEvent(
            agent_id=str(agent.get("AgentID", "")),
            groups=tuple(groups),
        )
    if name == EVENT_PARCEL_PROPERTIES:
        parcel = _first_block(body, "ParcelData")
        return ParcelPropertiesEvent(
            properties=ParcelPropertiesMessage(
                request_result=_as_int(parcel.get("RequestResult")),
                sequence_id=_as_int(parcel.get("SequenceID")),
                self_count=_as_int(parcel.get("SelfCount")),
                other_count=_as_int(parcel.get("OtherCount")),
                public_count=_as_int(parcel.get("PublicCount")),
                local_id=_as_int(parcel.get("LocalID")),
                owner_id=_as_uuid(parcel.get("OwnerID")),
                is_group_owned=bool(parcel.get("IsGroupOwned", False)),
                aabb_min=_as_vec3(parcel.get("AABBMin")),
                aabb_max=_as_vec3(parcel.get("AABBMax")),
                bitmap=_as_bytes(parcel.get("Bitmap")),
                area=_as_int(parcel.get("Area")),
                status=_as_int(parcel.get("Status")),
                max_prims=_as_int(parcel.get("MaxPrims")),
                total_prims=_as_int(parcel.get("TotalPrims")),
                parcel_flags=_as_int(parcel.get("ParcelFlags")),
                sale_price=_as_int(parcel.get("SalePrice")),
                name=str(parcel.get("Name", "")),
                description=str(parcel.get("Desc", "")),
                music_url=str(parcel.get("MusicURL", "")),
                media_url=str(parcel.get("MediaURL", "")),
                group_id=_as_uuid(parcel.get("GroupID")),
            )
        )
    return UnknownEvent(message=name, body=body)


def _as_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EventQueueDecodeError("expected an LLSD map in event body")
    return value


def _first_block(body: object, key: str) -> dict[str, object]:
    """Return the single map inside ``body[key]`` (OpenSim wraps it in an array)."""
    b = _as_map(body)
    block = b.get(key)
    if isinstance(block, list):
        if not block:
            raise EventQueueDecodeError(f"event block '{key}' is an empty array")
        block = block[0]
    return _as_map(block)


def _as_int(value: object) -> int:
    """Coerce an LLSD value to int (binary big-endian blob or integer)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        return int.from_bytes(value, "big")
    if value is None:
        return 0
    raise EventQueueDecodeError(f"cannot coerce {type(value).__name__} to int")


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    raise EventQueueDecodeError(f"cannot coerce {type(value).__name__} to float")


def _as_ip(value: object) -> str:
    """Render an IP value: 4 binary bytes -> dotted quad, or pass strings through."""
    if isinstance(value, (bytes, bytearray)):
        if len(value) == 4:
            return ".".join(str(b) for b in value)
        return value.hex()
    if value is None:
        return ""
    return str(value)


def _as_uuid(value: object) -> UUID:
    """Coerce an LLSD uuid value; missing/blank fields become the null UUID.

    OpenSim's LLSD encoder writes an unset UUID as an empty ``<uuid/>``
    element, which parses to the empty string rather than to the all-zero
    form — seen live on ``GroupID`` for an ungrouped parcel.
    """
    if isinstance(value, UUID):
        return value
    if value is None:
        return UUID(int=0)
    text = str(value).strip()
    if not text:
        return UUID(int=0)
    try:
        return UUID(text)
    except (TypeError, ValueError) as exc:
        raise EventQueueDecodeError(f"invalid uuid in event body: {value!r}") from exc


def _as_bytes(value: object) -> bytes:
    """Coerce an LLSD binary blob (the Bitmap field arrives as one)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if value is None:
        return b""
    raise EventQueueDecodeError(f"cannot coerce {type(value).__name__} to bytes")


def _as_vec3(value: object) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return (0.0, 0.0, 0.0)
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise EventQueueDecodeError("invalid vector3 in event body") from exc


__all__ = [
    "AgentGroupDataUpdateEvent",
    "CrossedRegionEvent",
    "EVENT_AGENT_GROUP_DATA_UPDATE",
    "EVENT_CROSSED_REGION",
    "EVENT_ENABLE_SIMULATOR",
    "EVENT_ESTABLISH_AGENT_COMMUNICATION",
    "EVENT_OBJECT_PHYSICS_PROPERTIES",
    "EVENT_PARCEL_PROPERTIES",
    "EVENT_SCRIPT_RUNNING_REPLY",
    "EVENT_TELEPORT_FINISH",
    "EnableSimulatorEvent",
    "EstablishAgentCommunicationEvent",
    "EventQueueBatch",
    "EventQueueDecodeError",
    "GroupMembership",
    "ObjectPhysicsPropertiesEvent",
    "ParcelPropertiesEvent",
    "ScriptRunningReplyEvent",
    "TeleportFinishEvent",
    "UnknownEvent",
    "decode_event_queue_payload",
]
