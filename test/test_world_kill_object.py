"""What goes away when the simulator kills an object.

`KillObject` names local ids, and a linkset's children are never among them.
Observed live on 2026-09-06: two prims rezzed and linked, then the root taken
away, produced exactly one `KillObject` carrying the root's local id and
nothing else --

    KillObject events since link:
        local_ids=176204334
    root 176204334: gone
    child 176204335: STILL IN WORLD VIEW

-- and the child sat in the world view for the rest of the session. A client
that removes only what it is told keeps a phantom prim for every linkset that
ever leaves view, which walking a real region means most of them.

The avatar case is the one that has to be got right in the other direction: a
seated avatar is a child of its seat, and deleting a chair does not delete
whoever was sitting in it.
"""

from __future__ import annotations

import unittest
from uuid import UUID

from vibestorm.udp.messages import KillObjectMessage
from vibestorm.world.models import WorldObject, WorldView

PCODE_PRIM = 9
PCODE_AVATAR = 47


def _object(index: int, local_id: int, parent_id: int, *, pcode: int = PCODE_PRIM) -> WorldObject:
    return WorldObject(
        full_id=UUID(int=index), local_id=local_id, parent_id=parent_id, pcode=pcode,
        material=0, click_action=0, scale=(1.0, 1.0, 1.0), state=0, crc=0,
        update_flags=0, region_handle=0, time_dilation=0, object_data_size=0,
        position=(1.0, 2.0, 3.0), rotation=(0.0, 0.0, 0.0, 1.0), variant="prim_basic",
        name_values={}, texture_entry_size=0, texture_anim_size=0, data_size=0,
        text_size=0, media_url_size=0, ps_block_size=0, extra_params_size=0,
        extra_params_entries=(), default_texture_id=None,
    )


def _world(*objects: WorldObject) -> WorldView:
    world = WorldView()
    for obj in objects:
        world.objects[obj.full_id] = obj
        world.local_id_to_full_id[obj.local_id] = obj.full_id
    return world


class KillObjectTests(unittest.TestCase):
    def test_the_named_object_goes(self) -> None:
        world = _world(_object(1, 10, 0))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.objects, {})

    def test_a_child_goes_with_its_root(self) -> None:
        # The live case: one KillObject for the root, nothing for the child.
        world = _world(_object(1, 10, 0), _object(2, 11, 10))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.objects, {})

    def test_a_grandchild_goes_too(self) -> None:
        # An attachment's own children are children of a child; sweeping one
        # level leaves everything below the first attached prim behind.
        world = _world(_object(1, 10, 0), _object(2, 11, 10), _object(3, 12, 11))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.objects, {})

    def test_the_local_id_index_is_cleaned_too(self) -> None:
        # A stale entry here points a future local id at the wrong prim, since
        # the simulator reuses local ids and UUIDs are what is stable.
        world = _world(_object(1, 10, 0), _object(2, 11, 10))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.local_id_to_full_id, {})

    def test_another_linkset_is_left_alone(self) -> None:
        world = _world(
            _object(1, 10, 0), _object(2, 11, 10), _object(3, 20, 0), _object(4, 21, 20)
        )

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(set(world.objects), {UUID(int=3), UUID(int=4)})

    def test_a_seated_avatar_survives_its_seat(self) -> None:
        # Sitting is parenting -- observed in tools/verify_seated_avatar.py --
        # so an avatar looks exactly like a linkset child here. Deleting a
        # chair does not delete whoever was sitting in it; they stand up.
        world = _world(_object(1, 10, 0), _object(2, 11, 10, pcode=PCODE_AVATAR))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(set(world.objects), {UUID(int=2)})

    def test_a_prim_attached_to_a_killed_avatar_still_goes(self) -> None:
        # The exception is the avatar itself, not everything below one: an
        # attachment on an avatar that left is gone with it.
        world = _world(
            _object(1, 10, 0, pcode=PCODE_AVATAR), _object(2, 11, 10)
        )

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.objects, {})

    def test_a_parent_cycle_terminates(self) -> None:
        # No simulator should send one, and a sweep that loops forever on a
        # malformed world view is worse than one that leaves a prim behind.
        world = _world(_object(1, 10, 11), _object(2, 11, 10))

        world.apply_kill_object(KillObjectMessage(local_ids=(10,)))

        self.assertEqual(world.objects, {})

    def test_a_terse_only_object_goes(self) -> None:
        from vibestorm.world.models import TerseWorldObject

        world = WorldView()
        world.terse_objects[7] = TerseWorldObject(
            local_id=7, state=0, is_avatar=False, region_handle=0, time_dilation=0,
            position=(1.0, 2.0, 3.0), velocity=(0.0, 0.0, 0.0),
            acceleration=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0),
            angular_velocity=(0.0, 0.0, 0.0),
        )

        world.apply_kill_object(KillObjectMessage(local_ids=(7,)))

        self.assertEqual(world.terse_objects, {})


if __name__ == "__main__":
    unittest.main()
