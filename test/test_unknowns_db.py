import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from vibestorm.fixtures.unknowns_db import UnknownsDatabase
from vibestorm.udp.messages import (
    ImprovedTerseObjectEntry,
    KillObjectMessage,
    ObjectUpdateCachedEntry,
    ObjectUpdateCompressedEntry,
    ObjectUpdateEntry,
    ObjectUpdatePayloadSummary,
)


class UnknownsDatabaseTests(unittest.TestCase):
    def test_records_and_summarizes_object_update_observations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            entry = ObjectUpdateEntry(
                local_id=7,
                state=0,
                full_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                crc=99,
                pcode=9,
                material=3,
                click_action=1,
                scale=(1.0, 1.0, 1.0),
                object_data_size=60,
                parent_id=0,
                update_flags=5,
                position=(1.0, 2.0, 3.0),
                rotation=(0.0, 0.0, 0.0, 1.0),
                variant="prim_basic",
                name_values={"Name": "cube"},
                texture_entry_size=64,
                texture_anim_size=0,
                data_size=0,
                text_size=0,
                media_url_size=0,
                ps_block_size=0,
                extra_params_size=0,
                default_texture_id=UUID("00895567-4724-cb43-ed92-0b47caed1546"),
                interesting_payloads=(
                    ObjectUpdatePayloadSummary(
                        field_name="TextureEntry",
                        size=64,
                        non_zero_bytes=20,
                        preview_hex="008955674724cb43ed920b47caed1546",
                        text_preview=None,
                    ),
                ),
            )

            packet_id = database.record_object_update_packet(
                session_id=session_id,
                observed_at_seconds=1.25,
                message_sequence=77,
                capture_reason="world.object_update_rich",
                region_handle=123456789,
                object_count=1,
                decode_status="decoded",
                decode_error=None,
                packet_tags=["decoded", "single_object", "interesting"],
            )
            database.record_object_update_entity(
                packet_id=packet_id,
                session_id=session_id,
                observed_at_seconds=1.25,
                message_sequence=77,
                capture_reason="world.object_update_rich",
                region_handle=123456789,
                entry=entry,
            )

            stats = database.read_stats(session_id=session_id)
            packet_summary = database.summarize_object_update_packets(limit=5, session_id=session_id)
            summary = database.summarize_payload_fingerprints(limit=5, session_id=session_id)

            self.assertEqual(stats.packet_count, 1)
            self.assertEqual(stats.entity_count, 1)
            self.assertEqual(stats.distinct_objects, 1)
            self.assertEqual(stats.distinct_fingerprints, 1)
            self.assertEqual(stats.rich_entities, 1)
            self.assertEqual(packet_summary[0]["decode_status"], "decoded")
            self.assertEqual(summary[0]["label"], "cube")
            self.assertEqual(summary[0]["sample_payloads"][0]["field_name"], "TextureEntry")

    def test_records_nearby_chat_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            database.record_nearby_chat(
                session_id=session_id,
                observed_at_seconds=12.5,
                message_sequence=101,
                from_name="Vibestorm Admin",
                source_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                owner_id="11111111-2222-3333-4444-555555555555",
                source_type=1,
                chat_type=1,
                audible=2,
                position=(128.0, 128.0, 25.0),
                message="typing what I am changing",
            )

            chat = database.recent_nearby_chat(limit=5, session_id=session_id)

            self.assertEqual(len(chat), 1)
            self.assertEqual(chat[0]["from_name"], "Vibestorm Admin")
            self.assertEqual(chat[0]["message"], "typing what I am changing")

    def test_records_unknown_udp_messages(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            database.record_unknown_udp_message(
                session_id=session_id,
                observed_at_seconds=3.5,
                message_sequence=44,
                failure_stage="dispatch",
                raw_message_number=0xFFFF00AA,
                encoded_length=4,
                payload=b"\xff\xff\x00\xaa\x01\x02\x03",
                error_text="unknown message number 0xFFFF00AA",
            )

            stats = database.read_stats(session_id=session_id)
            messages = database.recent_unknown_udp_messages(limit=5, session_id=session_id)

            self.assertEqual(stats.unknown_udp_messages, 1)
            self.assertEqual(messages[0]["failure_stage"], "dispatch")
            self.assertEqual(messages[0]["raw_message_number"], 0xFFFF00AA)

    def test_records_inbound_message_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            database.record_inbound_message(
                session_id=session_id,
                observed_at_seconds=2.0,
                message_sequence=55,
                message_name="SimStats",
                frequency="Low",
                wire_message_number=0xFFFF008C,
                body_size=30,
                is_reliable=False,
                payload_preview_hex="01020304",
            )

            stats = database.read_stats(session_id=session_id)
            messages = database.summarize_inbound_messages(limit=5, session_id=session_id)

            self.assertEqual(stats.inbound_messages, 1)
            self.assertEqual(messages[0]["message_name"], "SimStats")
            self.assertEqual(messages[0]["wire_message_number"], 0xFFFF008C)

    def test_records_improved_terse_observations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            packet_id = database.record_improved_terse_packet(
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                object_count=2,
                time_dilation=65535,
                packet_tags=["has_local_id", "has_texture_entry"],
            )
            database.record_improved_terse_entity(
                packet_id=packet_id,
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                entry=ImprovedTerseObjectEntry(
                    local_id=367911609,
                    state=1,
                    is_avatar=False,
                    position=(1.0, 2.0, 3.0),
                    velocity=(0.0, 0.0, 0.0),
                    acceleration=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    angular_velocity=(0.0, 0.0, 0.0),
                ),
            )
            database.record_improved_terse_entity(
                packet_id=packet_id,
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                entry=ImprovedTerseObjectEntry(
                    local_id=367911629,
                    state=1,
                    is_avatar=False,
                    position=(4.0, 5.0, 6.0),
                    velocity=(0.0, 0.0, 0.0),
                    acceleration=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    angular_velocity=(0.0, 0.0, 0.0),
                    texture_entry=b"\x11\x22\x33\x44",
                ),
            )

            stats = database.read_stats(session_id=session_id)
            packet_summary = database.summarize_improved_terse_packets(limit=5, session_id=session_id)
            local_id_summary = database.summarize_improved_terse_local_ids(limit=5, session_id=session_id)

            self.assertEqual(stats.terse_packet_count, 1)
            self.assertEqual(stats.terse_entity_count, 2)
            self.assertEqual(stats.terse_distinct_local_ids, 2)
            self.assertEqual(stats.terse_rich_entities, 1)
            self.assertEqual(packet_summary[0]["total_objects"], 2)
            self.assertEqual({item["local_id"] for item in local_id_summary}, {367911609, 367911629})

    def test_summarizes_terse_local_id_correlations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )
            packet_id = database.record_improved_terse_packet(
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                object_count=2,
                time_dilation=65535,
                packet_tags=["has_local_id"],
            )
            database.record_improved_terse_entity(
                packet_id=packet_id,
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                entry=ImprovedTerseObjectEntry(
                    local_id=101,
                    state=1,
                    is_avatar=False,
                    position=(1.0, 2.0, 3.0),
                    velocity=(0.0, 0.0, 0.0),
                    acceleration=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    angular_velocity=(0.0, 0.0, 0.0),
                ),
            )
            database.record_improved_terse_entity(
                packet_id=packet_id,
                session_id=session_id,
                observed_at_seconds=5.0,
                message_sequence=88,
                capture_reason="world.improved_terse_object_update",
                region_handle=1099511628032000,
                entry=ImprovedTerseObjectEntry(
                    local_id=202,
                    state=1,
                    is_avatar=False,
                    position=(4.0, 5.0, 6.0),
                    velocity=(0.0, 0.0, 0.0),
                    acceleration=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    angular_velocity=(0.0, 0.0, 0.0),
                ),
            )

            object_packet_id = database.record_object_update_packet(
                session_id=session_id,
                observed_at_seconds=6.0,
                message_sequence=89,
                capture_reason="world.object_update_rich",
                region_handle=1099511628032000,
                object_count=1,
                decode_status="decoded",
                decode_error=None,
                packet_tags=["decoded"],
            )
            database.record_object_update_entity(
                packet_id=object_packet_id,
                session_id=session_id,
                observed_at_seconds=6.0,
                message_sequence=89,
                capture_reason="world.object_update_rich",
                region_handle=1099511628032000,
                entry=ObjectUpdateEntry(
                    local_id=202,
                    state=0,
                    full_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
                    crc=99,
                    pcode=9,
                    material=3,
                    click_action=1,
                    scale=(1.0, 1.0, 1.0),
                    object_data_size=60,
                    parent_id=0,
                    update_flags=5,
                    position=(1.0, 2.0, 3.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                    variant="prim_basic",
                    name_values={},
                    texture_entry_size=0,
                    texture_anim_size=0,
                    data_size=0,
                    text_size=0,
                    media_url_size=0,
                    ps_block_size=0,
                    extra_params_size=0,
                    extra_params_entries=(),
                    default_texture_id=None,
                    interesting_payloads=(),
                ),
            )

            summary = database.summarize_improved_terse_local_id_correlations(limit=5, session_id=session_id)

            by_id = {item["local_id"]: item for item in summary}
            self.assertEqual(by_id[101]["status"], "terse_only")
            self.assertEqual(by_id[101]["full_seen_count"], 0)
            self.assertEqual(by_id[202]["status"], "promoted")
            self.assertEqual(by_id[202]["full_seen_count"], 1)
            self.assertEqual(by_id[202]["sample_full_id"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    def test_latest_session_filters_new_runs_without_deleting_old_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            first_session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="first-agent",
                configured_duration_seconds=60.0,
            )
            second_session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="second-agent",
                configured_duration_seconds=120.0,
            )

            database.record_inbound_message(
                session_id=first_session_id,
                observed_at_seconds=1.0,
                message_sequence=10,
                message_name="SimStats",
                frequency="Low",
                wire_message_number=0xFFFF008C,
                body_size=30,
                is_reliable=False,
                payload_preview_hex="aaaa",
            )
            database.record_inbound_message(
                session_id=second_session_id,
                observed_at_seconds=2.0,
                message_sequence=20,
                message_name="ChatFromSimulator",
                frequency="Low",
                wire_message_number=0xFFFF008B,
                body_size=40,
                is_reliable=False,
                payload_preview_hex="bbbb",
            )

            latest = database.latest_session()
            assert latest is not None
            self.assertEqual(latest.session_id, second_session_id)
            self.assertEqual(database.read_stats().inbound_messages, 2)
            self.assertEqual(database.read_stats(session_id=second_session_id).inbound_messages, 1)

    def test_records_kill_object_packets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )

            database.record_kill_object_packet(
                session_id=session_id,
                observed_at_seconds=7.5,
                message_sequence=111,
                capture_reason="world.kill_object",
                message=KillObjectMessage(local_ids=(7, 9, 11)),
            )

            with database._connect() as connection:
                row = connection.execute(
                    """
                    SELECT message_sequence, capture_reason, local_ids_json
                    FROM kill_object_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()

            assert row is not None
            self.assertEqual(row["message_sequence"], 111)
            self.assertEqual(row["capture_reason"], "world.kill_object")
            self.assertEqual(row["local_ids_json"], "[7, 9, 11]")

    def test_records_cached_and_compressed_packets(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
            session_id = database.begin_session(
                sim_ip="127.0.0.1",
                sim_port=9000,
                agent_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                configured_duration_seconds=60.0,
            )

            cached_packet_id = database.record_cached_packet(
                session_id=session_id,
                observed_at_seconds=8.0,
                message_sequence=120,
                capture_reason="world.object_update_cached",
                region_handle=123456789,
                time_dilation=42,
                packet_tags=["object_count:2"],
            )
            database.record_cached_entity(
                packet_id=cached_packet_id,
                session_id=session_id,
                observed_at_seconds=8.0,
                message_sequence=120,
                capture_reason="world.object_update_cached",
                region_handle=123456789,
                entry=ObjectUpdateCachedEntry(local_id=7, crc=0x11111111, update_flags=5),
            )

            compressed_packet_id = database.record_compressed_packet(
                session_id=session_id,
                observed_at_seconds=9.0,
                message_sequence=121,
                capture_reason="world.object_update_compressed",
                region_handle=123456789,
                time_dilation=43,
                packet_tags=["object_count:1"],
            )
            database.record_compressed_entity(
                packet_id=compressed_packet_id,
                session_id=session_id,
                observed_at_seconds=9.0,
                message_sequence=121,
                capture_reason="world.object_update_compressed",
                region_handle=123456789,
                entry=ObjectUpdateCompressedEntry(update_flags=6, data=b"\xaa\xbb\xcc\xdd"),
            )

            with database._connect() as connection:
                cached_row = connection.execute(
                    """
                    SELECT region_handle, time_dilation, packet_tags_json
                    FROM cached_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                cached_entity_row = connection.execute(
                    """
                    SELECT local_id, crc, update_flags
                    FROM cached_entities
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                compressed_row = connection.execute(
                    """
                    SELECT region_handle, time_dilation, packet_tags_json
                    FROM compressed_packets
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                compressed_entity_row = connection.execute(
                    """
                    SELECT update_flags, data_size, data_preview_hex
                    FROM compressed_entities
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()

            assert cached_row is not None
            assert cached_entity_row is not None
            assert compressed_row is not None
            assert compressed_entity_row is not None
            self.assertEqual(cached_row["region_handle"], 123456789)
            self.assertEqual(cached_row["time_dilation"], 42)
            self.assertEqual(cached_entity_row["local_id"], 7)
            self.assertEqual(cached_entity_row["crc"], 0x11111111)
            self.assertEqual(cached_entity_row["update_flags"], 5)
            self.assertEqual(compressed_row["region_handle"], 123456789)
            self.assertEqual(compressed_row["time_dilation"], 43)
            self.assertEqual(compressed_entity_row["update_flags"], 6)
            self.assertEqual(compressed_entity_row["data_size"], 4)
            self.assertEqual(compressed_entity_row["data_preview_hex"], "aabbccdd")
