"""Names for a prim's physics shape type and its material properties.

``ObjectPhysicsProperties`` arrives over the event queue with a prim's density,
friction, restitution, gravity multiplier and physics shape type. The client
decoded the whole message and then had no consumer for it, so none of it was
ever seen.

The shape type is the interesting field and the only one that needs a table:
it decides whether a prim is solid, walk-through, or collides as its convex
hull, which is the difference between a wall, a doorway and a ramp. Values are
OpenSim's ``PhysShapeType`` (``OpenSim/Framework/ExtraPhysicsData.cs``).

The other four are plain floats and need no decoding, only somewhere to go.
"""

from __future__ import annotations

from dataclasses import dataclass

PHYS_SHAPE_PRIM = 0
PHYS_SHAPE_NONE = 1
PHYS_SHAPE_CONVEX = 2
#: OpenSim's own marker for "no usable data", not a shape a prim can have.
PHYS_SHAPE_INVALID = 255

PHYS_SHAPE_NAMES: dict[int, str] = {
    PHYS_SHAPE_PRIM: "prim",
    PHYS_SHAPE_NONE: "none",
    PHYS_SHAPE_CONVEX: "convex",
    PHYS_SHAPE_INVALID: "invalid",
}

#: OpenSim's defaults for an ordinary prim (``SceneObjectPart``). Reporting a
#: value that merely matches the default as though it were deliberately set
#: overstates what the sim actually said.
DEFAULT_DENSITY = 1000.0
DEFAULT_FRICTION = 0.6
DEFAULT_RESTITUTION = 0.5
DEFAULT_GRAVITY_MULTIPLIER = 1.0


def physics_shape_name(shape_type: int) -> str:
    """Name for a physics shape type, keeping the number when unknown."""
    name = PHYS_SHAPE_NAMES.get(shape_type)
    return name if name is not None else f"unknown shape {shape_type}"


@dataclass(slots=True, frozen=True)
class PhysicsProperties:
    """One prim's physics material, as the sim reports it."""

    shape_type: int
    density: float
    friction: float
    restitution: float
    gravity_multiplier: float

    @property
    def shape_name(self) -> str:
        return physics_shape_name(self.shape_type)

    @property
    def is_collidable(self) -> bool:
        """False when the prim has no collision shape at all."""
        return self.shape_type != PHYS_SHAPE_NONE

    def non_default_fields(self) -> tuple[str, ...]:
        """Material values that differ from OpenSim's defaults."""
        differences: list[str] = []
        for label, value, default in (
            ("density", self.density, DEFAULT_DENSITY),
            ("friction", self.friction, DEFAULT_FRICTION),
            ("restitution", self.restitution, DEFAULT_RESTITUTION),
            ("gravity", self.gravity_multiplier, DEFAULT_GRAVITY_MULTIPLIER),
        ):
            if abs(value - default) > 1e-6:
                differences.append(f"{label}={value:g}")
        return tuple(differences)

    def describe(self) -> str:
        parts = [f"shape={self.shape_name}"]
        parts.extend(self.non_default_fields())
        return " ".join(parts)


def physics_properties_from_event(event: object) -> PhysicsProperties:
    """Build ``PhysicsProperties`` from an ``ObjectPhysicsPropertiesEvent``."""
    return PhysicsProperties(
        shape_type=int(event.physics_shape_type),
        density=float(event.density),
        friction=float(event.friction),
        restitution=float(event.restitution),
        gravity_multiplier=float(event.gravity_multiplier),
    )


__all__ = [
    "DEFAULT_DENSITY",
    "DEFAULT_FRICTION",
    "DEFAULT_GRAVITY_MULTIPLIER",
    "DEFAULT_RESTITUTION",
    "PHYS_SHAPE_CONVEX",
    "PHYS_SHAPE_INVALID",
    "PHYS_SHAPE_NAMES",
    "PHYS_SHAPE_NONE",
    "PHYS_SHAPE_PRIM",
    "PhysicsProperties",
    "physics_properties_from_event",
    "physics_shape_name",
]
