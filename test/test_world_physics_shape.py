"""Tests for physics shape naming and material reporting.

Values are OpenSim's ``PhysShapeType`` (``Framework/ExtraPhysicsData.cs``) and
the material defaults are ``SceneObjectPart``'s field initialisers.
"""

import re
import unittest
from pathlib import Path

from vibestorm.world.physics_shape import (
    DEFAULT_DENSITY,
    DEFAULT_FRICTION,
    DEFAULT_GRAVITY_MULTIPLIER,
    DEFAULT_RESTITUTION,
    PHYS_SHAPE_CONVEX,
    PHYS_SHAPE_INVALID,
    PHYS_SHAPE_NONE,
    PHYS_SHAPE_PRIM,
    PhysicsProperties,
    physics_properties_from_event,
    physics_shape_name,
)

_ENUM_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "opensim-source"
    / "OpenSim"
    / "Framework"
    / "ExtraPhysicsData.cs"
)


def _properties(**overrides: float) -> PhysicsProperties:
    values: dict = dict(
        shape_type=PHYS_SHAPE_PRIM,
        density=DEFAULT_DENSITY,
        friction=DEFAULT_FRICTION,
        restitution=DEFAULT_RESTITUTION,
        gravity_multiplier=DEFAULT_GRAVITY_MULTIPLIER,
    )
    values.update(overrides)
    return PhysicsProperties(**values)


class SourcePinTests(unittest.TestCase):
    def test_shape_values_match_opensim(self) -> None:
        if not _ENUM_SOURCE.exists():
            self.skipTest("opensim-source not present")
        body = _ENUM_SOURCE.read_text(encoding="utf-8", errors="replace")
        body = body.split("enum PhysShapeType", 1)[1].split("}", 1)[0]
        source = {name: int(value) for name, value in re.findall(r"(\w+)\s*=\s*(\d+)", body)}

        self.assertEqual(source["prim"], PHYS_SHAPE_PRIM)
        self.assertEqual(source["none"], PHYS_SHAPE_NONE)
        self.assertEqual(source["convex"], PHYS_SHAPE_CONVEX)
        self.assertEqual(source["invalid"], PHYS_SHAPE_INVALID)


class ShapeNameTests(unittest.TestCase):
    def test_known_shapes(self) -> None:
        self.assertEqual(physics_shape_name(PHYS_SHAPE_PRIM), "prim")
        self.assertEqual(physics_shape_name(PHYS_SHAPE_NONE), "none")
        self.assertEqual(physics_shape_name(PHYS_SHAPE_CONVEX), "convex")

    def test_unknown_shape_keeps_its_number(self) -> None:
        self.assertEqual(physics_shape_name(7), "unknown shape 7")

    def test_invalid_is_a_marker_not_a_shape(self) -> None:
        # OpenSim uses 255 to mean "no usable data", so it must not be
        # presented as though the prim were built that way.
        self.assertEqual(PHYS_SHAPE_INVALID, 255)
        self.assertEqual(physics_shape_name(PHYS_SHAPE_INVALID), "invalid")


class CollidabilityTests(unittest.TestCase):
    def test_shape_none_is_not_collidable(self) -> None:
        self.assertFalse(_properties(shape_type=PHYS_SHAPE_NONE).is_collidable)

    def test_prim_and_convex_are_collidable(self) -> None:
        self.assertTrue(_properties(shape_type=PHYS_SHAPE_PRIM).is_collidable)
        self.assertTrue(_properties(shape_type=PHYS_SHAPE_CONVEX).is_collidable)


class DescribeTests(unittest.TestCase):
    def test_default_material_reports_shape_only(self) -> None:
        # Every prim carries these numbers; echoing them back when they are
        # OpenSim's defaults would imply someone chose them.
        self.assertEqual(_properties().describe(), "shape=prim")

    def test_changed_values_are_named(self) -> None:
        described = _properties(density=42.0, friction=0.1).describe()

        self.assertIn("density=42", described)
        self.assertIn("friction=0.1", described)
        self.assertNotIn("restitution", described)

    def test_each_default_is_the_opensim_value(self) -> None:
        self.assertEqual(DEFAULT_DENSITY, 1000.0)
        self.assertEqual(DEFAULT_FRICTION, 0.6)
        self.assertEqual(DEFAULT_RESTITUTION, 0.5)
        self.assertEqual(DEFAULT_GRAVITY_MULTIPLIER, 1.0)


class FromEventTests(unittest.TestCase):
    def test_event_fields_map_across(self) -> None:
        from vibestorm.event_queue.events import ObjectPhysicsPropertiesEvent

        event = ObjectPhysicsPropertiesEvent(
            local_id=99,
            density=12.0,
            friction=0.25,
            gravity_multiplier=2.0,
            restitution=0.75,
            physics_shape_type=PHYS_SHAPE_CONVEX,
        )

        physics = physics_properties_from_event(event)

        self.assertEqual(physics.shape_type, PHYS_SHAPE_CONVEX)
        self.assertEqual(physics.density, 12.0)
        self.assertEqual(physics.friction, 0.25)
        self.assertEqual(physics.restitution, 0.75)
        self.assertEqual(physics.gravity_multiplier, 2.0)

    def test_restitution_and_gravity_are_not_swapped(self) -> None:
        # The event lists gravity_multiplier before restitution while the
        # dataclass lists them the other way round; positional construction
        # would silently transpose them.
        from vibestorm.event_queue.events import ObjectPhysicsPropertiesEvent

        physics = physics_properties_from_event(
            ObjectPhysicsPropertiesEvent(
                local_id=1,
                density=1.0,
                friction=1.0,
                gravity_multiplier=3.0,
                restitution=9.0,
                physics_shape_type=PHYS_SHAPE_PRIM,
            ),
        )

        self.assertEqual(physics.gravity_multiplier, 3.0)
        self.assertEqual(physics.restitution, 9.0)


if __name__ == "__main__":
    unittest.main()
