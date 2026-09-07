"""The child circuit to the region next door.

`NeighbourCircuit` is the only piece of this client that talks to a
simulator the avatar is not in, and it is the piece most likely to be
handed something unexpected: a region running a different build, a packet
for a message this client has never parsed, a handshake resent four times
because an ack went missing. Its contract is therefore narrow and worth
pinning -- it answers what it is asked, it remembers the terrain, and it
never raises, because a neighbour is a nicety and a viewer that falls over
because the region next door said something odd is worse than a viewer with
no neighbours at all.

The live half of the story -- that none of this yields terrain until the
neighbour's *seed capability* has been POSTed to -- is measured in the
module's own docstring. It cannot be asserted here: it is a fact about
OpenSim, not about this class, and this class has no HTTP.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import parse_packet_ack
from vibestorm.udp.neighbour import ACK_BATCH, NeighbourCircuit
from vibestorm.udp.packet import LL_RELIABLE_FLAG, build_packet, split_packet
from vibestorm.udp.reliable import PENDING_RELIABLE_LIMIT, RELIABLE_RESEND_AFTER_S
from vibestorm.udp.zerocode import decode_zerocode

REPO_ROOT = Path(__file__).resolve().parents[1]

AGENT = UUID("11111111-1111-1111-1111-111111111111")
SESSION = UUID("22222222-2222-2222-2222-222222222222")
OWNER = UUID("33333333-3333-3333-3333-333333333333")
CIRCUIT_CODE = 991046347

#: (1000, 1000) and (1000, 1001) in region coordinates -- the two regions the
#: live measurement in the module docstring was taken against.
ROOT_HANDLE = (256000 << 32) | 256000
NORTH_HANDLE = (256000 << 32) | 256256

LAYER_DATA_HIGH = bytes([0x0B])
START_PING_HIGH = bytes([0x01])
REGION_HANDSHAKE_LOW = bytes([0xFF, 0xFF, 0x00, 0x94])


def _handshake_body(sim_name: bytes = b"Vibestorm North", water: float = 20.0) -> bytes:
    """A RegionHandshake body, laid out from the message template in order."""
    body = bytearray()
    body += (9).to_bytes(4, "little")            # RegionFlags
    body += bytes([13])                          # SimAccess
    body += bytes([len(sim_name)]) + sim_name    # SimName
    body += OWNER.bytes                          # SimOwner
    body += bytes([0])                           # IsEstateManager
    body += pack("<f", water)                    # WaterHeight
    body += pack("<f", 1.0)                      # BillableFactor
    body += UUID(int=1).bytes                    # CacheID
    for index in range(8):                       # TerrainBase0..3, Detail0..3
        body += UUID(int=0xB0 + index).bytes
    for value in (10.0, 11.0, 12.0, 13.0):       # TerrainStartHeight00..11
        body += pack("<f", value)
    for value in (60.0, 61.0, 62.0, 63.0):       # TerrainHeightRange00..11
        body += pack("<f", value)
    body += UUID(int=9).bytes                    # RegionInfo2.RegionID
    return bytes(body)


def _land_blob(
    dc_offset: float = 21.0,
    patch_x: int = 0,
    patch_y: int = 0,
    layer_type: int | None = None,
) -> bytes:
    """One all-zero patch: a flat 16x16 square, `dc_offset` deciding how high.

    The layer type that decides whether this is ground lives *in the blob*,
    not in the LayerID byte beside it, so it is settable here.
    """
    from vibestorm.world.terrain import END_OF_PATCHES, LAYER_TYPE_LAND, BitPackWriter

    writer = BitPackWriter()
    writer.pack_bits(264, 16)                 # stride
    writer.pack_bits(16, 8)                   # patch size
    writer.pack_bits(LAYER_TYPE_LAND if layer_type is None else layer_type, 8)
    writer.pack_bits(0x36, 8)                 # quant_wbits: prequant 5, word 8
    writer.pack_float(dc_offset)
    writer.pack_bits(1, 16)                   # range
    writer.pack_bits(((patch_x & 0x1F) << 5) | (patch_y & 0x1F), 10)
    writer.pack_bits(0b10, 2)                 # ZERO_EOB: every coefficient zero
    writer.pack_bits(END_OF_PATCHES, 8)
    return writer.to_bytes()


OBJECT_UPDATE_HIGH = bytes([0x0C])
OBJECT_UPDATE_CACHED_HIGH = bytes([0x0E])
KILL_OBJECT_HIGH = bytes([0x10])


def _object_update_message(local_id: int, full_id: UUID, x: float = 1.0) -> bytes:
    """One prim in one ObjectUpdate, laid out from the message template."""
    body = (
        (7).to_bytes(8, "little")            # RegionHandle
        + (42).to_bytes(2, "little")         # TimeDilation
        + bytes([1])                         # one ObjectData block
        + local_id.to_bytes(4, "little")
        + bytes([3])                         # State
        + full_id.bytes
        + (99).to_bytes(4, "little")         # CRC
        + bytes([9, 3, 1])                   # PCode, Material, ClickAction
        + pack("<fff", 1.0, 2.0, 3.0)        # Scale
        + bytes([60])                        # ObjectData length
        + pack("<fff", x, 2.0, 3.0)          # position
        + (b"\x00" * 28)
        + pack("<ffff", 0.0, 0.0, 0.0, 1.0)  # rotation
        + (b"\x00" * 4)
        + (0).to_bytes(4, "little")          # ParentID
        + (5).to_bytes(4, "little")          # UpdateFlags
        + (b"\x00" * 23)                     # shape block
        + (0).to_bytes(2, "little")          # TextureEntry length
        + bytes([0])                         # TextureAnim length
        + (0).to_bytes(2, "little")          # NameValue length
        + (0).to_bytes(2, "little")          # Data length
        + bytes([0])                         # Text length
        + (b"\x00" * 4)
        + bytes([0, 0, 0])
        + (b"\x00" * 66)
    )
    return OBJECT_UPDATE_HIGH + body


def _object_update_cached_message(local_ids: tuple[int, ...]) -> bytes:
    body = bytearray((7).to_bytes(8, "little") + (42).to_bytes(2, "little"))
    body += bytes([len(local_ids)])
    for local_id in local_ids:
        body += local_id.to_bytes(4, "little")
        body += (0x11111111).to_bytes(4, "little")   # CRC
        body += (5).to_bytes(4, "little")            # UpdateFlags
    return OBJECT_UPDATE_CACHED_HIGH + bytes(body)


IMPROVED_TERSE_HIGH = bytes([0x0F])


def _kill_object_message(local_ids: tuple[int, ...]) -> bytes:
    body = bytes([len(local_ids)])
    for local_id in local_ids:
        body += local_id.to_bytes(4, "little")
    return KILL_OBJECT_HIGH + body


def _terse_update_message(local_id: int, x: float = 5.0) -> bytes:
    """One non-avatar prim in one ImprovedTerseObjectUpdate.

    The Data blob is 44 bytes for a prim -- the parser recognises 44 and 60
    and treats anything else as truncated, so the length is load-bearing.
    """
    data = (
        local_id.to_bytes(4, "little")
        + bytes([0])                          # State
        + bytes([0])                          # not an avatar
        + pack("<fff", x, 2.0, 3.0)           # position
        + (b"\x80\x00" * 3)                   # velocity, packed U16s
        + (b"\x80\x00" * 3)                   # acceleration
        + (b"\x80\x00" * 4)                   # rotation
        + (b"\x80\x00" * 3)                   # angular velocity
    )
    assert len(data) == 44, len(data)
    body = (
        (7).to_bytes(8, "little")             # RegionHandle
        + (42).to_bytes(2, "little")          # TimeDilation
        + bytes([1])                          # one ObjectData block
        + bytes([len(data)])
        + data
        + (0).to_bytes(2, "little")           # TextureEntry length
    )
    return IMPROVED_TERSE_HIGH + body


def _layer_data_message(blob: bytes, layer_type: int) -> bytes:
    return (
        LAYER_DATA_HIGH
        + bytes([layer_type])
        + len(blob).to_bytes(2, "little")
        + blob
    )


class NeighbourCircuitTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Parsing the message template is most of the cost of this file, and
        # nothing here mutates the index.
        cls.dispatcher = MessageDispatcher.from_repo_root(REPO_ROOT)

    def circuit(self) -> NeighbourCircuit:
        return NeighbourCircuit(
            handle=NORTH_HANDLE,
            address=("127.0.0.1", 9001),
            agent_id=AGENT,
            session_id=SESSION,
            circuit_code=CIRCUIT_CODE,
            dispatcher=self.dispatcher,
        )

    def inbound(self, message: bytes, *, sequence: int = 1, reliable: bool = True) -> bytes:
        return build_packet(
            message,
            sequence=sequence,
            flags=LL_RELIABLE_FLAG if reliable else 0,
        )

    def names(self, packets: list[bytes]) -> list[str]:
        out = []
        for packet in packets:
            view = split_packet(decode_zerocode(packet))
            out.append(self.dispatcher.dispatch(view.message).summary.name)
        return out


class WhereTheRegionSitsTests(NeighbourCircuitTestCase):
    def test_the_handle_says_where_the_region_is(self) -> None:
        circuit = self.circuit()
        self.assertEqual(circuit.region_x_meters, 256000)
        self.assertEqual(circuit.region_y_meters, 256256)

    def test_the_region_due_north_is_offset_by_one_region(self) -> None:
        # The whole point of the offset: everything this circuit reports is
        # in its own region's coordinates, and has to be drawn 256 m north.
        self.assertEqual(self.circuit().offset_from(ROOT_HANDLE), (0.0, 256.0))

    def test_the_offset_is_signed(self) -> None:
        # Looking the other way round. An unsigned subtraction wraps to about
        # four billion here, which draws the neighbour off the edge of the
        # solar system rather than one region south.
        south = NeighbourCircuit(
            handle=ROOT_HANDLE,
            address=("127.0.0.1", 9000),
            agent_id=AGENT,
            session_id=SESSION,
            circuit_code=CIRCUIT_CODE,
            dispatcher=self.dispatcher,
        )
        self.assertEqual(south.offset_from(NORTH_HANDLE), (0.0, -256.0))

    def test_east_and_west_move_along_x(self) -> None:
        east = NeighbourCircuit(
            handle=(256256 << 32) | 256000,
            address=("127.0.0.1", 9002),
            agent_id=AGENT,
            session_id=SESSION,
            circuit_code=CIRCUIT_CODE,
            dispatcher=self.dispatcher,
        )
        self.assertEqual(east.offset_from(ROOT_HANDLE), (256.0, 0.0))


class OpeningTheCircuitTests(NeighbourCircuitTestCase):
    def test_it_opens_with_a_circuit_code_and_a_throttle(self) -> None:
        circuit = self.circuit()
        packets = circuit.start()
        self.assertEqual(
            self.names(packets), ["UseCircuitCode", "AgentThrottle"]
        )

    def test_both_opening_packets_are_reliable(self) -> None:
        # Nothing else in this class retries, so if the first packet is lost
        # unreliably the circuit is simply never opened and the region next
        # door stays dark.
        for packet in self.circuit().start():
            header = split_packet(decode_zerocode(packet)).header
            self.assertTrue(header.is_reliable)

    def test_it_never_says_hello_twice(self) -> None:
        # `EnableSimulator` can arrive more than once for the same region --
        # on a second crossing, or simply resent -- and a second
        # UseCircuitCode on a live circuit is a new session to the simulator.
        circuit = self.circuit()
        self.assertEqual(len(circuit.start()), 2)
        self.assertEqual(circuit.start(), [])

    def test_it_does_not_move_the_avatar(self) -> None:
        # CompleteAgentMovement is what makes an agent root in a region. If
        # this circuit ever sent one, opening a neighbour would teleport the
        # avatar into it.
        self.assertNotIn("CompleteAgentMovement", self.names(self.circuit().start()))

    def test_every_packet_gets_its_own_sequence_number(self) -> None:
        circuit = self.circuit()
        packets = circuit.start() + circuit.drain_acks()
        circuit.queued_acks.append(7)
        packets += circuit.drain_acks()
        sequences = [split_packet(decode_zerocode(p)).header.sequence for p in packets]
        self.assertEqual(sequences, sorted(set(sequences)))


class AnsweringTheRegionTests(NeighbourCircuitTestCase):
    def test_a_handshake_names_the_region_and_its_water(self) -> None:
        circuit = self.circuit()
        circuit.handle_incoming(
            self.inbound(REGION_HANDSHAKE_LOW + _handshake_body())
        )
        self.assertEqual(circuit.region_name, "Vibestorm North")
        self.assertEqual(circuit.water_height, 20.0)
        self.assertEqual(circuit.handshakes_seen, 1)

    def test_a_handshake_names_the_region_s_own_ground_textures(self) -> None:
        # A neighbour is drawn as shaded ground until these arrive. They are
        # ordinary asset ids, so the region the avatar is in fetches them --
        # but only if somebody keeps them off the handshake first.
        circuit = self.circuit()
        circuit.handle_incoming(
            self.inbound(REGION_HANDSHAKE_LOW + _handshake_body())
        )
        self.assertEqual(
            circuit.terrain_detail, tuple(UUID(int=0xB0 + index) for index in range(4, 8))
        )
        self.assertEqual(circuit.terrain_start_height, (10.0, 11.0, 12.0, 13.0))
        self.assertEqual(circuit.terrain_height_range, (60.0, 61.0, 62.0, 63.0))

    def test_a_region_that_has_not_spoken_names_no_textures(self) -> None:
        # None rather than four zero UUIDs: "not asked yet" and "this region
        # has no ground textures" are drawn differently.
        self.assertIsNone(self.circuit().terrain_detail)

    def test_a_handshake_is_answered(self) -> None:
        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(REGION_HANDSHAKE_LOW + _handshake_body())
        )
        self.assertIn("RegionHandshakeReply", self.names(replies))

    def test_every_handshake_is_answered_and_not_just_the_first(self) -> None:
        # Measured: an unanswered handshake was resent twenty-nine times in
        # thirty seconds. Answering only the first leaves the region resending
        # for as long as the reply keeps going missing.
        circuit = self.circuit()
        for sequence in (1, 2, 3):
            replies = circuit.handle_incoming(
                self.inbound(
                    REGION_HANDSHAKE_LOW + _handshake_body(), sequence=sequence
                )
            )
            self.assertIn("RegionHandshakeReply", self.names(replies))
        self.assertEqual(circuit.handshakes_seen, 3)

    def test_the_handshake_reply_is_sent_reliably(self) -> None:
        # The simulator latches `m_gotRegionHandShake` on the first reply it
        # receives, and that latch is half of what unblocks the region's
        # initial data. A reply sent unreliably and then dropped is never
        # retried, and the region stays a name with no ground under it.
        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(REGION_HANDSHAKE_LOW + _handshake_body())
        )
        self.assertEqual(len(replies), 1)
        self.assertTrue(split_packet(decode_zerocode(replies[0])).header.is_reliable)

    def test_a_ping_is_answered_with_the_id_it_carried(self) -> None:
        from vibestorm.udp.messages import parse_complete_ping_check

        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(START_PING_HIGH + bytes([37]) + (0).to_bytes(4, "little"))
        )
        pongs = [
            parse_complete_ping_check(
                self.dispatcher.dispatch(split_packet(decode_zerocode(p)).message)
            )
            for p in replies
        ]
        self.assertEqual([pong.ping_id for pong in pongs], [37])


class RememberingTheTerrainTests(NeighbourCircuitTestCase):
    def test_a_land_patch_reaches_the_heightmap(self) -> None:
        from vibestorm.world.terrain import LAYER_TYPE_LAND

        circuit = self.circuit()
        circuit.handle_incoming(
            self.inbound(_layer_data_message(_land_blob(21.0), LAYER_TYPE_LAND))
        )
        self.assertEqual(circuit.terrain_packets, 1)
        self.assertEqual(circuit.heightmap.patch_count, 1)
        # An all-zero patch is flat, and the decoder puts it half a metre of
        # quantisation range above its DC offset. What matters is that the
        # ground the neighbour describes is the ground that is remembered, so
        # the assertion is on the difference rather than on that constant.
        first = circuit.heightmap.height_at(8.0, 8.0)
        self.assertAlmostEqual(circuit.heightmap.height_at(2.0, 13.0), first, places=6)

        higher = self.circuit()
        higher.handle_incoming(
            self.inbound(_layer_data_message(_land_blob(31.0), LAYER_TYPE_LAND))
        )
        self.assertAlmostEqual(higher.heightmap.height_at(8.0, 8.0) - first, 10.0, places=3)

    def test_cloud_is_not_counted_as_terrain(self) -> None:
        # Measured: a child circuit with no seed fetch receives layer type
        # 0x37 (cloud) and nothing else, so "did any terrain arrive" has to
        # mean land specifically -- otherwise the count says yes to a circuit
        # that in fact got no ground at all.
        from vibestorm.world.terrain import LAYER_TYPE_CLOUD

        circuit = self.circuit()
        circuit.handle_incoming(
            self.inbound(
                _layer_data_message(
                    _land_blob(layer_type=LAYER_TYPE_CLOUD), LAYER_TYPE_CLOUD
                )
            )
        )
        self.assertEqual(circuit.terrain_packets, 0)
        self.assertEqual(circuit.received["LayerData"], 1)


class WhatTheRegionHoldsTests(NeighbourCircuitTestCase):
    """A neighbour's objects, in a world of the neighbour's own."""

    PRIM = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_an_object_update_reaches_this_region_s_world(self) -> None:
        circuit = self.circuit()
        circuit.handle_incoming(self.inbound(_object_update_message(4242, self.PRIM)))
        self.assertIn(self.PRIM, circuit.world_view.objects)
        self.assertEqual(circuit.object_messages, 1)

    def test_two_regions_keep_their_own_objects(self) -> None:
        # Local ids are assigned per region, so object 42 next door and
        # object 42 underfoot are two different prims. One dictionary for
        # both silently loses one of them.
        north, east = self.circuit(), self.circuit()
        other = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
        north.handle_incoming(self.inbound(_object_update_message(42, self.PRIM, x=10.0)))
        east.handle_incoming(self.inbound(_object_update_message(42, other, x=20.0)))

        self.assertEqual(list(north.world_view.objects), [self.PRIM])
        self.assertEqual(list(east.world_view.objects), [other])
        self.assertAlmostEqual(north.world_view.objects[self.PRIM].position[0], 10.0)
        self.assertAlmostEqual(east.world_view.objects[other].position[0], 20.0)

    def test_a_cached_update_is_asked_for_in_full(self) -> None:
        # A cached update is the simulator saying "you already know these".
        # A circuit that has just opened knows nothing, so every one is a
        # miss -- and unasked, the region's contents are named and never
        # described.
        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(_object_update_cached_message((7, 9, 11)))
        )
        self.assertEqual(self.names(replies), ["RequestMultipleObjects"])

    def test_the_request_names_the_ids_that_were_cached(self) -> None:
        # No parser for this one -- it is a message this client only ever
        # sends -- so the assertion reads the wire bytes: a U8 count, then a
        # CacheMissType byte and a little-endian U32 per object.
        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(_object_update_cached_message((7, 9, 11)))
        )
        body = self.dispatcher.dispatch(
            split_packet(decode_zerocode(replies[0])).message
        ).body
        objects = body[32:]  # past AgentID and SessionID
        self.assertEqual(objects[0], 3)
        self.assertEqual(
            [
                int.from_bytes(objects[1 + index * 5 + 1 : 1 + index * 5 + 5], "little")
                for index in range(3)
            ],
            [7, 9, 11],
        )

    def test_a_cached_update_naming_nothing_asks_for_nothing(self) -> None:
        # The encoder refuses an empty request, so an empty cached update has
        # to be dropped here rather than turned into one.
        circuit = self.circuit()
        self.assertEqual(
            circuit.handle_incoming(self.inbound(_object_update_cached_message(()))),
            [],
        )

    def test_one_request_always_holds_what_one_cached_update_named(self) -> None:
        # Both counts are U8s, so the limit is reachable but never exceeded.
        from vibestorm.udp.neighbour import REQUEST_LIMIT

        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(_object_update_cached_message(tuple(range(1, 256))))
        )
        self.assertEqual(self.names(replies), ["RequestMultipleObjects"])
        self.assertEqual(REQUEST_LIMIT, 255)

    def test_the_request_for_the_missing_prims_is_sent_reliably(self) -> None:
        # The one packet in this whole exchange that cannot be re-derived. A
        # cached update is sent once; if the request it provokes is dropped,
        # nothing asks again and those prims are named and never described --
        # a neighbouring region with holes in it and no error anywhere.
        circuit = self.circuit()
        replies = circuit.handle_incoming(
            self.inbound(_object_update_cached_message((7, 9, 11)))
        )
        self.assertTrue(split_packet(decode_zerocode(replies[0])).header.is_reliable)

    def test_an_empty_cached_update_is_dropped_and_not_a_failed_encode(self) -> None:
        # Dropping it and letting the encoder raise both send nothing, so the
        # reply list cannot tell them apart. The counter can: a region that
        # says "you know about nothing" is ordinary, and logging it as an
        # undecodable message would bury the ones that matter.
        circuit = self.circuit()
        circuit.handle_incoming(self.inbound(_object_update_cached_message(())))
        self.assertEqual(
            [name for name in circuit.received if "undecodable" in name], []
        )

    def test_a_killed_prim_next_door_is_taken_off_the_map(self) -> None:
        # Without this the region next door only ever accumulates. Prims are
        # deleted, returned and walked out of view constantly, and the failure
        # is a neighbour that slowly fills with buildings nobody can see the
        # far side of.
        circuit = self.circuit()
        circuit.handle_incoming(self.inbound(_object_update_message(4242, self.PRIM)))
        circuit.handle_incoming(self.inbound(_kill_object_message((4242,))))
        self.assertEqual(circuit.world_view.objects, {})

    def test_a_prim_next_door_that_moves_moves(self) -> None:
        # Terse updates are how anything that moves reports it: vehicles,
        # physical objects, and every avatar after the first full update.
        # Dropping them leaves the region next door frozen at the moment the
        # circuit opened.
        circuit = self.circuit()
        circuit.handle_incoming(self.inbound(_terse_update_message(4242, x=5.0)))
        self.assertIn(4242, circuit.world_view.terse_objects)
        self.assertAlmostEqual(
            circuit.world_view.terse_objects[4242].position[0], 5.0, places=3
        )

    def test_an_object_update_owes_no_reply(self) -> None:
        circuit = self.circuit()
        self.assertEqual(
            circuit.handle_incoming(self.inbound(_object_update_message(1, self.PRIM))),
            [],
        )

    def test_a_truncated_object_update_does_not_raise(self) -> None:
        circuit = self.circuit()
        message = _object_update_message(1, self.PRIM)
        circuit.handle_incoming(self.inbound(message[: len(message) // 2]))
        self.assertEqual(circuit.world_view.objects, {})


class NothingGetsThroughTests(NeighbourCircuitTestCase):
    """Whatever the region next door sends, the viewer stays up."""

    BAD = (
        b"",
        b"\x00",
        b"\x00\x00\x00\x00\x00\x00",
        b"\xff" * 32,
        b"\x80\x00\x00\x00\x01\x00\xff\xff\xff\xff",
    )

    def test_rubbish_is_counted_and_dropped(self) -> None:
        circuit = self.circuit()
        for payload in self.BAD:
            with self.subTest(payload=payload.hex()):
                self.assertEqual(circuit.handle_incoming(payload), [])
        self.assertEqual(sum(circuit.received.values()), len(self.BAD))

    def test_a_truncated_handshake_does_not_raise(self) -> None:
        # A message this client knows, cut off mid-field: the dispatcher is
        # happy and the parser is not.
        circuit = self.circuit()
        body = _handshake_body()
        self.assertEqual(
            circuit.handle_incoming(
                self.inbound(REGION_HANDSHAKE_LOW + body[: len(body) // 2])
            ),
            [],
        )
        self.assertEqual(circuit.received["RegionHandshake:undecodable"], 1)
        self.assertEqual(circuit.region_name, "")

    def test_a_truncated_layer_blob_does_not_raise(self) -> None:
        from vibestorm.world.terrain import LAYER_TYPE_LAND

        circuit = self.circuit()
        blob = _land_blob()
        circuit.handle_incoming(
            self.inbound(_layer_data_message(blob[:3], LAYER_TYPE_LAND))
        )
        self.assertEqual(circuit.terrain_packets, 0)

    def test_an_unreliable_rubbish_packet_owes_no_ack(self) -> None:
        circuit = self.circuit()
        circuit.handle_incoming(b"\x00\x00\x00\x00\x01\x00")
        self.assertEqual(circuit.queued_acks, [])


class AckingTests(NeighbourCircuitTestCase):
    def test_reliable_packets_are_acked_in_batches(self) -> None:
        # One ack per terrain packet answers a burst with a burst; the
        # simulator sends the whole heightmap the moment the circuit opens.
        circuit = self.circuit()
        sent: list[bytes] = []
        for sequence in range(1, ACK_BATCH):
            sent += circuit.handle_incoming(
                self.inbound(START_PING_HIGH + bytes([1]) + (0).to_bytes(4, "little"),
                             sequence=sequence)
            )
        self.assertNotIn("PacketAck", self.names(sent))
        self.assertEqual(len(circuit.queued_acks), ACK_BATCH - 1)

        sent = circuit.handle_incoming(
            self.inbound(START_PING_HIGH + bytes([1]) + (0).to_bytes(4, "little"),
                         sequence=ACK_BATCH)
        )
        self.assertIn("PacketAck", self.names(sent))
        self.assertEqual(circuit.queued_acks, [])

    def test_the_ack_carries_the_sequence_numbers_that_arrived(self) -> None:
        circuit = self.circuit()
        for sequence in (11, 22, 33):
            circuit.handle_incoming(
                self.inbound(REGION_HANDSHAKE_LOW + _handshake_body(), sequence=sequence)
            )
        packets = circuit.drain_acks()
        acks = [
            parse_packet_ack(
                self.dispatcher.dispatch(split_packet(decode_zerocode(p)).message)
            )
            for p in packets
        ]
        self.assertEqual([tuple(a.packets) for a in acks], [(11, 22, 33)])

    def test_an_unreliable_packet_is_not_acked(self) -> None:
        circuit = self.circuit()
        circuit.handle_incoming(
            self.inbound(REGION_HANDSHAKE_LOW + _handshake_body(), reliable=False)
        )
        self.assertEqual(circuit.queued_acks, [])
        self.assertEqual(circuit.drain_acks(), [])

    def test_draining_nothing_sends_nothing(self) -> None:
        self.assertEqual(self.circuit().drain_acks(), [])


if __name__ == "__main__":
    unittest.main()


class TheChildCircuitResendsTooTests(NeighbourCircuitTestCase):
    """A child circuit sent three reliable packets and forgot all of them.

    `UseCircuitCode`, `AgentThrottle`, and a `RegionHandshakeReply` for every
    handshake. Losing the first does not degrade the neighbour, it means there
    is no neighbour -- the region next door never appears and nothing says
    why, which is indistinguishable from a viewer that does not draw
    neighbours at all.

    It also read no acks. `PacketAck` was counted in `received` and thrown
    away, and appended acks were never looked at, so as far as this circuit
    knew nothing it sent had ever been acknowledged. Harmless while nothing
    resends; a burst of five per packet the moment something does. The ack
    handling and the resends had to arrive together.
    """

    def setUp(self) -> None:
        self.child = self.circuit()

    def test_what_it_sends_is_remembered(self) -> None:
        self.child.start()
        labels = {p.label for p in self.child.pending_reliable.values()}
        self.assertEqual(labels, {"UseCircuitCode", "AgentThrottle"})

    def test_an_unacked_one_goes_out_again(self) -> None:
        self.child.start()
        (first, *_) = sorted(self.child.pending_reliable)
        self.assertEqual(self.child.drain_resends(100.0), [])
        again = self.child.drain_resends(100.0 + RELIABLE_RESEND_AFTER_S)
        self.assertEqual(len(again), 2)
        sequences = [split_packet(decode_zerocode(p)).header.sequence for p in again]
        self.assertIn(first, sequences)

    def test_and_it_carries_the_resent_bit_and_its_own_sequence(self) -> None:
        self.child.start()
        before = sorted(self.child.pending_reliable)
        self.child.drain_resends(100.0)
        again = self.child.drain_resends(101.0)
        self.assertEqual(
            sorted(split_packet(decode_zerocode(p)).header.sequence for p in again),
            before,
        )
        self.assertTrue(
            all(split_packet(decode_zerocode(p)).header.is_resent for p in again)
        )

    def test_a_packet_ack_stops_it_being_resent(self) -> None:
        # The message form. Until this it was counted and dropped.
        self.child.start()
        acked = sorted(self.child.pending_reliable)[0]
        self.child.handle_incoming(
            build_packet(
                bytes([0xFF, 0xFF, 0xFF, 0xFB])
                + bytes([1])
                + acked.to_bytes(4, "little"),
                sequence=500,
            )
        )
        self.assertNotIn(acked, self.child.pending_reliable)

    def test_an_appended_ack_stops_it_too(self) -> None:
        # The other channel. Reading one of the two makes a circuit that is
        # being acked perfectly look like one that is being ignored.
        self.child.start()
        acked = sorted(self.child.pending_reliable)[0]
        self.child.handle_incoming(
            build_packet(
                bytes([0xFF, 0xFF, 0x00, 0x06]) + bytes([0, 0, 0xFF, 0xFF, 0xFF]),
                sequence=501,
                appended_acks=(acked,),
            )
        )
        self.assertNotIn(acked, self.child.pending_reliable)

    def test_an_ack_clears_the_one_it_names_and_no_others(self) -> None:
        # The failure this guards is silent and total: a circuit that treats
        # any ack as an ack for everything stops resending the packet that was
        # actually lost, which is the one case the whole path exists for.
        self.child.start()
        acked, kept = sorted(self.child.pending_reliable)
        self.child.handle_incoming(
            build_packet(
                bytes([0xFF, 0xFF, 0xFF, 0xFB])
                + bytes([1])
                + acked.to_bytes(4, "little"),
                sequence=500,
            )
        )
        self.assertNotIn(acked, self.child.pending_reliable)
        self.assertIn(kept, self.child.pending_reliable)

    def test_and_an_appended_ack_is_just_as_narrow(self) -> None:
        self.child.start()
        acked, kept = sorted(self.child.pending_reliable)
        self.child.handle_incoming(
            build_packet(
                bytes([0xFF, 0xFF, 0x00, 0x06]) + bytes([0, 0, 0xFF, 0xFF, 0xFF]),
                sequence=501,
                appended_acks=(acked,),
            )
        )
        self.assertNotIn(acked, self.child.pending_reliable)
        self.assertIn(kept, self.child.pending_reliable)

    def test_it_gives_up_rather_than_talking_to_a_wall(self) -> None:
        self.child.start()
        now = 100.0
        for _ in range(30):
            now += RELIABLE_RESEND_AFTER_S
            self.child.drain_resends(now)
        self.assertEqual(self.child.pending_reliable, {})
        self.assertEqual(self.child.reliable_abandoned, 2)

    def test_the_unacked_packets_are_bounded(self) -> None:
        for _ in range(PENDING_RELIABLE_LIMIT * 3):
            self.child._packet(b"\x02\x10", reliable=True, label="Filler")
            self.assertLessEqual(
                len(self.child.pending_reliable), PENDING_RELIABLE_LIMIT
            )

    def test_making_room_drops_the_oldest_rather_than_the_newest(self) -> None:
        # Which way round matters. Dropping what was just sent means the
        # newest packet -- the one still most likely to be in flight and worth
        # retrying -- is the one that never gets a second chance, while stale
        # ones nobody is waiting on any more keep their place.
        for _ in range(PENDING_RELIABLE_LIMIT):
            self.child._packet(b"\x02\x10", reliable=True, label="Filler")
        oldest = next(iter(self.child.pending_reliable))
        newest_before = max(self.child.pending_reliable)

        self.child._packet(b"\x02\x10", reliable=True, label="TheNewOne")
        self.assertNotIn(oldest, self.child.pending_reliable)
        self.assertIn(newest_before, self.child.pending_reliable)
        self.assertEqual(
            self.child.pending_reliable[max(self.child.pending_reliable)].label,
            "TheNewOne",
        )

    def test_an_unreliable_packet_is_not_remembered(self) -> None:
        self.child._packet(b"\x02\x10", reliable=False, label="Filler")
        self.assertEqual(self.child.pending_reliable, {})

    def test_the_acks_it_sends_are_not_themselves_remembered(self) -> None:
        # `drain_acks` builds an unreliable packet, and an ack that expected an
        # ack would never stop.
        self.child.queued_acks = [1, 2, 3]
        self.child.drain_acks()
        self.assertEqual(self.child.pending_reliable, {})
