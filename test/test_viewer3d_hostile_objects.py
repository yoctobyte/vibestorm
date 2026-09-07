"""Rubbish into the scene and the renderer, above where the decoders stop.

`test_decoder_fuzz.py` covers bytes that do not parse. This covers the other
half: updates that parse *perfectly* and mean something impossible. A position
of NaN, a rotation of infinity, a scale of 1e300 -- every one of those is a
well-formed message. Nothing on the local test sim sends one; the main grid is
sixteen years of content this client has never seen, and the owner's first
priority begins "without crashes".

The failure being prevented is specific, and it is not a smear. The renderer
packs each model matrix with ``struct.pack("...f", ...)``, and that raises
``OverflowError`` on a finite number too large for a 32-bit float -- inside
the draw loop, on a frame, from one prim among fifteen thousand. So the bound
is enforced where a prim can still be left out: a transform that is not a
place gets no place, exactly like one whose parent never arrived, and the
existing machinery for that carries it the rest of the way.
"""

from __future__ import annotations

import struct
import unittest
import uuid
from types import SimpleNamespace

from vibestorm.viewer3d.linkset import (
    FLOAT32_MAX,
    IDENTITY,
    is_a_place,
    resolve_world_transforms,
)
from vibestorm.viewer3d.perspective import model_matrix
from vibestorm.viewer3d.scene import Scene

NAN = float("nan")
INF = float("inf")


def _prim(
    local_id: int,
    *,
    parent_id: int = 0,
    position: object = (128.0, 128.0, 25.0),
    scale: object = (0.5, 0.5, 0.5),
    rotation: object = IDENTITY,
    pcode: int = 9,
):
    """Enough of a `WorldObject` for the scene to read, and nothing else."""
    return SimpleNamespace(
        local_id=local_id,
        pcode=pcode,
        parent_id=parent_id,
        position=position,
        scale=scale,
        rotation=rotation,
        properties_family=None,
        name_values=None,
        shape=SimpleNamespace(path_curve=16, profile_curve=1),
        extra_params_entries=(),
        default_texture_id=None,
        texture_entry=None,
        hover_text=None,
        hover_text_color=None,
    )


def _world(*prims):
    return SimpleNamespace(
        objects={uuid.uuid4(): prim for prim in prims},
        terse_objects={},
        region=None,
        coarse_agents=(),
        local_id_to_full_id={},
        latest_sim_stats=None,
        environment=None,
        object_properties={},
        agent_presences={},
    )


def _drawn(scene: Scene) -> set[int]:
    return set(scene.object_entities) | set(scene.avatar_entities)


class IsAPlaceTests(unittest.TestCase):
    def test_an_ordinary_transform_is_a_place(self) -> None:
        self.assertTrue(is_a_place(((128.0, 128.0, 25.0), IDENTITY)))

    def test_a_nan_anywhere_in_it_is_not(self) -> None:
        for index in range(3):
            position = [0.0, 0.0, 0.0]
            position[index] = NAN
            self.assertFalse(is_a_place((tuple(position), IDENTITY)), position)
        for index in range(4):
            rotation = [0.0, 0.0, 0.0, 1.0]
            rotation[index] = NAN
            self.assertFalse(is_a_place(((0.0, 0.0, 0.0), tuple(rotation))), rotation)

    def test_an_infinity_is_not(self) -> None:
        self.assertFalse(is_a_place(((INF, 0.0, 0.0), IDENTITY)))
        self.assertFalse(is_a_place(((-INF, 0.0, 0.0), IDENTITY)))

    def test_the_bound_is_where_a_float32_stops(self) -> None:
        # And it is exactly there, because the number just past it is the one
        # that raises rather than rounds.
        self.assertTrue(is_a_place(((FLOAT32_MAX, 0.0, 0.0), IDENTITY)))
        self.assertFalse(is_a_place(((FLOAT32_MAX * 1.001, 0.0, 0.0), IDENTITY)))
        with self.assertRaises(OverflowError):
            struct.pack("<f", FLOAT32_MAX * 1.001)


class NowhereTests(unittest.TestCase):
    """A prim with no place, and everything downstream of it."""

    def test_a_prim_that_is_nowhere_gets_no_transform(self) -> None:
        resolved = resolve_world_transforms(
            {
                1: (0, (NAN, 0.0, 0.0), IDENTITY),
                2: (0, (INF, 0.0, 0.0), IDENTITY),
                3: (0, (0.0, 0.0, 0.0), (NAN, 0.0, 0.0, 1.0)),
                4: (0, (1e300, 0.0, 0.0), IDENTITY),
                5: (0, (10.0, 10.0, 10.0), IDENTITY),
            }
        )
        self.assertEqual(set(resolved), {5})

    def test_its_children_are_nowhere_too(self) -> None:
        resolved = resolve_world_transforms(
            {
                1: (0, (NAN, 0.0, 0.0), IDENTITY),
                2: (1, (1.0, 0.0, 0.0), IDENTITY),
                3: (2, (1.0, 0.0, 0.0), IDENTITY),
            }
        )
        self.assertEqual(resolved, {})

    def test_two_places_can_compose_into_nowhere(self) -> None:
        """Both inside the bound, and their sum outside it.

        This is why the composed result is checked and not the reported
        offsets: a parent and a child that are each drawable produce a child
        that is not.
        """
        resolved = resolve_world_transforms(
            {
                1: (0, (3.0e38, 0.0, 0.0), IDENTITY),
                2: (1, (3.0e38, 0.0, 0.0), IDENTITY),
            }
        )
        self.assertEqual(set(resolved), {1})

    def test_losing_a_place_this_way_is_reported_as_a_move(self) -> None:
        """Or last frame's entity stays on screen at last frame's position.

        The caller rebuilds what `moved` names and nothing else, so a prim
        that had a transform and now has none has to be in it -- the same
        rule as a prim whose parent went away.
        """
        was = {1: ((10.0, 10.0, 10.0), IDENTITY)}
        moved: set[int] = set()
        resolved = resolve_world_transforms(
            {1: (0, (NAN, 0.0, 0.0), IDENTITY)},
            unchanged=(),
            previous=was,
            moved=moved,
        )
        self.assertEqual(resolved, {})
        self.assertIn(1, moved)


class HostileSceneTests(unittest.TestCase):
    """The same, one layer up, where it decides what gets drawn."""

    def test_a_prim_that_is_nowhere_is_not_drawn_and_its_neighbours_are(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(
            _world(
                _prim(1, position=(NAN, NAN, NAN)),
                _prim(2, position=(INF, 0.0, 0.0)),
                _prim(3, position=(1e300, 0.0, 0.0)),
                _prim(4, rotation=(NAN, 0.0, 0.0, 1.0)),
                _prim(5),
                _prim(6, position=(130.0, 128.0, 25.0)),
            )
        )
        self.assertEqual(_drawn(scene), {5, 6})

    def test_a_size_that_is_not_a_number_is_not_drawn(self) -> None:
        scene = Scene()
        scene.refresh_from_world_view(
            _world(
                _prim(1, scale=(NAN, 1.0, 1.0)),
                _prim(2, scale=(INF, 1.0, 1.0)),
                _prim(3, scale=(1e300, 1.0, 1.0)),
                _prim(4, scale=None),
                _prim(5),
            )
        )
        self.assertEqual(_drawn(scene), {5})

    def test_a_degenerate_or_mirrored_size_is_still_drawn(self) -> None:
        """Nothing and inside-out are pictures. They are not crashes.

        A zero scale draws no pixels and a negative one draws its faces the
        wrong way round; both are legitimate things for a build to contain,
        and leaving them out would be the client deciding what content is
        allowed.
        """
        scene = Scene()
        scene.refresh_from_world_view(
            _world(
                _prim(1, scale=(0.0, 0.0, 0.0)),
                _prim(2, scale=(-1.0, -1.0, -1.0)),
            )
        )
        self.assertEqual(_drawn(scene), {1, 2})

    def test_a_terse_placeholder_that_is_nowhere_is_not_drawn(self) -> None:
        """The third door into the same room.

        `ImprovedTerseObjectUpdate` carries raw floats and no parent id, so a
        terse-only prim is neither composed by the resolve nor built by the
        loop that gates full updates -- it gets a placeholder of its own, into
        the same instance buffer.
        """
        world = _world(_prim(9))
        world.terse_objects = {
            1: SimpleNamespace(
                local_id=1, position=(NAN, 0.0, 0.0), rotation=IDENTITY, is_avatar=False
            ),
            2: SimpleNamespace(
                local_id=2, position=(1e300, 0.0, 0.0), rotation=IDENTITY, is_avatar=False
            ),
            3: SimpleNamespace(
                local_id=3, position=(0.0, 0.0, 0.0), rotation=(INF, 0.0, 0.0, 1.0),
                is_avatar=True,
            ),
            4: SimpleNamespace(
                local_id=4, position=(20.0, 20.0, 20.0), rotation=IDENTITY, is_avatar=False
            ),
        }
        scene = Scene()
        scene.refresh_from_world_view(world)
        self.assertEqual(_drawn(scene), {4, 9})

    def test_everything_the_scene_draws_survives_the_pack(self) -> None:
        """The end-to-end assertion: no entity can raise in the draw loop.

        `_instance_blob` packs `model_matrix(position, scale, rotation)` as
        32-bit floats for every entity, every frame. One that raises there
        takes the frame, and the next one, and the viewer.
        """
        world = _world(
            _prim(1, position=(NAN, NAN, NAN)),
            _prim(2, position=(INF, -INF, 0.0)),
            _prim(3, position=(1e300, -1e300, 1e300)),
            _prim(4, scale=(1e300, 1e300, 1e300)),
            _prim(5, scale=(NAN, NAN, NAN)),
            _prim(6, rotation=(INF, NAN, 0.0, 0.0)),
            _prim(7, position=(3.0e38, 0.0, 0.0), scale=(3.0e38, 1.0, 1.0)),
            _prim(8, parent_id=1, position=(1.0, 0.0, 0.0)),
            _prim(9),
        )
        # All three doors in one frame: full updates, a linkset child hanging
        # off one of them, and terse-only placeholders.
        world.terse_objects = {
            10: SimpleNamespace(
                local_id=10, position=(NAN, INF, -1e300), rotation=(NAN, NAN, NAN, NAN),
                is_avatar=False,
            ),
            11: SimpleNamespace(
                local_id=11, position=(30.0, 30.0, 30.0), rotation=IDENTITY, is_avatar=True
            ),
        }
        scene = Scene()
        scene.refresh_from_world_view(world)
        self.assertIn(9, _drawn(scene))
        self.assertIn(11, _drawn(scene))
        self.assertNotIn(10, _drawn(scene))
        for entity in list(scene.object_entities.values()) + list(
            scene.avatar_entities.values()
        ):
            rotation = entity.rotation if entity.rotation is not None else IDENTITY
            struct.pack(
                "19f",
                *model_matrix(entity.position, entity.scale, rotation),
                0.0,
                0.0,
                0.0,
            )
