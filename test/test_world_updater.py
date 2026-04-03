import unittest
from struct import pack
from uuid import UUID

from vibestorm.udp.messages import RegionHandshakeMessage
from vibestorm.udp.template import DecodedMessageNumber, MessageDispatch, MessageTemplateSummary
from vibestorm.world.models import WorldView
from vibestorm.world.updater import WorldUpdater


class WorldUpdaterTests(unittest.TestCase):
    @staticmethod
    def _dispatch(name: str, body: bytes, *, frequency: str = "Low", message_number: int = 0) -> MessageDispatch:
        return MessageDispatch(
            summary=MessageTemplateSummary(
                name=name,
                frequency=frequency,
                message_number=message_number,
                trust="Trusted",
                encoding="Unencoded",
                deprecation=None,
            ),
            message_number=DecodedMessageNumber(
                frequency=frequency,
                message_number=message_number,
                encoded_length=4 if frequency in {"Low", "Fixed"} else 2,
            ),
            body=body,
        )

    def test_region_handshake_sets_region_summary(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)

        event = updater.apply_region_handshake(
            RegionHandshakeMessage(
                region_flags=9,
                sim_access=13,
                sim_name="Vibestorm Test",
                sim_owner=UUID("12345678-1234-5678-1234-567812345678"),
                is_estate_manager=True,
                water_height=20.0,
                billable_factor=1.0,
                cache_id=UUID("87654321-4321-8765-4321-876543218765"),
                region_id=UUID("aaaaaaaa-1111-bbbb-2222-cccccccccccc"),
            ),
            region_x=256000,
            region_y=256256,
        )

        self.assertEqual(event.kind, "handshake.region")
        assert world.region is not None
        self.assertEqual(world.region.name, "Vibestorm Test")
        self.assertEqual((world.region.grid_x, world.region.grid_y), (1000, 1001))

    def test_apply_dispatch_updates_world_from_simstats(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        dispatched = self._dispatch(
            "SimStats",
            (
                (1000).to_bytes(4, "little")
                + (1001).to_bytes(4, "little")
                + (9).to_bytes(4, "little")
                + (15000).to_bytes(4, "little")
                + bytes([1])
                + (1).to_bytes(4, "little")
                + pack("<f", 10.0)
                + (1234).to_bytes(4, "little", signed=True)
                + bytes([0])
            ),
            message_number=0xFFFF008C,
        )

        event = updater.apply_dispatch(dispatched)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.kind, "sim.stats")
        assert world.latest_sim_stats is not None
        self.assertEqual(world.latest_sim_stats.object_capacity, 15000)

    def test_apply_dispatch_ignores_unmapped_messages(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        dispatched = self._dispatch("ParcelOverlay", b"", message_number=0xFFFF0091)

        event = updater.apply_dispatch(dispatched)

        self.assertIsNone(event)

    def test_apply_dispatch_tracks_object_entities(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        dispatched = self._dispatch(
            "ObjectUpdate",
            (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([60])
                + pack("<fff", 1.0, 2.0, 3.0)
                + (b"\x00" * 28)
                + pack("<ffff", 0.0, 0.0, 0.0, 1.0)
                + (b"\x00" * 4)
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
            ),
            frequency="High",
            message_number=12,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update")
        self.assertIn(object_id, world.objects)
        assert world.objects[object_id].position is not None
        self.assertAlmostEqual(world.objects[object_id].position[0], 1.0)

    def test_apply_dispatch_marks_rich_object_update_when_texture_entry_exists(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        dispatched = self._dispatch(
            "ObjectUpdate",
            (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (7).to_bytes(4, "little")
                + bytes([3])
                + object_id.bytes
                + (99).to_bytes(4, "little")
                + bytes([9, 3, 1])
                + pack("<fff", 1.0, 2.0, 3.0)
                + bytes([60])
                + pack("<fff", 1.0, 2.0, 3.0)
                + (b"\x00" * 28)
                + pack("<ffff", 0.0, 0.0, 0.0, 1.0)
                + (b"\x00" * 4)
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
            ),
            frequency="High",
            message_number=12,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update_rich")
        self.assertIn("local_id=7", event.detail)
        self.assertIn("TextureEntry:4", event.detail)

    def test_apply_dispatch_falls_back_to_object_summary_on_partial_decode(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        dispatched = self._dispatch(
            "ObjectUpdate",
            (123456789).to_bytes(8, "little") + (42).to_bytes(2, "little") + bytes([3]) + b"\x00" * 5,
            frequency="High",
            message_number=12,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update_partial")
        assert world.latest_object_update is not None
        self.assertEqual(world.latest_object_update.object_count, 3)
