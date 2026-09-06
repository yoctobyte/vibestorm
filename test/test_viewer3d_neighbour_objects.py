"""The prims standing in the region next door.

A child circuit receives `ObjectUpdate` for the region it is dialled into --
measured, three prims in `Vibestorm North` and a circuit that was only
looking.
So the neighbour's ground arriving without its buildings is not a limit of
the protocol; it is a limit of what the scene bothers to walk.

Two things make this more than "run the same loop twice". Local ids are
assigned *per region*: object 42 next door and object 42 underfoot are
different prims, and anything that remembers an entity by its local id --
the per-frame entity cache here, the packed instance cache in the renderer --
hands one of them the other's data unless it remembers which region too. And
the neighbour's positions are in its own frame, running 0..256 like
everybody's, so they need the same offset the ground does, applied after a
child has been composed through its parent rather than before.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

ROOT_HANDLE = (256000 << 32) | 256256
NORTH_HANDLE = (256000 << 32) | 256512
EAST_HANDLE = (256256 << 32) | 256256


def _prim(local_id: int, position, *, parent_id: int = 0, pcode: int = 9, uid: int = 0):
    from vibestorm.world.models import WorldObject

    return WorldObject(
        full_id=UUID(int=uid or local_id),
        local_id=local_id,
        parent_id=parent_id,
        pcode=pcode,
        material=0,
        click_action=0,
        scale=(1.0, 1.0, 1.0),
        state=0,
        crc=0,
        update_flags=0,
        region_handle=0,
        time_dilation=0,
        object_data_size=0,
        position=position,
        rotation=(0.0, 0.0, 0.0, 1.0),
        variant="prim_basic",
        name_values={},
        texture_entry_size=0,
        texture_anim_size=0,
        data_size=0,
        text_size=0,
        media_url_size=0,
        ps_block_size=0,
        extra_params_size=0,
        extra_params_entries=(),
        default_texture_id=None,
    )


def _terse(local_id: int, position, *, is_avatar: bool = False):
    from vibestorm.world.models import TerseWorldObject

    return TerseWorldObject(
        local_id=local_id,
        state=0,
        is_avatar=is_avatar,
        region_handle=0,
        time_dilation=0,
        position=position,
        velocity=(0.0, 0.0, 0.0),
        acceleration=(0.0, 0.0, 0.0),
        rotation=(0.0, 0.0, 0.0, 1.0),
        angular_velocity=(0.0, 0.0, 0.0),
    )


def _view(*objects, terse=()):
    from vibestorm.world.models import WorldView

    view = WorldView()
    for obj in objects:
        view.objects[obj.full_id] = obj
    for one in terse:
        view.terse_objects[one.local_id] = one
    return view


class BuildingOneRegionTests(unittest.TestCase):
    """`_build_entities` on its own: the shared loop, and what the offset does."""

    def _build(self, view, **kwargs):
        from vibestorm.viewer3d.scene import _build_entities

        kwargs.setdefault("cache", {})
        kwargs.setdefault("previous_placement", {})
        return _build_entities(view, **kwargs)

    def test_without_an_offset_a_prim_stays_where_it_said_it_was(self) -> None:
        built = self._build(_view(_prim(10, (50.0, 60.0, 25.0))))
        self.assertEqual(built.objects[10].position, (50.0, 60.0, 25.0))

    def test_an_offset_moves_the_prim_into_our_frame(self) -> None:
        built = self._build(
            _view(_prim(10, (50.0, 60.0, 25.0))), offset=(0.0, 256.0)
        )
        self.assertEqual(built.objects[10].position, (50.0, 316.0, 25.0))

    def test_the_offset_does_not_touch_height(self) -> None:
        # Regions sit beside each other, never above; a z that drifted with
        # the offset would sink the neighbour into its own ground.
        built = self._build(
            _view(_prim(10, (50.0, 60.0, 25.0))), offset=(256.0, -256.0)
        )
        self.assertEqual(built.objects[10].position[2], 25.0)

    def test_avatars_go_to_their_own_dict(self) -> None:
        built = self._build(_view(_prim(20, (5.0, 5.0, 22.0), pcode=47)))
        self.assertIn(20, built.avatars)
        self.assertNotIn(20, built.objects)

    def test_a_child_is_offset_after_it_is_lifted_onto_its_parent(self) -> None:
        # The order is the whole point. A child reports where it is relative
        # to its parent; adding the offset to *that* and then composing would
        # put the offset in twice for a child and once for a root, which
        # scatters every linkset next door across half a region.
        parent = _prim(10, (50.0, 60.0, 25.0))
        child = _prim(11, (1.0, 0.0, 0.0), parent_id=10, uid=11)
        built = self._build(_view(parent, child), offset=(0.0, 256.0))
        self.assertEqual(built.objects[11].position, (51.0, 316.0, 25.0))

    def test_a_terse_only_prim_is_offset_too(self) -> None:
        built = self._build(
            _view(terse=[_terse(42, (20.0, 30.0, 25.0))]), offset=(0.0, 256.0)
        )
        self.assertEqual(built.objects[42].position, (20.0, 286.0, 25.0))

    def test_a_terse_only_avatar_is_offset_too(self) -> None:
        built = self._build(
            _view(terse=[_terse(43, (20.0, 30.0, 25.0), is_avatar=True)]),
            offset=(256.0, 0.0),
        )
        self.assertEqual(built.avatars[43].position, (276.0, 30.0, 25.0))

    def test_the_region_handle_is_stamped_on_what_it_builds(self) -> None:
        built = self._build(
            _view(_prim(10, (1.0, 1.0, 1.0)), terse=[_terse(11, (2.0, 2.0, 2.0))]),
            region_handle=NORTH_HANDLE,
        )
        self.assertEqual(built.objects[10].region_handle, NORTH_HANDLE)
        self.assertEqual(built.objects[11].region_handle, NORTH_HANDLE)

    def test_the_region_underfoot_is_handle_zero(self) -> None:
        built = self._build(_view(_prim(10, (1.0, 1.0, 1.0))))
        self.assertEqual(built.objects[10].region_handle, 0)

    def test_an_unchanged_prim_is_handed_back_rather_than_rebuilt(self) -> None:
        view = _view(_prim(10, (50.0, 60.0, 25.0)))
        first = self._build(view)
        second = self._build(view, cache=first.cache, previous_placement=first.placement)
        self.assertIs(second.objects[10], first.objects[10])

    def test_nothing_at_all_is_not_an_error(self) -> None:
        built = self._build(None)
        self.assertEqual(built.objects, {})
        self.assertEqual(built.avatars, {})


class SceneNeighbourObjectTests(unittest.TestCase):
    """What `refresh_neighbours` makes of a circuit that has prims in it."""

    @classmethod
    def setUpClass(cls) -> None:
        from vibestorm.udp.dispatch import MessageDispatcher

        cls.dispatcher = MessageDispatcher.from_repo_root(
            Path(__file__).resolve().parents[1]
        )

    def _circuit(self, handle: int, *objects, terse=()):
        """A real `NeighbourCircuit`, with a world of its own.

        Not a stand-in: the scene reads `world_view` and `offset_from` off
        one, and a stub that grew them by hand would go on passing after the
        circuit stopped having them.
        """
        from vibestorm.udp.neighbour import NeighbourCircuit

        circuit = NeighbourCircuit(
            handle=handle,
            address=("127.0.0.1", 9001),
            agent_id=UUID(int=1),
            session_id=UUID(int=2),
            circuit_code=7,
            dispatcher=self.dispatcher,
            world_view=_view(*objects, terse=terse),
        )
        return circuit

    def _session(self, circuits: dict, *, root: int = ROOT_HANDLE):
        class _Session:
            region_handle = root
            neighbours = circuits
            texture_paths: dict = {}

        return _Session()

    def _scene(self, circuits: dict):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.refresh_neighbours(self._session(circuits))
        return scene

    def test_a_prim_next_door_reaches_the_scene(self) -> None:
        scene = self._scene(
            {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (50.0, 60.0, 25.0)))}
        )
        entity = scene.neighbour_object_entities[(NORTH_HANDLE, 10)]
        self.assertEqual(entity.position, (50.0, 316.0, 25.0))

    def test_an_avatar_next_door_reaches_the_scene(self) -> None:
        scene = self._scene(
            {
                NORTH_HANDLE: self._circuit(
                    NORTH_HANDLE, _prim(20, (5.0, 5.0, 22.0), pcode=47)
                )
            }
        )
        self.assertIn((NORTH_HANDLE, 20), scene.neighbour_avatar_entities)

    def test_a_neighbour_with_no_ground_yet_still_has_prims(self) -> None:
        # The ground waits for its patches -- a flat sheet at zero metres
        # over the sea looks worse than the sea. A prim does not: it is at
        # the height it reported whether or not the hill under it has come.
        scene = self._scene(
            {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (50.0, 60.0, 25.0)))}
        )
        self.assertEqual(scene.neighbour_terrain, ())
        self.assertEqual(len(scene.neighbour_object_entities), 1)

    def test_two_regions_sharing_a_local_id_keep_both_prims(self) -> None:
        # The failure being guarded: one dict keyed by local id, and the
        # region that came second silently replaces the first one's prim.
        scene = self._scene(
            {
                NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(42, (10.0, 10.0, 25.0))),
                EAST_HANDLE: self._circuit(EAST_HANDLE, _prim(42, (10.0, 10.0, 25.0))),
            }
        )
        self.assertEqual(len(scene.neighbour_object_entities), 2)
        self.assertEqual(
            scene.neighbour_object_entities[(NORTH_HANDLE, 42)].position,
            (10.0, 266.0, 25.0),
        )
        self.assertEqual(
            scene.neighbour_object_entities[(EAST_HANDLE, 42)].position,
            (266.0, 10.0, 25.0),
        )

    def test_a_region_that_went_away_takes_its_prims_with_it(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        circuits = {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (1.0, 1.0, 1.0)))}
        scene.refresh_neighbours(self._session(circuits))
        scene.refresh_neighbours(self._session({}))
        self.assertEqual(scene.neighbour_object_entities, {})

    def test_no_session_leaves_nothing_on_screen(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.refresh_neighbours(
            self._session(
                {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (1.0, 1.0, 1.0)))}
            )
        )
        scene.refresh_neighbours(None)
        self.assertEqual(scene.neighbour_object_entities, {})

    def test_a_session_with_no_region_of_its_own_draws_no_neighbours(self) -> None:
        # There is nothing to measure the offset from until login lands.
        scene = self._scene_without_root()
        self.assertEqual(scene.neighbour_object_entities, {})

    def _scene_without_root(self):
        from vibestorm.viewer3d.scene import Scene

        class _Session:
            region_handle = None
            neighbours = {NORTH_HANDLE: object()}
            texture_paths: dict = {}

        scene = Scene()
        scene.refresh_neighbours(_Session())
        return scene

    def test_an_unchanged_prim_next_door_is_not_rebuilt(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        circuits = {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (1.0, 1.0, 1.0)))}
        session = self._session(circuits)
        scene = Scene()
        scene.refresh_neighbours(session)
        first = scene.neighbour_object_entities[(NORTH_HANDLE, 10)]
        scene.refresh_neighbours(session)
        self.assertIs(scene.neighbour_object_entities[(NORTH_HANDLE, 10)], first)

    def test_the_root_region_s_cache_is_never_handed_a_neighbour(self) -> None:
        # Both are keyed by local id. One shared cache and the `is` fast path
        # returns a prim from next door for a prim underfoot -- drawn 256 m
        # from where it stands.
        from vibestorm.viewer3d.scene import Scene

        here = _prim(42, (10.0, 10.0, 25.0))
        scene = Scene()
        scene.refresh_from_world_view(_view(here))
        scene.refresh_neighbours(
            self._session(
                {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(42, (10.0, 10.0, 25.0)))}
            )
        )
        scene.refresh_from_world_view(_view(here))
        self.assertEqual(scene.object_entities[42].position, (10.0, 10.0, 25.0))

    def test_a_region_that_moves_relative_to_us_is_rebuilt(self) -> None:
        # Walking across a border renumbers every offset at once. An entity
        # kept from before carries the old one baked into its position.
        from vibestorm.viewer3d.scene import Scene

        circuits = {NORTH_HANDLE: self._circuit(NORTH_HANDLE, _prim(10, (1.0, 1.0, 1.0)))}
        scene = Scene()
        scene.refresh_neighbours(self._session(circuits))
        scene.refresh_neighbours(self._session(circuits, root=NORTH_HANDLE))
        self.assertEqual(
            scene.neighbour_object_entities[(NORTH_HANDLE, 10)].position,
            (1.0, 1.0, 1.0),
        )


class DrawableEntityTests(unittest.TestCase):
    """What the renderer is handed, and what the flag takes away."""

    def _scene(self):
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.object_entities = {1: _entity(1)}
        scene.avatar_entities = {2: _entity(2, kind="avatar")}
        scene.neighbour_object_entities = {(NORTH_HANDLE, 1): _entity(1, handle=NORTH_HANDLE)}
        scene.neighbour_avatar_entities = {
            (NORTH_HANDLE, 2): _entity(2, kind="avatar", handle=NORTH_HANDLE)
        }
        return scene

    def test_everything_is_drawn(self) -> None:
        self.assertEqual(len(self._scene().drawable_entities()), 4)

    def test_the_count_agrees_with_the_list(self) -> None:
        scene = self._scene()
        self.assertEqual(scene.drawable_entity_count(), len(scene.drawable_entities()))

    def test_the_flag_takes_the_neighbours_away(self) -> None:
        scene = self._scene()
        scene.render_neighbours = False
        self.assertEqual(len(scene.drawable_entities()), 2)
        self.assertEqual(scene.drawable_entity_count(), 2)

    def test_the_flag_leaves_our_own_region_alone(self) -> None:
        scene = self._scene()
        scene.render_neighbours = False
        self.assertTrue(
            all(entity.region_handle == 0 for entity in scene.drawable_entities())
        )


def _entity(
    local_id: int,
    *,
    kind: str = "prim",
    handle: int = 0,
    position=(1.0, 1.0, 1.0),
    scale=(1.0, 1.0, 1.0),
):
    from vibestorm.viewer3d.scene import SceneEntity

    return SceneEntity(
        local_id=local_id,
        pcode=47 if kind == "avatar" else 9,
        kind=kind,
        position=position,
        scale=scale,
        rotation=(0.0, 0.0, 0.0, 1.0),
        rotation_z_radians=0.0,
        region_handle=handle,
    )


class InstanceCacheTests(unittest.TestCase):
    """The packed model matrix, and the id collision it used to have."""

    def setUp(self) -> None:
        try:
            import moderngl  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"moderngl unavailable: {exc}")

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        return PerspectiveRenderer(Camera3D(), ctx=None)

    def test_the_same_id_in_two_regions_packs_two_matrices(self) -> None:
        renderer = self._renderer()
        here = _entity(42, position=(10.0, 10.0, 25.0))
        there = _entity(42, handle=NORTH_HANDLE, position=(10.0, 266.0, 25.0))
        first = renderer._instance_blob(here)
        second = renderer._instance_blob(there)
        self.assertNotEqual(first, second)
        # And the first is still itself: the second must not have evicted it.
        self.assertEqual(renderer._instance_blob(here), first)

    def test_an_avatar_next_door_is_not_given_our_pose(self) -> None:
        # Poses are keyed by local id too, and a pose belonging to a prim
        # that happens to share an id would bend the wrong avatar.
        from vibestorm.viewer3d.scene import Scene

        renderer = self._renderer()
        scene = Scene()
        scene.avatar_poses = {7: {"l_upper_leg": 0.5}}
        here = _entity(7, kind="avatar")
        there = _entity(7, kind="avatar", handle=NORTH_HANDLE)
        self.assertEqual(renderer._pose_for(scene, here), {"l_upper_leg": 0.5})
        self.assertEqual(renderer._pose_for(scene, there), {})

    def test_an_unchanged_entity_is_packed_once(self) -> None:
        renderer = self._renderer()
        here = _entity(42)
        self.assertIs(renderer._instance_blob(here), renderer._instance_blob(here))


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        return moderngl.create_standalone_context(), None
    except Exception as exc:  # noqa: BLE001 - any GL failure is a skip
        return None, f"standalone GL context unavailable: {exc}"


class NeighbourObjectGLTests(unittest.TestCase):
    """Render it and look at it: is there a prim where the neighbour's is?"""

    FBO_SIZE = (64, 64)

    def setUp(self) -> None:
        ctx, err = _try_create_context()
        if ctx is None:
            self.skipTest(err)
        self.ctx = ctx
        self._color_tex = ctx.texture(self.FBO_SIZE, components=4)
        self._depth_rb = ctx.depth_renderbuffer(self.FBO_SIZE)
        self.fbo = ctx.framebuffer(
            color_attachments=[self._color_tex], depth_attachment=self._depth_rb
        )
        self.fbo.use()
        ctx.viewport = (0, 0, *self.FBO_SIZE)

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self._depth_rb.release()
        self.ctx.release()

    def _draw(self, renderer, scene) -> None:
        # The renderer does not clear the depth buffer -- the app clears the
        # frame before calling it -- so a test that renders twice into one
        # framebuffer has to, or the second frame is depth-tested against the
        # first and nothing new is ever drawn.
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0, depth=1.0)
        renderer.render_gl(scene, aspect=1.0)

    def _read_pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        data = self.fbo.read(components=4)
        width, height = self.FBO_SIZE
        offset = (((height - 1) - y) * width + x) * 4
        return tuple(data[offset : offset + 4])

    def _scene(self, *, with_neighbour: bool):
        """Nothing but sky, and a large prim 100 m north of the border."""
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.render_water = False
        scene.render_terrain = False
        if with_neighbour:
            scene.neighbour_object_entities = {
                (NORTH_HANDLE, 42): _entity(
                    42,
                    handle=NORTH_HANDLE,
                    position=(128.0, 356.0, 30.0),
                    scale=(40.0, 40.0, 40.0),
                )
            }
        return scene

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # Standing near the border, looking north across it at where the
        # neighbour's prim stands. The centre ray hits it and nothing else.
        camera = Camera3D(eye_position=(128.0, 200.0, 30.0), target=(128.0, 356.0, 30.0))
        camera.set_mode("eye")
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def test_a_prim_next_door_is_drawn(self) -> None:
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=False))
            without = self._read_pixel(32, 32)
            self._draw(renderer, self._scene(with_neighbour=True))
            with_it = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()
        self.assertNotEqual(
            without[:3], with_it[:3], "the neighbour's prim never reached the picture"
        )

    def test_the_render_flag_turns_them_off(self) -> None:
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=False))
            empty = self._read_pixel(32, 32)
            scene = self._scene(with_neighbour=True)
            scene.render_neighbours = False
            self._draw(renderer, scene)
            hidden = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()
        self.assertEqual(empty[:3], hidden[:3])

    def test_it_is_drawn_next_door_and_not_on_top_of_us(self) -> None:
        # The quiet failure: an entity built without its offset lands in the
        # region the avatar is standing in, which reads as a prim appearing
        # rather than as one being in the wrong place.
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=True))
            correct = self._read_pixel(32, 32)

            scene = self._scene(with_neighbour=True)
            scene.neighbour_object_entities = {
                (NORTH_HANDLE, 42): _entity(
                    42,
                    handle=NORTH_HANDLE,
                    position=(128.0, 100.0, 30.0),
                    scale=(40.0, 40.0, 40.0),
                )
            }
            self._draw(renderer, scene)
            behind_us = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()
        self.assertNotEqual(correct[:3], behind_us[:3])
