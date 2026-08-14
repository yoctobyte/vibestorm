"""Names for the sound flags byte on prims and AttachedSound.

The inspector showed this as ``flags 0x00``. The byte says whether a sound
loops, whether it is one of a synchronised group, and whether the message is
in fact a *stop* rather than a play — which is the difference between "this
prim plays nothing" and "this prim was just told to go quiet".

Values are OpenSim's own ``SoundFlags`` enum, defined in
``OpenSim/Region/CoreModules/World/Sound/SoundModule.cs``.

**Not** the ``SOUND_*`` constants in ``LSL_Constants.cs``. Those look like an
obvious source and are not one: they are ``llLinkPlaySound`` *parameters*
(PLAY 0, LOOP 1, TRIGGER 2, SYNC 4), a different vocabulary that happens to
share the word "sound" and one coincidental value. Using them would have made
SYNC_SLAVE read as "sync" and TRIGGER read as SYNC_MASTER.
"""

from __future__ import annotations

from dataclasses import dataclass

SOUND_FLAG_NONE = 0
SOUND_FLAG_LOOP = 1 << 0
SOUND_FLAG_SYNC_MASTER = 1 << 1
SOUND_FLAG_SYNC_SLAVE = 1 << 2
SOUND_FLAG_SYNC_PENDING = 1 << 3
SOUND_FLAG_QUEUE = 1 << 4
SOUND_FLAG_STOP = 1 << 5

#: OpenSim's own composite: the three bits that mark a sound as part of a
#: synchronised set.
SOUND_FLAG_SYNC_MASK = (
    SOUND_FLAG_SYNC_MASTER | SOUND_FLAG_SYNC_SLAVE | SOUND_FLAG_SYNC_PENDING
)

_SOUND_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (SOUND_FLAG_LOOP, "loop"),
    (SOUND_FLAG_SYNC_MASTER, "sync master"),
    (SOUND_FLAG_SYNC_SLAVE, "sync slave"),
    (SOUND_FLAG_SYNC_PENDING, "sync pending"),
    (SOUND_FLAG_QUEUE, "queue"),
    (SOUND_FLAG_STOP, "stop"),
)


@dataclass(slots=True, frozen=True)
class DecodedSoundFlags:
    raw: int
    set_flags: tuple[str, ...]
    unknown_bits: int

    @property
    def is_looping(self) -> bool:
        return bool(self.raw & SOUND_FLAG_LOOP)

    @property
    def is_stop(self) -> bool:
        """True when this message silences the sound rather than starting it."""
        return bool(self.raw & SOUND_FLAG_STOP)

    @property
    def is_synchronised(self) -> bool:
        return bool(self.raw & SOUND_FLAG_SYNC_MASK)

    def describe(self) -> str:
        parts = list(self.set_flags)
        if self.unknown_bits:
            parts.append(f"unknown {self.unknown_bits:#x}")
        return ", ".join(parts) if parts else "none"


def decode_sound_flags(value: int) -> DecodedSoundFlags:
    """Split a sound flags byte into named and unnamed bits."""
    remaining = int(value) & 0xFF
    named: list[str] = []
    for bit, name in _SOUND_FLAG_NAMES:
        if remaining & bit:
            named.append(name)
            remaining &= ~bit
    return DecodedSoundFlags(
        raw=int(value) & 0xFF, set_flags=tuple(named), unknown_bits=remaining
    )


__all__ = [
    "SOUND_FLAG_LOOP",
    "SOUND_FLAG_NONE",
    "SOUND_FLAG_QUEUE",
    "SOUND_FLAG_STOP",
    "SOUND_FLAG_SYNC_MASK",
    "SOUND_FLAG_SYNC_MASTER",
    "SOUND_FLAG_SYNC_PENDING",
    "SOUND_FLAG_SYNC_SLAVE",
    "DecodedSoundFlags",
    "decode_sound_flags",
]
