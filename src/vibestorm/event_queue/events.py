"""Typed decoders for EventQueueGet LLSD events.

EventQueueGet returns an LLSD map ``{"id": <int>, "events": [{"message":
<name>, "body": {...}}, ...]}``. The body shapes mirror OpenSim's
``EventQueueGetHandlers.cs``. Note OpenSim's LLSD encoder emits ``uint`` and
``ulong`` as big-endian ``binary`` blobs (not ``<integer>``), so region
handles and sizes arrive as bytes; ``int`` fields arrive as ``<integer>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVENT_ENABLE_SIMULATOR = "EnableSimulator"
EVENT_ESTABLISH_AGENT_COMMUNICATION = "EstablishAgentCommunication"
EVENT_TELEPORT_FINISH = "TeleportFinish"
EVENT_CROSSED_REGION = "CrossedRegion"
EVENT_SCRIPT_RUNNING_REPLY = "ScriptRunningReply"
EVENT_OBJECT_PHYSICS_PROPERTIES = "ObjectPhysicsProperties"


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


def _as_vec3(value: object) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return (0.0, 0.0, 0.0)
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise EventQueueDecodeError("invalid vector3 in event body") from exc


__all__ = [
    "CrossedRegionEvent",
    "EVENT_CROSSED_REGION",
    "EVENT_ENABLE_SIMULATOR",
    "EVENT_ESTABLISH_AGENT_COMMUNICATION",
    "EVENT_OBJECT_PHYSICS_PROPERTIES",
    "EVENT_SCRIPT_RUNNING_REPLY",
    "EVENT_TELEPORT_FINISH",
    "EnableSimulatorEvent",
    "EstablishAgentCommunicationEvent",
    "EventQueueBatch",
    "EventQueueDecodeError",
    "ObjectPhysicsPropertiesEvent",
    "ScriptRunningReplyEvent",
    "TeleportFinishEvent",
    "UnknownEvent",
    "decode_event_queue_payload",
]
