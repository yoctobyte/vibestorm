import unittest
from pathlib import Path
from struct import pack
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
    parse_improved_terse_object_update,
    parse_object_update,
    parse_object_update_summary,
    parse_packet_ack,
    parse_agent_movement_complete,
    parse_chat_from_simulator,
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
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([3])
            + full_id.bytes
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
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)
        parsed = parse_object_update_summary(dispatched)
        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.time_dilation, 42)
        self.assertEqual(parsed.object_count, 1)

    def test_parse_object_update(self) -> None:
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([3])
            + full_id.bytes
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
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)
        parsed = parse_object_update(dispatched)

        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.time_dilation, 42)
        self.assertEqual(len(parsed.objects), 1)
        self.assertEqual(parsed.objects[0].local_id, 7)
        self.assertEqual(parsed.objects[0].full_id, full_id)
        self.assertEqual(parsed.objects[0].parent_id, 0)
        self.assertEqual(parsed.objects[0].update_flags, 5)
        self.assertEqual(parsed.objects[0].object_data_size, 60)
        self.assertEqual(parsed.objects[0].variant, "prim_basic")
        self.assertEqual(parsed.objects[0].position, (1.0, 2.0, 3.0))
        self.assertEqual(parsed.objects[0].texture_entry_size, 0)
        self.assertEqual(parsed.objects[0].interesting_payloads, ())

    def test_parse_avatar_style_object_update(self) -> None:
        avatar_id = UUID("11111111-2222-3333-4444-555555555555")
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
            + avatar_id.bytes
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
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)

        self.assertEqual(parsed.objects[0].variant, "avatar_basic")
        self.assertEqual(parsed.objects[0].position, (128.0, 128.0, 25.959999084472656))
        self.assertEqual(parsed.objects[0].name_values["FirstName"], "Vibestorm")
        self.assertEqual(parsed.objects[0].name_values["LastName"], "Admin")

    def test_parse_prim_object_update_with_texture_entry(self) -> None:
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        texture_id = UUID("00895567-4724-cb43-ed92-0b47caed1546")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        texture_entry = texture_id.bytes + (b"\x00" * 48)
        body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([3])
            + full_id.bytes
            + (99).to_bytes(4, "little")
            + bytes([9, 3, 1])
            + pack("<fff", 1.0, 2.0, 3.0)
            + bytes([len(object_data)])
            + object_data
            + (0).to_bytes(4, "little")
            + (5).to_bytes(4, "little")
            + (b"\x00" * 22)
            + len(texture_entry).to_bytes(2, "little")
            + texture_entry
            + bytes([0])
            + (0).to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + bytes([0])
            + (b"\x00" * 4)
            + bytes([0, 0, 0])
            + (b"\x00" * 66)
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)

        self.assertEqual(parsed.objects[0].texture_entry_size, 64)
        self.assertEqual(parsed.objects[0].default_texture_id, texture_id)
        self.assertEqual(parsed.objects[0].interesting_payloads[0].field_name, "TextureEntry")
        self.assertEqual(parsed.objects[0].interesting_payloads[0].size, 64)

    def test_parse_prim_object_update_collects_interesting_unknown_payloads(self) -> None:
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        text_payload = b"cube with hovertext"
        body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([3])
            + full_id.bytes
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
            + bytes([len(text_payload)])
            + text_payload
            + bytes([255, 0, 0, 255])
            + bytes([0])
            + bytes([0])
            + bytes([0])
            + (b"\x00" * 66)
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)

        payloads = {payload.field_name: payload for payload in parsed.objects[0].interesting_payloads}
        self.assertEqual(payloads["Text"].text_preview, "cube with hovertext")
        self.assertEqual(payloads["TextColor"].preview_hex, "ff0000ff")

    def test_parse_chat_from_simulator(self) -> None:
        source_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        owner_id = UUID("11111111-2222-3333-4444-555555555555")
        from_name = b"Vibestorm Admin"
        chat_text = "testing hovertext capture".encode("utf-8")
        body = (
            bytes([len(from_name)])
            + from_name
            + source_id.bytes
            + owner_id.bytes
            + bytes([1, 1, 2])
            + pack("<fff", 128.0, 129.0, 25.0)
            + len(chat_text).to_bytes(2, "little")
            + chat_text
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x8B]) + body)

        parsed = parse_chat_from_simulator(dispatched)

        self.assertEqual(parsed.from_name, "Vibestorm Admin")
        self.assertEqual(parsed.source_id, source_id)
        self.assertEqual(parsed.owner_id, owner_id)
        self.assertEqual(parsed.chat_type, 1)
        self.assertEqual(parsed.audible, 2)
        self.assertEqual(parsed.position, (128.0, 129.0, 25.0))
        self.assertEqual(parsed.message, "testing hovertext capture")

    def test_parse_chat_from_simulator_trims_nul_terminators(self) -> None:
        source_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        owner_id = UUID("11111111-2222-3333-4444-555555555555")
        from_name = b"Vibestorm Tester\x00"
        chat_text = b"rezzed another cube\x00"
        body = (
            bytes([len(from_name)])
            + from_name
            + source_id.bytes
            + owner_id.bytes
            + bytes([1, 1, 1])
            + pack("<fff", 128.0, 129.0, 25.0)
            + len(chat_text).to_bytes(2, "little")
            + chat_text
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x8B]) + body)

        parsed = parse_chat_from_simulator(dispatched)

        self.assertEqual(parsed.from_name, "Vibestorm Tester")
        self.assertEqual(parsed.message, "rezzed another cube")

    def test_parse_improved_terse_object_update(self) -> None:
        data_payload = bytes.fromhex("01020304aabbccdd")
        texture_payload = bytes.fromhex("11223344")
        body = (
            (1099511628032000).to_bytes(8, "little")
            + (65535).to_bytes(2, "little")
            + bytes([2])
            + bytes([len(data_payload)])
            + data_payload
            + len(texture_payload).to_bytes(2, "little")
            + texture_payload
            + bytes([3])
            + b"\x99\x88\x77"
            + (0).to_bytes(2, "little")
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0F]) + body)

        parsed = parse_improved_terse_object_update(dispatched)

        self.assertEqual(parsed.region_handle, 1099511628032000)
        self.assertEqual(parsed.time_dilation, 65535)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].local_id, 0x04030201)
        self.assertEqual(parsed.objects[0].data_size, len(data_payload))
        self.assertEqual(parsed.objects[0].texture_entry_size, len(texture_payload))
        self.assertEqual(parsed.objects[0].texture_entry_preview_hex, "11223344")
        self.assertIsNone(parsed.objects[1].local_id)
        self.assertEqual(parsed.objects[1].data_size, 3)
        self.assertEqual(parsed.objects[1].texture_entry_size, 0)
