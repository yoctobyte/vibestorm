"""Names for an inventory item's asset type.

An inventory listing of UUIDs and raw type numbers cannot answer the question
this project keeps asking: *does this account hold anything that would exercise
a decoder the region never triggers?* "8 items of type 13" does not; "8 body
parts, no sounds, no objects" does.

Values are OpenSim's ``INVENTORY_*`` LSL constants (``LSL_Constants.cs``).

**These name the item's ``type`` field only, never ``inv_type``.** The wire
carries both, and they are different enumerations that agree for the common
values and diverge exactly where it would be least noticeable — an animation is
asset type 20 but inventory type 19, a gesture 21 versus 20. ``llGetInventoryType``
returns ``item.Type``, which is what fixes these constants to the asset type;
libomv's ``InventoryType`` table is not in ``opensim-source/`` and is
deliberately left unnamed rather than guessed at.
"""

from __future__ import annotations

from collections import Counter

INVENTORY_TEXTURE = 0
INVENTORY_SOUND = 1
INVENTORY_LANDMARK = 3
INVENTORY_CLOTHING = 5
INVENTORY_OBJECT = 6
INVENTORY_NOTECARD = 7
INVENTORY_SCRIPT = 10
INVENTORY_BODYPART = 13
INVENTORY_ANIMATION = 20
INVENTORY_GESTURE = 21
INVENTORY_SETTING = 56
INVENTORY_MATERIAL = 57

ASSET_TYPE_NAMES: dict[int, str] = {
    INVENTORY_TEXTURE: "texture",
    INVENTORY_SOUND: "sound",
    INVENTORY_LANDMARK: "landmark",
    INVENTORY_CLOTHING: "clothing",
    INVENTORY_OBJECT: "object",
    INVENTORY_NOTECARD: "notecard",
    INVENTORY_SCRIPT: "script",
    INVENTORY_BODYPART: "body part",
    INVENTORY_ANIMATION: "animation",
    INVENTORY_GESTURE: "gesture",
    INVENTORY_SETTING: "setting",
    INVENTORY_MATERIAL: "material",
}

#: Asset types that could close a region-content gap if rezzed or worn. This is
#: the list the census keeps reporting as `absent=`, viewed from the account
#: side: an object can be rezzed, a sound attached to a prim, an animation
#: played, and any of those turns a unit-tested decoder into a verified one.
GAP_CLOSING_TYPES: tuple[int, ...] = (
    INVENTORY_OBJECT,
    INVENTORY_SOUND,
    INVENTORY_ANIMATION,
    INVENTORY_GESTURE,
    INVENTORY_TEXTURE,
)


def asset_type_name(asset_type: int | None) -> str:
    """Name for an item's ``type``, keeping the number when unknown."""
    if asset_type is None:
        return "untyped"
    name = ASSET_TYPE_NAMES.get(asset_type)
    return name if name is not None else f"unknown type {asset_type}"


def count_asset_types(items: object) -> Counter[str]:
    """Count items by named asset type."""
    counts: Counter[str] = Counter()
    for item in items or ():
        counts[asset_type_name(getattr(item, "type", None))] += 1
    return counts


def missing_gap_closing_types(items: object) -> tuple[str, ...]:
    """Gap-closing asset types this account does *not* hold.

    Reported rather than inferred from silence: "no sounds" is the answer to
    "can I verify the AttachedSound decoder from inventory", and a listing that
    simply omits sounds looks the same as one that was never checked.
    """
    present = {getattr(item, "type", None) for item in items or ()}
    return tuple(
        asset_type_name(asset_type)
        for asset_type in GAP_CLOSING_TYPES
        if asset_type not in present
    )


__all__ = [
    "ASSET_TYPE_NAMES",
    "GAP_CLOSING_TYPES",
    "INVENTORY_ANIMATION",
    "INVENTORY_BODYPART",
    "INVENTORY_CLOTHING",
    "INVENTORY_GESTURE",
    "INVENTORY_LANDMARK",
    "INVENTORY_MATERIAL",
    "INVENTORY_NOTECARD",
    "INVENTORY_OBJECT",
    "INVENTORY_SCRIPT",
    "INVENTORY_SETTING",
    "INVENTORY_SOUND",
    "INVENTORY_TEXTURE",
    "asset_type_name",
    "count_asset_types",
    "missing_gap_closing_types",
]
