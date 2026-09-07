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


def _terse(local_id: int, position, *, is_avatar: bool = False):
    from vibestorm.world.models import TerseWorldObject

    return TerseWorldObject(
        local_id=local_id,
        state=0,
        region_handle=0,
        time_dilation=0,
        position=position,
        velocity=(0.0, 0.0, 0.0),
        acceleration=(0.0, 0.0, 0.0),
        rotation=IDENTITY,
        angular_velocity=(0.0, 0.0, 0.0),
        is_avatar=is_avatar,
    )


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
        ).placed

        self.assertEqual(placed, {})

    def test_one_parented_prim_is_enough_to_compose(self) -> None:
        from vibestorm.viewer3d.scene import _region_frame_transforms

        world = _world(
            _prim(1, (10.0, 10.0, 20.0)),
            _prim(2, (2.0, 0.0, 0.0), parent_id=1),
        )

        placed = _region_frame_transforms(
            world.objects, world.terse_objects, cache={}, previous={}
        ).placed

        self.assertIn(2, placed)
        self.assertAlmostEqual(placed[2][0][0], 12.0)


class CarriedTransformsTests(unittest.TestCase):
    """The frame patches last frame's transforms; here is what that must not lose.

    Every one of these was written against a surviving mutant -- a change to
    the patch that no existing test could tell from the real thing. The
    randomised differential in `test_viewer3d_scene` walks one simulator
    operation per frame, and each of these needs two in the same frame, which
    is why they are here rather than left to it.
    """

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        return Scene()

    def test_a_full_update_takes_the_id_off_the_terse_one(self) -> None:
        """A full update for a terse-only id owns that id from then on.

        Leave the id marked as the terse update's and two things go wrong at
        once: the terse entry is written back over the full one, so the prim
        is drawn where the terse update last said rather than where the full
        one does, and the id is counted twice -- which is a spare +1 in the
        accounting, enough to hide a removal elsewhere in the same frame.
        Both faults are in this one frame, and the second is why the removed
        prim below is part of the test rather than a test of its own.
        """
        world = _world(
            _prim(8, (100.0, 100.0, 20.0)),
            _prim(9, (1.0, 0.0, 0.0), parent_id=7),
        )
        world.terse_objects[7] = _terse(7, (5.0, 5.0, 25.0))
        scene = self._scene()
        scene.refresh_from_world_view(world)
        self.assertAlmostEqual(scene.object_entities[9].position[0], 6.0)

        world.remember_object(_prim(7, (50.0, 50.0, 25.0)))
        world.objects.pop(UUID(int=8))
        scene.refresh_from_world_view(world)

        self.assertAlmostEqual(scene.object_entities[9].position[0], 51.0)
        self.assertNotIn(8, scene.object_entities)
        self.assertNotIn(8, scene._built.transforms)

    def test_a_frame_does_not_edit_the_frame_before_it(self) -> None:
        """`_BuiltEntities` is the record of one frame, and stays it.

        The repeat path hands the very same record back rather than building
        an equal one, so a frame that reached into the previous frame's
        dictionaries would make the record of what was drawn a lie -- and the
        lie would be retrospective, which is the kind nothing catches.
        """
        world = _world(_prim(1, (10.0, 10.0, 20.0)), _prim(2, (12.0, 10.0, 20.0)))
        scene = self._scene()
        scene.refresh_from_world_view(world)
        first = scene._built
        before = dict(first.transforms), dict(first.sources), set(first.terse_only)

        world.remember_object(_prim(1, (60.0, 10.0, 20.0)))
        world.remember_object(_prim(3, (7.0, 7.0, 20.0)))
        scene.refresh_from_world_view(world)

        self.assertEqual(
            (dict(first.transforms), dict(first.sources), set(first.terse_only)),
            before,
        )
        self.assertIsNot(scene._built, first)

    def _patch_outcomes(self, scene, world):
        """Refresh once, saying whether the patch was taken or declined."""
        from unittest import mock

        from vibestorm.viewer3d import scene as scene_module

        taken = []
        real = scene_module._patched_region_frame

        def spy(*args, **kwargs):
            answer = real(*args, **kwargs)
            taken.append(answer is not None)
            return answer

        with mock.patch.object(scene_module, "_patched_region_frame", spy):
            scene.refresh_from_world_view(world)
        return taken

    def test_an_arrival_is_patched_rather_than_rebuilt(self) -> None:
        """An arrival is not a removal, and the accounting has to know which.

        The check that catches a removal is a count, and a prim that arrives
        moves that count too. Stop counting arrivals and every frame with one
        reads as a frame that dropped something: still correct, because the
        answer is then rebuilt from scratch, and slower every time -- which no
        test of what was drawn can see.
        """
        world = _world(_prim(1, (10.0, 10.0, 20.0)))
        scene = self._scene()
        scene.refresh_from_world_view(world)

        world.remember_object(_prim(2, (12.0, 10.0, 20.0)))
        self.assertEqual(self._patch_outcomes(scene, world), [True])

        world.terse_objects[7] = _terse(7, (5.0, 5.0, 25.0))
        self.assertEqual(self._patch_outcomes(scene, world), [True])

    def test_a_removal_is_declined_rather_than_patched(self) -> None:
        """The other half of the same count, and the half that has to hold.

        A patched removal leaves the prim composing its children forever.
        """
        world = _world(_prim(1, (10.0, 10.0, 20.0)), _prim(2, (12.0, 10.0, 20.0)))
        scene = self._scene()
        scene.refresh_from_world_view(world)

        world.objects.pop(UUID(int=2))
        self.assertEqual(self._patch_outcomes(scene, world), [False])
        self.assertNotIn(2, scene._built.transforms)
