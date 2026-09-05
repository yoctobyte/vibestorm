"""Deriving an avatar's gait from where it has been.

There is no oracle here -- the simulator never tells us what a walk should
look like -- so what these pin is the arithmetic that keeps the walk *stable*:
a stride tied to distance rather than to frame rate, and the three ways a
sudden change of position is not someone running.
"""

from __future__ import annotations

import math
import unittest

from vibestorm.viewer3d.avatar_pose import (
    FULL_STRIDE_SPEED_MPS,
    MAX_STEP_SECONDS,
    STILL_SPEED_MPS,
    STOP_AFTER_SECONDS,
    STRIDE_LENGTH_M,
    TELEPORT_DISTANCE_M,
    AvatarMotion,
    advance_all,
    advance_motion,
    pose_for_motion,
)

ORIGIN = (0.0, 0.0, 0.0)


def _walk(steps: int, *, speed: float, dt: float = 0.05) -> AvatarMotion:
    """Walk in a straight line along +X for ``steps`` frames."""
    motion = AvatarMotion(position=ORIGIN)
    x = 0.0
    for _ in range(steps):
        x += speed * dt
        motion = advance_motion(motion, (x, 0.0, 0.0), dt)
    return motion


class MotionTests(unittest.TestCase):
    def test_a_first_sighting_is_standing(self) -> None:
        # There is no previous position to measure against, and guessing puts
        # a stride on someone who has not moved.
        motion = advance_motion(None, (10.0, 20.0, 30.0), 0.05)

        self.assertEqual(motion.position, (10.0, 20.0, 30.0))
        self.assertEqual(motion.speed_mps, 0.0)
        self.assertFalse(motion.walking)

    def test_walking_builds_up_speed_and_advances_the_stride(self) -> None:
        motion = _walk(30, speed=2.0)

        self.assertAlmostEqual(motion.speed_mps, 2.0, delta=0.2)
        self.assertTrue(motion.walking)
        self.assertGreater(motion.gait_phase, 0.0)

    def test_the_stride_follows_distance_not_frame_rate(self) -> None:
        # The same ground covered in twice as many frames must leave the feet
        # in the same place. Tie the phase to time instead and the gait speeds
        # up whenever the frame rate does.
        coarse = _walk(20, speed=2.0, dt=0.05)
        fine = _walk(40, speed=2.0, dt=0.025)

        self.assertAlmostEqual(coarse.position[0], fine.position[0], places=6)
        self.assertAlmostEqual(coarse.gait_phase, fine.gait_phase, places=6)

    def test_one_stride_is_one_full_cycle_of_the_stride_length(self) -> None:
        motion = AvatarMotion(position=ORIGIN, speed_mps=2.0)
        walked = advance_motion(motion, (STRIDE_LENGTH_M / 4.0, 0.0, 0.0), 0.2)

        self.assertAlmostEqual(walked.gait_phase, math.pi / 2.0, places=6)

    def test_a_teleport_is_not_a_sprint(self) -> None:
        motion = AvatarMotion(position=ORIGIN, speed_mps=1.0)

        jumped = advance_motion(motion, (TELEPORT_DISTANCE_M + 1.0, 0.0, 0.0), 0.05)

        self.assertEqual(jumped.speed_mps, 0.0)
        self.assertEqual(jumped.position, (TELEPORT_DISTANCE_M + 1.0, 0.0, 0.0))

    def test_a_long_gap_is_not_a_sprint(self) -> None:
        # A paused window or a stalled connection produces a real distance
        # over a long interval; the avatar did not run it.
        motion = AvatarMotion(position=ORIGIN, speed_mps=1.0)

        resumed = advance_motion(motion, (5.0, 0.0, 0.0), MAX_STEP_SECONDS + 1.0)

        self.assertEqual(resumed.speed_mps, 0.0)

    def test_the_stride_is_kept_across_a_gap_rather_than_snapped_to_zero(self) -> None:
        # Resetting the phase would jerk both legs to the passing position on
        # the frame after any hiccup.
        motion = AvatarMotion(position=ORIGIN, speed_mps=2.0, gait_phase=1.234)

        resumed = advance_motion(motion, (1.0, 0.0, 0.0), MAX_STEP_SECONDS + 1.0)

        self.assertAlmostEqual(resumed.gait_phase, 1.234)

    def test_rising_is_not_walking(self) -> None:
        # Flying straight up, or a physics correction settling the avatar onto
        # the ground, must not drive a stride.
        motion = AvatarMotion(position=ORIGIN, speed_mps=0.0)

        lifted = advance_motion(motion, (0.0, 0.0, 4.0), 0.05)

        self.assertEqual(lifted.speed_mps, 0.0)
        self.assertEqual(lifted.gait_phase, 0.0)

    def test_a_shuffle_below_the_floor_does_not_start_the_legs(self) -> None:
        # Position updates jitter by centimetres at rest. Without a floor the
        # figure twitches whenever a packet arrives.
        motion = AvatarMotion(position=ORIGIN)
        for step in range(1, 20):
            motion = advance_motion(motion, (0.002 * step, 0.0, 0.0), 0.05)

        self.assertLess(motion.speed_mps, STILL_SPEED_MPS)
        self.assertEqual(motion.gait_phase, 0.0)

    def test_a_sparse_position_stream_reads_the_same_speed_as_a_dense_one(self) -> None:
        # The real shape of the problem, and the reason this class exists. The
        # simulator sends positions when they change, at its own rate; the
        # viewer samples once a frame. So most frames see last frame's position
        # again. Divide "did not move" by a frame time, call it a speed, and
        # the reading alternates between zero and several times the truth --
        # a live walk at 1.9 m/s measured a 9.28 m/s peak that way, and every
        # avatar in the region breaks into a sprint between updates.
        dense = AvatarMotion(position=ORIGIN)
        sparse = AvatarMotion(position=ORIGIN)
        dense_peak = sparse_peak = 0.0
        speed, dt, every = 1.9, 1.0 / 60.0, 6  # 60 Hz frames, 10 Hz updates

        for frame in range(1, 121):
            travelled = speed * dt * frame
            dense = advance_motion(dense, (travelled, 0.0, 0.0), dt)
            held = speed * dt * (frame - frame % every)
            sparse = advance_motion(sparse, (held, 0.0, 0.0), dt)
            dense_peak = max(dense_peak, dense.speed_mps)
            sparse_peak = max(sparse_peak, sparse.speed_mps)

        self.assertAlmostEqual(sparse_peak, dense_peak, delta=0.2)
        self.assertAlmostEqual(sparse.speed_mps, speed, delta=0.2)
        self.assertTrue(sparse.walking)

    def test_a_pause_between_updates_is_not_a_stop(self) -> None:
        # Holding the speed through a gap is what the sparse stream above
        # relies on. Zeroing it the moment a frame repeats a position drops
        # every avatar back to a rest pose between updates, which reads as a
        # walk cycle stuttering rather than as a walk.
        walking = _walk(30, speed=2.0)

        held = advance_motion(walking, walking.position, STOP_AFTER_SECONDS / 2.0)

        self.assertAlmostEqual(held.speed_mps, walking.speed_mps)
        self.assertTrue(held.walking)

    def test_standing_still_long_enough_does_stop_the_walk(self) -> None:
        # The other half: held indefinitely, an avatar that walked out of the
        # update stream would walk on the spot forever.
        walking = _walk(30, speed=2.0)

        stopped = advance_motion(walking, walking.position, STOP_AFTER_SECONDS + 0.01)

        self.assertEqual(stopped.speed_mps, 0.0)
        self.assertFalse(stopped.walking)

    def test_a_step_after_a_long_wait_is_not_measured_against_the_whole_wait(self) -> None:
        # Speed is measured between moves, so the waiting time is the
        # denominator -- and an avatar that stood still for a minute before
        # setting off would have its first strides divided by that minute.
        standing = AvatarMotion(position=ORIGIN)
        for _ in range(600):
            standing = advance_motion(standing, ORIGIN, 0.1)

        moved = advance_motion(standing, (0.19, 0.0, 0.0), 0.1)

        self.assertGreaterEqual(moved.speed_mps, STILL_SPEED_MPS)

    def test_a_correction_snap_is_over_before_the_second_is(self) -> None:
        # Seen live: coming to a halt, the simulator snapped the avatar 0.46 m
        # in one 50 ms sample -- 9.3 m/s, inside the teleport gate and so read
        # as a sprint. Nothing bounds the reading itself, and nothing needs to:
        # the pose saturates far below it, and the stillness that follows is
        # what puts the legs down.
        motion = advance_motion(AvatarMotion(position=ORIGIN), (0.46, 0.0, 0.0), 0.05)
        self.assertTrue(motion.walking, "the snap does read as movement")

        for _ in range(8):
            motion = advance_motion(motion, (0.46, 0.0, 0.0), 0.05)

        self.assertFalse(motion.walking, "a snap left the figure walking on the spot")
        self.assertLess(0.05 * 8, 1.0, "and it is over inside a second")

    def test_avatars_that_have_gone_do_not_stay_in_the_table(self) -> None:
        # Local ids are recycled per region session, and a table that only
        # grows is a leak in a viewer meant to run for hours.
        previous = {1: AvatarMotion(position=ORIGIN), 2: AvatarMotion(position=ORIGIN)}

        current = advance_all(previous, {2: (1.0, 0.0, 0.0)}, 0.05)

        self.assertEqual(set(current), {2})


class PoseTests(unittest.TestCase):
    def test_standing_still_is_a_rest_pose_not_a_frozen_stride(self) -> None:
        # Fading the amplitude to zero would leave the legs wherever the last
        # step put them, which is a figure standing mid-stride forever.
        pose = pose_for_motion(AvatarMotion(position=ORIGIN, speed_mps=0.0, gait_phase=1.0))

        self.assertNotIn("leg_l", pose)
        self.assertNotIn("leg_r", pose)
        self.assertLess(pose["forearm_l"], 0.0, "a resting elbow is not straight")

    def test_the_legs_swing_opposite_each_other(self) -> None:
        pose = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=3.0, gait_phase=math.pi / 2.0)
        )

        self.assertAlmostEqual(pose["leg_l"], -pose["leg_r"])
        self.assertNotAlmostEqual(pose["leg_l"], 0.0)

    def test_the_arms_swing_opposite_the_leg_on_the_same_side(self) -> None:
        # Same-side arm and leg swinging together is the marionette walk, and
        # it is the single most obvious thing to get wrong here.
        pose = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=3.0, gait_phase=math.pi / 2.0)
        )

        self.assertLess(pose["leg_l"] * pose["arm_l"], 0.0)
        self.assertLess(pose["leg_r"] * pose["arm_r"], 0.0)

    def test_a_knee_only_ever_bends_one_way(self) -> None:
        for step in range(24):
            phase = 2.0 * math.pi * step / 24.0
            pose = pose_for_motion(
                AvatarMotion(position=ORIGIN, speed_mps=3.0, gait_phase=phase)
            )
            with self.subTest(phase=round(phase, 3)):
                self.assertLessEqual(pose["shin_l"], 1e-9)
                self.assertLessEqual(pose["shin_r"], 1e-9)

    def test_the_stride_stops_growing_once_it_is_a_run(self) -> None:
        # Past the full-stride speed the gait cycles faster -- which the
        # phase-by-distance rule gives for free -- rather than the legs
        # swinging further and further apart.
        fast = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=FULL_STRIDE_SPEED_MPS, gait_phase=math.pi / 2.0)
        )
        faster = pose_for_motion(
            AvatarMotion(
                position=ORIGIN,
                speed_mps=FULL_STRIDE_SPEED_MPS * 4.0,
                gait_phase=math.pi / 2.0,
            )
        )

        self.assertAlmostEqual(fast["leg_l"], faster["leg_l"])

    def test_a_slow_walk_swings_less_than_a_fast_one(self) -> None:
        slow = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=0.8, gait_phase=math.pi / 2.0)
        )
        fast = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=3.0, gait_phase=math.pi / 2.0)
        )

        self.assertLess(abs(slow["leg_l"]), abs(fast["leg_l"]))

    def test_every_bone_the_pose_names_is_a_real_bone(self) -> None:
        # A typo here is silent: bone_matrices looks the pose up by name and
        # a name nothing matches simply leaves that limb at rest.
        from vibestorm.viewer3d.avatar_mesh import AVATAR_BONES

        known = {bone.name for bone in AVATAR_BONES}
        posed = pose_for_motion(
            AvatarMotion(position=ORIGIN, speed_mps=3.0, gait_phase=1.0)
        )

        self.assertTrue(posed)
        for name in posed:
            self.assertIn(name, known)


if __name__ == "__main__":
    unittest.main()
