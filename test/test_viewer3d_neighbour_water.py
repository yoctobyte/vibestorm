"""The sea, where two regions disagree about where it is.

Water height is announced per region, in each region's own `RegionHandshake`,
and the plane the renderer draws is 2304 m across -- nine regions wide. So a
neighbour whose sea sits a metre below ours has our water drawn a metre up
its beach, and one whose sea sits well below ours can have its island drawn
looking sunk. The height was already being parsed off the neighbour's
handshake and put on the circuit; nothing downstream had ever read it.

The fix cuts the plane along the region edges that disagree and draws each
piece at the height its own region asked for. It is deliberately *not* done
for a region whose ground has not arrived: over open sea a lone rectangle at
a slightly different level is a worse picture than the seam it removes.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

ROOT_HANDLE = (256000 << 32) | 256256
NORTH_HANDLE = (256000 << 32) | 256512


def _apply_flat_patch(heightmap, patch_x: int, patch_y: int) -> None:
    """One 16x16 patch of flat ground, so `patch_count` stops being zero."""
    from vibestorm.world.terrain import HeightPatch, PatchHeader

    heightmap.apply_patch(
        HeightPatch(
            header=PatchHeader(
                quant_wbits=0, dc_offset=0.0, range=1, patch_x=patch_x, patch_y=patch_y
            ),
            heights=(0.0,) * 256,
        )
    )


def _terrain(handle: int, offset, water_height, *, patches: int = 1):
    """A neighbour's ground, with as much of a heightmap as the caller wants.

    `refresh_neighbours` only builds a `NeighbourTerrain` once patches have
    landed, so a test that wants one with no ground has to say so.
    """
    from vibestorm.viewer3d.scene import NeighbourTerrain
    from vibestorm.world.terrain import RegionHeightmap

    heightmap = RegionHeightmap()
    for patch in range(patches):
        _apply_flat_patch(heightmap, patch % 16, patch // 16)
    return NeighbourTerrain(
        handle=handle,
        offset=offset,
        heightmap=heightmap,
        water_height=water_height,
    )


def _scene(*terrains, water_height: float = 20.0, render_neighbours: bool = True):
    from vibestorm.viewer3d.scene import Scene

    scene = Scene()
    scene.water_height = water_height
    scene.render_neighbours = render_neighbours
    scene.neighbour_terrain = tuple(terrains)
    return scene


class WaterQuadTests(unittest.TestCase):
    """What rectangles the sea is cut into, and at what heights."""

    def _quads(self, *args, **kwargs):
        from vibestorm.viewer3d.perspective import _water_quads

        return _water_quads(_scene(*args, **kwargs))

    def test_one_region_is_one_rectangle(self) -> None:
        from vibestorm.viewer3d.perspective import (
            REGION_GROUND_SIZE_M,
            VOID_WATER_EXTENT_M,
        )

        quads = self._quads()
        self.assertEqual(
            quads,
            (
                (
                    -VOID_WATER_EXTENT_M,
                    -VOID_WATER_EXTENT_M,
                    REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M,
                    REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M,
                    20.0,
                ),
            ),
        )

    def test_a_neighbour_at_our_own_level_costs_nothing(self) -> None:
        # The common case by far, and the one that must not turn a single
        # rectangle into nine for no visible difference.
        quads = self._quads(_terrain(NORTH_HANDLE, (0.0, 256.0), 20.0))
        self.assertEqual(len(quads), 1)

    def test_a_neighbour_with_a_lower_sea_gets_its_own_rectangle(self) -> None:
        quads = self._quads(_terrain(NORTH_HANDLE, (0.0, 256.0), 15.0))
        north = [q for q in quads if q[4] == 15.0]
        self.assertEqual(north, [(0.0, 256.0, 256.0, 512.0, 15.0)])

    def test_everything_outside_it_stays_at_ours(self) -> None:
        quads = self._quads(_terrain(NORTH_HANDLE, (0.0, 256.0), 15.0))
        self.assertEqual({q[4] for q in quads}, {20.0, 15.0})
        self.assertEqual(len([q for q in quads if q[4] == 15.0]), 1)

    def test_the_pieces_tile_the_whole_plane(self) -> None:
        # Area is the cheap way to say both "no gap" and "no overlap": the
        # rectangles are axis-aligned and the total can only come out right
        # if they meet exactly.
        from vibestorm.viewer3d.perspective import (
            REGION_GROUND_SIZE_M,
            VOID_WATER_EXTENT_M,
        )

        quads = self._quads(
            _terrain(NORTH_HANDLE, (0.0, 256.0), 15.0),
            _terrain(ROOT_HANDLE + 1, (-256.0, 0.0), 12.0),
        )
        area = sum((q[2] - q[0]) * (q[3] - q[1]) for q in quads)
        side = REGION_GROUND_SIZE_M + 2 * VOID_WATER_EXTENT_M
        self.assertAlmostEqual(area, side * side)

    def test_the_region_underfoot_keeps_its_own_level(self) -> None:
        # The cut runs across the whole plane, so the cell the avatar is
        # standing in has to come back out at our height and not a
        # neighbour's.
        quads = self._quads(_terrain(NORTH_HANDLE, (0.0, 256.0), 15.0))
        underfoot = [q for q in quads if q[0] <= 128.0 <= q[2] and q[1] <= 128.0 <= q[3]]
        self.assertEqual([q[4] for q in underfoot], [20.0])

    def test_two_neighbours_each_get_their_own(self) -> None:
        quads = self._quads(
            _terrain(NORTH_HANDLE, (0.0, 256.0), 15.0),
            _terrain(ROOT_HANDLE + 1, (256.0, 0.0), 12.0),
        )
        heights = {(q[0], q[1]): q[4] for q in quads}
        self.assertEqual(heights[(0.0, 256.0)], 15.0)
        self.assertEqual(heights[(256.0, 0.0)], 12.0)

    def test_a_neighbour_that_never_said_is_left_alone(self) -> None:
        # `water_height` is None until that region's handshake arrives, and
        # guessing zero would drop its sea to the seabed for a frame or two.
        quads = self._quads(_terrain(NORTH_HANDLE, (0.0, 256.0), None))
        self.assertEqual(len(quads), 1)

    def test_the_neighbour_toggle_turns_the_cut_off_too(self) -> None:
        quads = self._quads(
            _terrain(NORTH_HANDLE, (0.0, 256.0), 15.0), render_neighbours=False
        )
        self.assertEqual(len(quads), 1)
        self.assertEqual(quads[0][4], 20.0)


#: Two rectangles meeting along x = 10, at 20 m and 15 m.
STEP = ((0.0, 0.0, 10.0, 10.0, 20.0), (10.0, 0.0, 20.0, 10.0, 15.0))


class WaterWallTests(unittest.TestCase):
    """The walls that close the step between two levels.

    Without them the two rectangles leave a slot, and a slot in the sea shows
    the sky through it -- which is a worse picture than the seam the levels
    were cut apart to fix.
    """

    def _walls(self, quads):
        from vibestorm.viewer3d.perspective import _water_walls

        return _water_walls(quads)

    def test_a_step_gets_one_wall(self) -> None:
        self.assertEqual(len(self._walls(STEP)), 1)

    def test_the_wall_runs_from_the_lower_level_to_the_higher(self) -> None:
        ((corners, _face),) = self._walls(STEP)
        self.assertEqual({corner[2] for corner in corners}, {15.0, 20.0})

    def test_the_wall_stands_on_the_edge_the_two_share(self) -> None:
        ((corners, _face),) = self._walls(STEP)
        self.assertEqual({corner[0] for corner in corners}, {10.0})
        self.assertEqual({corner[1] for corner in corners}, {0.0, 10.0})

    def test_the_wall_faces_sideways_and_away_from_the_higher_sea(self) -> None:
        # It is the side of a step, so its normal is horizontal -- which is
        # the whole point of carrying one: every wave term in the shader is a
        # function of world x and y, and on a vertical face that varies along
        # one axis only and draws as stripes rather than as ripples.
        #
        # STEP is the tall piece at x 0..10 and the low one at x 10..20, so
        # the wall on x = 10 faces +x, away from the tall one.
        ((_corners, face),) = self._walls(STEP)
        self.assertEqual(face, (1.0, 0.0, 0.0))

    def test_a_wall_the_other_way_round_faces_the_other_way(self) -> None:
        walls = self._walls(
            ((0.0, 0.0, 10.0, 10.0, 15.0), (10.0, 0.0, 20.0, 10.0, 20.0))
        )
        ((_corners, face),) = walls
        self.assertEqual(face, (-1.0, 0.0, 0.0))

    def test_a_wall_along_y_faces_along_y(self) -> None:
        walls = self._walls(
            ((0.0, 0.0, 10.0, 10.0, 20.0), (0.0, 10.0, 10.0, 20.0, 15.0))
        )
        ((_corners, face),) = walls
        self.assertEqual(face, (0.0, 1.0, 0.0))

    def test_every_wall_is_horizontal_whichever_way_it_faces(self) -> None:
        from vibestorm.viewer3d.perspective import _water_quads

        quads = _water_quads(_scene(_terrain(NORTH_HANDLE, (0.0, 256.0), 15.0)))
        for _corners, face in self._walls(quads):
            self.assertEqual(face[2], 0.0)
            self.assertAlmostEqual(face[0] ** 2 + face[1] ** 2, 1.0)

    def test_two_rectangles_at_one_level_need_no_wall(self) -> None:
        self.assertEqual(
            self._walls(((0.0, 0.0, 10.0, 10.0, 20.0), (10.0, 0.0, 20.0, 10.0, 20.0))),
            [],
        )

    def test_rectangles_that_do_not_touch_need_no_wall(self) -> None:
        self.assertEqual(
            self._walls(((0.0, 0.0, 10.0, 10.0, 20.0), (30.0, 0.0, 40.0, 10.0, 15.0))),
            [],
        )

    def test_rectangles_that_only_touch_at_a_corner_need_no_wall(self) -> None:
        # Sharing a single point is not an edge, and a wall of no length is
        # two degenerate triangles.
        self.assertEqual(
            self._walls(((0.0, 0.0, 10.0, 10.0, 20.0), (10.0, 10.0, 20.0, 20.0, 15.0))),
            [],
        )

    def test_a_region_next_door_is_walled_on_all_four_sides(self) -> None:
        from vibestorm.viewer3d.perspective import _water_quads

        quads = _water_quads(_scene(_terrain(NORTH_HANDLE, (0.0, 256.0), 15.0)))
        self.assertEqual(len(self._walls(quads)), 4)


class WaterMeshTests(unittest.TestCase):
    """Those rectangles as something GL can draw."""

    def test_each_face_is_four_corners_and_two_triangles(self) -> None:
        from vibestorm.viewer3d.perspective import (
            FLOATS_PER_WATER_VERTEX,
            _water_mesh,
        )

        # Two rectangles and the one wall between them, six floats a vertex.
        vertices, indices = _water_mesh(STEP)
        self.assertEqual(len(vertices), 3 * 4 * FLOATS_PER_WATER_VERTEX)
        self.assertEqual(len(indices), 3 * 6)

    def test_the_corners_sit_at_their_own_rectangle_height(self) -> None:
        from vibestorm.viewer3d.perspective import (
            FLOATS_PER_WATER_VERTEX,
            _water_mesh,
        )

        vertices, _ = _water_mesh(STEP)
        heights = vertices[2::FLOATS_PER_WATER_VERTEX]
        self.assertEqual(heights[:8], (20.0,) * 4 + (15.0,) * 4)

    def test_no_face_borrows_another_faces_corner(self) -> None:
        # Two pieces that meet along an edge sit at different heights, so a
        # shared corner would drag one of them to the other's level.
        from vibestorm.viewer3d.perspective import _water_mesh

        _, indices = _water_mesh(STEP)
        self.assertEqual(set(indices[:6]), {0, 1, 2, 3})
        self.assertEqual(set(indices[6:12]), {4, 5, 6, 7})
        self.assertEqual(set(indices[12:]), {8, 9, 10, 11})

    def test_a_flat_plane_has_nothing_to_wall(self) -> None:
        from vibestorm.viewer3d.perspective import (
            FLOATS_PER_WATER_VERTEX,
            _water_mesh,
        )

        vertices, indices = _water_mesh(((0.0, 0.0, 10.0, 10.0, 20.0),))
        self.assertEqual(len(vertices), 4 * FLOATS_PER_WATER_VERTEX)
        self.assertEqual(len(indices), 6)

    def test_the_winding_is_the_one_the_flat_plane_used(self) -> None:
        # Counter-clockwise seen from above, which is what the sea's shader
        # expects and what the single-rectangle plane always did.
        from vibestorm.viewer3d.perspective import _water_mesh

        _, indices = _water_mesh(((0.0, 0.0, 10.0, 10.0, 20.0),))
        self.assertEqual(indices, (0, 1, 2, 0, 2, 3))


class NeighbourWaterHeightTests(unittest.TestCase):
    """The height coming off the circuit and reaching the scene."""

    @classmethod
    def setUpClass(cls) -> None:
        from vibestorm.udp.dispatch import MessageDispatcher

        cls.dispatcher = MessageDispatcher.from_repo_root(
            Path(__file__).resolve().parents[1]
        )

    def _circuit(self, handle: int, water_height: float | None):
        from vibestorm.udp.neighbour import NeighbourCircuit

        circuit = NeighbourCircuit(
            handle=handle,
            address=("127.0.0.1", 9001),
            agent_id=UUID(int=1),
            session_id=UUID(int=2),
            circuit_code=7,
            dispatcher=self.dispatcher,
        )
        circuit.water_height = water_height
        _apply_flat_patch(circuit.heightmap, 0, 0)
        return circuit

    def _refresh(self, circuit):
        from vibestorm.viewer3d.scene import Scene

        class _Session:
            region_handle = ROOT_HANDLE
            neighbours = {circuit.handle: circuit}
            texture_paths: dict = {}

        scene = Scene()
        scene.refresh_neighbours(_Session())
        return scene

    def test_the_scene_carries_what_the_handshake_said(self) -> None:
        scene = self._refresh(self._circuit(NORTH_HANDLE, 15.5))
        self.assertEqual([t.water_height for t in scene.neighbour_terrain], [15.5])

    def test_a_region_that_has_not_said_yet_carries_nothing(self) -> None:
        scene = self._refresh(self._circuit(NORTH_HANDLE, None))
        self.assertEqual([t.water_height for t in scene.neighbour_terrain], [None])


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        return moderngl.create_standalone_context(), None
    except Exception as exc:  # noqa: BLE001 - any GL failure is a skip
        return None, f"standalone GL context unavailable: {exc}"


class NeighbourWaterGLTests(unittest.TestCase):
    """Render it and look: is the neighbour's sea at the neighbour's level?"""

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

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # Standing in our region, looking north and down at a point well
        # inside the neighbour's footprint.
        camera = Camera3D(eye_position=(128.0, 128.0, 60.0), target=(128.0, 384.0, 0.0))
        camera.set_mode("eye")
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def _draw(self, renderer, scene, at=(32, 32)):
        # The renderer does not clear the depth buffer.
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0, depth=1.0)
        renderer.render_gl(scene, aspect=1.0)
        data = self.fbo.read(components=4)
        width, height = self.FBO_SIZE
        x, y = at
        offset = (((height - 1) - y) * width + x) * 4
        return tuple(data[offset : offset + 4])

    def _sea_scene(self, neighbour_height):
        scene = _scene(_terrain(NORTH_HANDLE, (0.0, 256.0), neighbour_height))
        scene.render_terrain = False
        scene.water_alpha = 1.0
        return scene

    def test_a_lower_sea_next_door_changes_the_picture(self) -> None:
        renderer = self._renderer()
        try:
            level = self._draw(renderer, self._sea_scene(20.0))
            lower = self._draw(renderer, self._sea_scene(-40.0))
        finally:
            renderer.clear_caches()
        self.assertNotEqual(
            level[:3], lower[:3], "the neighbour's own sea level never reached the draw"
        )

    def test_agreeing_regions_draw_the_same_frame(self) -> None:
        renderer = self._renderer()
        try:
            alone = self._draw(renderer, self._sea_scene(None))
            agreed = self._draw(renderer, self._sea_scene(20.0))
        finally:
            renderer.clear_caches()
        self.assertEqual(alone, agreed)

    def test_our_own_sea_is_still_drawn_once_the_plane_is_cut(self) -> None:
        # The quiet failure the cut introduces. The plane stops being one
        # rectangle, but the draw call still asks for one rectangle's worth
        # of indices, so eight ninths of the sea -- our own region's cell
        # among them -- stops being drawn while the frame still changes
        # enough for a test that only looks next door to pass.
        renderer = self._renderer()
        try:
            uncut = self._draw(renderer, self._sea_scene(None), at=(32, 58))
            cut = self._draw(renderer, self._sea_scene(-40.0), at=(32, 58))
        finally:
            renderer.clear_caches()
        self.assertEqual(uncut, cut)

    def test_the_cut_up_plane_survives_being_drawn_twice(self) -> None:
        # The buffers grow when the plane is cut and are written in place
        # after that; a second frame at a different cut has to land in them
        # correctly rather than leaving the first frame's triangles behind.
        renderer = self._renderer()
        try:
            self._draw(renderer, self._sea_scene(-40.0))
            first = self._draw(renderer, self._sea_scene(-40.0))
            self._draw(renderer, self._sea_scene(20.0))
            back = self._draw(renderer, self._sea_scene(-40.0))
        finally:
            renderer.clear_caches()
        self.assertEqual(first, back)


class WaterWallGLTests(unittest.TestCase):
    """A wall of water must not be drawn in stripes.

    Every wave term in the sea's shader is a function of world x and y. On the
    sea itself that is a surface; on a wall closing the step between two sea
    levels it varies along one axis only, so the wall came out banded -- a
    thin ladder along the whole border, which from our own region reads as
    the border being broken rather than as water.

    The test is not a picture of stripes, which would need a threshold nobody
    can defend. It is what stripes *do*: they travel. Advance the waves and a
    striped wall changes; a wall that takes its own normal does not.

    And that on its own would pass for a viewer that had switched the sea's
    waves off altogether, so it is paired with the same camera looking at the
    same place with the step removed. Then there is no wall, the view is
    ordinary sea, and it has to ripple.
    """

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

    def _scene_at(self, phase, *, neighbour_height: float):
        # Our sea low and the neighbour's high, so the wall rises away from
        # the camera rather than standing behind it.
        scene = _scene(
            _terrain(NORTH_HANDLE, (0.0, 256.0), neighbour_height), water_height=5.0
        )
        scene.render_terrain = False
        scene.water_alpha = 1.0
        scene.water_phase = phase
        return scene

    def _frame(self, renderer, scene) -> bytes:
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0, depth=1.0)
        renderer.render_gl(scene, aspect=1.0)
        return self.fbo.read(components=4)

    def _renderer_facing_the_wall(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # Six metres south of the border at y = 256, level, at a height
        # between the two sea levels -- so the whole frame is the wall.
        camera = Camera3D(eye_position=(128.0, 250.0, 12.0), target=(128.0, 262.0, 12.0))
        camera.set_mode("eye")
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def test_the_wall_does_not_travel_with_the_waves(self) -> None:
        renderer = self._renderer_facing_the_wall()
        try:
            still = self._frame(renderer, self._scene_at((0.0, 0.0), neighbour_height=20.0))
            later = self._frame(renderer, self._scene_at((1.7, 2.9), neighbour_height=20.0))
        finally:
            renderer.clear_caches()
        self.assertEqual(still, later)

    def test_and_the_same_view_without_a_wall_does(self) -> None:
        # The other half. Without this, switching the sea's waves off entirely
        # would pass the test above with nothing to show for it.
        renderer = self._renderer_facing_the_wall()
        try:
            still = self._frame(renderer, self._scene_at((0.0, 0.0), neighbour_height=5.0))
            later = self._frame(renderer, self._scene_at((1.7, 2.9), neighbour_height=5.0))
        finally:
            renderer.clear_caches()
        self.assertNotEqual(still, later)

    def test_the_wall_is_still_water_rather_than_a_hole(self) -> None:
        # The cheapest way to stop a wall shimmering would be not to draw it,
        # and the slot it closes shows the *sky* through it. So the frame with
        # the step in it has to differ from one with no sea drawn at all.
        renderer = self._renderer_facing_the_wall()
        try:
            walled = self._frame(renderer, self._scene_at((0.0, 0.0), neighbour_height=20.0))
            dry = self._scene_at((0.0, 0.0), neighbour_height=20.0)
            dry.render_water = False
            without = self._frame(renderer, dry)
        finally:
            renderer.clear_caches()
        self.assertNotEqual(walled, without)

    def test_a_wall_seen_head_on_is_not_seen_at_a_grazing_angle(self) -> None:
        """The wall's normal has to be horizontal, and has to face the eye.

        Neither is checked by looking at a colour, because a plausible wrong
        colour is still a plausible colour. What checks both is the Fresnel
        term, which is the one thing in this shader that asks the normal a
        sharp question: how far off head-on am I?

        Standing square in front of a wall, the answer is "not at all", so how
        much sky the surface shows *at a grazing angle* cannot matter -- turn
        that term from nothing to everything and the frame must not move. Get
        the normal wrong in either of the two available ways and it does: a
        normal left pointing up is seen edge-on from here, and one never
        turned toward the eye points away from it. Both read as fully grazing,
        which is the opposite of the truth.
        """
        renderer = self._renderer_facing_the_wall()
        try:
            never = self._scene_at((0.0, 0.0), neighbour_height=20.0)
            never.water_fresnel = (0.0, 0.0)
            always = self._scene_at((0.0, 0.0), neighbour_height=20.0)
            always.water_fresnel = (0.0, 1.0)
            self.assertEqual(self._frame(renderer, never), self._frame(renderer, always))
        finally:
            renderer.clear_caches()

    def test_a_wall_seen_from_its_far_side_is_head_on_too(self) -> None:
        """And the same from the other side, which is the half that bites.

        A wall is built with one outward normal and nothing culls faces here,
        so it is drawn from both sides -- and from the far side that normal
        points *away* from the eye. Left alone it reads as fully grazing,
        which turns the Fresnel term inside out and reflects the ground where
        the sky should be. The camera in the test above happens to stand on
        the side the normal already faces, so it cannot see that at all.
        """
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # Inside the neighbour's footprint, looking back south at the wall.
        camera = Camera3D(eye_position=(128.0, 262.0, 12.0), target=(128.0, 250.0, 12.0))
        camera.set_mode("eye")
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            never = self._scene_at((0.0, 0.0), neighbour_height=20.0)
            never.water_fresnel = (0.0, 0.0)
            always = self._scene_at((0.0, 0.0), neighbour_height=20.0)
            always.water_fresnel = (0.0, 1.0)
            walled = self._frame(renderer, never)
            self.assertEqual(walled, self._frame(renderer, always))
            # And it really is the wall being looked at: take the step away
            # and the same pixel is a different colour entirely.
            flat = self._frame(
                renderer, self._scene_at((0.0, 0.0), neighbour_height=5.0)
            )
            self.assertNotEqual(walled, flat)
        finally:
            renderer.clear_caches()

    def test_and_a_sea_seen_along_its_surface_is(self) -> None:
        # The other half again: without it, a shader that had stopped reading
        # `water_fresnel` at all would pass the test above.
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(eye_position=(128.0, 60.0, 6.0), target=(128.0, 900.0, 5.2))
        camera.set_mode("eye")
        renderer = PerspectiveRenderer(camera, ctx=self.ctx)
        try:
            never = self._scene_at((0.0, 0.0), neighbour_height=5.0)
            never.water_fresnel = (0.0, 0.0)
            always = self._scene_at((0.0, 0.0), neighbour_height=5.0)
            always.water_fresnel = (0.0, 1.0)
            self.assertNotEqual(
                self._frame(renderer, never), self._frame(renderer, always)
            )
        finally:
            renderer.clear_caches()
