"""Putting a linkset's children back where they are.

The fact these are built on was observed, not assumed. Two prims were rezzed at
(130, 128, 27.1) and (134, 128, 27.1) on local OpenSim and then linked, and the
child's next update reported ``(4.0, 0.0, 0.0)`` -- its offset from the root.
``tools/verify_child_prim_frame.py`` is that observation, repeatable.

So the numbers in these tests are the shape of a real linkset, and the failure
they guard is the one that was live: a child drawn at its raw position, a few
metres from the region corner instead of beside its root.
"""

from __future__ import annotations

import math
import unittest

from vibestorm.viewer3d.linkset import (
    IDENTITY,
    compose,
    quat_multiply,
    quat_rotate,
    resolve_world_transforms,
)

#: A quarter turn about +Z, which takes +X to +Y.
YAW_90 = (0.0, 0.0, math.sin(math.pi / 4.0), math.cos(math.pi / 4.0))
#: A quarter turn about +Y, which takes +Z to +X. Deliberately a *different*
#: axis from YAW_90: two rotations about the same axis commute, so a test built
#: on one axis cannot see the order they are applied in.
PITCH_90 = (0.0, math.sin(math.pi / 4.0), 0.0, math.cos(math.pi / 4.0))


def _almost(case: unittest.TestCase, got, want, places: int = 5) -> None:
    for index, (a, b) in enumerate(zip(got, want, strict=True)):
        case.assertAlmostEqual(a, b, places=places, msg=f"component {index}: {got} != {want}")


class RotationTests(unittest.TestCase):
    def test_the_identity_leaves_a_vector_alone(self) -> None:
        _almost(self, quat_rotate(IDENTITY, (1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))

    def test_a_quarter_turn_about_z_takes_x_to_y(self) -> None:
        # The direction matters and is easy to get backwards: the wrong sign
        # puts every attachment on the wrong side of its avatar.
        _almost(self, quat_rotate(YAW_90, (1.0, 0.0, 0.0)), (0.0, 1.0, 0.0))

    def test_a_rotation_about_its_own_axis_does_nothing(self) -> None:
        _almost(self, quat_rotate(YAW_90, (0.0, 0.0, 5.0)), (0.0, 0.0, 5.0))

    def test_two_quarter_turns_are_a_half_turn(self) -> None:
        half = quat_multiply(YAW_90, YAW_90)

        _almost(self, quat_rotate(half, (1.0, 0.0, 0.0)), (-1.0, 0.0, 0.0))

    def test_multiplying_applies_the_right_hand_side_first(self) -> None:
        # compose() relies on this order. Two turns about different axes do not
        # commute, which is the only way to see it: yaw-then-pitch and
        # pitch-then-yaw send +Z to different places.
        yaw_then_pitch = quat_multiply(YAW_90, PITCH_90)
        pitch_then_yaw = quat_multiply(PITCH_90, YAW_90)

        # PITCH_90 first takes +Z to +X, then YAW_90 takes that +X to +Y.
        _almost(self, quat_rotate(yaw_then_pitch, (0.0, 0.0, 1.0)), (0.0, 1.0, 0.0))
        _almost(self, quat_rotate(pitch_then_yaw, (0.0, 0.0, 1.0)), (1.0, 0.0, 0.0))


class ComposeTests(unittest.TestCase):
    def test_the_case_that_was_observed_live(self) -> None:
        # Root at (130, 128, 27.1), child reporting (4, 0, 0). Before the fix
        # the viewer drew that child at (4, 0, 0) -- the region corner.
        root = ((130.0, 128.0, 27.121), IDENTITY)

        position, _rotation = compose(root, ((4.0, 0.0, 0.0), IDENTITY))

        _almost(self, position, (134.0, 128.0, 27.121), places=3)

    def test_the_offset_turns_with_the_parent(self) -> None:
        # Adding the offset without rotating it is the plausible half-fix, and
        # it is right for every upright root -- which is most of them, and why
        # it would survive casual testing.
        root = ((100.0, 100.0, 20.0), YAW_90)

        position, _rotation = compose(root, ((4.0, 0.0, 0.0), IDENTITY))

        _almost(self, position, (100.0, 104.0, 20.0))

    def test_the_child_s_own_turn_happens_inside_its_parent_s(self) -> None:
        # Different axes, so composing them the other way round gives a
        # different answer. With the same axis on both sides they commute and
        # the test cannot tell a correct compose from a reversed one.
        root = ((0.0, 0.0, 0.0), YAW_90)

        _position, rotation = compose(root, ((0.0, 0.0, 0.0), PITCH_90))

        # The child's pitch acts first, in the parent's frame: +Z to +X, which
        # the parent's yaw then carries round to +Y.
        _almost(self, quat_rotate(rotation, (0.0, 0.0, 1.0)), (0.0, 1.0, 0.0))


class ResolveTests(unittest.TestCase):
    def test_a_root_is_left_exactly_as_it_came(self) -> None:
        world = resolve_world_transforms({7: (0, (130.0, 128.0, 27.0), IDENTITY)})

        self.assertEqual(world[7], ((130.0, 128.0, 27.0), IDENTITY))

    def test_a_child_is_lifted_into_the_region_frame(self) -> None:
        world = resolve_world_transforms(
            {
                1: (0, (130.0, 128.0, 27.0), IDENTITY),
                2: (1, (4.0, 0.0, 0.0), IDENTITY),
            }
        )

        _almost(self, world[2][0], (134.0, 128.0, 27.0))

    def test_a_chain_resolves_all_the_way_out(self) -> None:
        # An attachment's own children are children of a child. Assuming one
        # level leaves everything below the first attached prim adrift.
        world = resolve_world_transforms(
            {
                1: (0, (100.0, 100.0, 20.0), IDENTITY),
                2: (1, (1.0, 0.0, 0.0), IDENTITY),
                3: (2, (0.0, 2.0, 0.0), IDENTITY),
            }
        )

        _almost(self, world[3][0], (101.0, 102.0, 20.0))

    def test_a_chain_resolves_whatever_order_it_arrives_in(self) -> None:
        # Updates are not ordered, and a dict that happens to hold the deepest
        # child first must not change the answer.
        deep_first = resolve_world_transforms(
            {
                3: (2, (0.0, 2.0, 0.0), IDENTITY),
                2: (1, (1.0, 0.0, 0.0), IDENTITY),
                1: (0, (100.0, 100.0, 20.0), IDENTITY),
            }
        )

        _almost(self, deep_first[3][0], (101.0, 102.0, 20.0))

    def test_a_child_whose_parent_has_not_arrived_is_left_out(self) -> None:
        # Not placed at its raw position, which is the bug, and not at the
        # origin either. The parent arrives a frame later.
        world = resolve_world_transforms({2: (1, (4.0, 0.0, 0.0), IDENTITY)})

        self.assertNotIn(2, world)

    def test_a_missing_rotation_is_treated_as_no_rotation(self) -> None:
        # Terse updates can arrive without one, and None would otherwise reach
        # the arithmetic.
        world = resolve_world_transforms(
            {
                1: (0, (10.0, 0.0, 0.0), None),
                2: (1, (1.0, 0.0, 0.0), None),
            }
        )

        _almost(self, world[2][0], (11.0, 0.0, 0.0))

    def test_a_parent_cycle_terminates_instead_of_hanging(self) -> None:
        # No simulator should send one. A viewer that spins forever on a
        # malformed update is worse than one that leaves two prims out.
        world = resolve_world_transforms(
            {
                1: (2, (0.0, 0.0, 0.0), IDENTITY),
                2: (1, (0.0, 0.0, 0.0), IDENTITY),
            }
        )

        self.assertEqual(world, {})

    def test_a_region_of_roots_is_unchanged(self) -> None:
        transforms = {n: (0, (float(n), 0.0, 0.0), IDENTITY) for n in range(1, 40)}

        world = resolve_world_transforms(transforms)

        self.assertEqual(len(world), 39)
        for local_id, (parent, position, rotation) in transforms.items():
            self.assertEqual(parent, 0)
            self.assertEqual(world[local_id], (position, rotation))


class IncrementalResolveTests(unittest.TestCase):
    """Re-resolving a region that mostly did not move.

    Composing every child of every linkset sixty times a second is most of a
    frame in a region of any size, and almost all of it arrives back at the
    answer from last frame. So a caller can say which objects are still the
    objects they were and hand back what it got then.

    The reuse has to be exact, not approximate: the caller recognises an
    unchanged child by the transform tuple being *the same object*, so these
    check identity, not equality. And the trap is the obvious one -- a child
    that did not move is somewhere else entirely if its root did.
    """

    #: A root and its child, the shape observed live.
    LINKSET = {
        1: (0, (130.0, 128.0, 27.0), IDENTITY),
        2: (1, (4.0, 0.0, 0.0), IDENTITY),
    }

    def test_an_untouched_child_keeps_the_transform_it_had(self) -> None:
        first = resolve_world_transforms(self.LINKSET)

        again = resolve_world_transforms(
            self.LINKSET, unchanged={1, 2}, previous=first
        )

        self.assertIs(again[2], first[2])

    def test_a_child_that_did_not_move_still_follows_its_root(self) -> None:
        # The child is genuinely untouched -- same offset, same rotation --
        # and belongs 70 m from where it was, because its root walked off.
        first = resolve_world_transforms(self.LINKSET)
        moved_root = {**self.LINKSET, 1: (0, (200.0, 128.0, 27.0), IDENTITY)}

        again = resolve_world_transforms(moved_root, unchanged={2}, previous=first)

        _almost(self, again[2][0], (204.0, 128.0, 27.0))

    def test_a_moved_root_carries_a_whole_chain(self) -> None:
        # Stopping at the first level leaves everything below an attachment
        # behind: the middle link is unchanged, so a check that only looks at
        # the immediate parent finds nothing to do.
        chain = {
            1: (0, (100.0, 100.0, 20.0), IDENTITY),
            2: (1, (1.0, 0.0, 0.0), IDENTITY),
            3: (2, (0.0, 2.0, 0.0), IDENTITY),
        }
        first = resolve_world_transforms(chain)
        moved_root = {**chain, 1: (0, (150.0, 100.0, 20.0), IDENTITY)}

        again = resolve_world_transforms(moved_root, unchanged={2, 3}, previous=first)

        _almost(self, again[3][0], (151.0, 102.0, 20.0))

    def test_a_rotating_root_swings_an_unchanged_child(self) -> None:
        # Position is not the only thing a parent contributes. A root that only
        # turned leaves its children's own transforms untouched.
        upright = {1: (0, (0.0, 0.0, 0.0), IDENTITY), 2: (1, (4.0, 0.0, 0.0), IDENTITY)}
        first = resolve_world_transforms(upright)
        turned = {1: (0, (0.0, 0.0, 0.0), YAW_90), 2: (1, (4.0, 0.0, 0.0), IDENTITY)}

        again = resolve_world_transforms(turned, unchanged={2}, previous=first)

        _almost(self, again[2][0], (0.0, 4.0, 0.0))

    def test_an_object_the_previous_answer_never_had_is_composed(self) -> None:
        # A caller can name an id as unchanged that the previous answer does
        # not have -- a child dropped last frame because its parent had not
        # arrived, or one that has only just come into view. Its root here is
        # genuinely still, so nothing else forces the composing: taking the
        # caller's word over the missing answer is what would drop it, and it
        # would stay dropped for as long as it sat still.
        settled_root = resolve_world_transforms({1: self.LINKSET[1]})

        world = resolve_world_transforms(
            self.LINKSET, unchanged={1, 2}, previous=settled_root
        )

        _almost(self, world[2][0], (134.0, 128.0, 27.0))

    def test_a_root_that_moved_is_reported_where_it_moved_to(self) -> None:
        first = resolve_world_transforms(self.LINKSET)
        moved_root = {**self.LINKSET, 1: (0, (200.0, 128.0, 27.0), IDENTITY)}

        again = resolve_world_transforms(moved_root, unchanged={2}, previous=first)

        _almost(self, again[1][0], (200.0, 128.0, 27.0))


if __name__ == "__main__":
    unittest.main()
