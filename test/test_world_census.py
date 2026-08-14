"""Tests for the region content census.

The census exists to answer one question: which decoders can this region
actually exercise? So the assertion that matters most is the negative one —
a feature with no example must be reported as absent, loudly, rather than
omitted. A silent zero reads as "fine" and is how a decoder stays unverified
for months.
"""

import unittest
from uuid import UUID

from vibestorm.world.census import (
    TRACKED_FEATURES,
    census_world,
    census_world as _census,
    format_census,
)
from vibestorm.world.permissions import PERM_ALL, PERM_COPY
from vibestorm.world.texture_entry import TextureEntry

FACE_TEX = UUID("11111111-1111-1111-1111-111111111111")
SOUND_ID = UUID("22222222-2222-2222-2222-222222222222")


class _Shape:
    def __init__(self, path_curve=0x10, profile_curve=1):
        self.path_curve = path_curve
        self.profile_curve = profile_curve


class _Props:
    def __init__(self, name="Object", masks=PERM_ALL):
        self.name = name
        self.base_mask = masks
        self.owner_mask = masks
        self.group_mask = 0
        self.everyone_mask = 0
        self.next_owner_mask = masks


class _Object:
    def __init__(self, local_id=1, pcode=9, **kwargs):
        self.local_id = local_id
        self.pcode = pcode
        self.shape = _Shape()
        self.properties_family = _Props()
        self.extra_params_entries = ()
        self.texture_entry = None
        self.hover_text = None
        self.media_url = None
        self.sound_id = None
        self.texture_animation = None
        for key, value in kwargs.items():
            setattr(self, key, value)


class _View:
    def __init__(self, *objects):
        self.objects = {UUID(int=obj.local_id): obj for obj in objects}


class CensusCountingTests(unittest.TestCase):
    def test_avatars_are_counted_apart_from_prims(self) -> None:
        census = _census(_View(_Object(1), _Object(2, pcode=47)))

        self.assertEqual(census.object_count, 1)
        self.assertEqual(census.avatar_count, 1)

    def test_avatars_do_not_pollute_the_shape_histogram(self) -> None:
        # An avatar has no prim shape; counting it would inflate whichever
        # bucket its empty shape data falls into.
        census = _census(_View(_Object(1, pcode=47)))

        self.assertEqual(sum(census.shapes.values()), 0)

    def test_shapes_are_classified(self) -> None:
        census = _census(
            _View(
                _Object(1, shape=_Shape(0x10, 1)),   # cube
                _Object(2, shape=_Shape(0x10, 0)),   # cylinder
                _Object(3, shape=_Shape(0x20, 0)),   # torus
            )
        )

        self.assertEqual(census.shapes["cube"], 1)
        self.assertEqual(census.shapes["cylinder"], 1)
        self.assertEqual(census.shapes["torus"], 1)

    def test_missing_shape_data_is_its_own_bucket(self) -> None:
        # This is the state that hid the ObjectUpdateCompressed shape bug, so
        # it must never be silently folded into "cube".
        census = _census(_View(_Object(1, shape=None)))

        self.assertEqual(census.shapes["unknown"], 1)
        self.assertEqual(census.shapes["cube"], 0)

    def test_unnamed_objects_are_listed_by_local_id(self) -> None:
        census = _census(_View(_Object(1), _Object(7, properties_family=None)))

        self.assertEqual(census.named, 1)
        self.assertEqual(census.unnamed_local_ids, [7])


class CensusFeatureTests(unittest.TestCase):
    def test_feature_records_a_count_and_an_example(self) -> None:
        census = _census(_View(_Object(42, hover_text="For sale")))

        self.assertEqual(census.features["hover text"], 1)
        self.assertEqual(census.feature_local_ids["hover text"], [42])

    def test_per_face_texture_needs_an_actual_override(self) -> None:
        plain = TextureEntry(default_texture_id=FACE_TEX)
        overridden = TextureEntry(
            default_texture_id=FACE_TEX, face_texture_ids=((0, FACE_TEX),)
        )

        self.assertEqual(
            _census(_View(_Object(1, texture_entry=plain))).features["per-face texture"], 0
        )
        self.assertEqual(
            _census(_View(_Object(1, texture_entry=overridden))).features["per-face texture"],
            1,
        )

    def test_an_empty_region_reports_every_feature_absent(self) -> None:
        census = _census(_View(_Object(1)))

        self.assertEqual(set(census.missing_features(TRACKED_FEATURES)), set(TRACKED_FEATURES))

    def test_absent_features_are_named_in_the_report(self) -> None:
        lines = format_census(_census(_View(_Object(1, hover_text="hi"))))
        absent = [line for line in lines if line.startswith("census absent=")]

        self.assertEqual(len(absent), 1)
        self.assertNotIn("hover text", absent[0])
        self.assertIn("light", absent[0])

    def test_a_region_with_everything_reports_nothing_absent(self) -> None:
        # The complement of the test above: with one prim carrying every
        # tracked feature there must be no "absent" line at all, which is the
        # state that would mean every decoder is live-verifiable here.
        import struct

        from vibestorm.udp.messages import ExtraParamEntry
        from vibestorm.world.extra_params import (
            EXTRA_PARAM_FLEXIBLE,
            EXTRA_PARAM_LIGHT,
            EXTRA_PARAM_MESH_FLAGS,
            EXTRA_PARAM_PROJECTION,
            EXTRA_PARAM_REFLECTION_PROBE,
            EXTRA_PARAM_RENDER_MATERIALS,
            EXTRA_PARAM_SCULPT,
        )

        def entry(param_type, data):
            return ExtraParamEntry(param_type=param_type, param_in_use=True, param_data=data)

        from vibestorm.world.texture_anim import decode_texture_animation

        everything = _Object(
            1,
            texture_animation=decode_texture_animation(
                bytes([0x01, 0xFF, 4, 2]) + struct.pack("<fff", 0.0, 8.0, 1.0)
            ),
            hover_text="hi",
            media_url="http://example.invalid/",
            sound_id=SOUND_ID,
            texture_entry=TextureEntry(
                default_texture_id=FACE_TEX, face_texture_ids=((0, FACE_TEX),)
            ),
            extra_params_entries=(
                entry(EXTRA_PARAM_FLEXIBLE, bytes(4) + struct.pack("<fff", 0, 0, 0)),
                entry(EXTRA_PARAM_LIGHT, bytes(4) + struct.pack("<fff", 1, 1, 1)),
                entry(EXTRA_PARAM_PROJECTION, FACE_TEX.bytes + struct.pack("<fff", 1, 1, 1)),
                entry(EXTRA_PARAM_REFLECTION_PROBE, struct.pack("<ff", 0.5, 1.0) + bytes([0])),
                entry(EXTRA_PARAM_RENDER_MATERIALS, bytes([1, 0]) + FACE_TEX.bytes),
                entry(EXTRA_PARAM_MESH_FLAGS, struct.pack("<I", 1)),
                entry(EXTRA_PARAM_SCULPT, FACE_TEX.bytes + bytes([1])),
            ),
        )

        # Sculpt and mesh asset are separate features on purpose, so the
        # "everything" region needs one of each.
        mesh_prim = _Object(
            2,
            extra_params_entries=(
                entry(EXTRA_PARAM_SCULPT, FACE_TEX.bytes + bytes([5])),
            ),
        )

        census = _census(_View(everything, mesh_prim))
        lines = format_census(census)

        self.assertEqual(
            census.missing_features(TRACKED_FEATURES),
            (),
            f"features still missing: {census.missing_features(TRACKED_FEATURES)}",
        )
        self.assertFalse([line for line in lines if line.startswith("census absent=")])


class CensusSculptTests(unittest.TestCase):
    """Sculpts and meshes share one ExtraParams block but two fetch paths.

    A sculpt map comes down through GetTexture; an authored mesh asset through
    GetMesh. "sculpt or mesh=3" does not say which pipeline a region exercises,
    so the census names the kind and the asset id.
    """

    def _sculpt_object(self, sculpt_type: int, local_id: int = 5):
        from vibestorm.udp.messages import ExtraParamEntry
        from vibestorm.world.extra_params import EXTRA_PARAM_SCULPT

        return _Object(
            local_id,
            extra_params_entries=(
                ExtraParamEntry(
                    param_type=EXTRA_PARAM_SCULPT,
                    param_in_use=True,
                    param_data=FACE_TEX.bytes + bytes([sculpt_type]),
                ),
            ),
        )

    def test_sculpt_types_are_named(self) -> None:
        census = _census(
            _View(
                self._sculpt_object(1, local_id=1),
                self._sculpt_object(2, local_id=2),
                self._sculpt_object(4, local_id=4),
            )
        )
        kinds = {kind for _, kind, _ in census.sculpt_assets}

        self.assertEqual(kinds, {"sculpt:sphere", "sculpt:torus", "sculpt:cylinder"})

    def test_type_5_is_reported_as_a_mesh_not_a_sculpt(self) -> None:
        # The distinction that decides which capability fetches the asset.
        census = _census(_View(self._sculpt_object(5)))

        self.assertEqual(census.sculpt_assets[0][1], "mesh")

    def test_asset_id_is_reported_so_a_404_can_be_traced(self) -> None:
        census = _census(_View(self._sculpt_object(1)))

        self.assertEqual(census.sculpt_assets[0][2], str(FACE_TEX))

    def test_unknown_sculpt_type_keeps_its_number(self) -> None:
        census = _census(_View(self._sculpt_object(7)))

        self.assertIn("7", census.sculpt_assets[0][1])

    def test_plain_prims_contribute_nothing(self) -> None:
        self.assertEqual(_census(_View(_Object(1))).sculpt_assets, [])

    def test_sculpts_do_not_satisfy_the_mesh_asset_feature(self) -> None:
        # The bug this split fixes: a region with three sculpts and no meshes
        # reported a non-zero "sculpt or mesh" count, so the GetMesh pipeline's
        # complete lack of live coverage never showed up in `absent=`.
        census = _census(_View(self._sculpt_object(1), self._sculpt_object(2, 6)))

        self.assertEqual(census.features["sculpt"], 2)
        self.assertEqual(census.features.get("mesh asset", 0), 0)
        self.assertIn("mesh asset", census.missing_features(TRACKED_FEATURES))
        self.assertNotIn("sculpt", census.missing_features(TRACKED_FEATURES))

    def test_a_mesh_asset_satisfies_only_its_own_feature(self) -> None:
        census = _census(_View(self._sculpt_object(5)))

        self.assertEqual(census.features["mesh asset"], 1)
        self.assertIn("sculpt", census.missing_features(TRACKED_FEATURES))


class CensusPermissionTests(unittest.TestCase):
    def test_permission_shapes_are_grouped_and_counted(self) -> None:
        census = _census(_View(_Object(1), _Object(2)))

        self.assertEqual(census.permissions[("base", "copy, modify, transfer, move")], 2)
        self.assertEqual(census.permissions[("group", "none")], 2)

    def test_unknown_permission_bits_are_surfaced(self) -> None:
        census = _census(
            _View(_Object(1, properties_family=_Props(masks=PERM_COPY | (1 << 17))))
        )

        self.assertTrue(census.unknown_permission_bits)

    def test_clean_masks_report_none_rather_than_saying_nothing(self) -> None:
        lines = format_census(_census(_View(_Object(1))))

        self.assertIn("census perms_unknown=none", lines)


class CensusFormatTests(unittest.TestCase):
    def test_report_survives_an_empty_region(self) -> None:
        lines = format_census(census_world(_View()))

        self.assertIn("census objects=0 avatars=0", lines)

    def test_example_count_is_capped(self) -> None:
        objects = [_Object(i, hover_text="x") for i in range(1, 11)]
        lines = format_census(_census(_View(*objects)), examples=2)
        row = next(line for line in lines if "feature[hover text]" in line)

        self.assertIn("=10 ", row)
        self.assertEqual(row.count(","), 1)


if __name__ == "__main__":
    unittest.main()
