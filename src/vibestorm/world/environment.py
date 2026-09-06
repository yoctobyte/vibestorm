"""The region's own weather: what `ExtEnvironment` says the sky and water are.

Everything this client draws above and below the prims -- the sky gradient, the
sun in it, the colour of the sea -- came out of constants compiled into the
shaders, so every region looked like the same afternoon. `RegionHandshake`
cannot fix that: it carries the four terrain textures, the elevation bands they
cover, and the water *height*, and nothing at all about colour.

The colour lives behind a capability. The local OpenSim offers two:

- `EnvironmentSettings` -- the legacy Windlight document, an array whose first
  entry is a message/region id map, then a day-cycle track of
  `[keyframe, frame-name]` pairs, then a map of frames.
- `ExtEnvironment` -- the EEP document, a map under `environment` holding one
  `day_cycle` with named `frames` and up to five `tracks`.

This module reads the second, because it is the one the current grid speaks and
the one whose day cycle is addressable by altitude. Both were fetched live to
find that out (2026-09-06); the legacy one is recorded in the coverage ledger
as present and not read.

Two things about the document are worth knowing before reading the code, since
neither is stated in it:

- **Track 0 is water and track 1 is the sky at ground level.** Tracks 2, 3 and
  4 are the sky above each of `track_altitudes`, and are empty here. The
  numbering is positional -- there is no `type` on a track, only on a frame --
  so the frames are checked against it rather than trusted.
- **A frame name is a number in a string.** They are hashes, not indices, and
  sorting by them means nothing; the tracks give the order.

Both fetches need the agent to actually be in the region. Asking straight after
login, before `UseCircuitCode`, gets a 503 from a capability that is perfectly
well resolved -- which reads like a broken URL rather than like being early.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

Color3 = tuple[float, float, float]
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

#: Track 0 carries the water frame, track 1 the sky at ground level. Positional
#: rather than labelled, so parsing checks each frame's own ``type`` against it.
WATER_TRACK = 0
GROUND_SKY_TRACK = 1


class EnvironmentError(ValueError):
    """Raised when an environment document cannot be read."""


@dataclass(slots=True, frozen=True)
class WaterSettings:
    """One water keyframe.

    ``fog_color`` is the colour looking *through* water, which is what gives a
    sea its colour, and ``fog_density`` how fast it gets there.
    """

    fog_color: Color3 = (0.016, 0.149, 0.251)
    fog_density: float = 16.0
    underwater_fog_mod: float = 0.25
    blur_multiplier: float = 0.04
    fresnel_offset: float = 0.5
    fresnel_scale: float = 0.4
    scale_above: float = 0.03
    scale_below: float = 0.2
    normal_scale: Vec3 = (2.0, 2.0, 2.0)
    wave1_direction: Vec2 = (1.05, -0.42)
    wave2_direction: Vec2 = (1.11, -1.16)
    normal_map: str = ""


@dataclass(slots=True, frozen=True)
class SkySettings:
    """One sky keyframe.

    The colour fields under ``legacy_haze`` are the ones that describe the sky
    a person sees; the atmospheric configs beside them (`rayleigh_config`,
    `mie_config`, `absorption_config`) describe the physical model a viewer may
    integrate instead, and are not read here. Which of the two a document
    carries is not a choice: OpenSim writes both.
    """

    ambient: Color3 = (1.05, 1.05, 1.05)
    blue_density: Color3 = (0.245, 0.449, 0.76)
    blue_horizon: Color3 = (0.495, 0.495, 0.64)
    haze_density: float = 0.7
    haze_horizon: float = 0.19
    density_multiplier: float = 0.00018
    distance_multiplier: float = 0.8
    cloud_color: Color3 = (0.41, 0.41, 0.41)
    cloud_shadow: float = 0.27
    cloud_scale: float = 0.42
    #: ``cloud_pos_density1`` and ``cloud_pos_density2``, verbatim. Each is
    #: (x, y, density): the first two are an offset into the cloud texture and
    #: the third is how much of it there is. The pair are the coarse layer and
    #: the fine one, and only their densities are read here -- there is no
    #: cloud texture to offset into.
    cloud_pos_density1: Vec3 = (1.0, 0.53, 0.88)
    cloud_pos_density2: Vec3 = (1.0, 0.53, 0.125)
    #: How fast the two layers drift, in whatever units the document means.
    cloud_scroll_rate: Vec2 = (0.5, 0.011)
    cloud_variance: float = 0.0
    sunlight_color: Color3 = (0.734, 0.782, 0.9)
    gamma: float = 1.0
    max_y: float = 1605.0
    star_brightness: float = 0.0
    moon_brightness: float = 0.5
    #: How large to draw the sun and the moon, as a multiple of whatever a
    #: viewer calls the usual size. Both 1.0 in OpenSim's default cycle, so
    #: this document says nothing about how they scale -- only that 1.0 is the
    #: unchanged one, which is what makes the direction readable at all.
    sun_scale: float = 1.0
    moon_scale: float = 1.0
    glow: Vec3 = (5.0, 0.001, -0.48)
    sun_rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    moon_rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    cloud_id: str = ""
    sun_id: str = ""
    moon_id: str = ""


@dataclass(slots=True, frozen=True)
class RegionEnvironment:
    """A region's day cycle: what its sky and water look like, and when.

    ``day_length`` and ``day_offset`` are seconds. The offset is *added* to the
    time of day before wrapping, which is how a region whose day is four hours
    long can still be told to start at dusk.
    """

    region_id: str = ""
    day_length: float = 14400.0
    day_offset: float = 0.0
    track_altitudes: tuple[float, ...] = ()
    water_track: tuple[tuple[float, WaterSettings], ...] = ()
    sky_track: tuple[tuple[float, SkySettings], ...] = ()

    def water_at(self, day_fraction: float) -> WaterSettings:
        """The water at a point in the day, 0.0 to 1.0."""
        return _sample(self.water_track, day_fraction, _blend_water, WaterSettings())

    def sky_at(self, day_fraction: float) -> SkySettings:
        """The sky at a point in the day, 0.0 to 1.0."""
        return _sample(self.sky_track, day_fraction, _blend_sky, SkySettings())

    def day_fraction_for(self, seconds: float) -> float:
        """Where in its own day a region is, given a time in seconds."""
        if self.day_length <= 0.0:
            return 0.0
        return ((seconds + self.day_offset) % self.day_length) / self.day_length

    def texture_assets(self) -> tuple[UUID, ...]:
        """Every texture the day cycle names, once each, in keyframe order.

        A day cycle is not only numbers. `moon_id`, `cloud_id` and the water's
        `normal_map` are asset ids, and they are ordinary textures behind the
        ordinary `GetTexture` capability -- fetched from OpenSim's own asset
        service, not a viewer's install. A client that reads the numbers and
        ignores these has to invent a moon, a cloud layer and a wave shape,
        and its sky is then its own rather than the region's.

        Collected across the whole track rather than from one keyframe,
        because nothing says a region uses one moon all day; the default cycle
        does, and a hand-written one need not.

        `sun_id` is in the document too and is the null id in every keyframe of
        the default cycle, which is the document's way of saying *no texture*
        rather than a missing field -- so a null is skipped here like any
        other absent asset, and the caller draws its own.
        """
        found: list[UUID] = []
        seen: set[UUID] = set()
        for _, water in self.water_track:
            _collect_asset(water.normal_map, found, seen)
        for _, sky in self.sky_track:
            for raw in (sky.moon_id, sky.cloud_id, sky.sun_id):
                _collect_asset(raw, found, seen)
        return tuple(found)


def _collect_asset(raw: str, found: list[UUID], seen: set[UUID]) -> None:
    """Add one asset id if it is a real one and not already listed."""
    if not raw:
        return
    try:
        asset_id = UUID(raw)
    except ValueError:
        return
    if asset_id.int == 0 or asset_id in seen:
        return
    seen.add(asset_id)
    found.append(asset_id)


def parse_environment_document(value: object) -> RegionEnvironment:
    """Read an ``ExtEnvironment`` GET reply into a day cycle.

    Missing fields take the defaults above rather than raising: a region that
    leaves the sun colour out is not a broken region, and a client that refuses
    to draw a sky over one is a worse answer than drawing the default.
    """
    if not isinstance(value, Mapping):
        raise EnvironmentError("environment document is not a map")
    environment = value.get("environment")
    if not isinstance(environment, Mapping):
        raise EnvironmentError("environment document has no 'environment' map")
    day_cycle = environment.get("day_cycle")
    if not isinstance(day_cycle, Mapping):
        raise EnvironmentError("environment document has no day cycle")

    frames = day_cycle.get("frames")
    frames = frames if isinstance(frames, Mapping) else {}
    tracks = day_cycle.get("tracks")
    tracks = tracks if isinstance(tracks, Sequence) and not isinstance(tracks, str) else ()

    return RegionEnvironment(
        region_id=str(environment.get("region_id") or ""),
        day_length=_float(environment.get("day_length"), 14400.0),
        day_offset=_float(environment.get("day_offset"), 0.0),
        track_altitudes=tuple(
            _float(entry, 0.0)
            for entry in _sequence(environment.get("track_altitudes"))
        ),
        water_track=tuple(
            _read_track(tracks, WATER_TRACK, frames, "water", _read_water)
        ),
        sky_track=tuple(
            _read_track(tracks, GROUND_SKY_TRACK, frames, "sky", _read_sky)
        ),
    )


def _read_track(tracks, index, frames, wanted_type, read):
    """The keyframes of one track, in order, skipping what does not belong.

    A frame whose own ``type`` disagrees with the track it is listed in is left
    out rather than read as the other thing: the track numbering is positional
    and undocumented, so this is the one place the document can contradict
    itself, and reading a water frame as a sky would produce a plausible,
    entirely wrong sky.
    """
    if index >= len(tracks):
        return []
    entries = tracks[index]
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return []
    out: list[tuple[float, object]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        frame = frames.get(str(entry.get("key_name") or ""))
        if not isinstance(frame, Mapping):
            continue
        if str(frame.get("type") or wanted_type) != wanted_type:
            continue
        keyframe = _float(entry.get("key_keyframe"), 0.0) % 1.0
        out.append((keyframe, read(frame)))
    out.sort(key=lambda pair: pair[0])
    return out


def _read_water(frame: Mapping[str, object]) -> WaterSettings:
    default = WaterSettings()
    return WaterSettings(
        fog_color=_color(frame.get("water_fog_color"), default.fog_color),
        fog_density=_float(frame.get("water_fog_density"), default.fog_density),
        underwater_fog_mod=_float(
            frame.get("underwater_fog_mod"), default.underwater_fog_mod
        ),
        blur_multiplier=_float(frame.get("blur_multiplier"), default.blur_multiplier),
        fresnel_offset=_float(frame.get("fresnel_offset"), default.fresnel_offset),
        fresnel_scale=_float(frame.get("fresnel_scale"), default.fresnel_scale),
        scale_above=_float(frame.get("scale_above"), default.scale_above),
        scale_below=_float(frame.get("scale_below"), default.scale_below),
        normal_scale=_color(frame.get("normal_scale"), default.normal_scale),
        wave1_direction=_vec2(frame.get("wave1_direction"), default.wave1_direction),
        wave2_direction=_vec2(frame.get("wave2_direction"), default.wave2_direction),
        normal_map=str(frame.get("normal_map") or ""),
    )


def _read_sky(frame: Mapping[str, object]) -> SkySettings:
    default = SkySettings()
    haze = frame.get("legacy_haze")
    haze = haze if isinstance(haze, Mapping) else {}
    return SkySettings(
        ambient=_color(haze.get("ambient"), default.ambient),
        blue_density=_color(haze.get("blue_density"), default.blue_density),
        blue_horizon=_color(haze.get("blue_horizon"), default.blue_horizon),
        haze_density=_float(haze.get("haze_density"), default.haze_density),
        haze_horizon=_float(haze.get("haze_horizon"), default.haze_horizon),
        density_multiplier=_float(
            haze.get("density_multiplier"), default.density_multiplier
        ),
        distance_multiplier=_float(
            haze.get("distance_multiplier"), default.distance_multiplier
        ),
        cloud_color=_color(frame.get("cloud_color"), default.cloud_color),
        cloud_shadow=_float(frame.get("cloud_shadow"), default.cloud_shadow),
        cloud_scale=_float(frame.get("cloud_scale"), default.cloud_scale),
        cloud_pos_density1=_color(
            frame.get("cloud_pos_density1"), default.cloud_pos_density1
        ),
        cloud_pos_density2=_color(
            frame.get("cloud_pos_density2"), default.cloud_pos_density2
        ),
        cloud_scroll_rate=_vec2(
            frame.get("cloud_scroll_rate"), default.cloud_scroll_rate
        ),
        cloud_variance=_float(frame.get("cloud_variance"), default.cloud_variance),
        sunlight_color=_color(frame.get("sunlight_color"), default.sunlight_color),
        gamma=_float(frame.get("gamma"), default.gamma),
        max_y=_float(frame.get("max_y"), default.max_y),
        star_brightness=_float(frame.get("star_brightness"), default.star_brightness),
        sun_scale=_float(frame.get("sun_scale"), default.sun_scale),
        moon_scale=_float(frame.get("moon_scale"), default.moon_scale),
        moon_brightness=_float(frame.get("moon_brightness"), default.moon_brightness),
        glow=_color(frame.get("glow"), default.glow),
        sun_rotation=_quat(frame.get("sun_rotation"), default.sun_rotation),
        moon_rotation=_quat(frame.get("moon_rotation"), default.moon_rotation),
        cloud_id=str(frame.get("cloud_id") or ""),
        sun_id=str(frame.get("sun_id") or ""),
        moon_id=str(frame.get("moon_id") or ""),
    )


def _sample(track, day_fraction, blend, fallback):
    """Interpolate a track at a point in the day.

    The day wraps, so the span between the last keyframe and the first runs
    *through* midnight and is shorter than the gap between their numbers. A
    single-keyframe track -- which is what water is here -- is that keyframe
    all day.
    """
    if not track:
        return fallback
    if len(track) == 1:
        return track[0][1]
    where = day_fraction % 1.0
    previous = track[-1]
    following = track[0]
    for index, entry in enumerate(track):
        if entry[0] <= where:
            previous = entry
            following = track[(index + 1) % len(track)]
    span = (following[0] - previous[0]) % 1.0
    if span <= 0.0:
        return previous[1]
    return blend(previous[1], following[1], ((where - previous[0]) % 1.0) / span)


def _blend_water(a: WaterSettings, b: WaterSettings, t: float) -> WaterSettings:
    return WaterSettings(
        fog_color=_lerp3(a.fog_color, b.fog_color, t),
        fog_density=_lerp(a.fog_density, b.fog_density, t),
        underwater_fog_mod=_lerp(a.underwater_fog_mod, b.underwater_fog_mod, t),
        blur_multiplier=_lerp(a.blur_multiplier, b.blur_multiplier, t),
        fresnel_offset=_lerp(a.fresnel_offset, b.fresnel_offset, t),
        fresnel_scale=_lerp(a.fresnel_scale, b.fresnel_scale, t),
        scale_above=_lerp(a.scale_above, b.scale_above, t),
        scale_below=_lerp(a.scale_below, b.scale_below, t),
        normal_scale=_lerp3(a.normal_scale, b.normal_scale, t),
        wave1_direction=_lerp2(a.wave1_direction, b.wave1_direction, t),
        wave2_direction=_lerp2(a.wave2_direction, b.wave2_direction, t),
        # A texture cannot be half another texture: it changes at the keyframe.
        normal_map=a.normal_map if t < 0.5 else b.normal_map,
    )


def _blend_sky(a: SkySettings, b: SkySettings, t: float) -> SkySettings:
    return SkySettings(
        ambient=_lerp3(a.ambient, b.ambient, t),
        blue_density=_lerp3(a.blue_density, b.blue_density, t),
        blue_horizon=_lerp3(a.blue_horizon, b.blue_horizon, t),
        haze_density=_lerp(a.haze_density, b.haze_density, t),
        haze_horizon=_lerp(a.haze_horizon, b.haze_horizon, t),
        density_multiplier=_lerp(a.density_multiplier, b.density_multiplier, t),
        distance_multiplier=_lerp(a.distance_multiplier, b.distance_multiplier, t),
        cloud_color=_lerp3(a.cloud_color, b.cloud_color, t),
        cloud_shadow=_lerp(a.cloud_shadow, b.cloud_shadow, t),
        cloud_scale=_lerp(a.cloud_scale, b.cloud_scale, t),
        cloud_pos_density1=_lerp3(a.cloud_pos_density1, b.cloud_pos_density1, t),
        cloud_pos_density2=_lerp3(a.cloud_pos_density2, b.cloud_pos_density2, t),
        cloud_scroll_rate=_lerp2(a.cloud_scroll_rate, b.cloud_scroll_rate, t),
        cloud_variance=_lerp(a.cloud_variance, b.cloud_variance, t),
        sunlight_color=_lerp3(a.sunlight_color, b.sunlight_color, t),
        gamma=_lerp(a.gamma, b.gamma, t),
        max_y=_lerp(a.max_y, b.max_y, t),
        star_brightness=_lerp(a.star_brightness, b.star_brightness, t),
        moon_brightness=_lerp(a.moon_brightness, b.moon_brightness, t),
        sun_scale=_lerp(a.sun_scale, b.sun_scale, t),
        moon_scale=_lerp(a.moon_scale, b.moon_scale, t),
        glow=_lerp3(a.glow, b.glow, t),
        sun_rotation=_slerp(a.sun_rotation, b.sun_rotation, t),
        moon_rotation=_slerp(a.moon_rotation, b.moon_rotation, t),
        cloud_id=a.cloud_id if t < 0.5 else b.cloud_id,
        sun_id=a.sun_id if t < 0.5 else b.sun_id,
        moon_id=a.moon_id if t < 0.5 else b.moon_id,
    )


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp2(a: Vec2, b: Vec2, t: float) -> Vec2:
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t))


def _lerp3(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t))


def _slerp(a: Quat, b: Quat, t: float) -> Quat:
    """Turn between two orientations along the shorter arc.

    The sun swings most of the way round the sky between keyframes, and a
    straight average of two quaternions a long way apart is not a rotation at
    all -- it shrinks towards zero in the middle, so the sun would slow down,
    dim, and speed up again once per keyframe.
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    if dot < 0.0:
        # Every rotation has two quaternions; take the one that goes the short
        # way round, or the sun sets by travelling backwards over the pole.
        b = (-b[0], -b[1], -b[2], -b[3])
        dot = -dot
    if dot > 0.9995:
        blended = tuple(_lerp(x, y, t) for x, y in zip(a, b, strict=True))
        return _normalized(blended)
    theta = math.acos(max(-1.0, min(1.0, dot)))
    sin_theta = math.sin(theta)
    first = math.sin((1.0 - t) * theta) / sin_theta
    second = math.sin(t * theta) / sin_theta
    return (
        a[0] * first + b[0] * second,
        a[1] * first + b[1] * second,
        a[2] * first + b[2] * second,
        a[3] * first + b[3] * second,
    )


def _normalized(value) -> Quat:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(component / length for component in value)  # type: ignore[return-value]


def _sequence(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()


def _float(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return fallback
    return float(value)


def _vec2(value: object, fallback: Vec2) -> Vec2:
    parts = _sequence(value)
    if len(parts) < 2:
        return fallback
    return (_float(parts[0], fallback[0]), _float(parts[1], fallback[1]))


def _color(value: object, fallback: Vec3) -> Vec3:
    """The first three components, whether the document wrote three or four.

    ``sunlight_color`` arrives with a fourth number and the others do not; the
    fourth is not an opacity for anything drawn here.
    """
    parts = _sequence(value)
    if len(parts) < 3:
        return fallback
    return (
        _float(parts[0], fallback[0]),
        _float(parts[1], fallback[1]),
        _float(parts[2], fallback[2]),
    )


def _quat(value: object, fallback: Quat) -> Quat:
    parts = _sequence(value)
    if len(parts) < 4:
        return fallback
    return (
        _float(parts[0], fallback[0]),
        _float(parts[1], fallback[1]),
        _float(parts[2], fallback[2]),
        _float(parts[3], fallback[3]),
    )
