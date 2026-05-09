"""WorldClient: top-level owner of one or more LiveCircuitSession circuits.

Today the session machinery assumes a single sim. Cross-sim support
(Phase 5) needs N circuits — one for the region the avatar is in
("current") and zero or more child circuits for visible neighbors.

This skeleton introduces the ownership shape without changing any wire
behavior: a WorldClient holds circuits keyed by region_handle, knows which
one is current, and can route outbound traffic to a specific circuit.

Future phases will move the run-loop's "session" reference to
``world_client.current``, hook the command/event bus through here, and
add the cross-sim transitions (EnableSimulator → child circuit,
CrossedRegion → promote child to current).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID

from vibestorm.bus import Bus, NoHandlerError
from vibestorm.bus.commands import (
    AddControlFlags,
    ClearControlFlags,
    RemoveControlFlags,
    SendChat,
    SetBodyRotation,
    SetCamera,
    SetControlFlags,
    SetHeadRotation,
    TeleportLocation,
)
from vibestorm.bus.events import (
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    InventorySnapshotReady,
    LayerDataReceived,
    RegionChanged,
    RegionMapTileReady,
    SessionClosed,
    TextureAssetReady,
    WorldStateChanged,
)
from vibestorm.udp.session import LiveCircuitSession, SessionEvent
from vibestorm.world.models import WorldView


def region_handle_from_meters(region_x_meters: int, region_y_meters: int) -> int:
    """Pack a (region_x, region_y) meter offset pair into the standard SL U64 region handle."""
    if not 0 <= region_x_meters <= 0xFFFFFFFF:
        raise ValueError("region_x_meters must fit in U32")
    if not 0 <= region_y_meters <= 0xFFFFFFFF:
        raise ValueError("region_y_meters must fit in U32")
    return (region_x_meters << 32) | region_y_meters


def region_handle_for_session(session: LiveCircuitSession) -> int:
    """Compute the region handle for a session from its bootstrap coordinates."""
    return region_handle_from_meters(session.bootstrap.region_x, session.bootstrap.region_y)


class WorldClientError(RuntimeError):
    """Raised on programming errors against the WorldClient API."""


@dataclass(slots=True)
class WorldClient:
    """Multi-circuit session owner.

    Holds N LiveCircuitSession instances keyed by region_handle. Exactly
    one is "current" (the region the agent is in); others are child sims
    (neighbors connected via EstablishAgentCommunication).

    v1 contract:
    - Pure container. Routing helpers are advisory; the run loop still
      owns its socket and packet pump for now.
    - ``world_view()`` returns the current circuit's view. Multi-region
      merging arrives in Phase 5.
    """

    circuits: dict[int, LiveCircuitSession] = field(default_factory=dict)
    current_handle: int | None = None
    bus: Bus = field(default_factory=Bus)
    _outbound_packets: deque[tuple[int, bytes]] = field(
        default_factory=deque,
        init=False,
        repr=False,
    )
    _outbound_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _command_handlers_registered: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._register_default_command_handlers()

    # ------------------------------------------------------------------ ops

    def add_circuit(self, session: LiveCircuitSession, *, make_current: bool = False) -> int:
        """Register a session under its region handle. Returns the handle.

        Raises if a circuit is already registered for the same handle —
        callers should ``remove_circuit`` first if they intend to replace.

        The session's ``on_event`` callback is wrapped (preserving any
        existing one) so SessionEvent records flow through the bus
        translator.
        """
        handle = region_handle_for_session(session)
        if handle in self.circuits:
            raise WorldClientError(f"circuit already registered for region_handle={handle:#018x}")

        previous_on_event = session.on_event

        def _bridge(evt: SessionEvent) -> None:
            if previous_on_event is not None:
                previous_on_event(evt)
            self.on_session_event(session, evt)

        session.on_event = _bridge

        promoting = make_current or self.current_handle is None
        previous_handle = self.current_handle
        self.circuits[handle] = session
        if promoting:
            self.current_handle = handle
            if previous_handle != handle:
                self.bus.publish(
                    RegionChanged(region_handle=handle, region_name=session.last_region_name)
                )
        return handle

    def remove_circuit(self, handle: int) -> LiveCircuitSession | None:
        """Drop a circuit. If it was the current circuit, leave current_handle unset."""
        session = self.circuits.pop(handle, None)
        if self.current_handle == handle:
            self.current_handle = None
        return session

    def set_current(self, handle: int) -> None:
        """Promote an existing child circuit to current."""
        if handle not in self.circuits:
            raise WorldClientError(f"no circuit registered for region_handle={handle:#018x}")
        if self.current_handle == handle:
            return
        self.current_handle = handle
        session = self.circuits[handle]
        self.bus.publish(RegionChanged(region_handle=handle, region_name=session.last_region_name))

    # ------------------------------------------------------------------ views

    @property
    def current(self) -> LiveCircuitSession | None:
        if self.current_handle is None:
            return None
        return self.circuits.get(self.current_handle)

    @property
    def child_handles(self) -> tuple[int, ...]:
        return tuple(h for h in self.circuits if h != self.current_handle)

    def get(self, handle: int) -> LiveCircuitSession | None:
        return self.circuits.get(handle)

    def all_circuits(self) -> Iterable[LiveCircuitSession]:
        return self.circuits.values()

    def world_view(self) -> WorldView | None:
        """Return the current circuit's WorldView (v1: single-region only)."""
        current = self.current
        if current is None:
            return None
        return current.world_view

    # ----------------------------------------------------------- outbound

    def queue_outbound_packet(self, handle: int, packet: bytes) -> None:
        """Queue a packet built outside the session loop for the UDP pump to send."""
        with self._outbound_lock:
            self._outbound_packets.append((handle, packet))

    def drain_outbound_packets(self, handle: int | None = None) -> tuple[tuple[int, bytes], ...]:
        """Return and remove queued outbound packets, optionally only for one circuit."""
        with self._outbound_lock:
            if handle is None:
                packets = tuple(self._outbound_packets)
                self._outbound_packets.clear()
                return packets

            matched: list[tuple[int, bytes]] = []
            remaining: deque[tuple[int, bytes]] = deque()
            while self._outbound_packets:
                item = self._outbound_packets.popleft()
                if item[0] == handle:
                    matched.append(item)
                else:
                    remaining.append(item)
            self._outbound_packets = remaining
            return tuple(matched)

    # ----------------------------------------------------------- bus bridge

    def on_session_event(self, session: LiveCircuitSession, event: SessionEvent) -> None:
        """Translate the per-session string-keyed SessionEvent stream into typed bus events.

        Wired into the session's ``on_event`` callback by ``add_circuit`` so
        consumers can subscribe to typed events without parsing detail strings.
        Unknown event kinds are silently ignored — the original SessionEvent
        is still recorded in ``session.events`` for retrospection.
        """
        handle = region_handle_for_session(session)
        kind = event.kind

        if kind == "chat.local":
            payload = self._parse_chat_local(event.detail, handle)
            if payload is not None:
                self.bus.publish(payload)
        elif kind == "chat.im":
            payload = self._parse_chat_im(event.detail, handle)
            if payload is not None:
                self.bus.publish(payload)
        elif kind == "chat.outbound":
            payload = self._parse_chat_outbound(event.detail, handle)
            if payload is not None:
                self.bus.publish(payload)
        elif kind == "chat.alert":
            self.bus.publish(
                ChatAlert(region_handle=handle, message=event.detail, is_agent_alert=False)
            )
        elif kind == "chat.agent_alert":
            self.bus.publish(
                ChatAlert(region_handle=handle, message=event.detail, is_agent_alert=True)
            )
        elif kind == "session.closed":
            self.bus.publish(SessionClosed(region_handle=handle, reason=event.detail))
        elif kind == "map.cache.ok":
            parts = _kv_split(event.detail)
            path = parts.get("path")
            if path and session.region_map_image_id is not None:
                self.bus.publish(
                    RegionMapTileReady(
                        region_handle=handle,
                        image_id=session.region_map_image_id,
                        cache_path=path,
                    )
                )
        elif kind == "texture.cache.ok":
            parts = _kv_split(event.detail)
            path = parts.get("path")
            texture_id_raw = parts.get("id")
            if not path or texture_id_raw is None:
                return
            try:
                texture_id = UUID(texture_id_raw)
            except ValueError:
                return
            self.bus.publish(
                TextureAssetReady(
                    region_handle=handle,
                    texture_id=texture_id,
                    cache_path=path,
                )
            )
        elif kind == "terrain.layer_data":
            # detail looks like ``type=0x4c bytes=NN``. Pull the type
            # back out and republish the most-recent blob from the
            # session — the session is the source of truth, the
            # SessionEvent is just the trigger.
            parts = _kv_split(event.detail)
            type_str = parts.get("type")
            if type_str is None:
                return
            try:
                layer_type = int(type_str, 16) if type_str.startswith("0x") else int(type_str)
            except ValueError:
                return
            data = session.latest_layer_data.get(layer_type)
            if data is None:
                return
            self.bus.publish(
                LayerDataReceived(
                    region_handle=handle,
                    layer_type=layer_type,
                    data=data,
                )
            )
        elif kind == "caps.inventory" and session.latest_inventory_fetch is not None:
            self.bus.publish(
                InventorySnapshotReady(
                    region_handle=handle,
                    snapshot=session.latest_inventory_fetch,
                )
            )
        elif kind.startswith("world."):
            self.bus.publish(WorldStateChanged(region_handle=handle, reason=kind[len("world."):]))

    # ----------------------------------------------------- command handlers

    def _register_default_command_handlers(self) -> None:
        if self._command_handlers_registered:
            return
        self._command_handlers_registered = True
        self.bus.register_handler(SetControlFlags, self._handle_set_control_flags)
        self.bus.register_handler(AddControlFlags, self._handle_add_control_flags)
        self.bus.register_handler(RemoveControlFlags, self._handle_remove_control_flags)
        self.bus.register_handler(ClearControlFlags, self._handle_clear_control_flags)
        self.bus.register_handler(SetBodyRotation, self._handle_set_body_rotation)
        self.bus.register_handler(SetHeadRotation, self._handle_set_head_rotation)
        self.bus.register_handler(SetCamera, self._handle_set_camera)
        self.bus.register_handler(SendChat, self._handle_send_chat)
        self.bus.register_handler(TeleportLocation, self._handle_teleport_location)

    def _require_current(self) -> LiveCircuitSession:
        current = self.current
        if current is None:
            raise WorldClientError("no current circuit; cannot dispatch command")
        return current

    def _handle_set_control_flags(self, cmd: SetControlFlags) -> None:
        self._require_current().set_control_flags(cmd.flags)

    def _handle_add_control_flags(self, cmd: AddControlFlags) -> None:
        self._require_current().add_control_flags(cmd.flags)

    def _handle_remove_control_flags(self, cmd: RemoveControlFlags) -> None:
        self._require_current().remove_control_flags(cmd.flags)

    def _handle_clear_control_flags(self, cmd: ClearControlFlags) -> None:
        self._require_current().clear_control_flags()

    def _handle_set_body_rotation(self, cmd: SetBodyRotation) -> None:
        self._require_current().set_body_rotation(cmd.rotation)

    def _handle_set_head_rotation(self, cmd: SetHeadRotation) -> None:
        self._require_current().set_head_rotation(cmd.rotation)

    def _handle_set_camera(self, cmd: SetCamera) -> None:
        current = self._require_current()
        current.camera_center = tuple(float(v) for v in cmd.center)  # type: ignore[assignment]
        current.camera_at_axis = tuple(float(v) for v in cmd.at_axis)  # type: ignore[assignment]
        current.camera_left_axis = tuple(float(v) for v in cmd.left_axis)  # type: ignore[assignment]
        current.camera_up_axis = tuple(float(v) for v in cmd.up_axis)  # type: ignore[assignment]

    def _handle_send_chat(self, cmd: SendChat) -> bytes:
        current = self._require_current()
        packet = current.build_chat_packet(
            cmd.message,
            chat_type=cmd.chat_type,
            channel=cmd.channel,
        )
        if self.current_handle is not None:
            self.queue_outbound_packet(self.current_handle, packet)
        return packet

    def _handle_teleport_location(self, cmd: TeleportLocation) -> bytes:
        current = self._require_current()
        handle = cmd.region_handle if cmd.region_handle is not None else self.current_handle
        if handle is None:
            raise WorldClientError("no current region handle; cannot teleport")
        packet = current.build_teleport_location_packet(
            region_handle=handle,
            position=cmd.position,
            look_at=cmd.look_at,
        )
        self.queue_outbound_packet(handle, packet)
        return packet

    # ------------------------------------------------------- event parsing

    @staticmethod
    def _parse_chat_local(detail: str, handle: int) -> ChatLocal | None:
        # SessionEvent("chat.local", "from='Name' type=N audible=N pos=(x,y,z) message='…'")
        parts = _kv_split(detail)
        try:
            from_name = parts["from"]
            chat_type = int(parts.get("type", "1"))
            audible = int(parts.get("audible", "0"))
            message = parts.get("message", "")
        except (KeyError, ValueError):
            return None
        return ChatLocal(
            region_handle=handle,
            from_name=from_name,
            chat_type=chat_type,
            audible=audible,
            message=message,
        )

    @staticmethod
    def _parse_chat_im(detail: str, handle: int) -> ChatIM | None:
        # SessionEvent("chat.im", "from='Name' dialog=N to=<uuid> message='…'")
        from uuid import UUID as _UUID

        parts = _kv_split(detail)
        try:
            from_name = parts["from"]
            dialog = int(parts.get("dialog", "0"))
            to_agent_id = _UUID(parts["to"])
            message = parts.get("message", "")
        except (KeyError, ValueError):
            return None
        return ChatIM(
            region_handle=handle,
            from_agent_name=from_name,
            to_agent_id=to_agent_id,
            message=message,
            dialog=dialog,
        )

    @staticmethod
    def _parse_chat_outbound(detail: str, handle: int) -> ChatOutbound | None:
        parts = _kv_split(detail)
        try:
            chat_type = int(parts.get("type", "1"))
            channel = int(parts.get("channel", "0"))
            message = parts.get("message", "")
        except ValueError:
            return None
        return ChatOutbound(
            region_handle=handle,
            chat_type=chat_type,
            channel=channel,
            message=message,
        )

def _kv_split(detail: str) -> dict[str, str]:
    """key=value tokenize using shlex so quoted values (with spaces) work.

    The session encodes string values with Python ``repr()`` (single or
    double quoted). shlex strips matching quotes for us. Tokens without
    ``=`` are ignored.
    """
    import shlex

    try:
        tokens = shlex.split(detail, posix=True)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        out[key] = value
    return out


__all__ = [
    "Bus",
    "NoHandlerError",
    "WorldClient",
    "WorldClientError",
    "region_handle_for_session",
    "region_handle_from_meters",
]
