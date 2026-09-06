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

#: The moon turns the same vector, and the arithmetic says so rather than the
#: naming: at midnight the default cycle's `moon_rotation` is a quarter turn
#: about -Y, which takes +X to straight up, and at noon it is the same turn
#: the other way, which takes it straight down. Opposite the sun at both, which
#: is what a moon does.
MOON_REFERENCE_DIRECTION: Vec3 = SUN_REFERENCE_DIRECTION

#: What counts as a fully dark sky's worth of stars.
#:
#: A normalising constant, not a physical one. Nothing documents the range of
#: `star_brightness`; what is observable is that OpenSim's default cycle writes
#: exactly 500 in both night keyframes and exactly 0 in all six daytime ones,
#: so this converts the one to "all of them" and leaves the other at none.
STAR_BRIGHTNESS_FULL: float = 500.0

#: How high above the viewer the cloud layer is drawn, in metres.
#:
#: A rendering choice. `max_y` sits in every sky frame at 1605 and reads like
#: an altitude, but nothing says it is the clouds' -- so this is a number
#: picked to look like weather rather than one taken off the wire. Low enough
#: that the layer has visible perspective, high enough not to sit on the
#: rooftops.
CLOUD_ALTITUDE_METRES: float = 320.0

#: How many metres of sky one unit of cloud noise spans at `cloud_scale` 1.
#: The other half of a choice: `cloud_scale` is a bare number in the document
#: with no unit attached, and this is what turns it into a size.
CLOUD_SCALE_METRES: float = 260.0

#: Where the coverage numbers land on the noise.
#:
#: `cloud_pos_density1`'s third component runs 0.88 to 1.0 across the default
#: cycle. Taken literally as "this fraction of the sky is cloud" that is
#: permanent overcast, which is not what the default sky looks like -- because
#: in the document it multiplies a *texture*, and the texture is what has the
#: holes in it. With no texture fetched, the noise stands in for it and these
#: two say where its edge falls.
#: What one unit of `cloud_scroll_rate` means, in cell widths per second.
#:
#: The rate is a bare pair of numbers with no unit in the document. At one
#: cell a second the default cycle's 0.5 would blow the sky past in a blink;
#: at this it crosses a cell in about a minute, which is weather.
CLOUD_DRIFT_PER_SECOND: float = 0.017

CLOUD_EDGE_LOW: float = 0.46
CLOUD_EDGE_HIGH: float = 0.78

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


def moon_direction(sky: SkySettings) -> Vec3:
    """Where the moon is. Same reference vector as the sun -- see the constant."""
    return quat_rotate(sky.moon_rotation, MOON_REFERENCE_DIRECTION)


def star_level(sky: SkySettings) -> float:
    """How much of the star field to draw, 0 to 1."""
    return _clamp(sky.star_brightness / STAR_BRIGHTNESS_FULL)


def moon_level(sky: SkySettings) -> float:
    """How bright the moon's disc is, 0 to 1.

    Not gated on night. The default cycle holds `moon_brightness` at 0.5 all
    day and lets the moon be up whenever its rotation puts it up, which is
    right: a daytime moon is a real thing, and against a sky already near 0.7
    a half-strength white disc is pale rather than absent -- which is roughly
    how it looks.
    """
    return _clamp(sky.moon_brightness)


def cloud_cover(sky: SkySettings) -> tuple[float, float]:
    """How much coarse and fine cloud there is, both 0 to 1.

    The third component of each `cloud_pos_density` pair. The other two are an
    offset into a cloud texture this tree has never fetched, so they are
    parsed and not used.
    """
    return _clamp(sky.cloud_pos_density1[2]), _clamp(sky.cloud_pos_density2[2])


def cloud_hue(sky: SkySettings) -> Color3:
    """What colour the clouds are: `cloud_color` as an albedo, lit.

    Taken raw, `cloud_color` draws storm clouds at noon -- it is 0.41 grey,
    against a sky the same derivation puts at about 0.5 blue, and cloud that
    is darker than the sky behind it is cloud in front of a thunderstorm. It
    is a surface colour, not a drawn one.

    Lit by the sum of the two lights the frame already has, it behaves: white
    with a little blue in it at noon, warm and bright at dusk when
    `sunlight_color` reaches 2.84, and a dark blue-grey at midnight. Every one
    of those comes out of the region's own numbers rather than being chosen.
    """
    lit = tuple(a + b for a, b in zip(sky.ambient, sky.sunlight_color, strict=True))
    return _clamped(
        tuple(
            albedo * light
            for albedo, light in zip(sky.cloud_color, lit, strict=True)
        )
    )


def cloud_size(sky: SkySettings) -> float:
    """How many metres across one cell of cloud is."""
    scale = sky.cloud_scale if sky.cloud_scale > 0.0 else 1.0
    return scale * CLOUD_SCALE_METRES


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
