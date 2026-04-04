import unittest
import json
from pathlib import Path
from struct import pack, unpack_from
from tempfile import TemporaryDirectory
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.packet import LL_RELIABLE_FLAG, LL_ZERO_CODE_FLAG, build_packet, split_packet
from vibestorm.udp.session import LiveCircuitSession, SessionConfig, SessionEvent
from vibestorm.udp.zerocode import decode_zerocode


class LiveCircuitSessionTests(unittest.TestCase):
    @staticmethod
    def _encode_v16(value: float, range_val: float) -> bytes:
        if range_val == 1.0:
            encoded = int(round((value + 1.0) * 32767.5))
        elif range_val == 64.0:
            encoded = int(round((value + 64.0) * 511.9921875))
        elif range_val == 128.0:
            encoded = int(round((value + 128.0) * 255.99609375))
        else:
            raise AssertionError(f"unsupported range {range_val}")
        encoded = max(0, min(65535, encoded))
        return encoded.to_bytes(2, "little")

    def _build_terse_prim_data(self, local_id: int) -> bytes:
        return (
            local_id.to_bytes(4, "little")
            + bytes([0x21])
            + bytes([0])
            + pack("<fff", 1.0, 2.0, 3.0)
            + self._encode_v16(0.0, 128.0) * 3
            + self._encode_v16(0.0, 64.0) * 3
            + self._encode_v16(0.0, 1.0) * 3
            + self._encode_v16(1.0, 1.0)
            + self._encode_v16(0.0, 64.0) * 3
        )

    @staticmethod
    def _decode_agent_update_camera(message: bytes) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        camera_center = tuple(unpack_from("<fff", message, 58))
        camera_at_axis = tuple(unpack_from("<fff", message, 70))
        return camera_center, camera_at_axis

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

    def test_camera_sweep_changes_agent_update_camera_center_and_axis(self) -> None:
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            config=SessionConfig(agent_update_interval_seconds=1.0, camera_sweep=True),
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
        session.handle_incoming(build_packet(bytes([0xFF, 0xFF, 0x00, 0xFA]) + body, sequence=41), 10.2)

        later = session.drain_due_packets(11.3)

        self.assertEqual(len(later), 1)
        agent_update = split_packet(decode_zerocode(later[0])).message
        camera_center, camera_at_axis = self._decode_agent_update_camera(agent_update)
        self.assertNotEqual(camera_center, (1.0, 2.0, 3.0))
        self.assertNotEqual(camera_at_axis, (1.0, 0.0, 0.0))

    def test_agent_update_stays_at_movement_position_without_camera_sweep(self) -> None:
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            config=SessionConfig(agent_update_interval_seconds=1.0, camera_sweep=False),
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
        session.handle_incoming(build_packet(bytes([0xFF, 0xFF, 0x00, 0xFA]) + body, sequence=41), 10.2)

        later = session.drain_due_packets(11.3)

        self.assertEqual(len(later), 1)
        agent_update = split_packet(decode_zerocode(later[0])).message
        camera_center, camera_at_axis = self._decode_agent_update_camera(agent_update)
        self.assertEqual(camera_center, (1.0, 2.0, 3.0))
        self.assertEqual(camera_at_axis, (1.0, 0.0, 0.0))

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

    def test_object_update_capture_writes_fixture_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    capture_dir=Path(tmpdir),
                    capture_messages=("ObjectUpdate",),
                    capture_mode="all",
                ),
            )
            session.start(10.0)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            inbound = build_packet(bytes([0x0C]) + body, sequence=77)

            session.handle_incoming(inbound, 10.2)

            capture_dir = Path(tmpdir) / "ObjectUpdate"
            payloads = sorted(capture_dir.glob("*.body.bin"))
            metadata = sorted(capture_dir.glob("*.json"))
            self.assertEqual(len(payloads), 1)
            self.assertEqual(len(metadata), 1)
            self.assertEqual(payloads[0].read_bytes(), body)
            captured = json.loads(metadata[0].read_text(encoding="utf-8"))
            self.assertEqual(captured["message_name"], "ObjectUpdate")
            self.assertEqual(captured["object_update"]["decode_status"], "decoded")
            self.assertEqual(captured["object_update"]["interesting_payloads"], [])

    def test_object_update_capture_smart_mode_skips_known_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    capture_dir=Path(tmpdir),
                    capture_messages=("ObjectUpdate",),
                    capture_mode="smart",
                ),
            )
            session.start(10.0)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            inbound = build_packet(bytes([0x0C]) + body, sequence=77)

            session.handle_incoming(inbound, 10.2)

            capture_dir = Path(tmpdir) / "ObjectUpdate"
            self.assertFalse(capture_dir.exists())

    def test_object_update_capture_smart_mode_skips_known_avatar_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    capture_dir=Path(tmpdir),
                    capture_messages=("ObjectUpdate",),
                    capture_mode="smart",
                ),
            )
            session.start(10.0)

            name_values = b"FirstName STRING RW SV Vibestorm\nLastName STRING RW SV Admin\nTitle STRING RW SV \x00"
            object_data = (
                (0).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + pack("<f", 1.0)
                + pack("<fff", 128.0, 128.0, 25.96)
                + (b"\x00" * 32)
                + pack("<f", 0.021197397261857986)
                + (b"\x00" * 12)
            )
            body = (
                (1099511628032000).to_bytes(8, "little")
                + (65535).to_bytes(2, "little")
                + bytes([1])
                + (367911621).to_bytes(4, "little")
                + bytes([0])
                + self.bootstrap.session_id.bytes
                + (0).to_bytes(4, "little")
                + bytes([47, 4, 0])
                + pack("<fff", 0.45, 0.6, 1.9)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (0).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (0).to_bytes(2, "little")
                + bytes([0])
                + len(name_values).to_bytes(2, "big")
                + name_values
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            inbound = build_packet(bytes([0x0C]) + body, sequence=78)

            session.handle_incoming(inbound, 10.2)

            capture_dir = Path(tmpdir) / "ObjectUpdate"
            self.assertFalse(capture_dir.exists())

    def test_object_update_capture_smart_mode_keeps_rich_prim_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    capture_dir=Path(tmpdir),
                    capture_messages=("ObjectUpdate",),
                    capture_mode="smart",
                ),
            )
            session.start(10.0)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (4).to_bytes(2, "little")
                + b"\x11\x22\x33\x44"
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            inbound = build_packet(bytes([0x0C]) + body, sequence=79)

            session.handle_incoming(inbound, 10.2)

            capture_dir = Path(tmpdir) / "ObjectUpdate"
            self.assertTrue(capture_dir.exists())
            self.assertEqual(len(list(capture_dir.glob("*.body.bin"))), 1)
            metadata = json.loads(next(capture_dir.glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(metadata["object_update"]["interesting_payloads"][0]["field_name"], "TextureEntry")

    def test_interesting_object_updates_are_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (4).to_bytes(2, "little")
                + b"\x11\x22\x33\x44"
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            inbound = build_packet(bytes([0x0C]) + body, sequence=79)

            session.handle_incoming(inbound, 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            stats = database.read_stats(session_id=session_info.session_id)
            packet_summaries = database.summarize_object_update_packets(limit=5, session_id=session_info.session_id)
            summaries = database.summarize_payload_fingerprints(limit=5, session_id=session_info.session_id)
            self.assertEqual(stats.packet_count, 1)
            self.assertEqual(stats.entity_count, 1)
            self.assertEqual(stats.rich_entities, 1)
            self.assertEqual(packet_summaries[0]["decode_status"], "decoded")
            self.assertEqual(summaries[0]["sample_payloads"][0]["field_name"], "TextureEntry")

    def test_partial_object_updates_are_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            body = (123456789).to_bytes(8, "little") + (42).to_bytes(2, "little") + bytes([3]) + b"\x00" * 5
            inbound = build_packet(bytes([0x0C]) + body, sequence=91)

            session.handle_incoming(inbound, 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            stats = database.read_stats(session_id=session_info.session_id)
            packet_summaries = database.summarize_object_update_packets(limit=5, session_id=session_info.session_id)
            self.assertEqual(stats.packet_count, 1)
            self.assertEqual(stats.entity_count, 0)
            self.assertEqual(stats.multi_object_packets, 1)
            self.assertEqual(stats.partial_packets, 1)
            self.assertEqual(packet_summaries[0]["decode_status"], "summary_only")

    def test_nearby_chat_is_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            source_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            owner_id = UUID("11111111-2222-3333-4444-555555555555")
            from_name = b"Vibestorm Admin"
            text = "testing local chat note".encode("utf-8")
            body = (
                bytes([len(from_name)])
                + from_name
                + source_id.bytes
                + owner_id.bytes
                + bytes([1, 1, 2])
                + pack("<fff", 128.0, 128.0, 25.0)
                + len(text).to_bytes(2, "little")
                + text
            )
            inbound = build_packet(bytes([0xFF, 0xFF, 0x00, 0x8B]) + body, sequence=90)

            session.handle_incoming(inbound, 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            chat = database.recent_nearby_chat(limit=5, session_id=session_info.session_id)
            self.assertEqual(len(chat), 1)
            self.assertEqual(chat[0]["message"], "testing local chat note")
            self.assertTrue(any(event.kind == "chat.local" for event in session.events))

    def test_unknown_udp_dispatch_failures_are_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            inbound = build_packet(bytes([0xFE, 0xAA, 0xBB, 0xCC]), sequence=92)

            responses = session.handle_incoming(inbound, 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            stats = database.read_stats(session_id=session_info.session_id)
            unknown = database.recent_unknown_udp_messages(limit=5, session_id=session_info.session_id)
            self.assertEqual(stats.unknown_udp_messages, 1)
            self.assertEqual(unknown[0]["failure_stage"], "dispatch")
            self.assertEqual(unknown[0]["raw_message_number"], 0x000000FE)
            self.assertEqual(len(responses), 0)
            self.assertTrue(any(event.kind == "udp.unknown" for event in session.events))

    def test_all_recognized_inbound_messages_are_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
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

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            stats = database.read_stats(session_id=session_info.session_id)
            messages = database.summarize_inbound_messages(limit=5, session_id=session_info.session_id)
            self.assertEqual(stats.inbound_messages, 1)
            self.assertEqual(messages[0]["message_name"], "SimStats")

    def test_improved_terse_updates_are_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            prim_a = self._build_terse_prim_data(367911609)
            prim_b = self._build_terse_prim_data(367911629)
            texture_payload = b"\x08\x00\x04\x00\x00\x00\x11\x22\x33\x44"
            body = (
                (1099511628032000).to_bytes(8, "little")
                + (65535).to_bytes(2, "little")
                + bytes([2])
                + bytes([len(prim_a)])
                + prim_a
                + (0).to_bytes(2, "little")
                + bytes([len(prim_b)])
                + prim_b
                + len(texture_payload).to_bytes(2, "little")
                + texture_payload
            )
            inbound = build_packet(bytes([0x0F]) + body, sequence=93)

            session.handle_incoming(inbound, 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            stats = database.read_stats(session_id=session_info.session_id)
            packet_summary = database.summarize_improved_terse_packets(limit=5, session_id=session_info.session_id)
            local_id_summary = database.summarize_improved_terse_local_ids(limit=5, session_id=session_info.session_id)
            self.assertEqual(stats.terse_packet_count, 1)
            self.assertEqual(stats.terse_entity_count, 2)
            self.assertEqual(stats.terse_distinct_local_ids, 2)
            self.assertEqual(stats.terse_rich_entities, 1)
            self.assertEqual(packet_summary[0]["total_objects"], 2)
            self.assertEqual({item["local_id"] for item in local_id_summary}, {367911609, 367911629})

    def test_kill_object_is_recorded_in_unknowns_db_and_world_state(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
            object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
            object_body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([len(object_data)])
                + object_data
                + (0).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (b"\x00" * 22)
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (0).to_bytes(2, "little")
                + (0).to_bytes(2, "little")
                + bytes([0])
                + (b"\x00" * 4)
                + bytes([0, 0, 0])
                + (b"\x00" * 66)
            )
            session.handle_incoming(build_packet(bytes([0x0C]) + object_body, sequence=94), 10.2)

            kill_body = (7).to_bytes(4, "little") + (9).to_bytes(4, "little")
            session.handle_incoming(build_packet(bytes([0x10]) + kill_body, sequence=95), 10.3)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            with database._connect() as connection:
                row = connection.execute(
                    """
                    SELECT local_ids_json
                    FROM kill_object_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_info.session_id,),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row["local_ids_json"]), [7, 9])
            self.assertNotIn(object_id, session.world_view.objects)
            self.assertNotIn(7, session.world_view.local_id_to_full_id)
            self.assertTrue(any(event.kind == "world.kill_object" for event in session.events))

    def test_object_update_cached_is_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            body = (
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
            session.handle_incoming(build_packet(bytes([0x0E]) + body, sequence=96), 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            with database._connect() as connection:
                packet_row = connection.execute(
                    """
                    SELECT region_handle, time_dilation, packet_tags_json
                    FROM cached_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_info.session_id,),
                ).fetchone()
                entity_rows = connection.execute(
                    """
                    SELECT local_id, crc, update_flags
                    FROM cached_entities
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_info.session_id,),
                ).fetchall()

            assert packet_row is not None
            self.assertEqual(packet_row["region_handle"], 123456789)
            self.assertEqual(packet_row["time_dilation"], 42)
            self.assertIn("object_count:2", json.loads(packet_row["packet_tags_json"]))
            self.assertEqual([(row["local_id"], row["crc"], row["update_flags"]) for row in entity_rows], [
                (7, 0x11111111, 5),
                (9, 0x22222222, 6),
            ])
            self.assertTrue(any(event.kind == "world.object_update_cached" for event in session.events))

    def test_object_update_compressed_is_recorded_in_unknowns_db(self) -> None:
        with TemporaryDirectory() as tmpdir:
            session = LiveCircuitSession(
                self.bootstrap,
                self.dispatcher,
                config=SessionConfig(
                    unknowns_db_path=Path(tmpdir) / "unknowns.sqlite3",
                ),
            )
            session.start(10.0)

            data_a = b"\x11\x22\x33"
            data_b = b"\xaa\xbb\xcc\xdd"
            body = (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([2])
                + (5).to_bytes(4, "little")
                + len(data_a).to_bytes(2, "little")
                + data_a
                + (6).to_bytes(4, "little")
                + len(data_b).to_bytes(2, "little")
                + data_b
            )
            session.handle_incoming(build_packet(bytes([0x0D]) + body, sequence=97), 10.2)

            from vibestorm.fixtures.unknowns_db import UnknownsDatabase

            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_info = database.latest_session()
            assert session_info is not None
            with database._connect() as connection:
                packet_row = connection.execute(
                    """
                    SELECT region_handle, time_dilation, packet_tags_json
                    FROM compressed_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_info.session_id,),
                ).fetchone()
                entity_rows = connection.execute(
                    """
                    SELECT update_flags, data_size, data_preview_hex
                    FROM compressed_entities
                    WHERE session_id = ?
                    ORDER BY id ASC
                    """,
                    (session_info.session_id,),
                ).fetchall()

            assert packet_row is not None
            self.assertEqual(packet_row["region_handle"], 123456789)
            self.assertEqual(packet_row["time_dilation"], 42)
            self.assertIn("object_count:2", json.loads(packet_row["packet_tags_json"]))
            self.assertEqual([(row["update_flags"], row["data_size"], row["data_preview_hex"]) for row in entity_rows], [
                (5, 3, "112233"),
                (6, 4, "aabbccdd"),
            ])
            self.assertTrue(any(event.kind == "world.object_update_compressed" for event in session.events))

    def test_object_properties_family_updates_known_world_object(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        object_body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([3])
            + object_id.bytes
            + (99).to_bytes(4, "little")
            + bytes([9, 3, 1])
            + pack("<fff", 1.0, 2.0, 3.0)
            + bytes([len(object_data)])
            + object_data
            + (0).to_bytes(4, "little")
            + (5).to_bytes(4, "little")
            + (b"\x00" * 22)
            + (0).to_bytes(2, "little")
            + bytes([0])
            + (0).to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + bytes([0])
            + (b"\x00" * 4)
            + bytes([0, 0, 0])
            + (b"\x00" * 66)
        )
        session.handle_incoming(build_packet(bytes([0x0C]) + object_body, sequence=98), 10.2)

        properties_body = (
            (5).to_bytes(4, "little")
            + object_id.bytes
            + UUID("11111111-2222-3333-4444-555555555555").bytes
            + UUID("99999999-8888-7777-6666-555555555555").bytes
            + (1).to_bytes(4, "little")
            + (2).to_bytes(4, "little")
            + (3).to_bytes(4, "little")
            + (4).to_bytes(4, "little")
            + (5).to_bytes(4, "little")
            + (0).to_bytes(4, "little", signed=True)
            + bytes([2])
            + (150).to_bytes(4, "little", signed=True)
            + (7).to_bytes(4, "little")
            + UUID("12345678-1234-5678-1234-567812345678").bytes
            + (11).to_bytes(2, "little")
            + b"Source Cube"
            + (11).to_bytes(2, "little")
            + b"hover entry"
        )
        session.handle_incoming(build_packet(bytes([0xFF, 0x0A]) + properties_body, sequence=99), 10.3)

        assert session.world_view.latest_object_properties_family is not None
        self.assertEqual(session.world_view.latest_object_properties_family.name, "Source Cube")
        self.assertIn(object_id, session.world_view.objects)
        self.assertIsNotNone(session.world_view.objects[object_id].properties_family)
        assert session.world_view.objects[object_id].properties_family is not None
        self.assertEqual(session.world_view.objects[object_id].properties_family.description, "hover entry")
        self.assertTrue(any(event.kind == "world.object_properties_family" for event in session.events))

    def test_object_extra_params_emits_event(self) -> None:
        session = LiveCircuitSession(self.bootstrap, self.dispatcher)
        session.start(10.0)

        data_a = b"\x11\x22\x33"
        data_b = b"\xaa\xbb\xcc\xdd"
        body = (
            self.bootstrap.agent_id.bytes
            + self.bootstrap.session_id.bytes
            + (7).to_bytes(4, "little")
            + (0x10).to_bytes(2, "little")
            + bytes([1])
            + len(data_a).to_bytes(4, "little")
            + bytes([len(data_a)])
            + data_a
            + (9).to_bytes(4, "little")
            + (0x20).to_bytes(2, "little")
            + bytes([0])
            + len(data_b).to_bytes(4, "little")
            + bytes([len(data_b)])
            + data_b
        )

        session.handle_incoming(build_packet(bytes([0xFF, 0xFF, 0x00, 0x63]) + body, sequence=100), 10.2)

        self.assertTrue(any(event.kind == "world.object_extra_params" for event in session.events))
