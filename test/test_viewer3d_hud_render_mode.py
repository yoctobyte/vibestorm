"""Tests for viewer3d HUD render-mode menu wiring."""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class RenderModeMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode((640, 480))

    def tearDown(self) -> None:
        self.pygame.quit()

    def _click(self, hud, button) -> bool:
        event = self.pygame.event.Event(
            self.pygame_gui.UI_BUTTON_PRESSED, {"ui_element": button}
        )
        return hud.process_event(event)

    def test_default_render_mode_is_2d_map(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_2D

        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        self.assertEqual(hud.render_mode, RENDER_MODE_2D)

    def test_initial_render_mode_can_start_in_3d(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            initial_render_mode=RENDER_MODE_3D,
        )

        self.assertEqual(hud.render_mode, RENDER_MODE_3D)
        self.assertTrue(hud.diagnostics_window.visible)

    def test_clicking_3d_button_changes_mode_and_fires_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        consumed = self._click(hud, hud.render_mode_3d_button)

        self.assertTrue(consumed)
        self.assertEqual(hud.render_mode, RENDER_MODE_3D)
        self.assertEqual(calls, [RENDER_MODE_3D])

    def test_clicking_same_mode_twice_only_fires_callback_once(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        self._click(hud, hud.render_mode_3d_button)
        self._click(hud, hud.render_mode_3d_button)

        self.assertEqual(calls, [RENDER_MODE_3D])
        self.assertEqual(hud.render_mode, RENDER_MODE_3D)

    def test_switching_back_to_2d_fires_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_2D, RENDER_MODE_3D

        calls: list[str] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_mode_change=calls.append,
        )

        self._click(hud, hud.render_mode_3d_button)
        self._click(hud, hud.render_mode_2d_button)

        self.assertEqual(calls, [RENDER_MODE_3D, RENDER_MODE_2D])
        self.assertEqual(hud.render_mode, RENDER_MODE_2D)

    def test_render_mode_button_hides_menu(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        # Open the View menu first.
        self._click(hud, hud.view_button)
        self.assertEqual(hud._open_menu, "view")

        self._click(hud, hud.render_mode_3d_button)

        self.assertIsNone(hud._open_menu)

    def test_status_bar_shows_active_mode(self) -> None:
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        scene = Scene()

        hud.update(0.016, scene)

        self.assertIn("mode=2D Map", hud.status_right.text)

        self._click(hud, hud.render_mode_3d_button)
        hud.update(0.016, scene)

        self.assertIn("mode=3D", hud.status_right.text)

    def test_diagnostics_window_reports_scene_counts(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import LayerDecodeStats, RegionHeightmap

        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            initial_render_mode=RENDER_MODE_3D,
        )
        scene = Scene(region_name="TestSim", avatar_position=(1.0, 2.0, 19.0))
        scene.water_height = 6.5
        scene.debug_terrain_source = "synthetic"
        scene.terrain_z_scale = 4.0
        scene.terrain_heightmap = RegionHeightmap(width=2, height=2, samples=[1.0, 2.0, 3.0, 4.0])
        scene.terrain_heightmap.patch_keys.add((0, 0))
        scene.terrain_heightmap.latest_layer_stats = LayerDecodeStats(
            patch_count=1,
            positions=((0, 0),),
            ranges=(16,),
            dc_offsets=(10.0,),
            prequants=(5,),
            nonzero_coefficients=3,
            coefficient_abs_max=9,
            height_min=1.0,
            height_max=4.0,
            height_mean=2.5,
        )

        hud.update(0.05, scene)

        text = hud.diagnostics_text.html_text
        self.assertIn("fps:", text)
        self.assertIn("terrain: synthetic 2x2 patches=1 rev=0 zscale=4.00", text)
        self.assertIn("height: min=1.00 max=4.00 mean=2.50", text)
        self.assertIn("patch keys: ((0, 0),)", text)
        self.assertIn("samples[0:4]: [1.0, 2.0, 3.0, 4.0]", text)
        self.assertIn("layer: patches=1 pos=((0, 0),)", text)
        self.assertIn("coeff: nz=3 absmax=9 h=min 1.00 max 4.00 mean 2.50", text)
        self.assertIn("water: level=6.5 avatar_z=19.0 above", text)

    def test_heightmap_debug_surface_maps_samples_to_grayscale(self) -> None:
        from vibestorm.viewer3d.hud import heightmap_debug_surface
        from vibestorm.world.terrain import RegionHeightmap

        terrain = RegionHeightmap(width=2, height=2, samples=[0.0, 1.0, 2.0, 3.0])

        surface = heightmap_debug_surface(self.pygame, terrain, size=2)

        self.assertEqual(surface.get_at((0, 0)).r, 0)
        self.assertEqual(surface.get_at((1, 1)).r, 255)
        self.assertEqual(surface.get_at((0, 0)).g, surface.get_at((0, 0)).r)
        self.assertEqual(surface.get_at((0, 0)).b, surface.get_at((0, 0)).r)

    def test_heightmap_menu_button_toggles_debug_window(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        self.assertFalse(hud.heightmap_window.visible)

        self._click(hud, hud.debug_button)
        self.assertEqual(hud._open_menu, "debug")
        consumed = self._click(hud, hud.heightmap_button)

        self.assertTrue(consumed)
        self.assertTrue(hud.heightmap_window.visible)
        self.assertIsNone(hud._open_menu)

    def test_heightmap_window_reports_loaded_terrain(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            initial_render_mode=RENDER_MODE_3D,
        )
        scene = Scene()
        scene.terrain_heightmap = RegionHeightmap(width=2, height=2, samples=[2.0, 4.0, 6.0, 8.0])
        scene.terrain_heightmap.patch_keys.add((0, 0))

        hud.update(0.016, scene)

        self.assertIn("live 2x2 patches=1", hud.heightmap_status.text)
        self.assertIn("min=2.00 max=8.00", hud.heightmap_status.text)

    def test_render_settings_menu_opens_window(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        self.assertFalse(hud.render_settings_window.visible)

        self._click(hud, hud.view_button)
        self.assertEqual(hud._open_menu, "view")
        consumed = self._click(hud, hud.render_settings_button)

        self.assertTrue(consumed)
        self.assertTrue(hud.render_settings_window.visible)
        self.assertIsNone(hud._open_menu)

    def test_render_settings_toggle_calls_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        calls: list[tuple[str, object]] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_setting_change=lambda name, value: calls.append((name, value)),
        )

        consumed = self._click(hud, hud.render_terrain_lines_button)

        self.assertTrue(consumed)
        self.assertEqual(calls, [("render_terrain_lines", False)])
        self.assertIn("[ ] Mesh Lines", hud.render_terrain_lines_button.text)

    def test_water_opacity_slider_calls_callback(self) -> None:
        from vibestorm.viewer3d.hud import HUD

        calls: list[tuple[str, object]] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_render_setting_change=lambda name, value: calls.append((name, value)),
        )
        event = self.pygame.event.Event(
            self.pygame_gui.UI_HORIZONTAL_SLIDER_MOVED,
            {"ui_element": hud.water_alpha_slider, "value": 85},
        )

        consumed = hud.process_event(event)

        self.assertTrue(consumed)
        self.assertEqual(calls, [("water_alpha", 0.85)])
        self.assertIn("85%", hud.water_alpha_label.text)

    def test_callback_optional(self) -> None:
        from vibestorm.viewer3d.hud import HUD, RENDER_MODE_3D

        # No callback provided — switching should still work without raising.
        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        self._click(hud, hud.render_mode_3d_button)

        self.assertEqual(hud.render_mode, RENDER_MODE_3D)

    def test_inventory_rows_include_folders_children_items_and_details(self) -> None:
        from uuid import UUID

        from vibestorm.caps.inventory_client import (
            InventoryCategoryEntry,
            InventoryFetchSnapshot,
            InventoryFolderContents,
            InventoryItemEntry,
        )
        from vibestorm.viewer3d.hud import inventory_snapshot_rows

        root_id = UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c")
        child_id = UUID("1f1cfb33-61db-4b40-894a-85f917fe3ad5")
        item_id = UUID("ef7ec4c0-a227-4d72-a489-a9b316e38514")
        rows = inventory_snapshot_rows(
            InventoryFetchSnapshot(
                folders=(
                    InventoryFolderContents(
                        folder_id=root_id,
                        owner_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
                        agent_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                        descendents=2,
                        version=7,
                        categories=(
                            InventoryCategoryEntry(
                                category_id=child_id,
                                parent_id=root_id,
                                name="Objects",
                                type_default=6,
                                version=1,
                            ),
                        ),
                        items=(
                            InventoryItemEntry(
                                item_id=item_id,
                                asset_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                                parent_id=root_id,
                                name="Test Object",
                                description="rezzable",
                                type=6,
                                inv_type=6,
                                flags=0,
                            ),
                        ),
                    ),
                ),
                inventory_root_folder_id=root_id,
            )
        )

        row_texts = [row.text for row in rows]
        self.assertIn("▾ ◼ Inventory Root [root]", row_texts)
        self.assertIn("   ▸ ◻ Objects (not loaded)", row_texts)
        self.assertIn("      • Test Object", row_texts)
        item_detail = rows[row_texts.index("      • Test Object")].detail_html
        self.assertIn("Kind: item", item_detail)
        self.assertIn("Description: rezzable", item_detail)
        self.assertIn(str(item_id), item_detail)

    def test_inventory_window_uses_selection_list_and_details_pane(self) -> None:
        from uuid import UUID

        from vibestorm.caps.inventory_client import (
            InventoryFetchSnapshot,
            InventoryFolderContents,
            InventoryItemEntry,
        )
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene

        root_id = UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c")
        item_id = UUID("ef7ec4c0-a227-4d72-a489-a9b316e38514")
        scene = Scene()
        scene.inventory_snapshot = InventoryFetchSnapshot(
            folders=(
                InventoryFolderContents(
                    folder_id=root_id,
                    owner_id=None,
                    agent_id=None,
                    descendents=1,
                    version=1,
                    categories=(),
                    items=(
                        InventoryItemEntry(
                            item_id=item_id,
                            asset_id=None,
                            parent_id=root_id,
                            name="Snapshot Notecard",
                            description="debug note",
                            type=7,
                            inv_type=7,
                            flags=0,
                        ),
                    ),
                ),
            ),
            inventory_root_folder_id=root_id,
        )
        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        hud.update(0.016, scene)
        selection = "      • Snapshot Notecard"
        event = self.pygame.event.Event(
            self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
            {"ui_element": hud.inventory_list, "text": selection},
        )
        consumed = hud.process_event(event)

        self.assertTrue(consumed)
        self.assertIn("Folders: 1 | Items: 1", hud.inventory_summary.text)
        self.assertIn("Snapshot Notecard", hud.inventory_details.html_text)
        self.assertIn("debug note", hud.inventory_details.html_text)

    def test_inventory_open_button_requests_unloaded_folder(self) -> None:
        from uuid import UUID

        from vibestorm.caps.inventory_client import (
            InventoryCategoryEntry,
            InventoryFetchSnapshot,
            InventoryFolderContents,
        )
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene

        root_id = UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c")
        child_id = UUID("1f1cfb33-61db-4b40-894a-85f917fe3ad5")
        scene = Scene()
        scene.inventory_snapshot = InventoryFetchSnapshot(
            folders=(
                InventoryFolderContents(
                    folder_id=root_id,
                    owner_id=None,
                    agent_id=None,
                    descendents=1,
                    version=1,
                    categories=(
                        InventoryCategoryEntry(
                            category_id=child_id,
                            parent_id=root_id,
                            name="Objects",
                            type_default=6,
                            version=1,
                        ),
                    ),
                    items=(),
                ),
            ),
            inventory_root_folder_id=root_id,
        )
        opened: list[UUID] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_inventory_open_folder=opened.append,
        )
        hud.update(0.016, scene)

        selection = "   ▸ ◻ Objects (not loaded)"
        hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
                {"ui_element": hud.inventory_list, "text": selection},
            )
        )
        consumed = self._click(hud, hud.inventory_open_button)

        self.assertTrue(consumed)
        self.assertEqual(opened, [child_id])


if __name__ == "__main__":
    unittest.main()
