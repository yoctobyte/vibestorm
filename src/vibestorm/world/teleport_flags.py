"""Names for the ``TeleportFlags`` word carried by every teleport message.

Unlike the parcel and region bitfields, this one is fully sourceable:
``OpenSim/Framework/Constants.cs`` defines ``TeleportFlags`` outright, so there
are no unnamed bits to apologise for and ``unknown_bits`` should stay zero
against a real sim.

The word says *how* the teleport was requested — ``ViaLocation`` for a map
click, ``ViaLandmark``, ``ViaLure``.

The enum also defines ``FinishedViaSameSim``, ``FinishedViaNewSim`` and
``FinishedViaLure``, which look like they would tell a local teleport from a
region crossing. **Against OpenSim they never arrive.** ``EntityTransferModule``
passes the request's own flag word straight through to ``SendTeleportStart``
and ``SendLocalTeleport`` without adding anything, and no file in the tree sets
any ``FinishedVia*`` bit — confirmed live on 2026-08-14, where a completed
local teleport reported ``via location`` and nothing else. They are named here
because they are real flags another grid may send, not because this client can
use them. What actually distinguishes the two cases is the message: a
``TeleportLocal`` is the whole of a same-region teleport, and a crossing
arrives as ``TeleportFinish`` over the event queue.

One entry in the C# enum is not a flag. ``notViaHGLogin = 0xbffffff`` is a
mask, and it is deliberately absent from the table below: folding it in would
match nearly every word and name almost every teleport after it.
"""

from __future__ import annotations

from vibestorm.world.land_flags import DecodedFlags

TELEPORT_FLAG_DEFAULT = 0
TELEPORT_FLAG_SET_HOME_TO_TARGET = 1 << 0
TELEPORT_FLAG_SET_LAST_TO_TARGET = 1 << 1
TELEPORT_FLAG_VIA_LURE = 1 << 2
TELEPORT_FLAG_VIA_LANDMARK = 1 << 3
TELEPORT_FLAG_VIA_LOCATION = 1 << 4
TELEPORT_FLAG_VIA_HOME = 1 << 5
TELEPORT_FLAG_VIA_TELEHUB = 1 << 6
TELEPORT_FLAG_VIA_LOGIN = 1 << 7
TELEPORT_FLAG_VIA_GODLIKE_LURE = 1 << 8
TELEPORT_FLAG_GODLIKE = 1 << 9
TELEPORT_FLAG_NINE_ONE_ONE = 1 << 10
TELEPORT_FLAG_DISABLE_CANCEL = 1 << 11
TELEPORT_FLAG_VIA_REGION_ID = 1 << 12
TELEPORT_FLAG_IS_FLYING = 1 << 13
TELEPORT_FLAG_RESET_HOME = 1 << 14
TELEPORT_FLAG_FORCE_REDIRECT = 1 << 15
TELEPORT_FLAG_FINISHED_VIA_LURE = 1 << 26
TELEPORT_FLAG_FINISHED_VIA_NEW_SIM = 1 << 28
TELEPORT_FLAG_FINISHED_VIA_SAME_SIM = 1 << 29
TELEPORT_FLAG_VIA_HG_LOGIN = 1 << 30

#: The mask in the C# enum that is not a flag. Exported so a reader who greps
#: for it finds the reason it is missing from the table rather than a bug.
TELEPORT_NOT_VIA_HG_LOGIN_MASK = 0xBFFFFFF

_TELEPORT_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (TELEPORT_FLAG_SET_HOME_TO_TARGET, "set home to target"),
    (TELEPORT_FLAG_SET_LAST_TO_TARGET, "set last to target"),
    (TELEPORT_FLAG_VIA_LURE, "via lure"),
    (TELEPORT_FLAG_VIA_LANDMARK, "via landmark"),
    (TELEPORT_FLAG_VIA_LOCATION, "via location"),
    (TELEPORT_FLAG_VIA_HOME, "via home"),
    (TELEPORT_FLAG_VIA_TELEHUB, "via telehub"),
    (TELEPORT_FLAG_VIA_LOGIN, "via login"),
    (TELEPORT_FLAG_VIA_GODLIKE_LURE, "via godlike lure"),
    (TELEPORT_FLAG_GODLIKE, "godlike"),
    (TELEPORT_FLAG_NINE_ONE_ONE, "nine one one"),
    (TELEPORT_FLAG_DISABLE_CANCEL, "cancel disabled"),
    (TELEPORT_FLAG_VIA_REGION_ID, "via region id"),
    (TELEPORT_FLAG_IS_FLYING, "flying"),
    (TELEPORT_FLAG_RESET_HOME, "reset home"),
    (TELEPORT_FLAG_FORCE_REDIRECT, "force redirect"),
    (TELEPORT_FLAG_FINISHED_VIA_LURE, "finished via lure"),
    (TELEPORT_FLAG_FINISHED_VIA_NEW_SIM, "finished, sim changed"),
    (TELEPORT_FLAG_FINISHED_VIA_SAME_SIM, "finished, same sim"),
    (TELEPORT_FLAG_VIA_HG_LOGIN, "via hypergrid login"),
)


def decode_teleport_flags(value: int) -> DecodedFlags:
    """Split a ``TeleportFlags`` word into named and unknown bits."""
    named: list[str] = []
    remaining = int(value)
    for bit, name in _TELEPORT_FLAG_NAMES:
        if remaining & bit:
            named.append(name)
            remaining &= ~bit
    return DecodedFlags(raw=int(value), set_flags=tuple(named), unknown_bits=remaining)


__all__ = [
    "TELEPORT_FLAG_DEFAULT",
    "TELEPORT_FLAG_DISABLE_CANCEL",
    "TELEPORT_FLAG_FINISHED_VIA_LURE",
    "TELEPORT_FLAG_FINISHED_VIA_NEW_SIM",
    "TELEPORT_FLAG_FINISHED_VIA_SAME_SIM",
    "TELEPORT_FLAG_FORCE_REDIRECT",
    "TELEPORT_FLAG_GODLIKE",
    "TELEPORT_FLAG_IS_FLYING",
    "TELEPORT_FLAG_NINE_ONE_ONE",
    "TELEPORT_FLAG_RESET_HOME",
    "TELEPORT_FLAG_SET_HOME_TO_TARGET",
    "TELEPORT_FLAG_SET_LAST_TO_TARGET",
    "TELEPORT_FLAG_VIA_GODLIKE_LURE",
    "TELEPORT_FLAG_VIA_HG_LOGIN",
    "TELEPORT_FLAG_VIA_HOME",
    "TELEPORT_FLAG_VIA_LANDMARK",
    "TELEPORT_FLAG_VIA_LOCATION",
    "TELEPORT_FLAG_VIA_LOGIN",
    "TELEPORT_FLAG_VIA_LURE",
    "TELEPORT_FLAG_VIA_REGION_ID",
    "TELEPORT_FLAG_VIA_TELEHUB",
    "TELEPORT_NOT_VIA_HG_LOGIN_MASK",
    "decode_teleport_flags",
]
