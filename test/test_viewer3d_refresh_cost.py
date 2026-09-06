"""What makes a still region cheap to refresh, pinned so it stays cheap.

`Scene.refresh_from_world_view` runs once a frame and walks every prim in
view whether or not anything moved, so its cost is the floor under the frame
rate before a triangle is drawn. `tools/bench_scene_refresh.py` measures it;
this holds the contracts the measurements rest on, because every one of them
is the kind of thing that can be quietly broken by a change that still
passes every behavioural test.

Nothing here asserts a number. A test that says "this must take under n
milliseconds" fails on a busy machine and gets deleted; a test that says
"this must still be the same tuple" fails exactly when someone breaks the
reason it was fast.
"""

from __future__ import annotations

import os
import unittest
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

IDENTITY = (0.0, 0.0, 0.0, 1.0)


def _prim(local_id: int, position, *, parent_id: int = 0, pcode: int = 9):
    from vibestorm.world.models import WorldObject

    return WorldObject(
        full_id=UUID(int=local_id),
        local_id=local_id,
        parent_id=parent_id,
        pcode=pcode,
        material=0,
        click_action=0,
        scale=(1.0, 1.0, 1.0),
        state=0,
        crc=0,
        update_flags=0,
        region_handle=0,
        time_dilation=0,
        object_data_size=0,
        position=position,
        rotation=IDENTITY,
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
    )


def _world(*objects):
    from vibestorm.world.models import WorldView

    view = WorldView()
    for obj in objects:
        view.remember_object(obj)
    return view


class TransformIdentityTests(unittest.TestCase):
    """`resolve_world_transforms` hands back the *same* tuple, not an equal one.

    The scene compares a child's remembered transform with `is`, which is
    what keeps a still linkset region from walking two nested tuples of
    floats per child per frame. If this ever starts returning equal-but-new
    tuples the viewer stays correct and quietly rebuilds every entity in the
    region, every frame -- a regression with no failing test unless one of
    these exists.
    """

    LINKSET = {
        1: (0, (130.0, 128.0, 27.0), IDENTITY),
        2: (1, (4.0, 0.0, 0.0), IDENTITY),
    }

    def test_a_root_that_did_not_move_keeps_its_very_tuple(self) -> None:
        from vibestorm.viewer3d.linkset import resolve_world_transforms

        first = resolve_world_transforms(self.LINKSET)
        again = resolve_world_transforms(
            self.LINKSET, unchanged={1, 2}, previous=first
        )

        self.assertIs(again[1], first[1])

    def test_a_child_that_did_not_move_keeps_its_very_tuple(self) -> None:
        from vibestorm.viewer3d.linkset import resolve_world_transforms

        first = resolve_world_transforms(self.LINKSET)
        again = resolve_world_transforms(
            self.LINKSET, unchanged={1, 2}, previous=first
        )

        self.assertIs(again[2], first[2])

    def test_a_child_under_a_moved_root_gets_a_new_tuple(self) -> None:
        # The other half of the contract: `is` has to say "changed" here, or
        # the scene keeps drawing the child where its root used to be.
        from vibestorm.viewer3d.linkset import resolve_world_transforms

        first = resolve_world_transforms(self.LINKSET)
        moved = {**self.LINKSET, 1: (0, (200.0, 128.0, 27.0), IDENTITY)}

        again = resolve_world_transforms(moved, unchanged={2}, previous=first)

        self.assertIsNot(again[2], first[2])


class EntityReuseTests(unittest.TestCase):
    """A prim nothing happened to is handed back the entity it already had."""

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        return Scene()

    def test_a_still_region_reuses_every_entity(self) -> None:
        world = _world(_prim(1, (10.0, 10.0, 20.0)), _prim(2, (12.0, 10.0, 20.0)))
        scene = self._scene()
        scene.refresh_from_world_view(world)
        first = dict(scene.object_entities)

        scene.refresh_from_world_view(world)

        for local_id, entity in first.items():
            self.assertIs(scene.object_entities[local_id], entity)

    def test_a_still_linkset_reuses_its_children(self) -> None:
        world = _world(
            _prim(1, (10.0, 10.0, 20.0)),
            _prim(2, (2.0, 0.0, 0.0), parent_id=1),
        )
        scene = self._scene()
        scene.refresh_from_world_view(world)
        child = scene.object_entities[2]

        scene.refresh_from_world_view(world)

        self.assertIs(scene.object_entities[2], child)

    def test_a_moved_root_rebuilds_its_child(self) -> None:
        # The reuse must not survive the thing it depends on changing.
        world = _world(
            _prim(1, (10.0, 10.0, 20.0)),
            _prim(2, (2.0, 0.0, 0.0), parent_id=1),
        )
        scene = self._scene()
        scene.refresh_from_world_view(world)
        child = scene.object_entities[2]

        world.remember_object(_prim(1, (60.0, 10.0, 20.0)))
        scene.refresh_from_world_view(world)

        self.assertIsNot(scene.object_entities[2], child)
        self.assertAlmostEqual(scene.object_entities[2].position[0], 62.0)

    def test_a_prim_that_moved_is_rebuilt(self) -> None:
        world = _world(_prim(1, (10.0, 10.0, 20.0)))
        scene = self._scene()
        scene.refresh_from_world_view(world)
        before = scene.object_entities[1]

        world.remember_object(_prim(1, (11.0, 10.0, 20.0)))
        scene.refresh_from_world_view(world)

        self.assertIsNot(scene.object_entities[1], before)
        self.assertAlmostEqual(scene.object_entities[1].position[0], 11.0)


class RegionOfRootsTests(unittest.TestCase):
    """A region with nothing parented does not pay for the composing at all."""

    def test_nothing_parented_composes_nothing(self) -> None:
        from vibestorm.viewer3d.scene import _region_frame_transforms

        world = _world(_prim(1, (10.0, 10.0, 20.0)), _prim(2, (12.0, 10.0, 20.0)))

        placed = _region_frame_transforms(
            world.objects, world.terse_objects, cache={}, previous={}
        )

        self.assertEqual(placed, {})

    def test_one_parented_prim_is_enough_to_compose(self) -> None:
        from vibestorm.viewer3d.scene import _region_frame_transforms

        world = _world(
            _prim(1, (10.0, 10.0, 20.0)),
            _prim(2, (2.0, 0.0, 0.0), parent_id=1),
        )

        placed = _region_frame_transforms(
            world.objects, world.terse_objects, cache={}, previous={}
        )

        self.assertIn(2, placed)
        self.assertAlmostEqual(placed[2][0][0], 12.0)
