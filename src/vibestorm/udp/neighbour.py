"""A child circuit to the region next door.

`EnableSimulator` names an address and a region handle, and a viewer that
wants the world to continue past its own region's edge dials it. The circuit
it opens is a *child* one: the agent is root somewhere else, so this sends
`UseCircuitCode` and nothing more. `CompleteAgentMovement` is what makes an
agent root in a region, and sending it here would move the avatar.

**UDP alone is not enough, and the reason is not on the wire.** A circuit
that connects, answers every `RegionHandshake` and acks everything it is
sent still receives no terrain -- only the region name, the water height and
a trickle of cloud. `EnableSimulator` arrives beside an
`EstablishAgentCommunication` carrying the neighbour's *seed capability*,
and the neighbour will not send its initial data until that URL has been
POSTed to. OpenSim's `ScenePresence.SendInitialData` returns early unless
both `m_gotRegionHandShake` and `Caps.CapsFlags.SentSeeds` are set, and
`SentSeeds` is set at the end of `BunchOfCaps.SeedCapRequest` -- the handler
for that POST. So whoever opens one of these must fetch the neighbour's seed
caps too; this class cannot, because it has no HTTP.

Measured against the local grid on 2026-09-06, with a second region
(`Vibestorm North`) standing beside the first. Forty seconds on the child
circuit, without the seed fetch and with it:

                          no seed fetch    seed fetch
      LayerData                       3            14
      ParcelOverlay                   0             4
      StartPingCheck                  8             7
      PacketAck                       2             2
      RegionHandshake                 1             1
      CoarseLocationUpdate            1             1

      terrain patches                 0           256
      layer types             cloud only   land + cloud

Not a difference of degree. Without the POST the region sends cloud and
nothing else; with it, the whole 256x256 heightmap arrives in eleven
packets. (A bare circuit that also never *answers* the handshake sees it
twenty-nine times in thirty seconds, because an unanswered handshake is
resent -- which is why the reply below goes out every time, not once.)

**Its objects arrive too, and they are worth having.** With three prims
standing in `Vibestorm North`, a child circuit to it received six object
messages in forty seconds -- `ObjectUpdate`, `ObjectUpdateCompressed` and
`ObjectUpdateCached`, two of each -- so a simulator does describe its
contents to an agent who is only looking. They are folded into a
`WorldView` of this circuit's own rather than the root region's, because
local ids are assigned per region: object 42 next door and object 42
underfoot are two different prims, and merging the two dictionaries
silently loses one of them.

`SendInitialData` also waits four heartbeats past both its gates on
purpose, so the pause before any of this starts is the simulator being
careful rather than the client being wrong. Pinned in
`test/test_opensim_source_pins.py`, along with the seed-capability chain
above.

Deliberately *not* a `LiveCircuitSession`. That class logs in, dresses the
avatar, drives the camera, fetches capabilities and keeps a `WorldView`; none
of that has any meaning on a circuit whose agent is somewhere else, and
threading a "child" flag through it would put a second, quieter shape inside
the one piece of code the whole client depends on. This is a small object that
answers what it is asked and remembers the terrain.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    MessageDecodeError,
    encode_agent_throttle,
    encode_complete_ping_check,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_request_multiple_objects,
    encode_use_circuit_code,
    parse_layer_data,
    parse_object_update_cached,
    parse_packet_ack,
    parse_region_handshake,
    parse_start_ping_check,
)
from vibestorm.udp.packet import LL_RELIABLE_FLAG, PacketView, build_packet, split_packet
from vibestorm.udp.reliable import PendingReliable, remember
from vibestorm.udp.zerocode import decode_zerocode, encode_zerocode
from vibestorm.world.models import WorldView
from vibestorm.world.terrain import RegionHeightmap, TerrainDecodeError
from vibestorm.world.updater import WorldUpdater

#: How many sequence numbers to hold before sending them back as one
#: `PacketAck`. The root circuit does the same; a child gets a burst of
#: terrain on connect and acking each packet separately would answer a burst
#: with a burst.
ACK_BATCH = 10

#: The object messages a child circuit is worth listening to. Measured: with
#: one prim standing in `Vibestorm North`, a child circuit to it received
#: `ObjectUpdate` -- so a simulator does describe its contents to an agent
#: who is only looking. Everything here goes to a `WorldUpdater` unchanged,
#: because a neighbouring region's objects arrive in exactly the same shapes
#: as the ones underfoot.
OBJECT_MESSAGES = frozenset(
    {
        "ObjectUpdate",
        "ObjectUpdateCompressed",
        "ObjectUpdateCached",
        "ImprovedTerseObjectUpdate",
        "KillObject",
    }
)

#: How many local ids fit in one `RequestMultipleObjects`. Never reached
#: from here: `ObjectUpdateCached` counts its own blocks in a U8 too, so one
#: message can never name more ids than one request can carry. Kept as a
#: guard rather than as a loop, because the encoder raises above it.
REQUEST_LIMIT = 255


@dataclass(slots=True)
class NeighbourCircuit:
    """One child circuit, to one neighbouring region.

    `handle` is the region handle `EnableSimulator` gave, which is also where
    the region sits: its high word is the region's x in metres and its low
    word its y, so the offset from the region the avatar is in is arithmetic
    rather than a lookup. See `offset_from`.
    """

    handle: int
    address: tuple[str, int]
    agent_id: UUID
    session_id: UUID
    circuit_code: int
    dispatcher: MessageDispatcher

    heightmap: RegionHeightmap = field(default_factory=RegionHeightmap)
    region_name: str = ""
    water_height: float | None = None
    #: The region's own four ground textures and the elevation band each
    #: covers, straight out of its handshake. Asset ids, so the *root*
    #: region's GetTexture capability fetches them like any other asset --
    #: a neighbour's ground does not need a second texture pipeline, only
    #: for someone to notice these exist.
    terrain_detail: tuple[UUID, UUID, UUID, UUID] | None = None
    terrain_start_height: tuple[float, float, float, float] | None = None
    terrain_height_range: tuple[float, float, float, float] | None = None
    #: Counted rather than logged: a child circuit is quiet and the useful
    #: question about one is "did anything arrive at all".
    received: Counter[str] = field(default_factory=Counter)
    handshakes_seen: int = 0
    terrain_packets: int = 0

    #: This region's objects, in *its* local-id space. Deliberately a world
    #: of its own rather than a corner of the root region's: local ids are
    #: assigned per region, so object 42 here and object 42 underfoot are two
    #: different prims and merging the two dictionaries silently loses one.
    world_view: WorldView = field(default_factory=WorldView)
    world_updater: WorldUpdater = field(init=False, repr=False)
    object_messages: int = 0

    started: bool = False
    next_sequence: int = 1
    queued_acks: list[int] = field(default_factory=list)

    #: The reliable packets this circuit has sent and not seen acked. A child
    #: circuit sends three -- `UseCircuitCode`, `AgentThrottle` and a
    #: `RegionHandshakeReply` per handshake -- and losing the first means the
    #: region next door never opens at all, which shows up as a neighbour that
    #: is simply missing.
    pending_reliable: dict[int, PendingReliable] = field(default_factory=dict)
    reliable_resends: int = 0
    reliable_abandoned: int = 0

    def __post_init__(self) -> None:
        self.world_updater = WorldUpdater(self.world_view)

    @property
    def region_x_meters(self) -> int:
        return (self.handle >> 32) & 0xFFFFFFFF

    @property
    def region_y_meters(self) -> int:
        return self.handle & 0xFFFFFFFF

    def offset_from(self, handle: int) -> tuple[float, float]:
        """Where this region's origin sits, in the frame of region `handle`.

        Metres, and signed: the region directly north of a 256 m region is at
        (0, 256). Everything drawn from this circuit is drawn at its own
        region coordinates plus this.
        """
        return (
            float(self.region_x_meters - ((handle >> 32) & 0xFFFFFFFF)),
            float(self.region_y_meters - (handle & 0xFFFFFFFF)),
        )

    # ------------------------------------------------------------- outbound

    def start(self) -> list[bytes]:
        """`UseCircuitCode`, and nothing else. Idempotent."""
        if self.started:
            return []
        self.started = True
        return [
            self._packet(
                encode_use_circuit_code(
                    self.circuit_code, self.session_id, self.agent_id
                ),
                reliable=True,
                label="UseCircuitCode",
            ),
            self._packet(
                encode_agent_throttle(
                    self.agent_id, self.session_id, self.circuit_code
                ),
                reliable=True,
                label="AgentThrottle",
            ),
        ]

    def _packet(
        self,
        message: bytes,
        *,
        reliable: bool = False,
        zerocoded: bool = False,
        label: str = "",
    ) -> bytes:
        sequence = self.next_sequence
        self.next_sequence += 1
        packet = build_packet(
            message,
            sequence=sequence,
            flags=LL_RELIABLE_FLAG if reliable else 0,
        )
        if zerocoded:
            packet = encode_zerocode(packet)
        if reliable:
            # No time to record: this circuit has no clock, and everything it
            # does is driven by a packet arriving rather than by a tick. The
            # sweep starts the clock the first time it sees one, which is what
            # `PendingReliable.sent_at` being optional is for.
            dropped = remember(
                self.pending_reliable, sequence, label=label, packet=packet, now=None
            )
            self.reliable_abandoned += len(dropped)
        return packet

    def drain_acks(self) -> list[bytes]:
        """Whatever acks are owed, as at most one packet."""
        if not self.queued_acks:
            return []
        owed = tuple(self.queued_acks)
        self.queued_acks = []
        return [self._packet(encode_packet_ack(owed))]

    # -------------------------------------------------------------- inbound

    def handle_incoming(self, payload: bytes) -> list[bytes]:
        """One packet in, whatever it is owed out.

        Never raises on the packet: a neighbour is a nicety, and a circuit
        that took the viewer down because the region next door sent something
        odd would be worse than no neighbour at all. Undecodable packets are
        counted and dropped.
        """
        try:
            view = _view(payload)
        except ValueError:
            self.received["<undecodable>"] += 1
            return []

        # Acks reach this circuit two ways -- appended to the tail of any
        # packet, or as a `PacketAck` message -- and until this it read
        # neither. `PacketAck` was counted in `received` and thrown away, so
        # every reliable packet this circuit sent stayed unacknowledged for
        # ever as far as it knew, which is fine while nothing resends and
        # becomes a burst of five the moment something does.
        for ack in view.appended_acks:
            self.pending_reliable.pop(ack, None)

        if view.header.is_reliable:
            self.queued_acks.append(view.header.sequence)

        try:
            dispatched = self.dispatcher.dispatch(view.message)
        except Exception:  # noqa: BLE001 - see the docstring
            self.received["<unknown message>"] += 1
            return self._acks_if_full()

        name = dispatched.summary.name
        self.received[name] += 1
        if name == "PacketAck":
            try:
                for ack in parse_packet_ack(dispatched).packets:
                    self.pending_reliable.pop(ack, None)
            except (MessageDecodeError, ValueError):
                self.received["PacketAck:undecodable"] += 1
            return self._acks_if_full()
        replies: list[bytes] = []
        try:
            if name == "RegionHandshake":
                replies = self._on_handshake(dispatched)
            elif name == "LayerData":
                self._on_layer_data(dispatched)
            elif name == "StartPingCheck":
                replies = self._on_ping(dispatched)
            elif name in OBJECT_MESSAGES:
                replies = self._on_object_message(name, dispatched)
        except (MessageDecodeError, TerrainDecodeError, ValueError):
            self.received[f"{name}:undecodable"] += 1
        return replies + self._acks_if_full()

    def drain_resends(self, now: float) -> list[bytes]:
        """Reliable packets this circuit sent and never saw acked, sent again.

        Same rules as the root circuit's, and the same reason: OpenSim resends
        for ever and this side used to resend never. A lost `UseCircuitCode`
        here does not degrade the neighbour, it means there is no neighbour --
        the region next door simply never appears, and nothing says why.
        """
        packets: list[bytes] = []
        for sequence, pending in list(self.pending_reliable.items()):
            if not pending.due(now):
                continue
            if pending.spent:
                del self.pending_reliable[sequence]
                self.reliable_abandoned += 1
                continue
            packets.append(pending.going_again(now))
            self.reliable_resends += 1
        return packets

    def _acks_if_full(self) -> list[bytes]:
        return self.drain_acks() if len(self.queued_acks) >= ACK_BATCH else []

    def _on_handshake(self, dispatched: object) -> list[bytes]:
        handshake = parse_region_handshake(dispatched)
        self.handshakes_seen += 1
        self.region_name = handshake.sim_name
        self.water_height = handshake.water_height
        self.terrain_detail = handshake.terrain_detail
        self.terrain_start_height = handshake.terrain_start_height
        self.terrain_height_range = handshake.terrain_height_range
        # Replied to every time, not only the first. The measurement above is
        # what says why: an unanswered handshake is resent, and twenty-nine
        # copies of it arrived in thirty seconds.
        return [self._packet(
            encode_region_handshake_reply(self.agent_id, self.session_id, 0),
            reliable=True,
            zerocoded=True,
            label="RegionHandshakeReply",
        )]

    def _on_layer_data(self, dispatched: object) -> None:
        layer = parse_layer_data(dispatched)
        before = self.heightmap.revision
        self.heightmap.apply_layer_blob(layer.data)
        if self.heightmap.revision != before:
            self.terrain_packets += 1

    def _on_object_message(self, name: str, dispatched: object) -> list[bytes]:
        self.world_updater.apply_dispatch(dispatched)
        self.object_messages += 1
        if name != "ObjectUpdateCached":
            return []
        # A cached update is the simulator saying "you already know these".
        # A circuit that has just opened knows nothing, so every one of them
        # is a miss and has to be asked for in full, or the region's contents
        # are named and never described.
        local_ids = [obj.local_id for obj in parse_object_update_cached(dispatched).objects]
        if not local_ids:
            return []
        return [
            self._packet(
                encode_request_multiple_objects(
                    self.agent_id, self.session_id, local_ids[:REQUEST_LIMIT]
                ),
                reliable=True,
                zerocoded=True,
                label="RequestMultipleObjects",
            )
        ]

    def _on_ping(self, dispatched: object) -> list[bytes]:
        ping = parse_start_ping_check(dispatched)
        return [self._packet(encode_complete_ping_check(ping.ping_id))]


def _view(payload: bytes) -> PacketView:
    return split_packet(decode_zerocode(payload))


__all__ = ["ACK_BATCH", "OBJECT_MESSAGES", "REQUEST_LIMIT", "NeighbourCircuit"]
