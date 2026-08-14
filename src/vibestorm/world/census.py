"""A structured inventory of everything a live region actually contains.

Written because the same ad-hoc script kept getting rewritten: "does this
region have a prim with a per-face texture / hover text / a light / an
attached sound?" That question drives almost every verification decision —
a decoder is only live-verified if the sim produces content that reaches it —
and answering it by hand each time both wastes effort and makes the answers
uncomparable between sessions.

Pure functions over a ``WorldView``. No network, no rendering; the CLI's
``world-census`` command supplies the view.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from vibestorm.world.extra_params import decode_extra_params
from vibestorm.world.permissions import decode_permissions

# Feature name -> a predicate over one WorldObject. Ordered so the report
# reads from "most prims have this" to "this region has none".
_PERMISSION_FIELDS = (
    ("base", "base_mask"),
    ("owner", "owner_mask"),
    ("group", "group_mask"),
    ("everyone", "everyone_mask"),
    ("next owner", "next_owner_mask"),
)


@dataclass(slots=True)
class WorldCensus:
    """What a region contains, grouped by the questions worth asking of it."""

    object_count: int = 0
    avatar_count: int = 0
    shapes: Counter[str] = field(default_factory=Counter)
    named: int = 0
    unnamed_local_ids: list[int] = field(default_factory=list)
    features: Counter[str] = field(default_factory=Counter)
    feature_local_ids: dict[str, list[int]] = field(default_factory=dict)
    permissions: Counter[tuple[str, str]] = field(default_factory=Counter)
    unknown_permission_bits: Counter[str] = field(default_factory=Counter)
    # (path_curve, profile_curve) pairs classify_prim_shape did not recognise.
    # "unclassified" on its own is not actionable; the curves are.
    unclassified_curves: Counter[tuple[int, int]] = field(default_factory=Counter)
    # (local_id, sculpt kind, asset uuid) per sculpt/mesh prim. Sculpts and
    # meshes ride the same ExtraParams block but fetch through different
    # capabilities — GetTexture for a sculpt map, GetMesh for a mesh asset —
    # so "sculpt or mesh=3" does not say which pipeline is being exercised.
    sculpt_assets: list[tuple[int, str, str]] = field(default_factory=list)

    def missing_features(self, expected: tuple[str, ...]) -> tuple[str, ...]:
        """Features with no example in this region.

        These are exactly the decoders that cannot be live-verified here, so
        the report calls them out rather than leaving a silent zero.
        """
        return tuple(name for name in expected if not self.features.get(name))


#: Every feature the census looks for. A zero here is a verification gap,
#: not a bug — it means the region contains nothing that exercises the path.
TRACKED_FEATURES: tuple[str, ...] = (
    "hover text",
    "media url",
    "attached sound",
    "per-face texture",
    "flexi",
    "light",
    "projector",
    "reflection probe",
    "render materials",
    "mesh flags",
    # Split deliberately: a sculpt map is fetched through GetTexture and an
    # authored mesh asset through GetMesh. Counting them together let a region
    # with three sculpts and no meshes report a non-zero total, which is the
    # exact silent-zero this report exists to prevent.
    "sculpt",
    "mesh asset",
    "texture animation",
)

PCODE_AVATAR = 47

#: Sculpt type (low nibble of the sculpt ExtraParams byte) to its name.
#: Type 5 is not a sculpt at all — it marks an authored mesh asset riding the
#: same block, which is why it fetches through GetMesh instead of GetTexture.
SCULPT_TYPE_NAMES: dict[int, str] = {
    1: "sculpt:sphere",
    2: "sculpt:torus",
    3: "sculpt:plane",
    4: "sculpt:cylinder",
    5: "mesh",
}


def _shape_name(world_object: object) -> str:
    from vibestorm.viewer3d.scene import classify_prim_shape, decode_sculpt_mesh_hint

    hint = decode_sculpt_mesh_hint(getattr(world_object, "extra_params_entries", ()))
    if hint is not None:
        return hint.shape
    shape_data = getattr(world_object, "shape", None)
    if shape_data is None:
        return "unknown"
    return classify_prim_shape(shape_data.path_curve, shape_data.profile_curve) or "unclassified"


def _sculpt_hint(world_object: object) -> tuple[str, str] | None:
    """The sculpt kind and asset id of a sculpt/mesh prim, if it is one."""
    from vibestorm.viewer3d.scene import EXTRA_PARAM_SCULPT

    for entry in getattr(world_object, "extra_params_entries", ()) or ():
        if getattr(entry, "param_type", None) != EXTRA_PARAM_SCULPT:
            continue
        if not getattr(entry, "param_in_use", True):
            continue
        data = getattr(entry, "param_data", b"") or b""
        if len(data) < 17:
            continue
        from uuid import UUID

        sculpt_type = data[16] & 0x0F
        kind = SCULPT_TYPE_NAMES.get(sculpt_type, f"sculpt:unknown({sculpt_type})")
        return kind, str(UUID(bytes=bytes(data[:16])))
    return None


def _features_of(world_object: object) -> list[str]:
    found: list[str] = []
    if getattr(world_object, "hover_text", None):
        found.append("hover text")
    if getattr(world_object, "media_url", None):
        found.append("media url")
    if getattr(world_object, "sound_id", None):
        found.append("attached sound")
    if getattr(world_object, "texture_animation", None) is not None:
        found.append("texture animation")

    entry = getattr(world_object, "texture_entry", None)
    if entry is not None and getattr(entry, "face_texture_ids", ()):
        found.append("per-face texture")

    entries = getattr(world_object, "extra_params_entries", ())
    decoded = decode_extra_params(entries)
    for name, attribute in (
        ("flexi", "flexible"),
        ("light", "light"),
        ("projector", "projection"),
        ("reflection probe", "reflection_probe"),
        ("render materials", "render_materials"),
    ):
        if getattr(decoded, attribute, None) is not None:
            found.append(name)
    if decoded.mesh_flags is not None:
        found.append("mesh flags")

    hint = _sculpt_hint(world_object)
    if hint is not None:
        found.append("mesh asset" if hint[0] == "mesh" else "sculpt")
    return found


def census_world(world_view: object) -> WorldCensus:
    """Walk a ``WorldView`` and count what it holds."""
    census = WorldCensus()
    for world_object in getattr(world_view, "objects", {}).values():
        pcode = getattr(world_object, "pcode", 0)
        local_id = getattr(world_object, "local_id", 0)
        if pcode == PCODE_AVATAR:
            census.avatar_count += 1
        else:
            census.object_count += 1
            shape = _shape_name(world_object)
            census.shapes[shape] += 1
            if shape == "unclassified":
                shape_data = getattr(world_object, "shape", None)
                census.unclassified_curves[
                    (shape_data.path_curve, shape_data.profile_curve)
                ] += 1

        properties = getattr(world_object, "properties_family", None)
        name = getattr(properties, "name", None) if properties is not None else None
        if name:
            census.named += 1
        else:
            census.unnamed_local_ids.append(local_id)

        hint = _sculpt_hint(world_object)
        if hint is not None:
            census.sculpt_assets.append((local_id, *hint))

        for feature in _features_of(world_object):
            census.features[feature] += 1
            census.feature_local_ids.setdefault(feature, []).append(local_id)

        if properties is not None:
            for label, attribute in _PERMISSION_FIELDS:
                decoded = decode_permissions(getattr(properties, attribute, 0) or 0)
                census.permissions[(label, decoded.describe())] += 1
                if decoded.unknown_bits:
                    census.unknown_permission_bits[f"{label} {decoded.unknown_bits:#010x}"] += 1
    return census


def format_census(census: WorldCensus, *, examples: int = 3) -> list[str]:
    """Render a census as report lines.

    Features with no example are listed explicitly under "absent": a decoder
    with nothing to decode is the single most useful thing this report says,
    and a silent omission reads as "fine".
    """
    lines = [
        f"census objects={census.object_count} avatars={census.avatar_count}",
        f"census named={census.named} unnamed={len(census.unnamed_local_ids)}",
    ]
    if census.unnamed_local_ids:
        shown = ", ".join(str(i) for i in census.unnamed_local_ids[:examples])
        lines.append(f"census unnamed_local_ids={shown}")

    for shape, count in sorted(census.shapes.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"census shape[{shape}]={count}")

    for (path_curve, profile_curve), count in sorted(census.unclassified_curves.items()):
        lines.append(
            f"census unclassified[path={path_curve:#04x} "
            f"profile={profile_curve:#04x}]={count}"
        )

    for local_id, kind, asset_id in census.sculpt_assets:
        lines.append(f"census sculpt[{kind}] local_id={local_id} asset={asset_id}")

    for feature in TRACKED_FEATURES:
        count = census.features.get(feature, 0)
        if not count:
            continue
        ids = census.feature_local_ids.get(feature, [])
        shown = ", ".join(str(i) for i in ids[:examples])
        lines.append(f"census feature[{feature}]={count} local_ids={shown}")

    absent = census.missing_features(TRACKED_FEATURES)
    if absent:
        lines.append(f"census absent={', '.join(absent)}")

    for (label, description), count in sorted(census.permissions.items()):
        lines.append(f"census perms[{label}]={description} x{count}")
    if census.unknown_permission_bits:
        for description, count in sorted(census.unknown_permission_bits.items()):
            lines.append(f"census perms_unknown[{description}]={count}")
    else:
        lines.append("census perms_unknown=none")
    return lines


__all__ = ["TRACKED_FEATURES", "WorldCensus", "census_world", "format_census"]
