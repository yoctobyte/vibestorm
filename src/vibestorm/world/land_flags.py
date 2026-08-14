"""Names for the parcel and region flag bitfields.

``ParcelProperties`` carries a ``parcel_flags`` word and ``RegionHandshake`` a
``region_flags`` word. Both reached consumers as raw integers.

An earlier note in the handoff recorded region flags as *unsourceable*, because
the viewer-facing bitfield is libomv's ``OpenMetaverse.RegionFlags`` and the
`RegionFlags.cs` in `OpenSim/Framework/` is a different enum entirely (the grid
service's record flags). That note was too strong: OpenSim's LSL constants
(``LSL_Constants.cs``) define ``REGION_FLAG_*`` and ``PARCEL_FLAG_*`` with real
numeric values, because ``llGetRegionFlags`` and ``llGetParcelFlags`` return
exactly these words.

**The sourced sets are deliberately partial.** LSL exposes the flags a script
can ask about, not every bit the protocol defines — nine region bits and
sixteen parcel bits. Everything else is reported through ``unknown_bits``
rather than being guessed at, which is the same contract
``world/permissions.py`` uses and the reason a partial table is still worth
having: it names what is sourced and stays loud about what is not.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---- parcel flags (LSL_Constants.cs, PARCEL_FLAG_*) -----------------------

PARCEL_FLAG_ALLOW_FLY = 0x1
PARCEL_FLAG_ALLOW_SCRIPTS = 0x2
PARCEL_FLAG_ALLOW_LANDMARK = 0x8
PARCEL_FLAG_ALLOW_TERRAFORM = 0x10
PARCEL_FLAG_ALLOW_DAMAGE = 0x20
PARCEL_FLAG_ALLOW_CREATE_OBJECTS = 0x40
PARCEL_FLAG_USE_ACCESS_GROUP = 0x100
PARCEL_FLAG_USE_ACCESS_LIST = 0x200
PARCEL_FLAG_USE_BAN_LIST = 0x400
PARCEL_FLAG_USE_LAND_PASS_LIST = 0x800
PARCEL_FLAG_LOCAL_SOUND_ONLY = 0x8000
PARCEL_FLAG_RESTRICT_PUSHOBJECT = 0x200000
PARCEL_FLAG_ALLOW_GROUP_SCRIPTS = 0x2000000
PARCEL_FLAG_ALLOW_CREATE_GROUP_OBJECTS = 0x4000000
PARCEL_FLAG_ALLOW_ALL_OBJECT_ENTRY = 0x8000000
PARCEL_FLAG_ALLOW_GROUP_OBJECT_ENTRY = 0x10000000

_PARCEL_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (PARCEL_FLAG_ALLOW_FLY, "allow fly"),
    (PARCEL_FLAG_ALLOW_SCRIPTS, "allow scripts"),
    (PARCEL_FLAG_ALLOW_LANDMARK, "allow landmark"),
    (PARCEL_FLAG_ALLOW_TERRAFORM, "allow terraform"),
    (PARCEL_FLAG_ALLOW_DAMAGE, "allow damage"),
    (PARCEL_FLAG_ALLOW_CREATE_OBJECTS, "allow create objects"),
    (PARCEL_FLAG_USE_ACCESS_GROUP, "group access only"),
    (PARCEL_FLAG_USE_ACCESS_LIST, "access list"),
    (PARCEL_FLAG_USE_BAN_LIST, "ban list"),
    (PARCEL_FLAG_USE_LAND_PASS_LIST, "land passes"),
    (PARCEL_FLAG_LOCAL_SOUND_ONLY, "local sound only"),
    (PARCEL_FLAG_RESTRICT_PUSHOBJECT, "restrict push"),
    (PARCEL_FLAG_ALLOW_GROUP_SCRIPTS, "allow group scripts"),
    (PARCEL_FLAG_ALLOW_CREATE_GROUP_OBJECTS, "allow group object create"),
    (PARCEL_FLAG_ALLOW_ALL_OBJECT_ENTRY, "allow all object entry"),
    (PARCEL_FLAG_ALLOW_GROUP_OBJECT_ENTRY, "allow group object entry"),
)

# ---- region flags (LSL_Constants.cs, REGION_FLAG_*) -----------------------

REGION_FLAG_ALLOW_DAMAGE = 0x1
REGION_FLAG_FIXED_SUN = 0x10
REGION_FLAG_BLOCK_TERRAFORM = 0x40
REGION_FLAG_SANDBOX = 0x100
REGION_FLAG_DISABLE_COLLISIONS = 0x1000
REGION_FLAG_DISABLE_PHYSICS = 0x4000
REGION_FLAG_BLOCK_FLY = 0x80000
REGION_FLAG_ALLOW_DIRECT_TELEPORT = 0x100000
REGION_FLAG_RESTRICT_PUSHOBJECT = 0x400000

_REGION_FLAG_NAMES: tuple[tuple[int, str], ...] = (
    (REGION_FLAG_ALLOW_DAMAGE, "allow damage"),
    (REGION_FLAG_FIXED_SUN, "fixed sun"),
    (REGION_FLAG_BLOCK_TERRAFORM, "block terraform"),
    (REGION_FLAG_SANDBOX, "sandbox"),
    (REGION_FLAG_DISABLE_COLLISIONS, "collisions disabled"),
    (REGION_FLAG_DISABLE_PHYSICS, "physics disabled"),
    (REGION_FLAG_BLOCK_FLY, "block fly"),
    (REGION_FLAG_ALLOW_DIRECT_TELEPORT, "allow direct teleport"),
    (REGION_FLAG_RESTRICT_PUSHOBJECT, "restrict push"),
)


@dataclass(slots=True, frozen=True)
class DecodedFlags:
    """A bitfield split into the bits that are named and the bits that are not."""

    raw: int
    set_flags: tuple[str, ...]
    #: Bits that were set but have no sourced name. Never dropped: an unnamed
    #: bit is the sim telling us something this client cannot yet read.
    unknown_bits: int

    @property
    def has_unknown(self) -> bool:
        return self.unknown_bits != 0

    def describe(self) -> str:
        parts = list(self.set_flags)
        if self.unknown_bits:
            parts.append(f"unknown {self.unknown_bits:#x}")
        return ", ".join(parts) if parts else "none"


def _decode(value: int, table: tuple[tuple[int, str], ...]) -> DecodedFlags:
    named: list[str] = []
    remaining = int(value)
    for bit, name in table:
        if remaining & bit:
            named.append(name)
            remaining &= ~bit
    return DecodedFlags(raw=int(value), set_flags=tuple(named), unknown_bits=remaining)


def decode_parcel_flags(value: int) -> DecodedFlags:
    """Split a ``ParcelProperties`` flag word into named and unknown bits."""
    return _decode(value, _PARCEL_FLAG_NAMES)


def decode_region_flags(value: int) -> DecodedFlags:
    """Split a ``RegionHandshake`` flag word into named and unknown bits.

    Expect ``unknown_bits`` to be non-zero against a real sim: OpenSim sets a
    good many bits that LSL never exposes. That is reported, not hidden.
    """
    return _decode(value, _REGION_FLAG_NAMES)


__all__ = [
    "PARCEL_FLAG_ALLOW_ALL_OBJECT_ENTRY",
    "PARCEL_FLAG_ALLOW_CREATE_GROUP_OBJECTS",
    "PARCEL_FLAG_ALLOW_CREATE_OBJECTS",
    "PARCEL_FLAG_ALLOW_DAMAGE",
    "PARCEL_FLAG_ALLOW_FLY",
    "PARCEL_FLAG_ALLOW_GROUP_OBJECT_ENTRY",
    "PARCEL_FLAG_ALLOW_GROUP_SCRIPTS",
    "PARCEL_FLAG_ALLOW_LANDMARK",
    "PARCEL_FLAG_ALLOW_SCRIPTS",
    "PARCEL_FLAG_ALLOW_TERRAFORM",
    "PARCEL_FLAG_LOCAL_SOUND_ONLY",
    "PARCEL_FLAG_RESTRICT_PUSHOBJECT",
    "PARCEL_FLAG_USE_ACCESS_GROUP",
    "PARCEL_FLAG_USE_ACCESS_LIST",
    "PARCEL_FLAG_USE_BAN_LIST",
    "PARCEL_FLAG_USE_LAND_PASS_LIST",
    "REGION_FLAG_ALLOW_DAMAGE",
    "REGION_FLAG_ALLOW_DIRECT_TELEPORT",
    "REGION_FLAG_BLOCK_FLY",
    "REGION_FLAG_BLOCK_TERRAFORM",
    "REGION_FLAG_DISABLE_COLLISIONS",
    "REGION_FLAG_DISABLE_PHYSICS",
    "REGION_FLAG_FIXED_SUN",
    "REGION_FLAG_RESTRICT_PUSHOBJECT",
    "REGION_FLAG_SANDBOX",
    "DecodedFlags",
    "decode_parcel_flags",
    "decode_region_flags",
]
