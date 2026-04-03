import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.packet import LL_RELIABLE_FLAG, LL_ZERO_CODE_FLAG, build_packet, split_packet
from vibestorm.udp.session import LiveCircuitSession, SessionConfig, SessionEvent
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

    def test_region_handshake_sends_reply_and_explicit_ack(self) -> None:
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

        self.assertEqual(len(packets), 2)
        reply = split_packet(decode_zerocode(packets[0]))
        self.assertTrue(reply.header.is_reliable)
        self.assertEqual(reply.appended_acks, ())
        self.assertEqual(self.dispatcher.dispatch(reply.message).summary.name, "RegionHandshakeReply")
        ack = split_packet(packets[1])
        self.assertEqual(self.dispatcher.dispatch(ack.message).summary.name, "PacketAck")
        self.assertEqual(session.last_region_name, "Test")
        self.assertTrue(any(event.kind == "handshake.region" for event in session.events))

    def test_packet_ack_clears_pending_reliable_sequences(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        packet_ack_message = b"\xFF\xFF\xFF\xFB" + bytes([2]) + pack("<I", 1) + pack("<I", 2)
        inbound = build_packet(packet_ack_message, sequence=40)
        session.handle_incoming(inbound, 11.0)

        self.assertEqual(session.pending_reliable, {})
        self.assertEqual(session.packet_acks_received, 2)

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

        self.assertEqual(len(immediate), 1)
        self.assertEqual(self.dispatcher.dispatch(split_packet(decode_zerocode(immediate[0])).message).summary.name, "AgentThrottle")
        self.assertEqual(len(later), 1)
        second = split_packet(decode_zerocode(later[0]))
        self.assertTrue(later[0][0] & LL_ZERO_CODE_FLAG)
        self.assertEqual(self.dispatcher.dispatch(second.message).summary.name, "AgentUpdate")
        self.assertEqual(session.agent_update_count, 1)
        self.assertTrue(session.throttle_sent)
        self.assertTrue(any(event.kind == "movement.complete" for event in session.events))

    def test_reliable_packets_record_appended_ack_event(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        inbound = build_packet(bytes([0x01, 0x10, 0x00, 0x00, 0x00, 0x00]), sequence=55, flags=LL_RELIABLE_FLAG)
        responses = session.handle_incoming(inbound, 11.0)

        self.assertEqual(session.ping_requests_handled, 1)
        self.assertEqual(len(responses), 2)
        response = split_packet(responses[0])
        self.assertEqual(response.appended_acks, ())
        ack = split_packet(responses[1])
        self.assertEqual(self.dispatcher.dispatch(ack.message).summary.name, "PacketAck")
        outbound = session._build_outbound_packet(bytes([0x02, 0x10]), now=11.1, label="TestOutbound")
        parsed = split_packet(outbound)
        self.assertEqual(parsed.appended_acks, ())
        self.assertEqual(session.appended_acks_received, 0)
        self.assertTrue(any(event.kind == "transport.reliable_in" for event in session.events))

    def test_duplicate_reliable_packet_is_not_reprocessed(self) -> None:
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

        first_packets = session.handle_incoming(inbound, 11.0)
        second_packets = session.handle_incoming(inbound, 11.1)

        self.assertEqual(len(first_packets), 2)
        self.assertEqual(len(second_packets), 1)
        self.assertEqual(self.dispatcher.dispatch(split_packet(decode_zerocode(first_packets[0])).message).summary.name, "RegionHandshakeReply")
        self.assertEqual(self.dispatcher.dispatch(split_packet(first_packets[1]).message).summary.name, "PacketAck")
        self.assertEqual(self.dispatcher.dispatch(split_packet(second_packets[0]).message).summary.name, "PacketAck")
        self.assertEqual(session.received_messages["RegionHandshake"], 2)
        self.assertTrue(any(event.kind == "transport.reliable_duplicate" for event in session.events))

    def test_session_emits_events_to_callback(self) -> None:
        seen: list[SessionEvent] = []
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            on_event=seen.append,
        )
        session.start(10.0)
        session._record_event(10.5, "session.completed", "duration elapsed")

        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen[-1].kind, "session.completed")
        self.assertEqual(session.events[-1].kind, "session.completed")

    def test_spawn_test_cube_emits_object_add(self) -> None:
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            config=SessionConfig(agent_update_interval_seconds=1.0, spawn_test_cube=True, spawn_delay_seconds=0.0),
        )
        session.start(10.0)
        session.movement_completed = True

        packets = session.drain_due_packets(10.1)

        self.assertGreaterEqual(len(packets), 1)
        names = [self.dispatcher.dispatch(split_packet(decode_zerocode(packet)).message).summary.name for packet in packets]
        self.assertIn("ObjectAdd", names)
        self.assertTrue(session.test_cube_spawned)

    def test_simstats_records_summary_event(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        body = (
            (1000).to_bytes(4, "little")
            + (1001).to_bytes(4, "little")
            + (9).to_bytes(4, "little")
            + (45000).to_bytes(4, "little")
            + bytes([1])
            + (1).to_bytes(4, "little")
            + bytes.fromhex("00002041")
            + (1234).to_bytes(4, "little", signed=True)
            + bytes([1])
            + (77).to_bytes(8, "little")
        )
        inbound = build_packet(bytes([0xFF, 0xFF, 0x00, 0x8C]) + body, sequence=41)
        session.handle_incoming(inbound, 10.2)

        self.assertTrue(any(event.kind == "sim.stats" for event in session.events))
        assert session.world_view.latest_sim_stats is not None
        self.assertEqual(session.world_view.latest_sim_stats.object_capacity, 45000)

    def test_coarse_location_records_summary_event(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        body = bytes([2, 128, 129, 8, 130, 131, 9]) + (-1).to_bytes(2, "little", signed=True) + (1).to_bytes(2, "little", signed=True) + bytes([1]) + self.bootstrap.agent_id.bytes
        inbound = build_packet(bytes([0xFF, 0x06]) + body, sequence=42)
        session.handle_incoming(inbound, 10.2)

        self.assertTrue(any(event.kind == "world.coarse_location" for event in session.events))
        self.assertEqual(len(session.world_view.coarse_agents), 2)
