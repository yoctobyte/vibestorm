"""AgentControlFlags bit positions for the AgentUpdate packet.

Values are the canonical libomv / OpenMetaverse positions also used by the SL
viewer and OpenSim. Stable since libsecondlife days; do not renumber.
"""

from __future__ import annotations

from enum import IntFlag


class AgentControlFlags(IntFlag):
    """Bits packed into AgentUpdate.ControlFlags (U32)."""

    NONE = 0x00000000

    AT_POS = 0x00000001
    AT_NEG = 0x00000002
    LEFT_POS = 0x00000004
    LEFT_NEG = 0x00000008
    UP_POS = 0x00000010
    UP_NEG = 0x00000020
    PITCH_POS = 0x00000040
    PITCH_NEG = 0x00000080
    YAW_POS = 0x00000100
    YAW_NEG = 0x00000200

    FAST_AT = 0x00000400
    FAST_LEFT = 0x00000800
    FAST_UP = 0x00001000

    FLY = 0x00002000
    STOP = 0x00004000
    FINISH_ANIM = 0x00008000
    STAND_UP = 0x00010000
    SIT_ON_GROUND = 0x00020000
    MOUSELOOK = 0x00040000

    NUDGE_AT_POS = 0x00080000
    NUDGE_AT_NEG = 0x00100000
    NUDGE_LEFT_POS = 0x00200000
    NUDGE_LEFT_NEG = 0x00400000
    NUDGE_UP_POS = 0x00800000
    NUDGE_UP_NEG = 0x01000000

    TURN_LEFT = 0x02000000
    TURN_RIGHT = 0x04000000

    AWAY = 0x08000000

    LBUTTON_DOWN = 0x10000000
    LBUTTON_UP = 0x20000000
    ML_LBUTTON_DOWN = 0x40000000
    ML_LBUTTON_UP = 0x80000000


# Helpful aggregates --------------------------------------------------------

# Bits the sim treats as "the agent is actively trying to move." Useful for
# resetting per-tick state (you typically clear these between AgentUpdates
# unless the key is still held).
DIRECTION_BITS: int = (
    AgentControlFlags.AT_POS
    | AgentControlFlags.AT_NEG
    | AgentControlFlags.LEFT_POS
    | AgentControlFlags.LEFT_NEG
    | AgentControlFlags.UP_POS
    | AgentControlFlags.UP_NEG
    | AgentControlFlags.NUDGE_AT_POS
    | AgentControlFlags.NUDGE_AT_NEG
    | AgentControlFlags.NUDGE_LEFT_POS
    | AgentControlFlags.NUDGE_LEFT_NEG
    | AgentControlFlags.NUDGE_UP_POS
    | AgentControlFlags.NUDGE_UP_NEG
    | AgentControlFlags.TURN_LEFT
    | AgentControlFlags.TURN_RIGHT
)


__all__ = ["AgentControlFlags", "DIRECTION_BITS"]
