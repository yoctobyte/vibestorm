"""Every inbound message, driven through `handle_incoming` at wire level.

A mutation battery pointed at `LiveCircuitSession.handle_incoming` made each
of its thirty-six ``if dispatched.summary.name == "X"`` branches never match,
one at a time. **Eighteen survived the whole suite.** Nothing noticed when the
client stopped reacting to eighteen of the messages a region sends it.

The parsers are not the gap -- `test_udp_messages.py` covers them thoroughly,
and the handlers those branches call have their own tests too. What was
missing is the wire between them: nothing built a packet, handed it to
`handle_incoming`, and checked the effect came out. It is the same shape the
sync work found twice (`test_sync_wiring.py`), and it is worth naming once
more: **when a feature is dispatch, the piece tests and the dispatch tests are
different tests, and a lot of the first says nothing about the second.**

The bodies here are lifted from the parser tests deliberately, so that these
tests fail on *routing* rather than on decoding -- if a body is wrong, the
parser test that shares it fails too and says so more clearly.

The one that matters most for a viewer that draws a world is
`ObjectUpdateCached`: the region announces objects by id and CRC, and the
client has to ask for the ones it does not hold. That branch could stop
matching and the only symptom would be a world that never fills in.
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.packet import build_packet, split_packet
from vibestorm.udp.session import LiveCircuitSession
from vibestorm.udp.zerocode import decode_zerocode

AGENT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OBJECT = UUID("33333333-3333-3333-3333-333333333333")
OWNER = UUID("22222222-2222-2222-2222-222222222222")
SOUND = UUID("11111111-1111-1111-1111-111111111111")


class _WireCase(unittest.TestCase):
    """One session, and a way to put a real packet into it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def setUp(self) -> None:
        self.bootstrap = LoginBootstrap(
            agent_id=AGENT,
            session_id=UUID("11111111-2222-3333-4444-555555555555"),
            secure_session_id=UUID("99999999-8888-7777-6666-555555555555"),
            circuit_code=0x12345678,
            sim_ip="127.0.0.1",
            sim_port=9000,
            seed_capability="http://127.0.0.1:9000/caps/seed",
            region_x=256,
            region_y=512,
            message="ok",
        )
        self.session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        self.session.start(10.0)
        self._sequence = 100

    def deliver(self, name: str, body: bytes) -> list[bytes]:
        """Frame `body` as `name` and hand it to the session.

        The header comes from the message template rather than being written
        out as hex, so a test cannot quietly address the wrong message -- and
        so this reads as "deliver an AttachedSound" rather than as 0xFF 0x0D.
        """
        summary = self.dispatcher.index.by_name[name]
        wire = summary.wire_message_number
        width = {"High": 1, "Medium": 2, "Low": 4, "Fixed": 4}[summary.frequency]
        self._sequence += 1
        return self.session.handle_incoming(
            build_packet(wire.to_bytes(width, "big") + body, sequence=self._sequence),
            11.0,
        )

    def names_of(self, packets: list[bytes]) -> list[str]:
        """What the session sent back, by message name."""
        names = []
        for packet in packets:
            view = split_packet(packet)
            if view.header.is_zero_coded:
                view = split_packet(decode_zerocode(packet))
            names.append(self.dispatcher.dispatch(view.message).summary.name)
        return names

    def kinds(self) -> list[str]:
        return [event.kind for event in self.session.events]

    def assertRecorded(self, kind: str) -> None:
        self.assertIn(kind, self.kinds(), f"no {kind!r} event; got {sorted(set(self.kinds()))}")


class ObjectUpdateCachedTests(_WireCase):
    """The one whose absence would empty the world.

    A region announces objects it believes the viewer may already have, by
    local id and CRC. Holding none of them, the client has to ask -- and if it
    does not, those objects never arrive and never will. Nothing else in the
    protocol asks a second time.
    """

    BODY = (
        (123456789).to_bytes(8, "little")
        + (42).to_bytes(2, "little")
        + bytes([2])
        + (7).to_bytes(4, "little")
        + (0x11111111).to_bytes(4, "little")
        + (5).to_bytes(4, "little")
        + (9).to_bytes(4, "little")
        + (0x22222222).to_bytes(4, "little")
        + (6).to_bytes(4, "little")
    )

    def _requests(self, packets: list[bytes]) -> list[bytes]:
        """The RequestMultipleObjects bodies among what the session sent back.

        Zerocode-decoded first: the session sends this one zerocoded, and a
        run of zeroes in a little-endian local id is exactly what that
        encoding collapses -- so searching the raw bytes for an id finds
        nothing and says so in a way that looks like the id was never asked
        for.
        """
        out = []
        for packet in packets:
            view = split_packet(packet)
            if view.header.is_zero_coded:
                # The whole packet, then split -- not the message alone. The
                # run-length decoder has to start where the encoder did.
                view = split_packet(decode_zerocode(packet))
            if self.dispatcher.dispatch(view.message).summary.name == "RequestMultipleObjects":
                out.append(view.message)
        return out

    def test_announced_objects_are_asked_for(self) -> None:
        requests = self._requests(self.deliver("ObjectUpdateCached", self.BODY))
        self.assertEqual(len(requests), 1)
        body = requests[0]
        self.assertIn((7).to_bytes(4, "little"), body)
        self.assertIn((9).to_bytes(4, "little"), body)

    def test_an_empty_announcement_asks_for_nothing(self) -> None:
        """The floor. A branch that always sent a request would pass the test
        above and flood the region with empty ones."""
        body = (123456789).to_bytes(8, "little") + (42).to_bytes(2, "little") + bytes([0])
        self.assertEqual(self._requests(self.deliver("ObjectUpdateCached", body)), [])

    def test_a_truncated_announcement_is_survived(self) -> None:
        """The branch swallows every exception from the parser, so this is a
        test of that decision rather than of the parser: a malformed cache
        announcement must not take the session down."""
        self.deliver("ObjectUpdateCached", (123456789).to_bytes(8, "little") + b"\x00")
        self.assertIsNone(self.session.close_reason)


class CircuitTests(_WireCase):
    def test_a_closed_circuit_stops_the_session(self) -> None:
        """Without it the client keeps sending AgentUpdates at a simulator
        that has hung up, which looks like a freeze rather than a
        disconnection."""
        self.assertEqual(self.deliver("CloseCircuit", b""), [])
        self.assertEqual(self.session.close_reason, "simulator closed circuit")
        self.assertRecorded("session.closed")


class ChatTests(_WireCase):
    def test_an_instant_message_is_recorded(self) -> None:
        """Layout lifted from `test_parse_improved_instant_message_decodes_basic_im`.

        Hand-rolling it put the timestamp where the message id goes, and the
        parser said so -- which is the argument for lifting bodies rather than
        writing them from a reading of the struct.
        """
        from_name = b"Some Sender\x00"
        text = b"hello there\x00"
        body = (
            AGENT.bytes
            + self.bootstrap.session_id.bytes
            + bytes([0])  # FromGroup
            + AGENT.bytes  # ToAgentID -- ourselves; OpenSim routes it back
            + (4096).to_bytes(4, "little")  # ParentEstateID
            + OWNER.bytes  # RegionID
            + pack("<fff", 128.0, 64.0, 22.5)
            + bytes([0])  # Offline
            + bytes([0])  # Dialog
            + SOUND.bytes  # ID
            + (1700000000).to_bytes(4, "little")
            + bytes([len(from_name)])
            + from_name
            + len(text).to_bytes(2, "little")
            + text
            + (0).to_bytes(2, "little")  # empty bucket
        )
        self.deliver("ImprovedInstantMessage", body)
        self.assertRecorded("chat.im")

    def test_an_alert_is_recorded(self) -> None:
        text = b"System message\x00"
        self.deliver("AlertMessage", bytes([len(text)]) + text)
        self.assertRecorded("chat.alert")

    def test_an_agent_alert_is_recorded(self) -> None:
        text = b"You cannot do that here\x00"
        self.deliver("AgentAlertMessage", AGENT.bytes + bytes([1]) + bytes([len(text)]) + text)
        self.assertRecorded("chat.agent_alert")


class TeleportProgressTests(_WireCase):
    def test_a_teleport_start_is_recorded(self) -> None:
        self.deliver("TeleportStart", (0x10).to_bytes(4, "little"))
        self.assertTrue(
            [k for k in self.kinds() if k.startswith("teleport")],
            f"no teleport event; got {sorted(set(self.kinds()))}",
        )

    def test_a_teleport_progress_step_is_recorded(self) -> None:
        step = b"resolving destination\x00"
        body = AGENT.bytes + (0x10).to_bytes(4, "little") + bytes([len(step)]) + step
        before = len(self.session.events)
        self.deliver("TeleportProgress", body)
        self.assertGreater(len(self.session.events), before)


class SoundTests(_WireCase):
    def test_an_attached_sound_is_kept(self) -> None:
        body = SOUND.bytes + OBJECT.bytes + OWNER.bytes + pack("<f", 1.0) + bytes([0x02])
        self.deliver("AttachedSound", body)
        self.assertIsNotNone(self.session.latest_attached_sound)
        self.assertEqual(self.session.latest_attached_sound.sound_id, SOUND)

    def test_a_gain_change_is_kept(self) -> None:
        self.deliver("AttachedSoundGainChange", OBJECT.bytes + pack("<f", 0.25))
        self.assertIsNotNone(self.session.latest_attached_sound_gain_change)
        self.assertEqual(self.session.latest_attached_sound_gain_change.object_id, OBJECT)

    def test_a_preload_is_kept(self) -> None:
        self.deliver("PreloadSound", bytes([1]) + OBJECT.bytes + OWNER.bytes + SOUND.bytes)
        self.assertIsNotNone(self.session.latest_preload_sound)
        self.assertEqual(len(self.session.latest_preload_sound.entries), 1)


class ObjectAnimationTests(_WireCase):
    def test_an_object_animation_is_kept(self) -> None:
        body = OBJECT.bytes + bytes([1]) + SOUND.bytes + (9).to_bytes(4, "little", signed=True)
        self.deliver("ObjectAnimation", body)
        self.assertIsNotNone(self.session.latest_object_animation)
        self.assertEqual(self.session.latest_object_animation.sender_id, OBJECT)


class TerrainAndParcelTests(_WireCase):
    def test_a_terrain_patch_is_kept_under_its_layer_type(self) -> None:
        from vibestorm.udp.messages import LAYER_TYPE_LAND

        payload = bytes(range(64))
        body = bytes([LAYER_TYPE_LAND]) + len(payload).to_bytes(2, "little") + payload
        self.deliver("LayerData", body)
        self.assertEqual(self.session.latest_layer_data.get(LAYER_TYPE_LAND), payload)

    def test_a_udp_parcel_properties_is_folded(self) -> None:
        """OpenSim never sends this one over UDP -- it builds an event-queue
        event instead, which is a separate branch with its own tests. This
        branch is for the grids that do, and until somebody logs into one it
        has no other check at all."""
        from test_udp_messages import SemanticMessageTests

        body = SemanticMessageTests._parcel_properties_body(
            None,
            owner_id=OWNER,
            group_id=SOUND,
            bitmap=bytes([0xFF, 0x00, 0xAA]),
            name=b"Sandbox",
            desc=b"a test parcel",
        )
        self.deliver("ParcelProperties", body)
        self.assertIsNotNone(self.session.latest_parcel_properties)
        self.assertEqual(self.session.latest_parcel_properties.local_id, 42)
        self.assertIn(42, self.session.parcel_properties_by_local_id)


class InventoryReplyTests(_WireCase):
    """The two replies the folder-sync work is built on.

    `create_agent_item` polls `session.created_inventory_items` for the
    callback id it sent, and gives up after thirty seconds. If this branch
    stopped matching, every create in `sync/` would report "no
    UpdateCreateInventoryItem reply" after a thirty-second wait, and the unit
    tests would all still pass because they fake the session.
    """

    def test_a_created_item_is_filed_under_its_callback_id(self) -> None:
        from test_create_inventory_item import _reply

        item_id = UUID("dddddddd-1111-2222-3333-444444444444")
        asset_id = UUID("eeeeeeee-1111-2222-3333-444444444444")
        self.deliver(
            "UpdateCreateInventoryItem",
            _reply(item_id=item_id, asset_id=asset_id, callback_id=1234),
        )
        created = self.session.created_inventory_items.get(1234)
        self.assertIsNotNone(created, f"nothing filed: {self.session.created_inventory_items}")
        self.assertEqual(created.item_id, item_id)
        self.assertEqual(created.asset_id, asset_id)

    def test_a_task_inventory_reply_is_acted_on(self) -> None:
        """It names an Xfer file rather than carrying the inventory, so the
        client has to ask for it. A branch that stopped matching would leave
        every object inventory read hanging until its timeout."""
        task_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        self.session.build_request_task_inventory_packet(42, now=11.0)
        body = task_id.bytes + pack("<h", 7) + bytes([8]) + b"task.inv"
        packets = self.deliver("ReplyTaskInventory", body)
        self.assertIn("RequestXfer", self.names_of(packets))


class XferTests(_WireCase):
    """The task inventory arrives as an Xfer, not in the reply that announces it.

    Everything the folder-sync work does begins by reading an object's
    inventory, and this is the whole of how it is read: the reply names a
    file, the client asks for it with `RequestXfer`, and the sim sends it
    back in pieces. Both halves are separate branches of `handle_incoming`
    and both survived a mutation battery, because the tests around them all
    fake the session.
    """

    INVENTORY = b"""
inv_item 0
{
	item_id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
	parent_id bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
	asset_id cccccccc-cccc-4ccc-8ccc-cccccccccccc
	type lsltext
	inv_type lsltext
	name hello.lsl|
	desc |
}
"""

    def _start_xfer(self) -> int:
        """Ask for local id 42's inventory and let the sim answer. Returns the xfer id."""
        task_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        self.session.build_request_task_inventory_packet(42, now=11.0)
        self.deliver(
            "ReplyTaskInventory",
            task_id.bytes + pack("<h", 7) + bytes([8]) + b"task.inv",
        )
        pending = list(self.session.pending_task_inventory_by_xfer)
        self.assertEqual(len(pending), 1, "the reply did not start an xfer")
        return pending[0]

    def _xfer_body(self, xfer_id: int, packet: int, payload: bytes) -> bytes:
        return pack("<Q", xfer_id) + pack("<I", packet) + pack("<H", len(payload)) + payload

    def test_a_final_packet_finishes_the_object_inventory(self) -> None:
        xfer_id = self._start_xfer()
        # Packet zero carries the total size ahead of its share of the file,
        # and the high bit says it is the last one. Here it is both.
        payload = len(self.INVENTORY).to_bytes(4, "little") + self.INVENTORY
        self.deliver("SendXferPacket", self._xfer_body(xfer_id, 0x80000000, payload))
        snapshot = self.session.object_inventory_snapshots.get(42)
        self.assertIsNotNone(snapshot, "no snapshot for local id 42")
        self.assertEqual([item.name for item in snapshot.items], ["hello.lsl"])

    def test_a_packet_is_confirmed_before_the_next_one_is_sent(self) -> None:
        """The sim waits for the confirm. Not sending one stalls the transfer
        rather than failing it, so it would show up as a read that never
        finishes and never says why."""
        xfer_id = self._start_xfer()
        packets = self.deliver("SendXferPacket", self._xfer_body(xfer_id, 0, b"\x00" * 8))
        self.assertIn("ConfirmXferPacket", self.names_of(packets))


class AssetTransferTests(_WireCase):
    """Assets that come over UDP rather than over HTTP.

    The sim decides which: a texture usually arrives through the HTTP asset
    capability, but a script's text, and anything on a grid with no such
    capability, comes down this path.
    """

    ASSET = UUID("cccccccc-1111-2222-3333-444444444444")

    def _start_transfer(self) -> UUID:
        self.session.build_transfer_request_packet(self.ASSET, 10, now=11.0)
        pending = list(self.session.pending_asset_transfers)
        self.assertEqual(len(pending), 1)
        return pending[0]

    def _info_body(self, transfer_id: UUID, *, status: int, size: int) -> bytes:
        return (
            transfer_id.bytes
            + pack("<i", 2)  # ChannelType: asset
            + pack("<i", 3)  # TargetType
            + pack("<i", status)
            + pack("<i", size)
            + pack("<H", 0)  # Params
        )

    def _packet_body(self, transfer_id: UUID, packet: int, data: bytes) -> bytes:
        return (
            transfer_id.bytes
            + pack("<i", 2)
            + pack("<i", packet)
            + pack("<i", 0)  # status: more to come
            + pack("<H", len(data))
            + data
        )

    def test_the_info_sets_the_size_the_packets_are_measured_against(self) -> None:
        """Without it the assembly has nothing to know it is finished by, and
        the asset is never handed over however many packets arrive."""
        transfer_id = self._start_transfer()
        self.deliver("TransferInfo", self._info_body(transfer_id, status=0, size=11))
        transfer = self.session.pending_asset_transfers[transfer_id]
        self.assertEqual(transfer.expected_size, 11)

    def test_the_packets_assemble_into_the_asset(self) -> None:
        transfer_id = self._start_transfer()
        self.deliver("TransferInfo", self._info_body(transfer_id, status=0, size=11))
        self.deliver("TransferPacket", self._packet_body(transfer_id, 0, b"asset "))
        self.assertNotIn(self.ASSET, self.session.fetched_assets, "handed over early")
        self.deliver("TransferPacket", self._packet_body(transfer_id, 1, b"bytes"))
        self.assertEqual(self.session.fetched_assets.get(self.ASSET), b"asset bytes")

    def test_a_refused_transfer_is_dropped_rather_than_left_pending(self) -> None:
        """A pending transfer that is never going to finish is a caller
        blocked until its timeout, and the sim has already said no."""
        transfer_id = self._start_transfer()
        self.deliver("TransferInfo", self._info_body(transfer_id, status=-2, size=0))
        self.assertNotIn(transfer_id, self.session.pending_asset_transfers)


class NothingEscapesTests(_WireCase):
    """No datagram, however malformed, ends the session.

    The receive loop calls `handle_incoming` bare -- there is no try around
    it -- so anything that escapes kills the session task and the viewer with
    it. `MalformedInputTests` below checks the handful of messages this file
    already builds bodies for; this checks *every* message in the template,
    because the region decides what arrives and a viewer does not get to
    assume it is well formed.

    Run against the whole template it found fourteen, and two of them were
    not the missing guard everyone else was:

    - `parse_agent_movement_complete` checked for 62 bytes and read to 70 --
      the length the fixed part would be with no region handle in it. A body
      between the two raised `struct.error`. This message arrives at login.
    - `parse_region_handshake` checked one constant against the whole body,
      too small even for an empty region name and blind to how long the name
      actually was, so a short one sliced past its end and `UUID(bytes=...)`
      raised `ValueError`. This is the first message a region sends.

    Both are the same mistake as the fuzz test on `apply_dispatch`: a parser
    that raises something other than `MessageDecodeError` for bytes it cannot
    read is a parser reaching past its own bounds check, and the fix belongs
    in the parser rather than in a wider catch upstream.
    """

    #: All-zero and all-ones sit either side of every length field: zero
    #: makes a variable block empty and 0xFF makes it claim 255 bytes that
    #: are not there, which is the shape that walks off the end.
    PATTERNS = (b"\x00", b"\xff")
    LENGTHS = (0, 1, 2, 3, 5, 9, 17, 33, 65, 129, 223, 400)

    @staticmethod
    def _dispatched_names() -> list[str]:
        """The names `handle_incoming` branches on, read off the source.

        So that a branch added later is fuzzed without anyone remembering to
        add it here -- which is exactly what did not happen for the eighteen
        this file was written for.
        """
        import re

        source = Path(__file__).resolve().parents[1] / "src/vibestorm/udp/session.py"
        return sorted(set(re.findall(r'summary\.name == "([A-Za-z]+)"', source.read_text())))

    def _sweep(self, names, lengths, bodies_for) -> None:
        for name in names:
            summary = self.dispatcher.index.by_name[name]
            width = {"High": 1, "Medium": 2, "Low": 4, "Fixed": 4}[summary.frequency]
            head = summary.wire_message_number.to_bytes(width, "big")
            for length in lengths:
                for body in bodies_for(length):
                    self._sequence += 1
                    packet = build_packet(head + body, sequence=self._sequence)
                    try:
                        self.session.handle_incoming(packet, 11.0)
                    except Exception as exc:  # noqa: BLE001 -- the thing under test
                        self.fail(
                            f"{name} with {length} bytes ({body.hex() or 'empty'}) "
                            f"escaped handle_incoming: {type(exc).__name__}: {exc}"
                        )

    def test_no_branch_of_the_dispatch_can_be_crashed(self) -> None:
        rng = random.Random(20260908)

        def bodies(length: int) -> list[bytes]:
            fixed = [pattern * length for pattern in self.PATTERNS]
            random_bodies = [bytes(rng.getrandbits(8) for _ in range(length)) for _ in range(2)]
            return fixed + random_bodies

        self._sweep(self._dispatched_names(), self.LENGTHS, bodies)

    def test_no_message_in_the_template_can_be_crashed(self) -> None:
        """Thinner, and over all of them.

        A message with no branch today may get one tomorrow, and a simulator
        is free to send anything in the template at any time regardless.
        """
        rng = random.Random(20260909)

        def bodies(length: int) -> list[bytes]:
            return [bytes(rng.getrandbits(8) for _ in range(length))]

        self._sweep(sorted(self.dispatcher.index.by_name), (0, 3, 33, 223), bodies)

    def test_the_sweep_is_reaching_the_parsers(self) -> None:
        """Or it is 3,000 packets that decode to nothing and prove nothing."""
        self.session.events.clear()
        self._sweep(["RegionHandshake"], (0, 5, 33), lambda n: [b"\xff" * n])
        self.assertIn("message.decode_error", self.kinds())


class MalformedInputTests(_WireCase):
    """A packet that does not parse must not end the session.

    `handle_incoming` is called bare from the receive loop -- no try around
    it -- so anything that escapes it ends the session task and the viewer
    with it. Every branch in that method catches `MessageDecodeError` for
    exactly this reason; the branch that handles object updates did not, and
    one truncated `ObjectUpdateCached` off the wire was enough.
    """

    TRUNCATED = [
        ("ObjectUpdateCached", (1).to_bytes(8, "little") + b"\x00"),
        ("ObjectUpdate", b"\x00" * 5),
        ("ObjectUpdateCompressed", b"\x00" * 5),
        ("ImprovedTerseObjectUpdate", b"\x00" * 5),
        ("KillObject", b"\xff"),
        ("ObjectProperties", b"\x01"),
        ("CoarseLocationUpdate", b"\xff\xff"),
        ("SimStats", b"\x00"),
        ("LayerData", b"\x01\xff\xff"),
        ("ImprovedInstantMessage", b"\x00" * 4),
        ("TransferInfo", b"\x00" * 4),
        ("TransferPacket", b"\x00" * 4),
        ("SendXferPacket", b"\x00" * 4),
        ("ReplyTaskInventory", b"\x00" * 4),
        ("UpdateCreateInventoryItem", b"\x00" * 4),
    ]

    def test_a_truncated_body_never_escapes(self) -> None:
        for name, body in self.TRUNCATED:
            with self.subTest(name):
                self.deliver(name, body)
        self.assertIsNone(self.session.close_reason)

    def test_the_failure_is_reported_rather_than_swallowed(self) -> None:
        """Silently ignoring a packet that will not parse is how a decoder bug
        becomes a world that is quietly missing things."""
        self.deliver("ObjectUpdateCached", (1).to_bytes(8, "little") + b"\x00")
        self.assertIn("world.decode_error", self.kinds())


if __name__ == "__main__":
    unittest.main()
