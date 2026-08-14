"""Readable summaries for the permission masks in ``ObjectPropertiesFamily``.

Bit values mirror OpenSim's ``PermissionMask`` (``OpenSim/Framework/Util.cs``).
They are not contiguous — the useful bits start at 13 and skip 17 and 18 — so
a mask printed as raw hex is close to unreadable, which is what the Object
Inspector showed for every prim in a region.

The low nibble carries "folded" permissions, a separate encoding used when an
object's contents' permissions have to be squeezed into the same word. Those
are decoded separately rather than mixed in with the real ones.
"""

from __future__ import annotations

from dataclasses import dataclass

# Real permission bits.
PERM_TRANSFER = 1 << 13   # 0x00002000
PERM_MODIFY = 1 << 14     # 0x00004000
PERM_COPY = 1 << 15       # 0x00008000
PERM_EXPORT = 1 << 16     # 0x00010000
PERM_MOVE = 1 << 19       # 0x00080000
PERM_DAMAGE = 1 << 20     # 0x00100000, unused by OpenSim

# Folded permissions live in the low nibble and mean the same three rights
# shifted down by PERM_FOLDING_SHIFT.
FOLDED_TRANSFER = 1 << 0
FOLDED_MODIFY = 1 << 1
FOLDED_COPY = 1 << 2
FOLDED_EXPORT = 1 << 3
FOLDED_MASK = 0x0F
PERM_FOLDING_SHIFT = 13

# Deprecated; OpenSim's own comment says new code must never set it.
PERM_SLAM = 1 << 4

# ``All`` deliberately excludes Export, which has to be granted explicitly.
PERM_ALL = 0x8E000

_NAMED_BITS: tuple[tuple[int, str], ...] = (
    (PERM_COPY, "copy"),
    (PERM_MODIFY, "modify"),
    (PERM_TRANSFER, "transfer"),
    (PERM_MOVE, "move"),
    (PERM_EXPORT, "export"),
    (PERM_DAMAGE, "damage"),
)

_FOLDED_BITS: tuple[tuple[int, str], ...] = (
    (FOLDED_COPY, "copy"),
    (FOLDED_MODIFY, "modify"),
    (FOLDED_TRANSFER, "transfer"),
    (FOLDED_EXPORT, "export"),
)


@dataclass(slots=True, frozen=True)
class DecodedPermissions:
    """One permission mask, split into what it grants and what is left over."""

    mask: int
    granted: tuple[str, ...]
    folded: tuple[str, ...]
    slam: bool
    unknown_bits: int

    @property
    def is_full(self) -> bool:
        """True when every right in OpenSim's ``All`` is granted."""
        return self.mask & PERM_ALL == PERM_ALL

    def describe(self) -> str:
        """A one-line summary suitable for an inspector row."""
        parts = [", ".join(self.granted) if self.granted else "none"]
        if self.folded:
            parts.append(f"folded: {', '.join(self.folded)}")
        if self.slam:
            parts.append("slam")
        if self.unknown_bits:
            parts.append(f"unknown {self.unknown_bits:#010x}")
        return " | ".join(parts)


def decode_permissions(mask: int) -> DecodedPermissions:
    """Split a permission mask into named rights.

    ``unknown_bits`` keeps whatever is left after the known flags so an
    unrecognised bit is reported rather than silently dropped — the raw hex
    was at least honest about carrying something.
    """
    mask = int(mask) & 0xFFFFFFFF
    granted = tuple(name for bit, name in _NAMED_BITS if mask & bit)
    folded = tuple(name for bit, name in _FOLDED_BITS if mask & bit)
    known = PERM_SLAM | FOLDED_MASK
    for bit, _name in _NAMED_BITS:
        known |= bit
    return DecodedPermissions(
        mask=mask,
        granted=granted,
        folded=folded,
        slam=bool(mask & PERM_SLAM),
        unknown_bits=mask & ~known,
    )


__all__ = [
    "FOLDED_COPY",
    "FOLDED_EXPORT",
    "FOLDED_MASK",
    "FOLDED_MODIFY",
    "FOLDED_TRANSFER",
    "PERM_ALL",
    "PERM_COPY",
    "PERM_DAMAGE",
    "PERM_EXPORT",
    "PERM_FOLDING_SHIFT",
    "PERM_MODIFY",
    "PERM_MOVE",
    "PERM_SLAM",
    "PERM_TRANSFER",
    "DecodedPermissions",
    "decode_permissions",
]
