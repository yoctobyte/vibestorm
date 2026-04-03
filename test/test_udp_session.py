import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.packet import LL_RELIABLE_FLAG, LL_ZERO_CODE_FLAG, build_packet, split_packet
from vibestorm.udp.session import LiveCircuitSession, SessionConfig
from vibestorm.udp.zerocode import decode_zerocode


class LiveCircuitSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        self.bootstrap = LoginBootstrap(
            agent_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
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

    def test_start_sends_initial_reliable_packets(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        packets = session.start(10.0)

        self.assertEqual(len(packets), 2)

        first = split_packet(packets[0])
        second = split_packet(packets[1])
        self.assertTrue(first.header.is_reliable)
        self.assertTrue(second.header.is_reliable)
        self.assertEqual(first.header.sequence, 1)
        self.assertEqual(second.header.sequence, 2)
        self.assertEqual(self.dispatcher.dispatch(first.message).summary.name, "UseCircuitCode")
        self.assertEqual(self.dispatcher.dispatch(second.message).summary.name, "CompleteAgentMovement")

    def test_region_handshake_sends_reply_and_piggybacks_ack(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        sim_owner = UUID("12345678-1234-5678-1234-567812345678")
        cache_id = UUID("87654321-4321-8765-4321-876543218765")
        region_id = UUID("aaaaaaaa-1111-bbbb-2222-cccccccccccc")
        body = bytearray()
        body += (9).to_bytes(4, "little")
        body += bytes([13])
        body += bytes([4])
        body += b"Test"
        body += sim_owner.bytes
        body += bytes([1])
        body += pack("<f", 20.0)
        body += pack("<f", 1.0)
        body += cache_id.bytes
        body += b"\x00" * (16 * 8)
        body += b"\x00" * (4 * 8)
        body += region_id.bytes
        inbound = build_packet(
            bytes([0xFF, 0xFF, 0x00, 0x94]) + bytes(body),
            sequence=22,
            flags=LL_RELIABLE_FLAG,
        )

        packets = session.handle_incoming(inbound, 11.0)

        self.assertEqual(len(packets), 1)
        reply = split_packet(decode_zerocode(packets[0]))
        self.assertTrue(reply.header.is_reliable)
        self.assertEqual(reply.appended_acks, (22,))
        self.assertEqual(self.dispatcher.dispatch(reply.message).summary.name, "RegionHandshakeReply")
        self.assertEqual(session.last_region_name, "Test")

    def test_packet_ack_clears_pending_reliable_sequences(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        packet_ack_message = b"\xFF\xFF\xFF\xFB" + bytes([2]) + pack("<I", 1) + pack("<I", 2)
        inbound = build_packet(packet_ack_message, sequence=40)
        session.handle_incoming(inbound, 11.0)

        self.assertEqual(session.pending_reliable, {})

    def test_agent_update_is_sent_on_interval_after_movement_complete(self) -> None:
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            config=SessionConfig(agent_update_interval_seconds=1.0),
        )
        session.start(10.0)

        body = (
            self.bootstrap.agent_id.bytes
            + self.bootstrap.session_id.bytes
            + bytes.fromhex("0000803f0000004000004040")
            + bytes.fromhex("000080bf000000000000803f")
            + (123456789).to_bytes(8, "little")
            + (42).to_bytes(4, "little")
            + (3).to_bytes(2, "little")
            + b"sim"
        )
        inbound = build_packet(bytes([0xFF, 0xFF, 0x00, 0xFA]) + body, sequence=41)

        immediate = session.handle_incoming(inbound, 10.2)
        later = session.drain_due_packets(11.3)

        self.assertEqual(immediate, [])
        self.assertEqual(len(later), 1)
        decoded = decode_zerocode(later[0])
        view = split_packet(decoded)
        self.assertTrue(later[0][0] & LL_ZERO_CODE_FLAG)
        self.assertEqual(self.dispatcher.dispatch(view.message).summary.name, "AgentUpdate")
        self.assertEqual(session.agent_update_count, 1)
