import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from vibestorm.fixtures.unknowns_db import UnknownsDatabase
from vibestorm.udp.messages import ObjectUpdateEntry, ObjectUpdatePayloadSummary


class UnknownsDatabaseTests(unittest.TestCase):
    def test_records_and_summarizes_object_update_observations(self) -> None:
        with TemporaryDirectory() as tmpdir:
            database = UnknownsDatabase(Path(tmpdir) / "unknowns.sqlite3")
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
                observed_at_seconds=1.25,
                message_sequence=77,
                capture_reason="world.object_update_rich",
                region_handle=123456789,
                entry=entry,
            )

            stats = database.read_stats()
            packet_summary = database.summarize_object_update_packets(limit=5)
            summary = database.summarize_payload_fingerprints(limit=5)

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
            database.record_nearby_chat(
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

            chat = database.recent_nearby_chat(limit=5)

            self.assertEqual(len(chat), 1)
            self.assertEqual(chat[0]["from_name"], "Vibestorm Admin")
            self.assertEqual(chat[0]["message"], "typing what I am changing")
