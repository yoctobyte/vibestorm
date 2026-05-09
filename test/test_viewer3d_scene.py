import math
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.bus.events import (
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    LayerDataReceived,
    RegionChanged,
    RegionMapTileReady,
)
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


if __name__ == "__main__":
    unittest.main()
