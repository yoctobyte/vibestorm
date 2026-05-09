import unittest
from struct import pack
from uuid import UUID

from vibestorm.udp.messages import RegionHandshakeMessage
from vibestorm.udp.template import DecodedMessageNumber, MessageDispatch, MessageTemplateSummary
from vibestorm.world.models import WorldView
from vibestorm.world.updater import WorldUpdater


class WorldUpdaterTests(unittest.TestCase):
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
        self.assertEqual(world.region.water_height, 20.0)

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
                + (b"\x00" * 23)
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

    def test_apply_dispatch_parses_multi_object_update(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        object_id_a = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_id_b = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")

        def _build_prim_entry(local_id: int, full_id: UUID, pos_x: float) -> bytes:
            object_data = (
                pack("<fff", pos_x, 2.0, 3.0)  # position
                + b"\x00" * 28
                + pack("<ffff", 0.0, 0.0, 0.0, 1.0)  # rotation
                + b"\x00" * 4
            )
            assert len(object_data) == 60
            return (
                local_id.to_bytes(4, "little")
                + bytes([0])                          # state
                + full_id.bytes                       # full_id
                + (0).to_bytes(4, "little")           # crc
                + bytes([9, 0, 0])                    # pcode=9, material, click_action
                + pack("<fff", 1.0, 1.0, 1.0)         # scale
                + bytes([60]) + object_data           # ObjectData
                + b"\x00" * 8                         # parent_id + update_flags
                + b"\x00" * 23                        # shape params
                + b"\x00\x00"                         # TextureEntry len=0
                + bytes([0])                          # TextureAnim len=0
                + b"\x00\x00"                         # NameValue len=0
                + b"\x00\x00"                         # Data len=0
                + bytes([0])                          # Text len=0
                + b"\x00\x00\x00\x00"                # TextColor
                + bytes([0])                          # MediaURL len=0
                + bytes([0])                          # PSBlock len=0
                + bytes([0])                          # ExtraParams len=0
                + b"\x00" * 66                        # 66 fixed bytes tail
            )

        dispatched = self._dispatch(
            "ObjectUpdate",
            (
                (123456789).to_bytes(8, "little")     # region_handle
                + (42).to_bytes(2, "little")           # time_dilation
                + bytes([2])                           # object_count=2
                + _build_prim_entry(7, object_id_a, 1.0)
                + _build_prim_entry(8, object_id_b, 5.0)
            ),
            frequency="High",
            message_number=12,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update")
        self.assertIn("objects=2", event.detail)
        self.assertIn(object_id_a, world.objects)
        self.assertIn(object_id_b, world.objects)
        assert world.objects[object_id_a].position is not None
        assert world.objects[object_id_b].position is not None
        self.assertAlmostEqual(world.objects[object_id_a].position[0], 1.0)
        self.assertAlmostEqual(world.objects[object_id_b].position[0], 5.0)

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
                + (b"\x00" * 23)
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

    def test_apply_dispatch_tracks_improved_terse_object_update_summary(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        prim_data = self._build_terse_prim_data()
        texture_payload = b"\x08\x00\x04\x00\x00\x00\x11\x22\x33\x44"
        dispatched = self._dispatch(
            "ImprovedTerseObjectUpdate",
            (
                (1099511628032000).to_bytes(8, "little")
                + (65535).to_bytes(2, "little")
                + bytes([2])
                + bytes([len(prim_data)])
                + prim_data
                + len(texture_payload).to_bytes(2, "little")
                + texture_payload
                + bytes([len(prim_data)])
                + prim_data
                + (0).to_bytes(2, "little")
            ),
            frequency="High",
            message_number=15,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.improved_terse_object_update")
        self.assertIn("objects=2", event.detail)
        self.assertIn("rich_entries=1", event.detail)
        self.assertIn("local_ids=67305985", event.detail)
        assert world.latest_object_update is not None
        self.assertEqual(world.latest_object_update.object_count, 2)
        self.assertEqual(len(world.terse_objects), 1)
        self.assertIn(67305985, world.terse_objects)

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

    def test_apply_dispatch_removes_objects_on_kill_object(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_update = self._dispatch(
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
                + (b"\x00" * 23)
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
        kill = self._dispatch(
            "KillObject",
            bytes([2]) + (7).to_bytes(4, "little") + (99).to_bytes(4, "little"),
            frequency="High",
            message_number=16,
        )

        updater.apply_dispatch(object_update)
        event = updater.apply_dispatch(kill)

        assert event is not None
        self.assertEqual(event.kind, "world.kill_object")
        self.assertEqual(event.detail, "local_ids=7,99")
        self.assertNotIn(object_id, world.objects)
        self.assertNotIn(7, world.local_id_to_full_id)

    def test_full_object_update_promotes_terse_only_object(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        prim_data = self._build_terse_prim_data()
        terse = self._dispatch(
            "ImprovedTerseObjectUpdate",
            (
                (1099511628032000).to_bytes(8, "little")
                + (65535).to_bytes(2, "little")
                + bytes([1])
                + bytes([len(prim_data)])
                + prim_data
                + (0).to_bytes(2, "little")
            ),
            frequency="High",
            message_number=15,
        )
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        full = self._dispatch(
            "ObjectUpdate",
            (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([1])
                + (0x04030201).to_bytes(4, "little")
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
                + (b"\x00" * 23)
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

        updater.apply_dispatch(terse)
        updater.apply_dispatch(full)

        self.assertNotIn(0x04030201, world.terse_objects)
        self.assertIn(object_id, world.objects)

    def test_apply_dispatch_reports_object_update_cached(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        dispatched = self._dispatch(
            "ObjectUpdateCached",
            (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([2])
                + (7).to_bytes(4, "little")
                + (0x11111111).to_bytes(4, "little")
                + (5).to_bytes(4, "little")
                + (9).to_bytes(4, "little")
                + (0x22222222).to_bytes(4, "little")
                + (6).to_bytes(4, "little")
            ),
            frequency="High",
            message_number=14,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update_cached")
        self.assertIn("objects=2", event.detail)
        self.assertIn("local_ids=7,9", event.detail)

    def test_apply_dispatch_reports_object_update_compressed(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        data_a = b"\x11\x22\x33"
        data_b = b"\xaa\xbb\xcc\xdd"
        dispatched = self._dispatch(
            "ObjectUpdateCompressed",
            (
                (123456789).to_bytes(8, "little")
                + (42).to_bytes(2, "little")
                + bytes([2])
                + (5).to_bytes(4, "little")
                + len(data_a).to_bytes(2, "little")
                + data_a
                + (6).to_bytes(4, "little")
                + len(data_b).to_bytes(2, "little")
                + data_b
            ),
            frequency="High",
            message_number=13,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_update_compressed")
        self.assertIn("objects=2", event.detail)
        self.assertIn("decoded=0", event.detail)  # stub data blobs too short to decode

    def test_apply_dispatch_applies_object_properties_family(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        object_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        object_update = self._dispatch(
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
                + (b"\x00" * 23)
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
        properties = self._dispatch(
            "ObjectPropertiesFamily",
            (
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
            ),
            frequency="Medium",
            message_number=0xFF0A,
        )

        updater.apply_dispatch(object_update)
        event = updater.apply_dispatch(properties)

        assert event is not None
        self.assertEqual(event.kind, "world.object_properties_family")
        self.assertIn("name='Source Cube'", event.detail)
        assert world.latest_object_properties_family is not None
        self.assertEqual(world.latest_object_properties_family.name, "Source Cube")
        self.assertIsNotNone(world.objects[object_id].properties_family)
        assert world.objects[object_id].properties_family is not None
        self.assertEqual(world.objects[object_id].properties_family.description, "hover entry")

    def test_apply_dispatch_reports_object_extra_params(self) -> None:
        world = WorldView()
        updater = WorldUpdater(world)
        data_a = b"\x11\x22\x33"
        data_b = b"\xaa\xbb\xcc\xdd"
        dispatched = self._dispatch(
            "ObjectExtraParams",
            (
                UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee").bytes
                + UUID("11111111-2222-3333-4444-555555555555").bytes
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
            ),
            frequency="Low",
            message_number=0xFFFF0063,
        )

        event = updater.apply_dispatch(dispatched)

        assert event is not None
        self.assertEqual(event.kind, "world.object_extra_params")
        self.assertIn("objects=2", event.detail)
        self.assertIn("local_ids=7,9", event.detail)
