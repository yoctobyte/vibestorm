import math
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.bus.events import (
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    EventQueueEventReceived,
    LayerDataReceived,
    ParcelOverlayReceived,
    ParcelPropertiesReceived,
    RegionChanged,
    RegionMapTileReady,
)
from vibestorm.udp.messages import ParcelPropertiesMessage
from vibestorm.viewer3d.scene import (
    DEFAULT_MARKER_COLOR,
    PATH_CURVE_CIRCLE,
    PATH_CURVE_LINE,
    PCODE_AVATAR,
    PCODE_PRIM,
    PCODE_TREE,
    PROFILE_CURVE_CIRCLE,
    PROFILE_CURVE_EQUIL_TRIANGLE,
    PROFILE_CURVE_HALF_CIRCLE,
    PROFILE_CURVE_SQUARE,
    Scene,
    SceneEntity,
    _kind_for_pcode,
    _quat_to_yaw,
    classify_prim_shape,
)


def _make_entity(local_id: int, pcode: int) -> SceneEntity:
    return SceneEntity(
        local_id=local_id,
        pcode=pcode,
        kind=_kind_for_pcode(pcode),
        position=(0.0, 0.0, 0.0),
        scale=(1.0, 1.0, 1.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
        rotation_z_radians=0.0,
    )


def _make_parcel(*, name: str, bitmap: bytes = b"") -> ParcelPropertiesMessage:
    return ParcelPropertiesMessage(
        request_result=0,
        sequence_id=-1,
        self_count=0,
        other_count=0,
        public_count=0,
        local_id=1,
        owner_id=UUID(int=0),
        is_group_owned=False,
        aabb_min=(0.0, 0.0, 0.0),
        aabb_max=(256.0, 256.0, 0.0),
        bitmap=bitmap,
        area=65536,
        status=0,
        max_prims=15000,
        total_prims=0,
        parcel_flags=0,
        sale_price=0,
        name=name,
        description="",
        music_url="",
        media_url="",
        group_id=UUID(int=0),
    )


class SceneEventApplicationTests(unittest.TestCase):
    def test_apply_region_changed_clears_entities_and_tile(self) -> None:
        from vibestorm.world.terrain import RegionHeightmap

        scene = Scene(
            object_entities={1: _make_entity(1, PCODE_PRIM)},
            avatar_entities={2: _make_entity(2, PCODE_AVATAR)},
            map_tile_path=Path("/tmp/old.png"),
            region_handle=0xAA,
            region_name="OldSim",
            terrain_heightmap=RegionHeightmap(),
        )

        scene.apply_region_changed(RegionChanged(region_handle=0xBB, region_name="NewSim"))

        self.assertEqual(scene.region_handle, 0xBB)
        self.assertEqual(scene.region_name, "NewSim")
        self.assertEqual(scene.water_height, 20.0)
        self.assertEqual(scene.object_entities, {})
        self.assertEqual(scene.avatar_entities, {})
        self.assertIsNone(scene.map_tile_path)
        self.assertIsNone(scene.terrain_heightmap)
        self.assertIsNone(scene.debug_terrain_source)

    def test_apply_region_changed_preserves_synthetic_debug_terrain(self) -> None:
        from vibestorm.world.terrain import synthetic_heightmap

        heightmap = synthetic_heightmap(width=32, height=32)
        scene = Scene(
            region_handle=0xAA,
            map_tile_path=Path("/tmp/old.png"),
            terrain_heightmap=heightmap,
            debug_terrain_source="synthetic",
        )

        scene.apply_region_changed(RegionChanged(region_handle=0xBB, region_name="NewSim"))

        self.assertEqual(scene.region_handle, 0xBB)
        self.assertIsNone(scene.map_tile_path)
        self.assertIs(scene.terrain_heightmap, heightmap)
        self.assertEqual(scene.debug_terrain_source, "synthetic")

    def test_apply_map_tile_ready_sets_path_for_current_region(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_map_tile_ready(
            RegionMapTileReady(region_handle=0xAA, image_id=UUID(int=0), cache_path="/tmp/x.png")
        )

        self.assertEqual(scene.map_tile_path, Path("/tmp/x.png"))

    def test_apply_map_tile_ready_ignored_for_other_region(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_map_tile_ready(
            RegionMapTileReady(region_handle=0xBB, image_id=UUID(int=0), cache_path="/tmp/x.png")
        )

        self.assertIsNone(scene.map_tile_path)

    def test_apply_parcel_properties_sets_name(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_parcel_properties(
            ParcelPropertiesReceived(
                region_handle=0xAA,
                properties=_make_parcel(name="Your Parcel"),
            )
        )

        self.assertEqual(scene.parcel_name, "Your Parcel")

    def test_apply_parcel_properties_ignored_for_other_region(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_parcel_properties(
            ParcelPropertiesReceived(
                region_handle=0xBB,
                properties=_make_parcel(name="Elsewhere"),
            )
        )

        self.assertIsNone(scene.parcel_name)

    def test_apply_parcel_properties_prefers_parcel_under_avatar(self) -> None:
        # A region-wide request draws one reply per parcel. Only the parcel
        # whose bitmap covers the avatar should name the HUD.
        scene = Scene(region_handle=0xAA, avatar_position=(10.0, 10.0, 25.0))
        # Cell (2, 2) is the 4 m LandUnit containing (10, 10).
        covering = bytearray(512)
        index = 2 * 64 + 2
        covering[index // 8] |= 1 << (index % 8)

        scene.apply_parcel_properties(
            ParcelPropertiesReceived(
                region_handle=0xAA,
                properties=_make_parcel(name="Far Parcel", bitmap=bytes(512)),
            )
        )
        self.assertIsNone(scene.parcel_name)

        scene.apply_parcel_properties(
            ParcelPropertiesReceived(
                region_handle=0xAA,
                properties=_make_parcel(name="Home Parcel", bitmap=bytes(covering)),
            )
        )
        self.assertEqual(scene.parcel_name, "Home Parcel")

    def test_apply_parcel_overlay_decodes_grid_once_complete(self) -> None:
        # A 256 m region is 64x64 LandUnits = 4096 cells, split into four
        # 1024-byte packets. Decode must wait for the whole set.
        scene = Scene(region_handle=0xAA)
        packets = [bytes([1]) * 1024 for _ in range(4)]

        for sequence_id in range(3):
            scene.apply_parcel_overlay(
                ParcelOverlayReceived(
                    region_handle=0xAA,
                    sequence_id=sequence_id,
                    data=packets[sequence_id],
                )
            )
            self.assertIsNone(scene.parcel_overlay)
            self.assertEqual(scene.parcel_borders, ())

        scene.apply_parcel_overlay(
            ParcelOverlayReceived(region_handle=0xAA, sequence_id=3, data=packets[3])
        )

        self.assertIsNotNone(scene.parcel_overlay)
        self.assertEqual(scene.parcel_overlay.cells_per_edge, 64)
        self.assertEqual(scene.parcel_borders, ())  # ownership only, no border flags

    def test_apply_parcel_overlay_exposes_border_segments(self) -> None:
        scene = Scene(region_handle=0xAA)
        # Set the west-border flag on cell 0 only.
        first = bytearray([1]) * 1024
        first[0] = 1 | 0x40
        packets = [bytes(first)] + [bytes([1]) * 1024 for _ in range(3)]

        for sequence_id, data in enumerate(packets):
            scene.apply_parcel_overlay(
                ParcelOverlayReceived(
                    region_handle=0xAA, sequence_id=sequence_id, data=data
                )
            )

        self.assertEqual(scene.parcel_borders, ((0, 0, 0, 4),))

    def test_apply_parcel_overlay_ignored_for_other_region(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_parcel_overlay(
            ParcelOverlayReceived(
                region_handle=0xBB, sequence_id=0, data=bytes(1024)
            )
        )

        self.assertEqual(scene.parcel_overlay_packets, {})

    def test_region_change_clears_parcel_overlay(self) -> None:
        scene = Scene(region_handle=0xAA)
        for sequence_id in range(4):
            scene.apply_parcel_overlay(
                ParcelOverlayReceived(
                    region_handle=0xAA, sequence_id=sequence_id, data=bytes([1]) * 1024
                )
            )
        self.assertIsNotNone(scene.parcel_overlay)

        scene.apply_region_changed(RegionChanged(region_handle=0xBB, region_name="NewSim"))

        self.assertIsNone(scene.parcel_overlay)
        self.assertEqual(scene.parcel_borders, ())
        self.assertEqual(scene.parcel_overlay_packets, {})

    def test_teleport_finish_reports_to_chat(self) -> None:
        from vibestorm.event_queue.events import TeleportFinishEvent

        scene = Scene()

        scene.apply_event_queue_event(
            EventQueueEventReceived(
                region_handle=0xAA,
                event=TeleportFinishEvent(
                    agent_id="a",
                    location_id=4,
                    sim_ip="127.0.0.1",
                    sim_port=9000,
                    region_handle=0x1234,
                    seed_capability="http://x/",
                    sim_access=13,
                    teleport_flags=0,
                    region_size_x=256,
                    region_size_y=256,
                ),
            )
        )

        self.assertEqual(len(scene.chat_lines), 1)
        self.assertEqual(scene.chat_lines[-1].kind, "alert")
        self.assertIn("Teleport complete", scene.chat_lines[-1].message)
        self.assertIn("127.0.0.1:9000", scene.chat_lines[-1].message)

    def test_script_running_reply_reports_to_chat(self) -> None:
        from vibestorm.event_queue.events import ScriptRunningReplyEvent

        scene = Scene()

        scene.apply_event_queue_event(
            EventQueueEventReceived(
                region_handle=0xAA,
                event=ScriptRunningReplyEvent(
                    object_id="obj-1", item_id="item-1", running=True, mono=True
                ),
            )
        )

        self.assertEqual(len(scene.chat_lines), 1)
        self.assertIn("running (Mono)", scene.chat_lines[-1].message)

    def test_region_management_events_are_not_chat_worthy(self) -> None:
        from vibestorm.event_queue.events import EnableSimulatorEvent

        scene = Scene()

        scene.apply_event_queue_event(
            EventQueueEventReceived(
                region_handle=0xAA,
                event=EnableSimulatorEvent(
                    handle=1, ip="127.0.0.1", port=9000, region_size_x=256, region_size_y=256
                ),
            )
        )

        self.assertEqual(len(scene.chat_lines), 0)

    def test_apply_chat_local_appends_chat_line(self) -> None:
        scene = Scene()

        scene.apply_chat_local(
            ChatLocal(region_handle=0, from_name="Alice", chat_type=1, audible=1, message="hi")
        )

        self.assertEqual(len(scene.chat_lines), 1)
        self.assertEqual(scene.chat_lines[-1].kind, "local")
        self.assertEqual(scene.chat_lines[-1].sender, "Alice")
        self.assertEqual(scene.chat_lines[-1].message, "hi")

    def test_apply_chat_im_alert_outbound(self) -> None:
        scene = Scene()
        scene.apply_chat_im(
            ChatIM(
                region_handle=0,
                from_agent_name="Bob",
                to_agent_id=UUID(int=1),
                message="yo",
                dialog=0,
            )
        )
        scene.apply_chat_alert(ChatAlert(region_handle=0, message="restart"))
        scene.apply_chat_outbound(
            ChatOutbound(region_handle=0, chat_type=1, channel=0, message="ok")
        )

        kinds = [line.kind for line in scene.chat_lines]
        self.assertEqual(kinds, ["im", "alert", "outbound"])

    def test_chat_lines_capped_at_buffer_size(self) -> None:
        scene = Scene()
        for i in range(200):
            scene.apply_chat_local(
                ChatLocal(
                    region_handle=0,
                    from_name="A",
                    chat_type=1,
                    audible=1,
                    message=f"m{i}",
                )
            )
        self.assertEqual(len(scene.chat_lines), 128)
        self.assertEqual(scene.chat_lines[-1].message, "m199")

    def test_apply_layer_data_accumulates_land_heightmap(self) -> None:
        from vibestorm.world.terrain import END_OF_PATCHES, LAYER_TYPE_LAND, BitPackWriter

        w = BitPackWriter()
        w.pack_bits(264, 16)
        w.pack_bits(16, 8)
        w.pack_bits(LAYER_TYPE_LAND, 8)
        w.pack_bits(0x30, 8)  # prequant=5, word_bits=2
        w.pack_float(10.0)
        w.pack_bits(4, 16)
        w.pack_bits((1 << 5) | 2, 10)  # patch x=1, y=2
        w.pack_bits(0b10, 2)  # all-zero coefficients
        w.pack_bits(END_OF_PATCHES, 8)

        scene = Scene(region_handle=0xAA)
        scene.apply_layer_data_received(
            LayerDataReceived(
                region_handle=0xAA,
                layer_type=LAYER_TYPE_LAND,
                data=w.to_bytes(),
            )
        )

        self.assertIsNotNone(scene.terrain_heightmap)
        assert scene.terrain_heightmap is not None
        self.assertEqual(scene.terrain_heightmap.revision, 1)
        index = (2 * 16) * 256 + (1 * 16)
        self.assertAlmostEqual(scene.terrain_heightmap.samples[index], 12.0, places=6)

    def test_apply_layer_data_ignores_other_regions_and_non_land(self) -> None:
        scene = Scene(region_handle=0xAA)

        scene.apply_layer_data_received(
            LayerDataReceived(region_handle=0xBB, layer_type=0x4C, data=b"")
        )
        scene.apply_layer_data_received(
            LayerDataReceived(region_handle=0xAA, layer_type=0x57, data=b"")
        )

        self.assertIsNone(scene.terrain_heightmap)

    def test_apply_layer_data_preserves_synthetic_debug_terrain(self) -> None:
        from vibestorm.bus.events import LayerDataReceived
        from vibestorm.world.terrain import synthetic_heightmap

        scene = Scene(region_handle=0xAA)
        scene.terrain_heightmap = synthetic_heightmap(width=32, height=32)
        scene.debug_terrain_source = "synthetic"
        before = scene.terrain_heightmap

        scene.apply_layer_data_received(
            LayerDataReceived(region_handle=0xAA, layer_type=0x4C, data=b"bad")
        )

        self.assertIs(scene.terrain_heightmap, before)
        self.assertEqual(scene.debug_terrain_source, "synthetic")


class SceneWorldViewRefreshTests(unittest.TestCase):
    def test_refresh_with_none_world_view_clears_entities(self) -> None:
        scene = Scene(object_entities={1: _make_entity(1, PCODE_PRIM)})
        scene.refresh_from_world_view(None)
        self.assertEqual(scene.object_entities, {})

    def test_refresh_categorizes_objects_by_pcode(self) -> None:
        from vibestorm.world.models import WorldObject, WorldView

        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(50.0, 60.0, 25.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None,
        )
        view.objects[UUID(int=2)] = WorldObject(
            full_id=UUID(int=2), local_id=20, parent_id=0, pcode=PCODE_AVATAR,
            material=0, click_action=0, scale=(0.5, 0.5, 1.8), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(80.0, 80.0, 22.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="avatar_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertIn(10, scene.object_entities)
        self.assertEqual(scene.object_entities[10].kind, "prim")
        self.assertIn(20, scene.avatar_entities)
        self.assertEqual(scene.avatar_entities[20].kind, "avatar")
        self.assertNotIn(20, scene.object_entities)

    def test_refresh_skips_objects_without_position(self) -> None:
        from vibestorm.world.models import WorldObject, WorldView

        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=None, rotation=None,
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.object_entities, {})

    def test_refresh_uses_terse_for_avatars_without_full_update(self) -> None:
        from vibestorm.world.models import TerseWorldObject, WorldView

        view = WorldView()
        view.terse_objects[42] = TerseWorldObject(
            local_id=42, state=0, is_avatar=True, region_handle=0, time_dilation=0,
            position=(20.0, 30.0, 25.0), velocity=(0.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            angular_velocity=(0.0, 0.0, 0.0),
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertIn(42, scene.avatar_entities)
        self.assertEqual(scene.avatar_entities[42].position, (20.0, 30.0, 25.0))
        self.assertEqual(scene.avatar_entities[42].kind, "avatar")

    def test_refresh_prefers_full_object_over_terse(self) -> None:
        from vibestorm.world.models import TerseWorldObject, WorldObject, WorldView

        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=99, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(2.0, 3.0, 4.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(10.0, 10.0, 10.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None,
        )
        view.terse_objects[99] = TerseWorldObject(
            local_id=99, state=0, is_avatar=False, region_handle=0, time_dilation=0,
            position=(20.0, 20.0, 20.0), velocity=(0.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            angular_velocity=(0.0, 0.0, 0.0),
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.object_entities[99].position, (10.0, 10.0, 10.0))
        self.assertEqual(scene.object_entities[99].scale, (2.0, 3.0, 4.0))

    def test_refresh_surfaces_default_texture_id(self) -> None:
        from vibestorm.world.models import WorldObject, WorldView
        from vibestorm.world.texture_entry import TextureEntry

        tex = UUID("12345678-1234-1234-1234-123456789abc")
        face_tex = UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
        texture_entry = TextureEntry(default_texture_id=tex, face_texture_ids=((2, face_tex),))
        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=16,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=tex, texture_entry=texture_entry,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.object_entities[10].default_texture_id, tex)
        self.assertIs(scene.object_entities[10].texture_entry, texture_entry)

    def test_apply_texture_asset_ready_records_texture_path(self) -> None:
        from vibestorm.bus.events import TextureAssetReady

        texture_id = UUID("aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb")
        scene = Scene(region_handle=10)

        scene.apply_texture_asset_ready(
            TextureAssetReady(
                region_handle=10,
                texture_id=texture_id,
                cache_path="/tmp/texture.png",
            )
        )

        self.assertEqual(scene.texture_paths[texture_id], Path("/tmp/texture.png"))

    def test_refresh_surfaces_sun_phase(self) -> None:
        from vibestorm.world.models import SimulatorTimeSnapshot, WorldView

        view = WorldView()
        view.latest_time = SimulatorTimeSnapshot(
            usec_since_start=0,
            sec_per_day=14400,
            sec_per_year=5256000,
            sun_phase=2.5,
            sun_direction=(0.1, 0.2, 0.3),
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.sun_phase, 2.5)
        self.assertEqual(scene.sun_direction, (0.1, 0.2, 0.3))

    def test_refresh_with_no_world_time_leaves_sun_phase_none(self) -> None:
        from vibestorm.world.models import WorldView

        scene = Scene()
        scene.refresh_from_world_view(WorldView())
        self.assertIsNone(scene.sun_phase)

    def test_apply_mesh_asset_ready_records_cache_path(self) -> None:
        from vibestorm.bus.events import MeshAssetReady

        mesh_id = UUID("11111111-2222-3333-4444-555555555555")
        scene = Scene(region_handle=0x1234)

        scene.apply_mesh_asset_ready(
            MeshAssetReady(
                region_handle=0x1234,
                mesh_id=mesh_id,
                cache_path="/tmp/mesh.llmesh",
            )
        )

        self.assertEqual(scene.mesh_paths[mesh_id], Path("/tmp/mesh.llmesh"))

    def test_refresh_surfaces_region_water_height(self) -> None:
        from vibestorm.world.models import WorldView

        view = WorldView()
        view.set_region(name="WetSim", grid_x=1, grid_y=2, water_height=6.5)

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.water_height, 6.5)


class QuatToYawTests(unittest.TestCase):
    def test_identity_quat_yields_zero_yaw(self) -> None:
        self.assertAlmostEqual(_quat_to_yaw((0.0, 0.0, 0.0, 1.0)), 0.0)

    def test_90deg_yaw_around_z(self) -> None:
        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        self.assertAlmostEqual(_quat_to_yaw((0.0, 0.0, s, c)), math.pi / 2, places=4)

    def test_none_returns_zero(self) -> None:
        self.assertEqual(_quat_to_yaw(None), 0.0)

    def test_malformed_returns_zero(self) -> None:
        self.assertEqual(_quat_to_yaw((1.0, 2.0)), 0.0)


class SceneEntityTintTests(unittest.TestCase):
    def test_known_pcode_picks_palette_via_refresh(self) -> None:
        from vibestorm.world.models import WorldObject, WorldView

        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_AVATAR,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="avatar_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.avatar_entities[10].tint, (255, 200, 80))
        self.assertEqual(scene.avatar_entities[10].color, (255, 200, 80))

    def test_unknown_pcode_falls_back_to_default(self) -> None:
        entity = SceneEntity(
            local_id=1,
            pcode=200,
            kind="unknown",
            position=(0.0, 0.0, 0.0),
            scale=(1.0, 1.0, 1.0),
            rotation=None,
            rotation_z_radians=0.0,
        )
        self.assertEqual(entity.tint, DEFAULT_MARKER_COLOR)


class KindForPcodeTests(unittest.TestCase):
    def test_known_pcodes(self) -> None:
        self.assertEqual(_kind_for_pcode(PCODE_PRIM), "prim")
        self.assertEqual(_kind_for_pcode(PCODE_AVATAR), "avatar")
        self.assertEqual(_kind_for_pcode(PCODE_TREE), "tree")

    def test_unknown_pcode_is_unknown(self) -> None:
        self.assertEqual(_kind_for_pcode(200), "unknown")


class ClassifyPrimShapeTests(unittest.TestCase):
    def test_line_square_is_cube(self) -> None:
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_LINE, PROFILE_CURVE_SQUARE), "cube"
        )

    def test_line_circle_is_cylinder(self) -> None:
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_LINE, PROFILE_CURVE_CIRCLE), "cylinder"
        )

    def test_line_triangle_is_prism(self) -> None:
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_LINE, PROFILE_CURVE_EQUIL_TRIANGLE), "prism"
        )

    def test_circle_half_circle_is_sphere(self) -> None:
        # The shape observed in the live OpenSim default sphere fixture.
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_CIRCLE, PROFILE_CURVE_HALF_CIRCLE), "sphere"
        )

    def test_circle_circle_is_torus(self) -> None:
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_CIRCLE, PROFILE_CURVE_CIRCLE), "torus"
        )

    def test_flexible_path_classifies_by_its_profile(self) -> None:
        # OpenSim's Extrusion enum (PrimitiveBaseShape.cs) is Straight=0x10,
        # Curve1=0x20, Curve2=0x30, Flexible=0x80. Flexible is a path mode, not
        # a shape: a flexi prim is a straight extrusion that bends at runtime,
        # so its profile still decides the cross-section. Omitting 0x80 sent
        # every flexi prim to the unclassified fallback — seen live as
        # `census unclassified[path=0x80 profile=0x01]=1`.
        from vibestorm.viewer3d.scene import PATH_CURVE_FLEXIBLE

        self.assertEqual(
            classify_prim_shape(PATH_CURVE_FLEXIBLE, PROFILE_CURVE_SQUARE), "cube"
        )
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_FLEXIBLE, PROFILE_CURVE_CIRCLE), "cylinder"
        )
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_FLEXIBLE, PROFILE_CURVE_EQUIL_TRIANGLE),
            "prism",
        )

    def test_flexible_path_does_not_become_a_torus(self) -> None:
        # Guards the obvious wrong fix: 0x80 must join the *linear* branch,
        # not the circular one.
        from vibestorm.viewer3d.scene import PATH_CURVE_FLEXIBLE

        self.assertNotEqual(
            classify_prim_shape(PATH_CURVE_FLEXIBLE, PROFILE_CURVE_CIRCLE), "torus"
        )

    def test_unknown_combo_returns_none(self) -> None:
        self.assertIsNone(classify_prim_shape(0xFF, 0xFF))

    def test_profile_curve_high_bits_ignored(self) -> None:
        # Per libomv convention only the low 3 bits of profile_curve carry the
        # profile family — high bits encode hollow style and are masked off.
        self.assertEqual(
            classify_prim_shape(PATH_CURVE_LINE, PROFILE_CURVE_SQUARE | 0x10), "cube"
        )


class SceneShapePopulatedTests(unittest.TestCase):
    def test_refresh_populates_shape_for_prim(self) -> None:
        from vibestorm.udp.messages import PrimShapeData
        from vibestorm.world.models import WorldObject, WorldView

        sphere_shape = PrimShapeData(
            path_curve=PATH_CURVE_CIRCLE,
            profile_curve=PROFILE_CURVE_HALF_CIRCLE,
            path_begin=0, path_end=0, path_scale_x=100, path_scale_y=100,
            path_shear_x=0, path_shear_y=0, path_twist=0, path_twist_begin=0,
            path_radius_offset=0, path_taper_x=0, path_taper_y=0,
            path_revolutions=0, path_skew=0,
            profile_begin=0, profile_end=0, profile_hollow=0,
        )
        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None, shape=sphere_shape,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertEqual(scene.object_entities[10].shape, "sphere")

    def test_refresh_routes_mesh_extra_param_to_mesh_placeholder(self) -> None:
        from vibestorm.udp.messages import ExtraParamEntry, PrimShapeData
        from vibestorm.world.models import WorldObject, WorldView

        mesh_asset_id = UUID("11111111-2222-3333-4444-555555555555")
        cube_shape = PrimShapeData(
            path_curve=PATH_CURVE_LINE,
            profile_curve=PROFILE_CURVE_SQUARE,
            path_begin=0, path_end=0, path_scale_x=100, path_scale_y=100,
            path_shear_x=0, path_shear_y=0, path_twist=0, path_twist_begin=0,
            path_radius_offset=0, path_taper_x=0, path_taper_y=0,
            path_revolutions=0, path_skew=0,
            profile_begin=0, profile_end=0, profile_hollow=0,
        )
        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=24,
            extra_params_entries=(
                ExtraParamEntry(
                    param_type=0x30,
                    param_in_use=True,
                    param_data=mesh_asset_id.bytes + bytes([5]),
                ),
            ),
            default_texture_id=None, shape=cube_shape,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)
        entity = scene.object_entities[10]

        self.assertEqual(entity.shape, "mesh")
        self.assertEqual(entity.mesh_source_kind, "mesh")
        self.assertEqual(entity.mesh_asset_id, mesh_asset_id)
        self.assertEqual(entity.sculpt_type, 5)

    def test_refresh_routes_sculpt_extra_param_to_sculpt_placeholder(self) -> None:
        from vibestorm.udp.messages import ExtraParamEntry
        from vibestorm.world.models import WorldObject, WorldView

        sculpt_asset_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=24,
            extra_params_entries=(
                ExtraParamEntry(
                    param_type=0x30,
                    param_in_use=True,
                    param_data=sculpt_asset_id.bytes + bytes([2]),
                ),
            ),
            default_texture_id=None, shape=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)
        entity = scene.object_entities[10]

        self.assertEqual(entity.shape, "torus")
        self.assertEqual(entity.mesh_source_kind, "sculpt")
        self.assertEqual(entity.mesh_asset_id, sculpt_asset_id)
        self.assertEqual(entity.sculpt_type, 2)

    def test_refresh_preserves_sculpt_flags_on_scene_entity(self) -> None:
        from vibestorm.udp.messages import ExtraParamEntry
        from vibestorm.world.models import WorldObject, WorldView

        sculpt_asset_id = UUID("bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee")
        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=24,
            extra_params_entries=(
                ExtraParamEntry(
                    param_type=0x30,
                    param_in_use=True,
                    param_data=sculpt_asset_id.bytes + bytes([0x80 | 0x40 | 2]),
                ),
            ),
            default_texture_id=None, shape=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)
        entity = scene.object_entities[10]

        self.assertEqual(entity.shape, "torus")
        self.assertEqual(entity.mesh_source_kind, "sculpt")
        self.assertEqual(entity.sculpt_type, 0xC2)

    def test_refresh_leaves_shape_none_when_world_object_has_no_shape(self) -> None:
        from vibestorm.world.models import WorldObject, WorldView

        view = WorldView()
        view.objects[UUID(int=1)] = WorldObject(
            full_id=UUID(int=1), local_id=10, parent_id=0, pcode=PCODE_PRIM,
            material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
            update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
            position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic", name_values={}, texture_entry_size=0,
            texture_anim_size=0, data_size=0, text_size=0, media_url_size=0,
            ps_block_size=0, extra_params_size=0, extra_params_entries=(),
            default_texture_id=None, shape=None,
        )

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertIsNone(scene.object_entities[10].shape)


class SceneChatTypeTests(unittest.TestCase):
    """Typing indicators are ChatFromSimulator packets with no message."""

    def _chat(self, name: str, chat_type: int, message: str = "") -> object:
        from vibestorm.bus.events import ChatLocal

        return ChatLocal(
            region_handle=0,
            from_name=name,
            chat_type=chat_type,
            audible=1,
            message=message,
        )

    def test_typing_notification_does_not_become_a_blank_chat_line(self) -> None:
        from vibestorm.world.chat_types import CHAT_TYPE_START_TYPING

        scene = Scene()
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_START_TYPING))

        self.assertEqual(list(scene.chat_lines), [])
        self.assertIn("Ann", scene.typing_senders)

    def test_stop_typing_clears_the_indicator(self) -> None:
        from vibestorm.world.chat_types import CHAT_TYPE_START_TYPING, CHAT_TYPE_STOP_TYPING

        scene = Scene()
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_START_TYPING))
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_STOP_TYPING))

        self.assertEqual(scene.typing_senders, {})
        self.assertEqual(list(scene.chat_lines), [])

    def test_actually_saying_something_clears_the_indicator(self) -> None:
        # A sim does not always send stop-typing before the message itself;
        # without this the indicator would stick forever.
        from vibestorm.world.chat_types import CHAT_TYPE_SAY, CHAT_TYPE_START_TYPING

        scene = Scene()
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_START_TYPING))
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_SAY, "hello"))

        self.assertEqual(scene.typing_senders, {})
        self.assertEqual([line.message for line in scene.chat_lines], ["hello"])

    def test_say_has_no_delivery_qualifier(self) -> None:
        from vibestorm.world.chat_types import CHAT_TYPE_SAY

        scene = Scene()
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_SAY, "hello"))

        self.assertIsNone(scene.chat_lines[0].delivery())

    def test_whisper_and_shout_are_qualified(self) -> None:
        from vibestorm.world.chat_types import CHAT_TYPE_SHOUT, CHAT_TYPE_WHISPER

        scene = Scene()
        scene.apply_chat_local(self._chat("Ann", CHAT_TYPE_WHISPER, "psst"))
        scene.apply_chat_local(self._chat("Bob", CHAT_TYPE_SHOUT, "HEY"))

        self.assertEqual(scene.chat_lines[0].delivery(), "whisper")
        self.assertEqual(scene.chat_lines[1].delivery(), "shout")

    def test_non_local_lines_carry_no_chat_type(self) -> None:
        from vibestorm.bus.events import ChatAlert

        scene = Scene()
        scene.apply_chat_alert(ChatAlert(region_handle=0, message="server restart"))

        self.assertIsNone(scene.chat_lines[0].chat_type)
        self.assertIsNone(scene.chat_lines[0].delivery())


class SceneObjectPhysicsTests(unittest.TestCase):
    """ObjectPhysicsProperties is per-object detail, not a chat line."""

    def _event(self, local_id: int, shape_type: int = 0, **overrides: float) -> object:
        from vibestorm.bus.events import EventQueueEventReceived
        from vibestorm.event_queue.events import ObjectPhysicsPropertiesEvent

        values: dict = dict(
            density=1000.0, friction=0.6, gravity_multiplier=1.0, restitution=0.5
        )
        values.update(overrides)
        return EventQueueEventReceived(
            region_handle=0,
            event=ObjectPhysicsPropertiesEvent(
                local_id=local_id, physics_shape_type=shape_type, **values
            ),
        )

    def test_physics_is_recorded_without_polluting_the_chat_log(self) -> None:
        scene = Scene()
        scene.apply_event_queue_event(self._event(42, shape_type=2))

        self.assertEqual(list(scene.chat_lines), [])
        self.assertEqual(scene.object_physics[42].shape_name, "convex")

    def test_later_updates_replace_earlier_ones(self) -> None:
        # A script can change a prim's physics at runtime and the sim resends.
        scene = Scene()
        scene.apply_event_queue_event(self._event(42, shape_type=0))
        scene.apply_event_queue_event(self._event(42, shape_type=1))

        self.assertEqual(scene.object_physics[42].shape_name, "none")
        self.assertFalse(scene.object_physics[42].is_collidable)

    def test_objects_are_tracked_separately(self) -> None:
        scene = Scene()
        scene.apply_event_queue_event(self._event(1, shape_type=0))
        scene.apply_event_queue_event(self._event(2, shape_type=2))

        self.assertEqual(scene.object_physics[1].shape_name, "prim")
        self.assertEqual(scene.object_physics[2].shape_name, "convex")


class SceneSimHealthTests(unittest.TestCase):
    """The HUD's fps is the client's; sim health is the region's.

    Without both, a stutter cannot be attributed to either side.
    """

    def _view_with_stats(self, *pairs: tuple[int, float]) -> object:
        from vibestorm.udp.messages import SimStatEntry, SimStatsMessage
        from vibestorm.world.models import WorldView

        view = WorldView()
        view.apply_sim_stats(
            SimStatsMessage(
                region_x=1000,
                region_y=1000,
                region_flags=0,
                object_capacity=15000,
                stats=tuple(SimStatEntry(stat_id=i, stat_value=v) for i, v in pairs),
                pid=0,
                region_flags_extended=(),
            ),
        )
        return view

    def test_sim_health_is_empty_before_any_stats_arrive(self) -> None:
        from vibestorm.world.models import WorldView

        scene = Scene()
        scene.refresh_from_world_view(WorldView())

        self.assertEqual(scene.sim_health, "")

    def test_sim_health_reports_named_region_numbers(self) -> None:
        # Wire ids, not internal array slots: 1 sim fps, 13 agents, 11 prims.
        view = self._view_with_stats((1, 55.0), (13, 1.0), (11, 32.0))

        scene = Scene()
        scene.refresh_from_world_view(view)

        self.assertIn("sim fps=55", scene.sim_health)
        self.assertIn("agents=1", scene.sim_health)
        self.assertIn("total prims=32", scene.sim_health)

    def test_sim_health_tracks_later_updates(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(self._view_with_stats((1, 55.0)))
        scene.refresh_from_world_view(self._view_with_stats((1, 12.0)))

        self.assertIn("sim fps=12", scene.sim_health)
        self.assertNotIn("55", scene.sim_health)


if __name__ == "__main__":
    unittest.main()
