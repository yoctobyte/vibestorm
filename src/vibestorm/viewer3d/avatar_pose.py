"""Posing avatars from where they have been.

An SL animation asset is keyframe data against a skeleton this tree does not
have, and the skeleton ships inside viewers. `AvatarAnimation` names which
animations are running, but the names are UUIDs whose meaning is only knowable
from a table this project has no source for -- so acting on them would be
guessing, and a guess that is wrong looks exactly like a bug.

What *is* knowable is where every avatar in the region is, frame after frame.
Two positions and a clock give a speed, and a speed is enough to say whether
someone is standing, walking or running -- which is most of the difference a
person notices from across a parcel. So the gait here is derived, not played
back: it is honest about being this client's own idea of walking rather than
the animation the simulator is actually running.

Two consequences worth knowing:

- It works for **every** avatar, including one running a custom animation this
  client could never have decoded.
- The phase advances with **distance travelled**, not with time, so a foot
  plants at the same point in the stride however irregularly the updates
  arrive, and a stutter in the network does not become a stutter in the walk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Below this the avatar is standing. Position updates jitter by a few
#: centimetres even at rest, and without a floor the figure twitches.
STILL_SPEED_MPS: float = 0.25

#: The speed the stride reaches full size at. A default SL walk is about
#: 3.2 m/s; running is faster but the gait does not keep growing, it just
#: cycles quicker, which falls out of phase-by-distance for free.
FULL_STRIDE_SPEED_MPS: float = 3.2

#: One full stride -- left foot forward, right foot forward, left again --
#: per this many metres. About 1.5 m of ground per two paces.
STRIDE_LENGTH_M: float = 1.5

#: Peak hip swing at full stride.
LEG_SWING_RADIANS: float = 0.52

#: Arms counter-swing at about two thirds of the legs, which is what makes a
#: walk read as a walk rather than as a marionette.
ARM_SWING_RATIO: float = 0.62

#: The knee bends on the back half of the stride and straightens on the front.
#: Bending both ways looks like a limp.
KNEE_BEND_RADIANS: float = 0.62

#: How far the hip folds when seated. Just short of a right angle: a thigh at
#: exactly 90 degrees reads as a mannequin rather than as someone sitting.
#: Negative because a positive bone pitch swings a bone's far end *backwards*,
#: and a seated thigh points forwards.
SIT_HIP_RADIANS: float = -1.45

#: And the knee folds back by about as much, which puts the shin vertical
#: again. Positive, for the same reason the hip is negative.
SIT_KNEE_RADIANS: float = 1.40

#: A resting elbow is not straight. Small, constant, and it stops the arms
#: reading as planks when the avatar is standing still.
ELBOW_REST_RADIANS: float = -0.12

#: How fast the smoothed speed follows the measured one, per second. High
#: enough to start walking promptly, low enough that one late packet does not
#: read as a sprint.
SPEED_RESPONSE_PER_S: float = 6.0

#: A step smaller than this is not movement. Positions arrive as *events* --
#: the simulator sends an update when something changes, at its own rate --
#: while the viewer samples once a frame, so most frames see the same position
#: twice. Dividing "no movement" by a frame time and calling it a speed makes
#: the reading alternate between zero and several times the real value; a live
#: walk at 1.9 m/s measured a peak of 9.28 that way.
MOVEMENT_EPSILON_M: float = 0.005

#: No position change for this long means the avatar has stopped, rather than
#: that the next update is merely late. Longer than a few update intervals, so
#: an ordinary gap in the stream does not read as stopping.
STOP_AFTER_SECONDS: float = 0.35

#: Longest gap treated as continuous motion. Beyond it -- a teleport, a
#: paused window, a region change -- the distance is real but was not walked.
MAX_STEP_SECONDS: float = 0.5

#: Beyond this in one step the avatar did not walk there.
TELEPORT_DISTANCE_M: float = 12.0


@dataclass(frozen=True, slots=True)
class AvatarMotion:
    """What one avatar has been doing, in as little state as will do.

    ``gait_phase`` is in radians and only ever advances, so it survives being
    read at any frame rate: the pose is a function of it, not of a frame count.
    """

    position: tuple[float, float, float]
    speed_mps: float = 0.0
    gait_phase: float = 0.0
    #: How long the position has stood still. Speed is measured *between
    #: moves*, not between frames, so this is the denominator waiting for its
    #: numerator.
    idle_seconds: float = 0.0

    @property
    def walking(self) -> bool:
        return self.speed_mps >= STILL_SPEED_MPS


def advance_motion(
    previous: AvatarMotion | None,
    position: tuple[float, float, float],
    dt_seconds: float,
) -> AvatarMotion:
    """Fold one new position into an avatar's motion state.

    A first sighting, a non-positive step, an implausibly long gap or an
    implausibly long jump all reset to standing at the new position rather
    than inventing a sprint out of a teleport.
    """
    if previous is None or dt_seconds <= 0.0 or dt_seconds > MAX_STEP_SECONDS:
        return AvatarMotion(position=position, speed_mps=0.0,
                            gait_phase=previous.gait_phase if previous else 0.0)

    dx = position[0] - previous.position[0]
    dy = position[1] - previous.position[1]
    # Horizontal only: a lift or a fall is not a stride.
    travelled = math.hypot(dx, dy)
    if travelled > TELEPORT_DISTANCE_M:
        return AvatarMotion(position=position, speed_mps=0.0, gait_phase=previous.gait_phase)

    if travelled < MOVEMENT_EPSILON_M:
        # Nothing moved. Hold the speed and let the clock run: an update is
        # simply not due yet. Only a gap long enough to mean *stopped* clears
        # it, and clearing it is what returns the legs to rest.
        #
        # This is also what absorbs a correction snap. Coming to a halt the
        # simulator moved the avatar 0.46 m in one 50 ms sample -- 9.3 m/s,
        # inside the teleport gate and so read as a sprint -- and the stillness
        # that follows zeroes it before the second is out.
        idle = previous.idle_seconds + dt_seconds
        stopped = idle >= STOP_AFTER_SECONDS
        return AvatarMotion(
            position=position,
            speed_mps=0.0 if stopped else previous.speed_mps,
            gait_phase=previous.gait_phase,
            # Capped: once we have concluded the avatar stopped, the wait is
            # over as far as the *next* step is concerned. Letting it run on
            # would divide the first stride of a new walk by however long the
            # avatar happened to stand there, and read a walk as a crawl.
            idle_seconds=min(idle, STOP_AFTER_SECONDS),
        )

    # Measure over the whole interval since the last move, not since the last
    # frame, so the answer does not depend on how often we happened to look.
    interval = previous.idle_seconds + dt_seconds
    measured = travelled / interval
    blend = min(1.0, SPEED_RESPONSE_PER_S * interval)
    speed = previous.speed_mps + (measured - previous.speed_mps) * blend

    phase = previous.gait_phase
    if speed >= STILL_SPEED_MPS:
        phase = (phase + 2.0 * math.pi * travelled / STRIDE_LENGTH_M) % (2.0 * math.pi)
    return AvatarMotion(position=position, speed_mps=speed, gait_phase=phase)


def pose_for_motion(motion: AvatarMotion) -> dict[str, float]:
    """Bone pitches, in radians, for an avatar in this state.

    An avatar under :data:`STILL_SPEED_MPS` gets the resting pose -- arms down
    with a slight bend at the elbow -- rather than a stride frozen mid-swing,
    which is what a fading amplitude would leave behind.
    """
    rest = {
        "forearm_l": ELBOW_REST_RADIANS,
        "forearm_r": ELBOW_REST_RADIANS,
    }
    if not motion.walking:
        return rest

    amount = min(1.0, motion.speed_mps / FULL_STRIDE_SPEED_MPS)
    swing = math.sin(motion.gait_phase)
    leg = LEG_SWING_RADIANS * amount * swing
    arm = LEG_SWING_RADIANS * ARM_SWING_RATIO * amount * swing

    # The trailing leg is the one swung back, and its knee is what bends. Both
    # halves of that were wrong when this shipped, and the two errors hid each
    # other: the bend fired on the leg swung *forward*, and it bent the knee
    # the wrong way, so the ankle ended up in front of the knee. A positive
    # bone pitch swings a bone's far end backward -- see ``_pitch_at`` -- so a
    # knee that bends the way a knee bends is a positive shin angle, on the leg
    # whose own angle is positive.
    bend_l = KNEE_BEND_RADIANS * amount * max(0.0, swing)
    bend_r = KNEE_BEND_RADIANS * amount * max(0.0, -swing)

    return {
        "leg_l": leg,
        "leg_r": -leg,
        "shin_l": bend_l,
        "shin_r": bend_r,
        # Arms swing opposite the leg on the same side.
        "arm_l": -arm,
        "arm_r": arm,
        "forearm_l": ELBOW_REST_RADIANS - 0.18 * amount,
        "forearm_r": ELBOW_REST_RADIANS - 0.18 * amount,
    }


def sit_pose() -> dict[str, float]:
    """The pose for an avatar sitting on something.

    Sitting is knowable without decoding a single animation: the simulator
    reparents a seated avatar onto its seat, so an avatar with a parent is an
    avatar sitting on something. Observed live -- see
    ``tools/verify_seated_avatar.py``, where sitting on a rezzed prim set the
    avatar's ``parent_id`` to that prim and changed its reported position to
    ``(-0.415, 0.0, 0.9)``, the seat's frame.

    What this cannot know is *how* they are sitting. A poseball or a scripted
    animation can put an avatar in any shape at all, and none of that is
    readable here. This is the ordinary case -- knees forward, shins down --
    and it is a great deal closer than standing to attention on a chair.
    """
    return {
        "leg_l": SIT_HIP_RADIANS,
        "leg_r": SIT_HIP_RADIANS,
        "shin_l": SIT_KNEE_RADIANS,
        "shin_r": SIT_KNEE_RADIANS,
        # Arms a little forward, off the lap, rather than clipping through the
        # thighs the resting pose would put them in.
        "arm_l": -0.22,
        "arm_r": -0.22,
        "forearm_l": ELBOW_REST_RADIANS - 0.30,
        "forearm_r": ELBOW_REST_RADIANS - 0.30,
    }


def advance_all(
    previous: dict[int, AvatarMotion],
    positions: dict[int, tuple[float, float, float]],
    dt_seconds: float,
) -> dict[int, AvatarMotion]:
    """Advance every avatar still present, dropping the ones that have gone.

    Rebuilt rather than mutated so an avatar that leaves and comes back does
    not resume a stride from wherever it was interrupted -- and so the dict
    cannot grow without bound over a long session.
    """
    return {
        local_id: advance_motion(previous.get(local_id), position, dt_seconds)
        for local_id, position in positions.items()
    }


def rest_pose() -> dict[str, float]:
    """The pose an avatar with no motion history is drawn in."""
    return pose_for_motion(AvatarMotion(position=(0.0, 0.0, 0.0)))


__all__ = [
    "MOVEMENT_EPSILON_M",
    "SIT_HIP_RADIANS",
    "SIT_KNEE_RADIANS",
    "STOP_AFTER_SECONDS",
    "AvatarMotion",
    "advance_all",
    "advance_motion",
    "pose_for_motion",
    "rest_pose",
    "sit_pose",
]
