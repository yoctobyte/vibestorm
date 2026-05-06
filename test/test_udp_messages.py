import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    AgentWearableEntry,
    encode_agent_cached_texture,
    encode_object_add,
    encode_agent_is_now_wearing,
    encode_agent_set_appearance,
    encode_agent_update,
    encode_agent_throttle,
    encode_agent_wearables_request,
    encode_chat_from_viewer,
    encode_complete_agent_movement,
    encode_complete_ping_check,
    encode_map_block_request,
    encode_packet_ack,
    encode_region_handshake_reply,
    encode_teleport_location_request,
    encode_use_circuit_code,
    parse_coarse_location_update,
    parse_improved_terse_object_update,
    parse_agent_alert_message,
    parse_alert_message,
    parse_improved_instant_message,
    parse_kill_object,
    parse_map_block_reply,
    parse_object_extra_params,
    parse_object_update,
    parse_object_update_cached,
    parse_object_update_compressed,
    parse_object_properties_family,
    parse_object_update_summary,
    parse_packet_ack,
    parse_agent_cached_texture_response,
    parse_agent_movement_complete,
    parse_agent_wearables_update,
    parse_avatar_appearance,
    parse_chat_from_simulator,
    parse_layer_data,
    parse_complete_ping_check,
    parse_region_handshake,
    parse_sim_stats,
    parse_simulator_viewer_time,
    parse_start_ping_check,
    parse_use_circuit_code,
    parse_shape_extra_params,
)


class SemanticMessageTests(unittest.TestCase):
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

    def _build_terse_prim_data(self) -> bytes:
        return (
            (0x04030201).to_bytes(4, "little")
            + bytes([0x21])  # attachment/state byte
            + bytes([0])  # is_avatar
            + pack("<fff", 1.0, 2.0, 3.0)
            + self._encode_v16(4.0, 128.0)
            + self._encode_v16(5.0, 128.0)
            + self._encode_v16(6.0, 128.0)
            + self._encode_v16(0.5, 64.0)
            + self._encode_v16(1.5, 64.0)
            + self._encode_v16(2.5, 64.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(1.0, 1.0)
            + self._encode_v16(3.0, 64.0)
            + self._encode_v16(4.0, 64.0)
            + self._encode_v16(5.0, 64.0)
        )

    def _build_terse_avatar_data(self) -> bytes:
        return (
            (0x08070605).to_bytes(4, "little")
            + bytes([0x07])  # state
            + bytes([1])  # is_avatar
            + pack("<ffff", 0.0, 0.0, 0.0, 1.0)
            + pack("<fff", 10.0, 20.0, 30.0)
            + self._encode_v16(1.0, 128.0)
            + self._encode_v16(2.0, 128.0)
            + self._encode_v16(3.0, 128.0)
            + self._encode_v16(0.0, 64.0)
            + self._encode_v16(0.0, 64.0)
            + self._encode_v16(0.0, 64.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(0.0, 1.0)
            + self._encode_v16(1.0, 1.0)
            + self._encode_v16(0.0, 64.0)
            + self._encode_v16(0.0, 64.0)
            + self._encode_v16(0.0, 64.0)
        )

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

    def test_encode_agent_update_control_flags_round_trip(self) -> None:
        from struct import unpack_from

        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        flags = 0x02000401  # AT_POS | FAST_AT | TURN_LEFT
        payload = encode_agent_update(
            agent_id,
            session_id,
            body_rotation=(0.1, 0.2, 0.3),
            head_rotation=(0.4, 0.5, 0.6),
            state=2,
            control_flags=flags,
            flags=0x80,
        )
        # Header(1) + agent(16) + session(16) + body(12) + head(12) + state(1)
        # + camera 4*12=48 + far(4) = 110 bytes before ControlFlags U32.
        offset = 1 + 16 + 16 + 12 + 12 + 1 + 48 + 4
        (parsed_flags,) = unpack_from("<I", payload, offset)
        self.assertEqual(parsed_flags, flags)
        self.assertEqual(payload[1 + 16 + 16 + 12 + 12], 2)  # state
        self.assertEqual(payload[-1], 0x80)  # trailing Flags U8

    def test_encode_agent_throttle_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_throttle(agent_id, session_id, 0x12345678)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentThrottle")

    def test_encode_agent_wearables_request_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_wearables_request(agent_id, session_id)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentWearablesRequest")

    def test_encode_agent_is_now_wearing_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_is_now_wearing(
            agent_id,
            session_id,
            (
                AgentWearableEntry(
                    item_id=UUID("00000000-0000-0000-0000-000000000001"),
                    asset_id=UUID("00000000-0000-0000-0000-000000000002"),
                    wearable_type=4,
                ),
            ),
        )
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentIsNowWearing")

    def test_encode_agent_set_appearance_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_set_appearance(agent_id, session_id, serial_num=7)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentSetAppearance")

    def test_encode_agent_cached_texture_dispatches(self) -> None:
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        payload = encode_agent_cached_texture(agent_id, session_id, serial_num=7)
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "AgentCachedTexture")

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

    def test_parse_agent_wearables_update(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        item_id = UUID("00000000-0000-0000-0000-000000000010")
        asset_id = UUID("00000000-0000-0000-0000-000000000020")
        body = (
            agent_id.bytes
            + session_id.bytes
            + (7).to_bytes(4, "little")
            + bytes([1])
            + item_id.bytes
            + asset_id.bytes
            + bytes([5])
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x01, 0x7E]) + body)
        parsed = parse_agent_wearables_update(dispatched)
        self.assertEqual(parsed.serial_num, 7)
        self.assertEqual(len(parsed.wearables), 1)
        self.assertEqual(parsed.wearables[0].item_id, item_id)
        self.assertEqual(parsed.wearables[0].asset_id, asset_id)
        self.assertEqual(parsed.wearables[0].wearable_type, 5)

    def test_parse_agent_cached_texture_response(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        texture_id = UUID("00000000-0000-0000-0000-000000000030")
        body = (
            agent_id.bytes
            + session_id.bytes
            + (7).to_bytes(4, "little", signed=True)
            + bytes([1])
            + texture_id.bytes
            + bytes([8, 0])
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x01, 0x81]) + body)
        parsed = parse_agent_cached_texture_response(dispatched)
        self.assertEqual(parsed.serial_num, 7)
        self.assertEqual(len(parsed.textures), 1)
        self.assertEqual(parsed.textures[0].texture_id, texture_id)
        self.assertEqual(parsed.textures[0].texture_index, 8)
        self.assertEqual(parsed.textures[0].host_name, "")

    def test_parse_avatar_appearance(self) -> None:
        sender_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        texture_entry = UUID("89556747-24cb-43ed-920b-47caed15465f").bytes
        visual_params = bytes([1, 2, 3, 4])
        attachment_id = UUID("00000000-0000-0000-0000-000000000040")
        body = (
            sender_id.bytes
            + bytes([0])
            + len(texture_entry).to_bytes(2, "little")
            + texture_entry
            + bytes([len(visual_params)])
            + visual_params
            + bytes([1])
            + bytes([2])
            + (7).to_bytes(4, "little", signed=True)
            + (9).to_bytes(4, "little")
            + bytes([1])
            + pack("<fff", 0.0, 0.0, 1.5)
            + bytes([1])
            + attachment_id.bytes
            + bytes([3])
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x9E]) + body)
        parsed = parse_avatar_appearance(dispatched)
        self.assertEqual(parsed.sender_id, sender_id)
        self.assertEqual(parsed.texture_entry, texture_entry)
        self.assertEqual(parsed.visual_params, visual_params)
        self.assertEqual(parsed.appearance_version, 2)
        self.assertEqual(parsed.cof_version, 7)
        self.assertEqual(parsed.appearance_flags, 9)
        self.assertEqual(parsed.hover_height, (0.0, 0.0, 1.5))
        self.assertEqual(parsed.attachments, ((attachment_id, 3),))

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

    def test_parse_region_handshake_trims_trailing_nul(self) -> None:
        sim_name = b"TestSim\x00"
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
        body += bytes.fromhex("0000a041")
        body += bytes.fromhex("0000803f")
        body += cache_id.bytes
        body += b"\x00" * (16 * 8)
        body += b"\x00" * (4 * 8)
        body += region_id.bytes
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x94]) + bytes(body))

        parsed = parse_region_handshake(dispatched)

        self.assertEqual(parsed.sim_name, "TestSim")

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
            + (b"\x00" * 23)
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
            + (b"\x00" * 23)
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
            + (b"\x00" * 23)
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
            + (b"\x00" * 23)
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

    def test_parse_prim_object_update_decodes_shape_block(self) -> None:
        # Sphere-shaped prim: PathCurve=0x20 (CIRCLE/revolve), ProfileCurve=0x05
        # (HalfCircle), PathScaleX/Y=100, ProfileBegin=0x100, ProfileEnd=0x200,
        # ProfileHollow=0x80. Other fields zero.
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = (
            pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28)
            + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        )
        shape_block = (
            bytes([0x20, 0x05])                         # PathCurve, ProfileCurve
            + (0).to_bytes(2, "little")                 # PathBegin
            + (0).to_bytes(2, "little")                 # PathEnd
            + bytes([100, 100, 0, 0])                   # PathScale X/Y, Shear X/Y
            + bytes([0, 0, 0, 0, 0])                    # PathTwist..PathTaperY
            + bytes([0, 0])                             # PathRevolutions, PathSkew
            + (0x100).to_bytes(2, "little")             # ProfileBegin
            + (0x200).to_bytes(2, "little")             # ProfileEnd
            + (0x80).to_bytes(2, "little")              # ProfileHollow
        )
        assert len(shape_block) == 23
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
            + shape_block
            + (0).to_bytes(2, "little")    # TextureEntry length
            + bytes([0])                    # TextureAnim length
            + (0).to_bytes(2, "little")    # NameValue length
            + (0).to_bytes(2, "little")    # Data length
            + bytes([0])                    # Text length
            + (b"\x00" * 4)                 # TextColor
            + bytes([0, 0, 0])              # MediaURL, PSBlock, ExtraParams (all U8)
            + (b"\x00" * 66)
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)
        shape = parsed.objects[0].shape

        self.assertIsNotNone(shape)
        self.assertEqual(shape.path_curve, 0x20)
        self.assertEqual(shape.profile_curve, 0x05)
        self.assertEqual(shape.path_scale_x, 100)
        self.assertEqual(shape.path_scale_y, 100)
        self.assertEqual(shape.profile_begin, 0x100)
        self.assertEqual(shape.profile_end, 0x200)
        self.assertEqual(shape.profile_hollow, 0x80)

    def test_parse_avatar_object_update_has_no_shape(self) -> None:
        # Avatars (pcode=47) have no path/profile shape data — the parser
        # only sets ``shape`` for prims (pcode=9).
        from vibestorm.udp.messages import parse_object_update as _por  # noqa: F401

        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = (
            (b"\x00" * 16)
            + pack("<fff", 128.0, 128.0, 25.96)
            + (b"\x00" * 48)
        )
        assert len(object_data) == 76
        name_values = b"FirstName STRING RW SV Vibestorm\nLastName STRING RW SV Admin\x00"
        body = (
            (123456789).to_bytes(8, "little")
            + (42).to_bytes(2, "little")
            + bytes([1])
            + (7).to_bytes(4, "little")
            + bytes([0])
            + full_id.bytes
            + (99).to_bytes(4, "little")
            + bytes([47, 0, 0])
            + pack("<fff", 0.45, 0.6, 1.7)
            + bytes([len(object_data)])
            + object_data
            + (0).to_bytes(4, "little")
            + (0).to_bytes(4, "little")
            + (b"\x00" * 23)
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
        self.assertIsNone(parsed.objects[0].shape)

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
            + (b"\x00" * 23)
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

    def test_parse_prim_object_update_decodes_extra_params_blob(self) -> None:
        full_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_data = pack("<fff", 1.0, 2.0, 3.0) + (b"\x00" * 28) + pack("<ffff", 0.0, 0.0, 0.0, 1.0) + (b"\x00" * 4)
        extra_params = (
            bytes([2])
            + (0x10).to_bytes(2, "little")
            + bytes([1])
            + (3).to_bytes(4, "little")
            + b"\x11\x22\x33"
            + (0x20).to_bytes(2, "little")
            + bytes([0])
            + (4).to_bytes(4, "little")
            + b"\xaa\xbb\xcc\xdd"
        )
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
            + (b"\x00" * 23)
            + (0).to_bytes(2, "little")
            + bytes([0])
            + (0).to_bytes(2, "little")
            + (0).to_bytes(2, "little")
            + bytes([0])
            + (b"\x00" * 4)
            + bytes([0])
            + bytes([0])
            + len(extra_params).to_bytes(1, "little")
            + extra_params
            + (b"\x00" * 66)
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)

        self.assertEqual(len(parsed.objects[0].extra_params_entries), 2)
        self.assertEqual(parsed.objects[0].extra_params_entries[0].param_type, 0x10)
        self.assertEqual(parsed.objects[0].extra_params_entries[1].param_data, b"\xaa\xbb\xcc\xdd")
        payloads = {payload.field_name: payload for payload in parsed.objects[0].interesting_payloads}
        self.assertIn("ExtraParamsDecoded", payloads)

    def test_parse_captured_prim_object_update_decodes_sculpt_extra_params(self) -> None:
        body = (Path.cwd() / "test/fixtures/live/ObjectUpdate/005-seq004356.body.bin").read_bytes()
        dispatched = self.dispatcher.dispatch(bytes([0x0C]) + body)

        parsed = parse_object_update(dispatched)

        self.assertEqual(parsed.objects[0].local_id, 492042959)
        self.assertEqual(parsed.objects[0].extra_params_size, 24)
        self.assertEqual(len(parsed.objects[0].extra_params_entries), 1)
        self.assertEqual(parsed.objects[0].extra_params_entries[0].param_type, 0x30)
        self.assertTrue(parsed.objects[0].extra_params_entries[0].param_in_use)
        self.assertEqual(len(parsed.objects[0].extra_params_entries[0].param_data), 17)
        payloads = {payload.field_name: payload for payload in parsed.objects[0].interesting_payloads}
        self.assertIn("ExtraParamsDecoded", payloads)
        self.assertNotIn("Trailing", payloads)

    def test_parse_layer_data_extracts_type_and_data(self) -> None:
        # LayerData is High-frequency message #11 — wire byte 0x0B.
        # Body is: Type (U8) | Data (U16 length-prefix little-endian) | data...
        from vibestorm.udp.messages import (
            LAYER_TYPE_LAND,
            LayerDataMessage,
            MessageDecodeError,
        )

        payload = bytes(range(64))  # 64 bytes of stand-in patch data
        body = (
            bytes([LAYER_TYPE_LAND])
            + len(payload).to_bytes(2, "little")
            + payload
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0B]) + body)

        parsed = parse_layer_data(dispatched)

        self.assertIsInstance(parsed, LayerDataMessage)
        self.assertEqual(parsed.layer_type, LAYER_TYPE_LAND)
        self.assertEqual(parsed.data, payload)

    def test_parse_layer_data_truncated_payload_raises(self) -> None:
        # Declared 100 bytes but only 4 actually present.
        from vibestorm.udp.messages import MessageDecodeError

        body = bytes([0x4C]) + (100).to_bytes(2, "little") + b"abcd"
        dispatched = self.dispatcher.dispatch(bytes([0x0B]) + body)

        with self.assertRaises(MessageDecodeError):
            parse_layer_data(dispatched)

    def test_parse_layer_data_short_header_raises(self) -> None:
        # Need at least 1 (Type) + 2 (length) bytes.
        from vibestorm.udp.messages import MessageDecodeError

        dispatched = self.dispatcher.dispatch(bytes([0x0B, 0x4C]))
        with self.assertRaises(MessageDecodeError):
            parse_layer_data(dispatched)

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
        prim_data = self._build_terse_prim_data()
        avatar_data = self._build_terse_avatar_data()
        texture_payload = b"\x08\x00\x04\x00\x00\x00\x11\x22\x33\x44"
        body = (
            (1099511628032000).to_bytes(8, "little")
            + (65535).to_bytes(2, "little")
            + bytes([2])
            + bytes([len(prim_data)])
            + prim_data
            + len(texture_payload).to_bytes(2, "little")
            + texture_payload
            + bytes([len(avatar_data)])
            + avatar_data
            + (0).to_bytes(2, "little")
        )
        dispatched = self.dispatcher.dispatch(bytes([0x0F]) + body)

        parsed = parse_improved_terse_object_update(dispatched)

        self.assertEqual(parsed.region_handle, 1099511628032000)
        self.assertEqual(parsed.time_dilation, 65535)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].local_id, 0x04030201)
        self.assertFalse(parsed.objects[0].is_avatar)
        self.assertEqual(parsed.objects[0].state, 0x21)
        self.assertAlmostEqual(parsed.objects[0].position[0], 1.0, places=4)
        self.assertAlmostEqual(parsed.objects[0].position[1], 2.0, places=4)
        self.assertAlmostEqual(parsed.objects[0].position[2], 3.0, places=4)
        assert parsed.objects[0].texture_entry is not None
        self.assertEqual(parsed.objects[0].texture_entry, b"\x11\x22\x33\x44")

        self.assertEqual(parsed.objects[1].local_id, 0x08070605)
        self.assertTrue(parsed.objects[1].is_avatar)
        self.assertEqual(parsed.objects[1].state, 0x07)
        self.assertAlmostEqual(parsed.objects[1].position[0], 10.0, places=4)
        self.assertAlmostEqual(parsed.objects[1].position[1], 20.0, places=4)
        self.assertAlmostEqual(parsed.objects[1].position[2], 30.0, places=4)
        self.assertIsNone(parsed.objects[1].texture_entry)
        self.assertIsNotNone(parsed.objects[1].collision_plane)

    def test_parse_kill_object(self) -> None:
        body = bytes([3]) + (7).to_bytes(4, "little") + (9).to_bytes(4, "little") + (11).to_bytes(4, "little")
        dispatched = self.dispatcher.dispatch(bytes([0x10]) + body)

        parsed = parse_kill_object(dispatched)

        self.assertEqual(parsed.local_ids, (7, 9, 11))

    def test_parse_object_update_cached(self) -> None:
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
        dispatched = self.dispatcher.dispatch(bytes([0x0E]) + body)

        parsed = parse_object_update_cached(dispatched)

        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.time_dilation, 42)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].local_id, 7)
        self.assertEqual(parsed.objects[0].crc, 0x11111111)
        self.assertEqual(parsed.objects[0].update_flags, 5)
        self.assertEqual(parsed.objects[1].local_id, 9)

    def test_parse_object_update_compressed(self) -> None:
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
        dispatched = self.dispatcher.dispatch(bytes([0x0D]) + body)

        parsed = parse_object_update_compressed(dispatched)

        self.assertEqual(parsed.region_handle, 123456789)
        self.assertEqual(parsed.time_dilation, 42)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].update_flags, 5)
        self.assertEqual(parsed.objects[0].data, data_a)
        self.assertEqual(parsed.objects[1].update_flags, 6)
        self.assertEqual(parsed.objects[1].data, data_b)

    def test_parse_object_properties_family_with_short_lengths(self) -> None:
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        owner_id = UUID("11111111-2222-3333-4444-555555555555")
        group_id = UUID("99999999-8888-7777-6666-555555555555")
        last_owner_id = UUID("12345678-1234-5678-1234-567812345678")
        name = "Source Cube".encode("utf-8")
        description = "OpenSim-style short UTF-8".encode("utf-8")
        body = (
            (5).to_bytes(4, "little")
            + object_id.bytes
            + owner_id.bytes
            + group_id.bytes
            + (1).to_bytes(4, "little")
            + (2).to_bytes(4, "little")
            + (3).to_bytes(4, "little")
            + (4).to_bytes(4, "little")
            + (5).to_bytes(4, "little")
            + (0).to_bytes(4, "little", signed=True)
            + bytes([2])
            + (150).to_bytes(4, "little", signed=True)
            + (7).to_bytes(4, "little")
            + last_owner_id.bytes
            + len(name).to_bytes(2, "little")
            + name
            + len(description).to_bytes(2, "little")
            + description
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0x0A]) + body)

        parsed = parse_object_properties_family(dispatched)

        self.assertEqual(parsed.object_id, object_id)
        self.assertEqual(parsed.owner_id, owner_id)
        self.assertEqual(parsed.group_id, group_id)
        self.assertEqual(parsed.sale_type, 2)
        self.assertEqual(parsed.sale_price, 150)
        self.assertEqual(parsed.category, 7)
        self.assertEqual(parsed.last_owner_id, last_owner_id)
        self.assertEqual(parsed.name, "Source Cube")
        self.assertEqual(parsed.description, "OpenSim-style short UTF-8")

    def test_parse_object_properties_family_with_byte_lengths(self) -> None:
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        owner_id = UUID("11111111-2222-3333-4444-555555555555")
        group_id = UUID("99999999-8888-7777-6666-555555555555")
        last_owner_id = UUID("12345678-1234-5678-1234-567812345678")
        name = b"TemplateCube"
        description = b"Variable1"
        body = (
            (5).to_bytes(4, "little")
            + object_id.bytes
            + owner_id.bytes
            + group_id.bytes
            + (1).to_bytes(4, "little")
            + (2).to_bytes(4, "little")
            + (3).to_bytes(4, "little")
            + (4).to_bytes(4, "little")
            + (5).to_bytes(4, "little")
            + (0).to_bytes(4, "little", signed=True)
            + bytes([1])
            + (25).to_bytes(4, "little", signed=True)
            + (9).to_bytes(4, "little")
            + last_owner_id.bytes
            + bytes([len(name)])
            + name
            + bytes([len(description)])
            + description
        )
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0x0A]) + body)

        parsed = parse_object_properties_family(dispatched)

        self.assertEqual(parsed.object_id, object_id)
        self.assertEqual(parsed.name, "TemplateCube")
        self.assertEqual(parsed.description, "Variable1")

    def test_parse_shape_extra_params(self) -> None:
        payload = (
            bytes([2])
            + (0x10).to_bytes(2, "little")
            + (3).to_bytes(4, "little")
            + b"\x11\x22\x33"
            + (0x20).to_bytes(2, "little")
            + (4).to_bytes(4, "little")
            + b"\xaa\xbb\xcc\xdd"
        )

        parsed = parse_shape_extra_params(payload)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].param_type, 0x10)
        self.assertTrue(parsed[0].param_in_use)
        self.assertEqual(parsed[0].param_data, b"\x11\x22\x33")
        self.assertEqual(parsed[1].param_type, 0x20)
        self.assertTrue(parsed[1].param_in_use)
        self.assertEqual(parsed[1].param_data, b"\xaa\xbb\xcc\xdd")

    def test_parse_shape_extra_params_with_param_in_use_layout(self) -> None:
        payload = (
            bytes([1])
            + (0x40).to_bytes(2, "little")
            + bytes([0])
            + (2).to_bytes(4, "little")
            + b"\x44\x55"
        )

        parsed = parse_shape_extra_params(payload)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].param_type, 0x40)
        self.assertFalse(parsed[0].param_in_use)
        self.assertEqual(parsed[0].param_data, b"\x44\x55")

    def test_parse_object_extra_params(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        data_a = b"\x11\x22\x33"
        data_b = b"\xaa\xbb\xcc\xdd"
        body = (
            agent_id.bytes
            + session_id.bytes
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
        dispatched = self.dispatcher.dispatch(bytes([0xFF, 0xFF, 0x00, 0x63]) + body)

        parsed = parse_object_extra_params(dispatched)

        self.assertEqual(parsed.agent_id, agent_id)
        self.assertEqual(parsed.session_id, session_id)
        self.assertEqual(len(parsed.objects), 2)
        self.assertEqual(parsed.objects[0].object_local_id, 7)
        self.assertEqual(parsed.objects[0].param_type, 0x10)
        self.assertTrue(parsed.objects[0].param_in_use)
        self.assertEqual(parsed.objects[0].param_data, data_a)
        self.assertEqual(parsed.objects[1].object_local_id, 9)
        self.assertEqual(parsed.objects[1].param_size, 4)
        self.assertEqual(parsed.objects[1].param_data, data_b)

    def test_encode_map_block_request_round_trips(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        payload = encode_map_block_request(
            agent_id,
            session_id,
            min_x=1000,
            max_x=1000,
            min_y=2000,
            max_y=2000,
        )
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "MapBlockRequest")
        body = dispatched.body
        self.assertEqual(UUID(bytes=body[0:16]), agent_id)
        self.assertEqual(UUID(bytes=body[16:32]), session_id)
        # Flags(4) + EstateID(4) + Godlike(1)
        self.assertEqual(body[32:41], b"\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        # MinX, MaxX, MinY, MaxY (each U16 little-endian)
        self.assertEqual(body[41:43], (1000).to_bytes(2, "little"))
        self.assertEqual(body[43:45], (1000).to_bytes(2, "little"))
        self.assertEqual(body[45:47], (2000).to_bytes(2, "little"))
        self.assertEqual(body[47:49], (2000).to_bytes(2, "little"))

    def test_encode_map_block_request_rejects_oversized_coords(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        with self.assertRaises(ValueError):
            encode_map_block_request(
                agent_id, session_id, min_x=70000, max_x=0, min_y=0, max_y=0
            )

    def test_parse_map_block_reply_decodes_two_entries(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        map_image_a = UUID("00000000-1111-2222-3333-444444444444")
        map_image_b = UUID("55555555-6666-7777-8888-999999999999")
        name_a = b"Vibestorm Test\x00"
        name_b = b"Other Region\x00"
        entry_a = (
            (1000).to_bytes(2, "little")  # X
            + (2000).to_bytes(2, "little")  # Y
            + bytes([len(name_a)])
            + name_a
            + bytes([13])  # Access (PG=13)
            + (0xDEADBEEF).to_bytes(4, "little")  # RegionFlags
            + bytes([20])  # WaterHeight
            + bytes([5])  # Agents
            + map_image_a.bytes
        )
        entry_b = (
            (1001).to_bytes(2, "little")
            + (2000).to_bytes(2, "little")
            + bytes([len(name_b)])
            + name_b
            + bytes([21])  # Access (Mature=21)
            + (0).to_bytes(4, "little")
            + bytes([23])
            + bytes([0])
            + map_image_b.bytes
        )
        body = (
            agent_id.bytes
            + (0).to_bytes(4, "little")  # Flags
            + bytes([2])  # entry count
            + entry_a
            + entry_b
        )
        dispatched = self.dispatcher.dispatch(b"\xFF\xFF\x01\x99" + body)

        parsed = parse_map_block_reply(dispatched)

        self.assertEqual(parsed.agent_id, agent_id)
        self.assertEqual(parsed.flags, 0)
        self.assertEqual(len(parsed.entries), 2)
        self.assertEqual(parsed.entries[0].x, 1000)
        self.assertEqual(parsed.entries[0].y, 2000)
        self.assertEqual(parsed.entries[0].name, "Vibestorm Test")
        self.assertEqual(parsed.entries[0].access, 13)
        self.assertEqual(parsed.entries[0].region_flags, 0xDEADBEEF)
        self.assertEqual(parsed.entries[0].water_height, 20)
        self.assertEqual(parsed.entries[0].agents, 5)
        self.assertEqual(parsed.entries[0].map_image_id, map_image_a)
        self.assertEqual(parsed.entries[1].name, "Other Region")
        self.assertEqual(parsed.entries[1].map_image_id, map_image_b)

    def test_encode_chat_from_viewer_round_trips(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        payload = encode_chat_from_viewer(
            agent_id, session_id, "hello, world", chat_type=1, channel=0
        )
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "ChatFromViewer")
        body = dispatched.body
        self.assertEqual(UUID(bytes=body[0:16]), agent_id)
        self.assertEqual(UUID(bytes=body[16:32]), session_id)
        msg_len = int.from_bytes(body[32:34], "little")
        self.assertEqual(msg_len, len("hello, world") + 1)
        self.assertEqual(body[34 : 34 + msg_len], b"hello, world\x00")
        self.assertEqual(body[34 + msg_len], 1)  # chat_type
        self.assertEqual(int.from_bytes(body[35 + msg_len : 39 + msg_len], "little", signed=True), 0)

    def test_encode_chat_from_viewer_supports_negative_channel(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        payload = encode_chat_from_viewer(
            agent_id, session_id, "/me waves", chat_type=2, channel=-1234
        )
        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "ChatFromViewer")
        body = dispatched.body
        msg_len = int.from_bytes(body[32:34], "little")
        self.assertEqual(body[34 + msg_len], 2)
        self.assertEqual(int.from_bytes(body[35 + msg_len : 39 + msg_len], "little", signed=True), -1234)

    def test_encode_teleport_location_request_round_trips(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        payload = encode_teleport_location_request(
            agent_id,
            session_id,
            region_handle=(256 << 32) | 512,
            position=(10.0, 20.0, 30.0),
            look_at=(1.0, 0.0, 0.0),
        )

        dispatched = self.dispatcher.dispatch(payload)
        self.assertEqual(dispatched.summary.name, "TeleportLocationRequest")
        body = dispatched.body
        self.assertEqual(UUID(bytes=body[0:16]), agent_id)
        self.assertEqual(UUID(bytes=body[16:32]), session_id)
        self.assertEqual(int.from_bytes(body[32:40], "little"), (256 << 32) | 512)
        self.assertEqual(body[40:52], pack("<fff", 10.0, 20.0, 30.0))
        self.assertEqual(body[52:64], pack("<fff", 1.0, 0.0, 0.0))

    def test_parse_improved_instant_message_decodes_basic_im(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        session_id = UUID("11111111-2222-3333-4444-555555555555")
        to_agent_id = UUID("22222222-3333-4444-5555-666666666666")
        region_id = UUID("77777777-8888-9999-aaaa-bbbbbbbbbbbb")
        im_id = UUID("ccccccc1-2222-3333-4444-555555555555")
        from_name = b"Some Sender\x00"
        msg_text = b"hello there\x00"
        bucket = b"\x01\x02\x03"
        body = (
            agent_id.bytes
            + session_id.bytes
            + bytes([0])  # FromGroup
            + to_agent_id.bytes
            + (4096).to_bytes(4, "little")  # ParentEstateID
            + region_id.bytes
            + pack("<fff", 128.0, 64.0, 22.5)  # Position
            + bytes([0])  # Offline
            + bytes([0])  # Dialog
            + im_id.bytes
            + (1700000000).to_bytes(4, "little")  # Timestamp
            + bytes([len(from_name)])
            + from_name
            + len(msg_text).to_bytes(2, "little")
            + msg_text
            + len(bucket).to_bytes(2, "little")
            + bucket
        )
        dispatched = self.dispatcher.dispatch(b"\xFF\xFF\x00\xFE" + body)

        parsed = parse_improved_instant_message(dispatched)

        self.assertEqual(parsed.agent_id, agent_id)
        self.assertEqual(parsed.session_id, session_id)
        self.assertFalse(parsed.from_group)
        self.assertEqual(parsed.to_agent_id, to_agent_id)
        self.assertEqual(parsed.parent_estate_id, 4096)
        self.assertEqual(parsed.region_id, region_id)
        self.assertEqual(parsed.position, (128.0, 64.0, 22.5))
        self.assertEqual(parsed.dialog, 0)
        self.assertEqual(parsed.im_id, im_id)
        self.assertEqual(parsed.timestamp, 1700000000)
        self.assertEqual(parsed.from_agent_name, "Some Sender")
        self.assertEqual(parsed.message, "hello there")
        self.assertEqual(parsed.binary_bucket, bucket)

    def test_parse_alert_message_decodes_text(self) -> None:
        text = b"System message\x00"
        body = bytes([len(text)]) + text
        dispatched = self.dispatcher.dispatch(b"\xFF\xFF\x00\x86" + body)
        parsed = parse_alert_message(dispatched)
        self.assertEqual(parsed.message, "System message")

    def test_parse_agent_alert_message_decodes_modal_flag(self) -> None:
        agent_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        text = b"You cannot do that here\x00"
        body = agent_id.bytes + bytes([1]) + bytes([len(text)]) + text
        dispatched = self.dispatcher.dispatch(b"\xFF\xFF\x00\x87" + body)
        parsed = parse_agent_alert_message(dispatched)
        self.assertEqual(parsed.agent_id, agent_id)
        self.assertTrue(parsed.modal)
        self.assertEqual(parsed.message, "You cannot do that here")
