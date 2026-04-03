import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    encode_object_add,
    encode_agent_update,
    encode_agent_throttle,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_use_circuit_code,
    parse_coarse_location_update,
    parse_object_update_summary,
    parse_packet_ack,
    parse_agent_movement_complete,
    parse_complete_ping_check,
    parse_region_handshake,
    parse_sim_stats,
    parse_simulator_viewer_time,
    parse_start_ping_check,
    parse_use_circuit_code,
)


class SemanticMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def test_parse_start_ping_check(self) -> None:
        dispatched = self.dispatcher.dispatch(bytes([0x01, 0x7F, 0x04, 0x03, 0x02, 0x01]))
        parsed = parse_start_ping_check(dispatched)
        self.assertEqual(parsed.ping_id, 0x7F)
        self.assertEqual(parsed.oldest_unacked, 0x01020304)

    def test_parse_packet_ack(self) -> None:
        dispatched = self.dispatcher.dispatch(b"\xFF\xFF\xFF\xFB\x02" + (1).to_bytes(4, "little") + (2).to_bytes(4, "little"))
        parsed = parse_packet_ack(dispatched)
        self.assertEqual(parsed.packets, (1, 2))

    def test_encode_packet_ack(self) -> None:
        dispatched = self.dispatcher.dispatch(encode_packet_ack((1, 2)))
        parsed = parse_packet_ack(dispatched)
        self.assertEqual(parsed.packets, (1, 2))

    def test_parse_complete_ping_check(self) -> None:
        dispatched = self.dispatcher.dispatch(bytes([0x02, 0x11]))
        parsed = parse_complete_ping_check(dispatched)
        self.assertEqual(parsed.ping_id, 0x11)

    def test_encode_complete_ping_check(self) -> None:
        payload = encode_complete_ping_check(0x44)
        dispatched = self.dispatcher.dispatch(payload)
        parsed = parse_complete_ping_check(dispatched)
        self.assertEqual(parsed.ping_id, 0x44)

    def test_encode_agent_update_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_update(agent_id, session_id)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentUpdate")

    def test_encode_agent_throttle_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_throttle(agent_id, session_id, 0x12345678)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentThrottle")

    def test_encode_object_add_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_object_add(agent_id, session_id)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "ObjectAdd")

    def test_encode_and_parse_use_circuit_code(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_use_circuit_code(0x12345678, session_id, agent_id)
        dispatched = self.dispatcher.dispatch(payload)
        parsed = parse_use_circuit_code(dispatched)
        self.assertEqual(parsed.code, 0x12345678)
        self.assertEqual(parsed.session_id, session_id)
        self.assertEqual(parsed.agent_id, agent_id)

    def test_encode_complete_agent_movement_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_complete_agent_movement(agent_id, session_id, 0x12345678)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "CompleteAgentMovement")

    def test_encode_region_handshake_reply_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_region_handshake_reply(agent_id, session_id, 0x12345678)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "RegionHandshakeReply")

    def test_parse_agent_movement_complete(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        body = (
            agent_id.bytes
            + session_id.bytes
            + bytes.fromhex("0000803f0000004000004040")
            + bytes.fromhex("000080bf000000000000803f")
            + (123456789).to_bytes(8, "little")
            + (42).to_bytes(4, "little")
            + (3).to_bytes(2, "little")
            + b"sim"
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0xFA]) + body)
        parsed = parse_agent_movement_complete(dispatched)
        self.assertEqual(parsed.agent_id, agent_id)
        self.assertEqual(parsed.session_id, session_id)
        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.timestamp, 42)
        self.assertEqual(parsed.channel_version, "sim")

    def test_parse_region_handshake(self) -> None:
        sim_name = b"TestSim"
        agent_owner = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        cache_id = UUID("11111111-2222-3333-4444-555555555555")
        region_id = UUID("99999999-8888-7777-6666-555555555555")
        body = bytearray()
        body += (9).to_bytes(4, "little")
        body += bytes([13])
        body += bytes([len(sim_name)])
        body += sim_name
        body += agent_owner.bytes
        body += bytes([1])
        body += bytes.fromhex("0000a041")  # 20.0
        body += bytes.fromhex("0000803f")  # 1.0
        body += cache_id.bytes
        body += b"\x00" * (16 * 8)
        body += b"\x00" * (4 * 8)
        body += region_id.bytes
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x94]) + bytes(body))
        parsed = parse_region_handshake(dispatched)
        self.assertEqual(parsed.sim_name, "TestSim")
        self.assertEqual(parsed.region_flags, 9)
        self.assertEqual(parsed.sim_access, 13)
        self.assertTrue(parsed.is_estate_manager)
        self.assertEqual(parsed.cache_id, cache_id)
        self.assertEqual(parsed.region_id, region_id)

    def test_parse_sim_stats(self) -> None:
        body = (
            (1000).to_bytes(4, "little")
            + (1001).to_bytes(4, "little")
            + (9).to_bytes(4, "little")
            + (45000).to_bytes(4, "little")
            + bytes([2])
            + (1).to_bytes(4, "little")
            + bytes.fromhex("00002041")
            + (2).to_bytes(4, "little")
            + bytes.fromhex("0000a041")
            + (1234).to_bytes(4, "little", signed=True)
            + bytes([1])
            + (77).to_bytes(8, "little")
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x8C]) + body)
        parsed = parse_sim_stats(dispatched)
        self.assertEqual(parsed.region_x, 1000)
        self.assertEqual(len(parsed.stats), 2)
        self.assertEqual(parsed.stats[1].stat_id, 2)
        self.assertEqual(parsed.pid, 1234)
        self.assertEqual(parsed.region_flags_extended, (77,))

    def test_parse_coarse_location_update(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        body = bytes([2, 128, 129, 8, 130, 131, 9]) + (-1).to_bytes(2, "little", signed=True) + (1).to_bytes(2, "little", signed=True) + bytes([1]) + agent_id.bytes
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0x06]) + body)
        parsed = parse_coarse_location_update(dispatched)
        self.assertEqual(len(parsed.locations), 2)
        self.assertEqual(parsed.locations[0].x, 128)
        self.assertEqual(parsed.you_index, -1)
        self.assertEqual(parsed.agent_ids, (agent_id,))

    def test_parse_simulator_viewer_time(self) -> None:
        body = (
            (123456789).to_bytes(8, "little")
            + (86400).to_bytes(4, "little")
            + (31536000).to_bytes(4, "little")
            + bytes.fromhex("0000803f0000000000000000")
            + bytes.fromhex("0000003f")
            + bytes.fromhex("000000000000803f00000000")
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x96]) + body)
        parsed = parse_simulator_viewer_time(dispatched)
        self.assertEqual(parsed.usec_since_start, 123456789)
        self.assertEqual(parsed.sec_per_day, 86400)
        self.assertEqual(parsed.sun_phase, 0.5)

    def test_parse_object_update_summary(self) -> None:
        body = (123456789).to_bytes(8, "little") + (42).to_bytes(2, "little") + bytes([3]) + b"\x00" * 5
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)
        parsed = parse_object_update_summary(dispatched)
        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.time_dilation, 42)
        self.assertEqual(parsed.object_count, 3)
