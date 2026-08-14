"""Names for the material and click-action bytes carried by every prim.

Both arrive in every ``ObjectUpdate``, on every object, and both reached the
inspector as bare integers — "Material: 3", "Click Action: 0". They are small
enumerations with real meaning: the material picks collision sound and default
physics, and the click action decides what a left-click does, which is the
difference between a chair, a shop and a door.

Values are OpenSim's LSL constants (``LSL_Constants.cs``, ``PRIM_MATERIAL_*``
and ``CLICK_ACTION_*``) — the same source as the texture-animation modes.
"""

from __future__ import annotations

PRIM_MATERIAL_STONE = 0
PRIM_MATERIAL_METAL = 1
PRIM_MATERIAL_GLASS = 2
PRIM_MATERIAL_WOOD = 3
PRIM_MATERIAL_FLESH = 4
PRIM_MATERIAL_PLASTIC = 5
PRIM_MATERIAL_RUBBER = 6
PRIM_MATERIAL_LIGHT = 7

PRIM_MATERIAL_NAMES: dict[int, str] = {
    PRIM_MATERIAL_STONE: "stone",
    PRIM_MATERIAL_METAL: "metal",
    PRIM_MATERIAL_GLASS: "glass",
    PRIM_MATERIAL_WOOD: "wood",
    PRIM_MATERIAL_FLESH: "flesh",
    PRIM_MATERIAL_PLASTIC: "plastic",
    PRIM_MATERIAL_RUBBER: "rubber",
    PRIM_MATERIAL_LIGHT: "light",
}

#: OpenSim's default for a new prim (``SceneObjectPart.m_material``).
DEFAULT_PRIM_MATERIAL = PRIM_MATERIAL_WOOD

# CLICK_ACTION_NONE and CLICK_ACTION_TOUCH are both 0 in LSL_Constants: "no
# action set" and "touch" are the same wire value, because touch *is* what an
# unconfigured prim does. Named "touch" for that reason.
CLICK_ACTION_TOUCH = 0
CLICK_ACTION_SIT = 1
CLICK_ACTION_BUY = 2
CLICK_ACTION_PAY = 3
CLICK_ACTION_OPEN = 4
CLICK_ACTION_PLAY = 5
CLICK_ACTION_OPEN_MEDIA = 6
CLICK_ACTION_ZOOM = 7
CLICK_ACTION_DISABLED = 8
CLICK_ACTION_IGNORE = 9

CLICK_ACTION_NAMES: dict[int, str] = {
    CLICK_ACTION_TOUCH: "touch",
    CLICK_ACTION_SIT: "sit",
    CLICK_ACTION_BUY: "buy",
    CLICK_ACTION_PAY: "pay",
    CLICK_ACTION_OPEN: "open",
    CLICK_ACTION_PLAY: "play",
    CLICK_ACTION_OPEN_MEDIA: "open media",
    CLICK_ACTION_ZOOM: "zoom",
    CLICK_ACTION_DISABLED: "disabled",
    CLICK_ACTION_IGNORE: "ignore",
}


def prim_material_name(material: int) -> str:
    """Name for a prim material, keeping the number when unknown."""
    name = PRIM_MATERIAL_NAMES.get(material)
    return name if name is not None else f"unknown material {material}"


def click_action_name(click_action: int) -> str:
    """Name for a click action, keeping the number when unknown."""
    name = CLICK_ACTION_NAMES.get(click_action)
    return name if name is not None else f"unknown action {click_action}"


__all__ = [
    "CLICK_ACTION_BUY",
    "CLICK_ACTION_DISABLED",
    "CLICK_ACTION_IGNORE",
    "CLICK_ACTION_NAMES",
    "CLICK_ACTION_OPEN",
    "CLICK_ACTION_OPEN_MEDIA",
    "CLICK_ACTION_PAY",
    "CLICK_ACTION_PLAY",
    "CLICK_ACTION_SIT",
    "CLICK_ACTION_TOUCH",
    "CLICK_ACTION_ZOOM",
    "DEFAULT_PRIM_MATERIAL",
    "PRIM_MATERIAL_FLESH",
    "PRIM_MATERIAL_GLASS",
    "PRIM_MATERIAL_LIGHT",
    "PRIM_MATERIAL_METAL",
    "PRIM_MATERIAL_NAMES",
    "PRIM_MATERIAL_PLASTIC",
    "PRIM_MATERIAL_RUBBER",
    "PRIM_MATERIAL_STONE",
    "PRIM_MATERIAL_WOOD",
    "click_action_name",
    "prim_material_name",
]
