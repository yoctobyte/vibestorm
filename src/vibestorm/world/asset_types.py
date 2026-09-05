"""SL/OpenSim asset type names and their numeric equivalents.

Task inventory names asset types as strings (``lsltext``, ``notecard``) while
the UDP messages and the asset capabilities use the integers. Anything that
reads an object's contents and then asks for one of its assets has to cross
that boundary, so the mapping lives here rather than inside the viewer HUD,
where it started and where a headless caller could not reach it without
importing pygame.
"""

from __future__ import annotations

#: Asset type name -> numeric type, for the types this project handles.
ASSET_TYPE_BY_NAME: dict[str, int] = {
    "texture": 0,
    "sound": 1,
    "calling_card": 2,
    "landmark": 3,
    "script": 4,      # legacy
    "clothing": 5,
    "object": 6,
    "notecard": 7,
    "category": 8,
    "root_category": 9,
    "lsltext": 10,    # LSL script (current)
    "lslbytecode": 11,
    "texture_tga": 12,
    "bodypart": 13,
    "trash": 14,
    "snapshot_category": 15,
    "lost_and_found": 16,
    "sound_wav": 17,
    "image_tga": 18,
    "image_jpeg": 19,
    "animation": 20,
    "gesture": 21,
    "simstate": 22,
}

#: Numeric type -> the name this project prefers for it. Built from the table
#: above, first name wins, so the legacy "script" does not shadow "lsltext".
ASSET_NAME_BY_TYPE: dict[int, str] = {}
for _name, _value in ASSET_TYPE_BY_NAME.items():
    ASSET_NAME_BY_TYPE.setdefault(_value, _name)
ASSET_NAME_BY_TYPE[10] = "lsltext"
del _name, _value


def asset_type_to_int(asset_type: str) -> int | None:
    """Numeric asset type for a task-inventory type string.

    Accepts a already-numeric string too, because task inventory has been seen
    carrying both. Returns None when it is neither.
    """
    text = asset_type.strip().lower()
    if text in ASSET_TYPE_BY_NAME:
        return ASSET_TYPE_BY_NAME[text]
    try:
        return int(text)
    except (ValueError, TypeError):
        return None


__all__ = ["ASSET_NAME_BY_TYPE", "ASSET_TYPE_BY_NAME", "asset_type_to_int"]
