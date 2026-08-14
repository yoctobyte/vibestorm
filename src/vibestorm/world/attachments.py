"""Decoding a prim's attachment point out of its ``state`` byte.

Every prim carries a ``state`` byte that the client kept raw. It means two
different things depending on what the prim is:

- for a tree or grass prim, it is the vegetation species;
- for an **attachment**, it is the attachment point — but **nibble-swapped**.

That swap is not a guess. ``LLClientView`` writes it twice, once in the full
update and once in the terse one::

    int st = 0xff & (int)part.ParentGroup.AttachmentPoint;
    state = (byte)((st >> 4) | (st << 4));

Reading the byte without undoing it yields a plausible small number for the
low-numbered points — attachment point 1 (chest) arrives as 0x10 = 16, which
is a perfectly valid point (right eye). So a missing swap does not look like
corruption; it looks like a different attachment point.

Point names are OpenSim's LSL constants (``LSL_Constants.cs``, ``ATTACH_*``).
"""

from __future__ import annotations

#: The NameValue OpenSim puts on an attachment's root prim. Its presence is how
#: a client tells an attachment from an ordinary prim whose state byte happens
#: to be non-zero.
ATTACH_ITEM_ID_KEY = "AttachItemID"

ATTACHMENT_POINT_NAMES: dict[int, str] = {
    1: "chest",
    2: "head",
    3: "left shoulder",
    4: "right shoulder",
    5: "left hand",
    6: "right hand",
    7: "left foot",
    8: "right foot",
    9: "back",
    10: "pelvis",
    11: "mouth",
    12: "chin",
    13: "left ear",
    14: "right ear",
    15: "left eye",
    16: "right eye",
    17: "nose",
    18: "right upper arm",
    19: "right lower arm",
    20: "left upper arm",
    21: "left lower arm",
    22: "right hip",
    23: "right upper leg",
    24: "right lower leg",
    25: "left hip",
    26: "left upper leg",
    27: "left lower leg",
    28: "belly",
    # 29 and 30 are ATTACH_RPEC/ATTACH_LPEC, and also ATTACH_LEFT_PEC/
    # ATTACH_RIGHT_PEC with the sides swapped — an upstream bug SL kept for
    # compatibility (SVC-580). Named by the newer, correct-sided constants.
    29: "left pec",
    30: "right pec",
    31: "hud center 2",
    32: "hud top right",
    33: "hud top center",
    34: "hud top left",
    35: "hud center 1",
    36: "hud bottom left",
    37: "hud bottom",
    38: "hud bottom right",
    39: "neck",
    40: "avatar center",
    41: "left ring finger",
    42: "right ring finger",
    43: "tail base",
    44: "tail tip",
    45: "left wing",
    46: "right wing",
    47: "jaw",
    48: "face left ear",
    49: "face right ear",
    50: "face left eye",
    51: "face right eye",
    52: "tongue",
    53: "groin",
    54: "hind left foot",
    55: "hind right foot",
}

#: Points 31-38 are HUD slots: attached to the viewer's screen, not the body,
#: and visible only to the wearer.
HUD_ATTACHMENT_POINTS = frozenset(range(31, 39))


def decode_attachment_point(state: int) -> int:
    """Undo the nibble swap and return the attachment point number.

    Only meaningful when the prim really is an attachment; see
    ``is_attachment``.
    """
    value = int(state) & 0xFF
    return ((value >> 4) | (value << 4)) & 0xFF


def attachment_point_name(point: int) -> str:
    """Name for an attachment point, keeping the number when unknown."""
    name = ATTACHMENT_POINT_NAMES.get(point)
    return name if name is not None else f"unknown point {point}"


def is_hud_attachment(point: int) -> bool:
    """True for the eight HUD slots, which are screen-space, not body-space."""
    return point in HUD_ATTACHMENT_POINTS


def is_attachment(name_values: dict[str, str] | None) -> bool:
    """Whether a prim is an attachment, from its NameValues.

    The state byte alone cannot answer this: an ordinary tree prim also has a
    non-zero state. OpenSim marks an attachment's root with ``AttachItemID``.
    """
    return bool(name_values) and ATTACH_ITEM_ID_KEY in name_values


def describe_attachment(state: int, name_values: dict[str, str] | None) -> str | None:
    """One line describing where a prim is attached, or None if it is not."""
    if not is_attachment(name_values):
        return None
    point = decode_attachment_point(state)
    label = attachment_point_name(point)
    if is_hud_attachment(point):
        return f"HUD: {label} ({point})"
    return f"{label} ({point})"


__all__ = [
    "ATTACHMENT_POINT_NAMES",
    "ATTACH_ITEM_ID_KEY",
    "HUD_ATTACHMENT_POINTS",
    "attachment_point_name",
    "decode_attachment_point",
    "describe_attachment",
    "is_attachment",
    "is_hud_attachment",
]
