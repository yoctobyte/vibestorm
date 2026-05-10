"""Tests for the viewer3d Object Inspector."""

import os
import unittest
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


class ObjectInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
            import pygame_gui
        except ImportError as exc:  # pragma: no cover
            self.skipTest(f"viewer dependencies unavailable: {exc}")
        self.pygame = pygame
        self.pygame_gui = pygame_gui
        pygame.init()
        pygame.display.set_mode((640, 480))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_object_inspector_row_and_details(self) -> None:
        from vibestorm.udp.messages import ObjectPropertiesFamilyMessage
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene, SceneEntity
        from vibestorm.world.models import WorldObject, WorldView

        scene = Scene()
        scene.avatar_position = (100.0, 100.0, 20.0)

        entity = SceneEntity(
            local_id=7,
            pcode=9,
            kind="prim",
            position=(110.0, 100.0, 20.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            name="Cube",
            shape="cube",
            default_texture_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        )
        scene.object_entities[7] = entity

        world_view = WorldView()
        obj_uuid = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        owner_uuid = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

        world_obj = WorldObject(
            full_id=obj_uuid,
            local_id=7,
            parent_id=0,
            pcode=9,
            material=3,
            click_action=0,
            scale=(1.0, 1.0, 1.0),
            state=0,
            crc=12345,
            update_flags=0x01,
            region_handle=0,
            time_dilation=0,
            object_data_size=0,
            position=(110.0, 100.0, 20.0),
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
            default_texture_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            properties_family=ObjectPropertiesFamilyMessage(
                request_flags=0,
                object_id=obj_uuid,
                owner_id=owner_uuid,
                group_id=UUID(int=0),
                base_mask=0,
                owner_mask=0,
                group_mask=0,
                everyone_mask=0,
                next_owner_mask=0,
                ownership_cost=0,
                sale_type=0,
                sale_price=0,
                category=0,
                last_owner_id=UUID(int=0),
                name="Cube",
                description="A test cube",
            ),
        )
        world_view.objects[obj_uuid] = world_obj
        world_view.local_id_to_full_id[7] = obj_uuid

        hud = HUD((640, 480), on_chat_submit=lambda text: None)

        # Make the window visible so _refresh_inspector runs
        hud.inspector_window.show()

        hud.update(0.016, scene, world_view)

        # Assert row exists
        row_list = hud.inspector_list.item_list
        row_texts = [r["text"] for r in row_list]
        self.assertTrue(any("Cube" in r and "10.0m" in r for r in row_texts))

        # Select it
        selection = next(r for r in row_texts if "Cube" in r)
        event = self.pygame.event.Event(
            self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
            {"ui_element": hud.inspector_list, "text": selection},
        )
        hud.process_event(event)

        # Check details
        details_html = hud.inspector_details.html_text
        self.assertIn("Cube", details_html)
        self.assertIn("7", details_html)
        self.assertIn(str(obj_uuid), details_html)
        self.assertIn("110.00, 100.00, 20.00", details_html)  # Position
        self.assertIn("cube", details_html)  # Shape
        self.assertIn("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", details_html)  # texture UUID
        self.assertIn(str(owner_uuid), details_html)  # owner UUID
        self.assertIn("A test cube", details_html)  # description

    def test_object_inspector_close_hides_and_reopens_on_selection(self) -> None:
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene, SceneEntity

        scene = Scene()
        scene.object_entities[7] = SceneEntity(
            local_id=7,
            pcode=9,
            kind="prim",
            position=(10.0, 10.0, 20.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            name="First",
            shape="cube",
        )
        scene.object_entities[8] = SceneEntity(
            local_id=8,
            pcode=9,
            kind="prim",
            position=(20.0, 10.0, 20.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            name="Second",
            shape="cube",
        )
        hud = HUD((640, 480), on_chat_submit=lambda text: None)
        hud.inspector_window.show()
        hud.update(0.016, scene, None)

        self.assertTrue(hud.inspector_window.visible)
        hud.inspector_window.on_close_window_button_pressed()

        self.assertFalse(hud.inspector_window.visible)

        hud.select_inspector_object(8)
        hud.update(0.016, scene, None)

        self.assertTrue(hud.inspector_window.visible)
        self.assertTrue(hud.inspector_list.visible)
        self.assertTrue(hud.inspector_details.visible)
        self.assertTrue(hud.inspector_inventory.visible)
        self.assertIn("Second", hud.inspector_details.html_text)

    def test_object_inspector_loads_and_displays_object_inventory(self) -> None:
        from vibestorm.viewer3d.hud import HUD
        from vibestorm.viewer3d.scene import Scene, SceneEntity
        from vibestorm.world.object_inventory import parse_task_inventory_text

        scene = Scene()
        scene.object_entities[7] = SceneEntity(
            local_id=7,
            pcode=9,
            kind="prim",
            position=(10.0, 10.0, 20.0),
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            name="Box",
            shape="cube",
        )
        requested: list[int] = []
        hud = HUD(
            (640, 480),
            on_chat_submit=lambda text: None,
            on_object_inventory_request=requested.append,
        )
        hud.inspector_window.show()
        hud.update(0.016, scene, None)
        row_text = next(row["text"] for row in hud.inspector_list.item_list if "Box" in row["text"])
        hud.process_event(
            self.pygame.event.Event(
                self.pygame_gui.UI_SELECTION_LIST_NEW_SELECTION,
                {"ui_element": hud.inspector_list, "text": row_text},
            )
        )

        consumed = self.pygame.event.Event(
            self.pygame_gui.UI_BUTTON_PRESSED,
            {"ui_element": hud.inspector_load_inventory_button},
        )
        self.assertTrue(hud.process_event(consumed))
        self.assertEqual(requested, [7])

        scene.object_inventory_snapshots[7] = parse_task_inventory_text(
            "inv_item 0\n{\n item_id aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n"
            " inv_type notecard\n name Read Me|\n}\n",
            local_id=7,
            task_id=None,
            serial=1,
            filename="task.inv\x00",
        )
        hud.update(0.016, scene, None)

        self.assertIn("Read Me", hud.inspector_inventory.html_text)
        self.assertIn("notecard", hud.inspector_inventory.html_text)
        self.assertIn("task.inv", hud.inspector_inventory.html_text)
        self.assertNotIn("\x00", hud.inspector_inventory.html_text)


if __name__ == "__main__":
    unittest.main()
