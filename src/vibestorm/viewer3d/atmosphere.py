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

import math

from vibestorm.viewer3d.linkset import quat_rotate
from vibestorm.world.environment import SkySettings, WaterSettings

Color3 = tuple[float, float, float]
Vec2 = tuple[float, float]
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

#: How many metres of sky one tile of the region's cloud texture spans at
#: `cloud_scale` 1. The other half of a choice: `cloud_scale` is a bare number
#: in the document with no unit attached, and this is what turns it into a
#: size.
#:
#: A tile, since `cloud_id` turned out to be a real 512x512 texture that
#: repeats seamlessly. One tile holds a whole sky's worth of shapes, so it has
#: to span most of a sky: at the default cycle's 0.42 this is about a
#: kilometre, against a cloud layer 320 metres up, and the repeat falls where
#: perspective has already crushed it toward the horizon.
CLOUD_SCALE_METRES: float = 2400.0

#: How many cells of the stand-in noise fill one of those tiles.
#:
#: The noise is what is drawn until the texture arrives, and the two are not
#: the same kind of thing: a tile of the texture holds several clouds where
#: one cell of value noise holds about one. Drawn a cell to a tile, the
#: fallback is a single cloud across the whole sky. This is the ratio that
#: makes the two read at the same size.
CLOUD_NOISE_CELLS_PER_TILE: float = 9.2

#: Where the coverage numbers land on the noise.
#:
#: `cloud_pos_density1`'s third component runs 0.88 to 1.0 across the default
#: cycle. Taken literally as "this fraction of the sky is cloud" that is
#: permanent overcast, which is not what the default sky looks like -- because
#: in the document it multiplies a *texture*, and the texture is what has the
#: holes in it. With no texture fetched, the noise stands in for it and these
#: two say where its edge falls.
#: What one unit of `cloud_scroll_rate` means, in tile widths per second.
#:
#: The rate is a bare pair of numbers with no unit in the document. At one
#: tile a second the default cycle's 0.5 would blow the sky past in a blink;
#: at this it moves the layer just under a metre a second, which is weather.
CLOUD_DRIFT_PER_SECOND: float = 0.00185

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

#: The sea's own colour when no region has said otherwise: `WaterSettings`'
#: own default, not a second guess beside it. Every region supplies this, so
#: the fallback only shows before the first environment fetch lands.
DEFAULT_WATER_FOG: Color3 = WaterSettings().fog_color

#: How long one ripple is, in metres, before `normal_scale` divides it down.
#: The *mean* of the two waves' lengths; how far apart the two are is off the
#: wire, see `water_wave_number`.
#:
#: A rendering choice, and it has to be: `normal_map` names a texture nobody
#: here has fetched, so there is no map to scale and the waves are made out of
#: sines instead. What *is* off the wire is which way they run and how
#: fast -- `wave1_direction` and `wave2_direction` -- and how steep the surface
#: gets, from `scale_above`.
WATER_WAVE_LENGTH_M: float = 9.0

#: How far apart the two waves' lengths are allowed to get, as a ratio.
#:
#: The dispersion relation below has no upper bound in it: a document whose
#: second wave barely moves asks for a wave hundreds of metres long, which is
#: not a wave any more but a tilt in the whole sea. Three is about where a
#: swell stops reading as the same sea as the one crossing it.
WATER_WAVE_LENGTH_SPREAD: float = 3.0

#: How fast a wave travels, in metres per second per unit of a wave direction.
#: The default cycle's two directions are about 1.1 and 1.6 long, so this puts
#: them at roughly 0.6 and 0.9 m/s: a swell that crosses its own wavelength in
#: seven seconds, which is a calm sea rather than a pond or a storm.
WATER_WAVE_SPEED_M_PER_S: float = 0.55

#: How large the sun and the moon are drawn at the default scale, as an
#: angular radius in radians.
#:
#: Both about 2.5 degrees, a little over four times life size, and both are
#: the sizes the two hard-coded thresholds they replace already worked out to:
#: the moon's pair of cosines reproduces exactly, and the sun keeps the angle
#: at which it was half as bright, though its falloff is a smoothstep now
#: rather than a 900th power. Life size is not an option -- at a quarter of a
#: degree each draws as a dot, and every viewer in this world has made the
#: same choice.
SUN_DISC_RADIUS_RAD: float = 0.0442
MOON_DISC_RADIUS_RAD: float = 0.0447

#: How much of a disc's radius its soft edge takes. Without one the rim
#: aliases into a ring of steps as the camera turns.
DISC_EDGE_FRACTION: float = 0.2254

#: What a density of one is worth as a distance, in metres.
#:
#: A rendering choice standing in for a unit the document does not give. See
#: `underwater_reach`: the default cycle's density works out to thirty metres
#: of visibility with this constant, which is a clear sea rather than a murky
#: one -- and murk is the failure that cannot be undone from inside the
#: viewer, since a swimmer who can see nothing cannot tell a dense sea from a
#: broken renderer.
UNDERWATER_REFERENCE_M: float = 120.0

#: What `scale_above` is worth as a slope. The document gives 0.03, which is a
#: distortion strength for a normal map and not an angle; multiplied by this it
#: becomes a surface that leans about ten degrees at the steepest, which is
#: what makes the Fresnel term visible as ripples rather than as a flat sheet.
WATER_WAVE_STEEPNESS: float = 6.0


def sun_disc(sky: SkySettings) -> Vec2:
    """How large to draw the sun, as the cosines of its outer and inner edge.

    Cosines rather than an angle because that is what a shader compares a dot
    product against, and a dot product is all the sky shader has: there is no
    disc geometry up there, only the angle between the ray and the light.
    """
    return _disc(SUN_DISC_RADIUS_RAD, sky.sun_scale)


def moon_disc(sky: SkySettings) -> Vec2:
    """The same for the moon, which the document scales separately."""
    return _disc(MOON_DISC_RADIUS_RAD, sky.moon_scale)


def _disc(radius: float, scale: float) -> Vec2:
    # Floored rather than allowed to reach zero: at a radius of nothing the
    # two edges are the same cosine, and a smoothstep whose edges meet is a
    # step -- an aliased dot instead of no sun at all. The same floor is what
    # catches a negative scale, which would otherwise turn the disc inside out.
    radius = max(radius * scale, 1e-5)
    return (math.cos(radius), math.cos(radius * (1.0 - DISC_EDGE_FRACTION)))


def cloud_shadow_scale(sky: SkySettings) -> float:
    """How much of the direct sun the cloud layer keeps off the ground.

    Returned as what is *left*, so it multiplies. It is the direct light it
    takes and not the ambient: cloud over a landscape dims the sun and leaves
    the sky lighting everything, which is why an overcast day has soft shadows
    rather than dark ones.

    Independent of the clouds actually drawn, which are procedural and do not
    line up with anything. The region says how much shade there is; where it
    falls is not in the document.
    """
    return 1.0 - _clamp(sky.cloud_shadow)


def water_fog(water: WaterSettings) -> Color3:
    """The colour of the sea itself: what is seen looking *through* it.

    Kept apart from `water_tint`, which is the same colour with a fixed share
    of sky already mixed into it. The renderer that has a Fresnel term wants
    the two separately, so it can decide how much sky per pixel; the one that
    does not still wants them premixed.
    """
    return _clamped(water.fog_color)


def water_fresnel(water: WaterSettings) -> tuple[float, float]:
    """How much sky the surface shows back, as (base, grazing).

    Read as Schlick's approximation -- reflectance rises as the fifth power of
    one minus the cosine of the viewing angle -- with `fresnel_offset` as the
    amount reflected looking straight down and `fresnel_scale` as how much more
    is added at a grazing angle. That is a reading of the two names, not of any
    implementation, and it is worth saying that 0.5 is nothing like water's
    real reflectance straight down, which is about 0.02. The number is an
    artistic one and is used as given.
    """
    return (_clamp(water.fresnel_offset), _clamp(water.fresnel_scale))


def water_waves(water: WaterSettings) -> tuple[float, float, float, float]:
    """The two wave directions as unit vectors: (d1x, d1y, d2x, d2y).

    Their *lengths* are dropped here and picked back up in `water_wave_speed`,
    because a direction and a speed are two different things to a shader even
    though the document packs them into one vector.

    A zero-length direction falls back to east and north rather than producing
    a NaN: a document may say a wave stands still, and a surface with one wave
    on it is better than a surface with none.
    """
    return (*_unit(water.wave1_direction, (1.0, 0.0)), *_unit(water.wave2_direction, (0.0, 1.0)))


def water_wave_speed(water: WaterSettings) -> tuple[float, float]:
    """How fast each wave's phase advances, in radians per second.

    Each wave against its own wave number, so the faster wave is not also the
    busier one -- it is the longer one, and it comes round *less* often.
    """
    first, second = water_wave_number(water)
    return (
        _length(water.wave1_direction) * WATER_WAVE_SPEED_M_PER_S * first,
        _length(water.wave2_direction) * WATER_WAVE_SPEED_M_PER_S * second,
    )


def water_wave_number(water: WaterSettings) -> Vec2:
    """Radians of wave per metre of sea, for each of the two waves.

    `normal_scale` is how many times the normal map repeats across whatever it
    repeats across; with no map to repeat, it is read here as how fine the
    ripples are, larger being finer. Averaged over the three components, which
    are the three layers the map would have been sampled at. That fixes the
    mean of the two lengths.

    What separates them is the one piece of physics the document supports.
    The two wave *directions* have different lengths -- 1.1 and 1.6 in the
    default cycle -- and that length is a speed. In deep water a wave's phase
    speed goes as the square root of its wavelength, so a wave half again as
    fast is more than twice as long, and the ratio of the two lengths is the
    square of the ratio of the two speeds.

    This is worth doing for more than tidiness. Two sines of the *same* length
    crossing at an angle are a perfect diamond lattice, and a lattice is what
    a sea most obviously is not; seen from above it reads as woven mesh. Two
    of different lengths are not periodic in any direction a camera looks
    along. The regularity was never a filtering problem, it was two waves
    being secretly the same wave.
    """
    scale = sum(water.normal_scale) / 3.0
    metres = max(WATER_WAVE_LENGTH_M / max(scale, 1e-3), 1e-3)
    # The speed ratio. The length ratio is its square, so the first wave is
    # this much longer than the mean and the second this much shorter, which
    # keeps the mean where `normal_scale` put it. Clamped both ways so that one
    # nearly still wave cannot stretch the other across the whole region.
    stretch = min(
        max(
            max(_length(water.wave1_direction), 1e-3)
            / max(_length(water.wave2_direction), 1e-3),
            1.0 / WATER_WAVE_LENGTH_SPREAD,
        ),
        WATER_WAVE_LENGTH_SPREAD,
    )
    return (2.0 * math.pi / (metres * stretch), 2.0 * math.pi * stretch / metres)


def water_wave_slope(water: WaterSettings) -> float:
    """How far the surface leans at the steepest point of a wave."""
    return max(0.0, water.scale_above) * WATER_WAVE_STEEPNESS


def water_wave_slope_below(water: WaterSettings) -> float:
    """The same, for a viewer under the surface.

    `scale_below` is a separate number in the document -- 0.2 against 0.03
    above -- and it is larger, which is the right way round: seen from
    underneath, a surface is a lens rather than a mirror and the same swell
    bends the view much further.
    """
    return max(0.0, water.scale_below) * WATER_WAVE_STEEPNESS


def underwater_reach(water: WaterSettings) -> float:
    """How far a viewer can see under the surface, in metres.

    `water_fog_density` is the only density the water frame carries -- 16 in
    the default cycle -- and `underwater_fog_mod` (0.25) is what modifies it
    for a viewer who is under rather than over. Neither has a unit anywhere:
    16 is not 16 of anything the document names, and taken literally as an
    extinction coefficient per metre it would put visibility at six
    centimetres.

    So the pair are read as a *ratio* and `UNDERWATER_REFERENCE_M` turns it
    into a distance. The default cycle's 16 x 0.25 gives thirty metres, which
    is a clear sea; a region that doubles its density halves that.

    One clamp, not three. Guarding each factor against a negative document
    was dead code the moment the divisor was floored: a negative density
    produces a negative product, and flooring that gives the same very long
    reach flooring a zero does.
    """
    density = water.fog_density * water.underwater_fog_mod
    return UNDERWATER_REFERENCE_M / max(density, 0.01)


def _unit(vector: Vec2, fallback: Vec2) -> Vec2:
    length = _length(vector)
    if length <= 0.0:
        return fallback
    return (vector[0] / length, vector[1] / length)


def _length(vector: Vec2) -> float:
    return math.hypot(vector[0], vector[1])



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


def cloud_offsets(sky: SkySettings) -> tuple[float, float, float, float]:
    """Where in the cloud texture each of the two layers starts.

    The first two components of `cloud_pos_density1` and of
    `cloud_pos_density2`, whose third components are the two densities. They
    were parsed and unusable while there was no texture to offset into.

    In texture widths, so 1.0 is a whole tile and does nothing to a field that
    repeats -- which the region's own cloud texture does, seamlessly. Nor is
    there anything to see in the difference between the two here: they are
    *equal* in every keyframe of the default cycle, so the second layer sits
    exactly on the first and the sky is one field at the sum of two densities.
    None of that is a reason to drop them. What the document says is where
    each layer starts, and a region that says 0.3 means a different sky from
    one that says 0.8.
    """
    return (
        sky.cloud_pos_density1[0],
        sky.cloud_pos_density1[1],
        sky.cloud_pos_density2[0],
        sky.cloud_pos_density2[1],
    )


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
    """How many metres across one tile of the cloud field is."""
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


#: What the sea does before any region has said otherwise: `WaterSettings`'
#: own defaults, read the same way a region's would be. Derived rather than
#: written out, so a change to any of the readings above cannot leave the
#: fallback describing a different sea from the real one.
_DEFAULT_WATER = WaterSettings()
DEFAULT_WATER_FRESNEL: Vec2 = water_fresnel(_DEFAULT_WATER)
DEFAULT_WATER_WAVES: tuple[float, float, float, float] = water_waves(_DEFAULT_WATER)
DEFAULT_WATER_WAVE_SPEED: Vec2 = water_wave_speed(_DEFAULT_WATER)
DEFAULT_WATER_RIPPLE: tuple[float, float, float] = (
    *water_wave_number(_DEFAULT_WATER),
    water_wave_slope(_DEFAULT_WATER),
)
DEFAULT_WATER_RIPPLE_BELOW: float = water_wave_slope_below(_DEFAULT_WATER)
DEFAULT_UNDERWATER_REACH: float = underwater_reach(_DEFAULT_WATER)

#: And what the sky does: `SkySettings`' own defaults, read the same way.
_DEFAULT_SKY = SkySettings()
DEFAULT_SUN_DISC: Vec2 = sun_disc(_DEFAULT_SKY)
DEFAULT_MOON_DISC: Vec2 = moon_disc(_DEFAULT_SKY)
