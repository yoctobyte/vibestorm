"""Turning the avatar with the turn control bits.

The simulator does not turn the avatar for us. Holding
AGENT_CONTROL_TURN_LEFT against OpenSim for eight seconds left the reported
yaw at exactly 0; setting a yawed BodyRotation took effect on the next
AgentUpdate, and walking then followed the new facing
(``tools/verify_avatar_turn.py``). So the client integrates the turn bits into
the rotation it sends.
"""

import math
import unittest

from pathlib import Path
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.control_flags import AgentControlFlags
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import packed_quaternion_yaw, yaw_to_packed_quaternion
from vibestorm.udp.session import LiveCircuitSession, SessionConfig


def _bootstrap() -> LoginBootstrap:
    return LoginBootstrap(
        agent_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        session_id=UUID("11111111-2222-3333-4444-555555555555"),
        secure_session_id=UUID("99999999-8888-7777-6666-555555555555"),
        circuit_code=0x12345678,
        sim_ip="127.0.0.1",
        sim_port=9000,
        seed_capability="http://127.0.0.1:9000/caps/seed",
        region_x=256,
        region_y=512,
        message="ok",
    )


class PackedQuaternionTests(unittest.TestCase):
    def test_yaw_round_trips_through_the_wire_form(self) -> None:
        for degrees in (-179.0, -90.0, -45.0, 0.0, 30.0, 90.0, 179.0):
            with self.subTest(degrees=degrees):
                packed = yaw_to_packed_quaternion(math.radians(degrees))
                self.assertAlmostEqual(
                    math.degrees(packed_quaternion_yaw(packed)), degrees, places=4
                )

    def test_half_a_turn_reads_back_on_the_positive_branch(self) -> None:
        # +180 and -180 are the same rotation; atan2 picks the positive one.
        # Pinned so the round trip above can exclude the boundary honestly.
        for degrees in (180.0, -180.0):
            packed = yaw_to_packed_quaternion(math.radians(degrees))
            self.assertAlmostEqual(math.degrees(packed_quaternion_yaw(packed)), 180.0, places=4)

    def test_a_yaw_is_a_rotation_about_z_only(self) -> None:
        x, y, z = yaw_to_packed_quaternion(math.radians(90.0))
        self.assertEqual((x, y), (0.0, 0.0))
        self.assertAlmostEqual(z, math.sin(math.radians(45.0)), places=6)

    def test_yaw_past_half_a_turn_wraps_instead_of_mirroring(self) -> None:
        # The packed form implies a non-negative w, so it can only carry a yaw
        # in [-pi, pi]. 270 degrees left must come back as 90 degrees right,
        # not as a mirrored rotation.
        packed = yaw_to_packed_quaternion(math.radians(270.0))
        self.assertAlmostEqual(math.degrees(packed_quaternion_yaw(packed)), -90.0, places=4)

    def test_identity_is_the_zero_vector(self) -> None:
        self.assertEqual(yaw_to_packed_quaternion(0.0), (0.0, 0.0, 0.0))
        self.assertEqual(packed_quaternion_yaw((0.0, 0.0, 0.0)), 0.0)


class _TurnSession:
    """A real session, driven only through its public rotation surface."""

    def __init__(self, rate_degrees: float = 90.0) -> None:
        self.session = LiveCircuitSession(
            _bootstrap(),
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(turn_rate_degrees_per_second=rate_degrees),
        )

    def hold(self, flag: AgentControlFlags | int) -> None:
        self.session.add_control_flags(int(flag))

    def tick(self, seconds: float) -> None:
        self.session._apply_turn_flags(seconds)

    @property
    def yaw_degrees(self) -> float:
        return math.degrees(packed_quaternion_yaw(self.session.body_rotation))


class TurnFlagIntegrationTests(unittest.TestCase):
    def test_turn_left_yaws_positively_at_the_configured_rate(self) -> None:
        s = _TurnSession(rate_degrees=90.0)
        s.hold(AgentControlFlags.TURN_LEFT)
        s.tick(0.5)
        self.assertAlmostEqual(s.yaw_degrees, 45.0, places=4)

    def test_turn_right_yaws_the_other_way(self) -> None:
        s = _TurnSession(rate_degrees=90.0)
        s.hold(AgentControlFlags.TURN_RIGHT)
        s.tick(1.0)
        self.assertAlmostEqual(s.yaw_degrees, -90.0, places=4)

    def test_turning_accumulates_across_ticks(self) -> None:
        s = _TurnSession(rate_degrees=90.0)
        s.hold(AgentControlFlags.TURN_LEFT)
        for _ in range(4):
            s.tick(0.25)
        self.assertAlmostEqual(s.yaw_degrees, 90.0, places=4)

    def test_both_turn_keys_held_cancel_out(self) -> None:
        s = _TurnSession()
        s.hold(AgentControlFlags.TURN_LEFT)
        s.hold(AgentControlFlags.TURN_RIGHT)
        s.tick(1.0)
        self.assertEqual(s.session.body_rotation, (0.0, 0.0, 0.0))

    def test_no_turn_flag_leaves_the_rotation_alone(self) -> None:
        s = _TurnSession()
        s.session.body_rotation = (0.0, 0.0, 0.25)
        s.hold(AgentControlFlags.AT_POS)
        s.tick(1.0)
        self.assertEqual(s.session.body_rotation, (0.0, 0.0, 0.25))

    def test_the_head_follows_the_body(self) -> None:
        s = _TurnSession()
        s.hold(AgentControlFlags.TURN_LEFT)
        s.tick(0.5)
        self.assertEqual(s.session.head_rotation, s.session.body_rotation)

    def test_a_zero_length_tick_changes_nothing(self) -> None:
        s = _TurnSession()
        s.hold(AgentControlFlags.TURN_LEFT)
        s.tick(0.0)
        self.assertEqual(s.session.body_rotation, (0.0, 0.0, 0.0))

    def test_turning_continuously_wraps_rather_than_mirroring(self) -> None:
        # Three seconds at 90 deg/s is 270 degrees left, which the packed
        # rotation must report as 90 degrees right.
        s = _TurnSession(rate_degrees=90.0)
        s.hold(AgentControlFlags.TURN_LEFT)
        for _ in range(3):
            s.tick(1.0)
        self.assertAlmostEqual(s.yaw_degrees, -90.0, places=4)


if __name__ == "__main__":
    unittest.main()
