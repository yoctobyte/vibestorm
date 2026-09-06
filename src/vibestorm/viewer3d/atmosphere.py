"""Turning a region's day cycle into a sky, a sun and a sea.

`world/environment.py` reads what the region publishes. This decides what to
draw with it, and the two are kept apart on purpose: the first is protocol and
has one right answer, the second is a rendering choice and has several.

What is *not* attempted here is Windlight's own sky, which is an integral
through a modelled atmosphere -- the `rayleigh_config`, `mie_config` and
`absorption_config` blocks sit in every frame beside the colours these use, and
reproducing them is a project of its own. What this does is take each colour
parameter in the direction it plainly means:

- **The zenith is `blue_density`.** Straight up is the least air to look
  through, so it is the sky's own colour with the least haze in front of it.
- **The horizon is `blue_horizon`, washed toward the colour of the light by
  `haze_horizon`.** Haze is lit air; it takes the light's colour, and
  `haze_horizon` is how much of it there is at the horizon.
- **`ambient` is the brightness of the whole thing.** Over the day it runs
  from 1.05 at noon to 0.26 at midnight in the default cycle, which is the one
  parameter that makes a night sky dark rather than a blue one at 3 a.m.
- **The sea is its own fog colour with some sky in it.** A real water surface
  is mostly reflection at a grazing angle and mostly fog looking down; with no
  Fresnel term here, one fixed mixture stands in for both.

The sun's *direction* is not a choice: each sky keyframe carries a
`sun_rotation`, and the default cycle's rotations trace an exact arc -- down at
midnight, +5.4 degrees at 0.125, straight up at 0.5, +4.3 degrees at 0.875 --
once they are read as turning **+X**, which is east. That is where
`SUN_REFERENCE_DIRECTION` comes from, and it is checked in the tests against
all eight keyframes rather than asserted.

Which matters more than it looks, because the simulator does not send the sun.
`SimulatorViewerTimeMessage.SunDirection` arrives as `(0, 0, 0)` from OpenSim,
every time, so the renderer had been falling back to a fixed direction and the
sun had never moved in any session.
"""

from __future__ import annotations

from vibestorm.viewer3d.linkset import quat_rotate
from vibestorm.world.environment import SkySettings, WaterSettings

Color3 = tuple[float, float, float]
Vec3 = tuple[float, float, float]

#: The vector a sky keyframe's ``sun_rotation`` turns. East, and level.
#: Derived by reading the default day cycle's eight rotations as turns of each
#: axis in turn: only +X produces an arc that is down at midnight, level at
#: dawn and dusk, and straight up at noon.
SUN_REFERENCE_DIRECTION: Vec3 = (1.0, 0.0, 0.0)

#: What a region that says nothing looks like: the colours this viewer picked
#: by eye before any of them came off the wire. Kept as the fallback rather
#: than deleted -- a region whose environment cannot be read is still a region
#: worth drawing, and a black sky over it would be a worse answer than a
#: generic blue one.
DEFAULT_SKY_HORIZON_COLOR: Color3 = (0.62, 0.74, 0.86)
DEFAULT_SKY_ZENITH_COLOR: Color3 = (0.16, 0.36, 0.62)
DEFAULT_WATER_TINT: Color3 = (0.18, 0.36, 0.55)

#: How much of the sky the sea shows back. A real surface is nearly all
#: reflection at a grazing angle and nearly all fog looking straight down;
#: without a Fresnel term one mixture has to serve for both.
WATER_SKY_REFLECTANCE: float = 0.35


#: How lit the world stays at the darkest point of the cycle. Not zero: the
#: moon is real, and a viewer whose region goes absolutely black at midnight
#: cannot be used at midnight.
NIGHT_LIGHT_FLOOR: float = 0.18


def daylight_scale(sky: SkySettings) -> float:
    """How brightly to light the ground and the prims, 0 to 1.

    The sky darkened at night before this and everything under it did not, so a
    region at midnight was a black sky over a field in full sun. Scalar rather
    than a colour: the sky already carries the hue of the light, and making the
    prim shader's `v_light` a vec3 is a change worth making on its own.
    """
    return NIGHT_LIGHT_FLOOR + (1.0 - NIGHT_LIGHT_FLOOR) * _brightness(sky.ambient)


def light_hues(sky: SkySettings) -> tuple[Color3, Color3]:
    """The colour of the ambient light and of the sunlight, in that order.

    Two colours because the shaders already have two terms, and each parameter
    names one of them: `ambient` is the light off the sky, `sunlight_color` the
    light straight from the sun. Brightness is divided out of both -- that is
    `daylight_scale`'s job, and multiplying the two would count the day twice.

    The default cycle puts the interesting colour in `ambient`: pink at dawn
    (1.00, 0.57, 0.78), warm at dusk (1.00, 0.79, 0.79), blue at midnight
    (0.62, 0.74, 1.00). `sunlight_color` is white at the horizon and faintly
    blue at noon, and is *brighter than one* at dawn and dusk -- 2.8 at the
    sunset keyframe -- which is why its brightness cannot be used as a level.
    """
    return _light_hue(sky.ambient), _light_hue(sky.sunlight_color)


def sun_direction(sky: SkySettings) -> Vec3:
    """Where the sun is, from the day cycle rather than from the simulator."""
    return quat_rotate(sky.sun_rotation, SUN_REFERENCE_DIRECTION)


def sky_gradient(sky: SkySettings) -> tuple[Color3, Color3]:
    """The horizon and zenith colours for one moment in the day."""
    brightness = _brightness(sky.ambient)
    light = _light_hue(sky.sunlight_color)
    haze = _clamp(sky.haze_horizon)
    horizon = _scaled(_mix(sky.blue_horizon, light, haze), brightness)
    zenith = _scaled(sky.blue_density, brightness)
    return horizon, zenith


def water_tint(water: WaterSettings, horizon: Color3) -> Color3:
    """The colour of the sea: its own fog, with the sky it reflects in it."""
    return _clamped(_mix(water.fog_color, horizon, WATER_SKY_REFLECTANCE))


def _brightness(ambient: Color3) -> float:
    """One number for how lit the sky is, from the ambient colour.

    Averaged rather than taken from any one channel: the default cycle's night
    ambient is bluer than its day ambient, and a red-channel reading would call
    a blue night darker than it is.
    """
    return _clamp(sum(ambient) / 3.0)


def _light_hue(sunlight_color: Color3) -> Color3:
    """The colour of the daylight with its brightness divided out.

    Haze takes the light's *colour*; how bright it is comes from `ambient`, and
    multiplying the two would count the day twice.
    """
    peak = max(sunlight_color)
    if peak <= 0.0:
        return (1.0, 1.0, 1.0)
    return (
        sunlight_color[0] / peak,
        sunlight_color[1] / peak,
        sunlight_color[2] / peak,
    )


def _mix(a: Color3, b: Color3, t: float) -> Color3:
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def _scaled(color: Color3, factor: float) -> Color3:
    return _clamped((color[0] * factor, color[1] * factor, color[2] * factor))


def _clamped(color: Color3) -> Color3:
    return (_clamp(color[0]), _clamp(color[1]), _clamp(color[2]))


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)
