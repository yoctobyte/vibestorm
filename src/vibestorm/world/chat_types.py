"""Names for the chat type byte in ``ChatFromSimulator``.

Chat arrived as ``type=1 audible=0`` and stayed that way all the way to the
CLI, which is unreadable: whether a line was whispered, shouted, said by the
region itself, or is in fact not chat at all but a typing indicator, is the
first thing a reader wants to know.

Values are OpenSim's ``ChatTypeEnum`` (``OpenSim/Framework/ChatTypeEnum.cs``).

The neighbouring ``sourcetype`` and ``audible`` bytes are deliberately *not*
named here. OpenSim writes them from ``ChatSourceType`` and
``ChatAudibleLevel``, which are libomv enums: the names appear throughout
OpenSim's call sites but the numeric values are defined in a library that is
not in ``opensim-source/``. Guessing them would produce exactly the kind of
plausible-but-wrong labelling that the SimStats work ran into, so those two
bytes stay raw until a real source turns up.
"""

from __future__ import annotations

CHAT_TYPE_WHISPER = 0
CHAT_TYPE_SAY = 1
CHAT_TYPE_SHOUT = 2
CHAT_TYPE_START_TYPING = 4
CHAT_TYPE_STOP_TYPING = 5
CHAT_TYPE_DEBUG_CHANNEL = 6
CHAT_TYPE_REGION = 7
CHAT_TYPE_OWNER = 8
CHAT_TYPE_DIRECT = 9
CHAT_TYPE_BROADCAST = 0xFF

CHAT_TYPE_NAMES: dict[int, str] = {
    CHAT_TYPE_WHISPER: "whisper",
    CHAT_TYPE_SAY: "say",
    CHAT_TYPE_SHOUT: "shout",
    # 3 is an obsolete second encoding of Say and is never sent by current
    # OpenSim; it is left unnamed so that seeing it would be reported rather
    # than quietly folded into "say".
    CHAT_TYPE_START_TYPING: "start typing",
    CHAT_TYPE_STOP_TYPING: "stop typing",
    CHAT_TYPE_DEBUG_CHANNEL: "debug channel",
    CHAT_TYPE_REGION: "region",
    CHAT_TYPE_OWNER: "owner",
    CHAT_TYPE_DIRECT: "direct",
    CHAT_TYPE_BROADCAST: "broadcast",
}

#: Types that carry no message: they signal that someone started or stopped
#: typing. A chat log that renders these as empty lines looks broken.
TYPING_CHAT_TYPES: frozenset[int] = frozenset(
    {CHAT_TYPE_START_TYPING, CHAT_TYPE_STOP_TYPING}
)


def chat_type_name(chat_type: int) -> str:
    """Name for a chat type, keeping the number when it is not known."""
    name = CHAT_TYPE_NAMES.get(chat_type)
    return name if name is not None else f"unknown type {chat_type}"


def is_typing_notification(chat_type: int) -> bool:
    """True for the start/stop typing signals, which carry no message."""
    return chat_type in TYPING_CHAT_TYPES


__all__ = [
    "CHAT_TYPE_BROADCAST",
    "CHAT_TYPE_DEBUG_CHANNEL",
    "CHAT_TYPE_DIRECT",
    "CHAT_TYPE_NAMES",
    "CHAT_TYPE_OWNER",
    "CHAT_TYPE_REGION",
    "CHAT_TYPE_SAY",
    "CHAT_TYPE_SHOUT",
    "CHAT_TYPE_START_TYPING",
    "CHAT_TYPE_STOP_TYPING",
    "CHAT_TYPE_WHISPER",
    "TYPING_CHAT_TYPES",
    "chat_type_name",
    "is_typing_notification",
]
