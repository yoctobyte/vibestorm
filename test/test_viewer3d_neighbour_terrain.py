"""The regions next door, in the picture.

Standing at the north edge of a region, a viewer that draws only the region
it is in shows sea where the neighbour is: the void-water sheet extends past
the border and there is nothing above it. The neighbour's heightmap does
arrive -- `NeighbourCircuit` fetches it -- so this is about the last step,
which is putting it on screen at the right place.

"The right place" is the whole difficulty. The neighbour's samples are in
*its* region coordinates, running 0..256 like everybody else's, and the
region due north sits at (0, 256) in ours. A sheet drawn without that offset
lands exactly on top of the region the avatar is standing in, which does not
look like a bug at a glance -- it looks like the ground changed colour.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def _solid_tile(color: tuple[int, int, int], size: int = 4) -> Path:
    """A small solid-colour PNG, so the loader runs end to end."""
    import tempfile

    import pygame

    surface = pygame.Surface((size, size))
    surface.fill(color)
    handle, path = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    pygame.image.save(surface, path)
    return Path(path)


def _flat_heightmap(height: float, *, revision: int = 1):
    from vibestorm.world.terrain import RegionHeightmap

    return RegionHeightmap(
        width=2,
        height=2,
        samples=[height] * 4,
        revision=revision,
        patch_keys={(0, 0)},
    )


def _ramp_heightmap(low: float, high: float, *, revision: int = 1):
    """South edge at `low`, north edge at `high`. Row-major, y increasing."""
    from vibestorm.world.terrain import RegionHeightmap

    return RegionHeightmap(
        width=2,
        height=2,
        samples=[low, low, high, high],
        revision=revision,
        patch_keys={(0, 0)},
    )


class MeshOriginTests(unittest.TestCase):
    def test_a_sheet_with_no_origin_starts_at_the_region_corner(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        vertices, _ = terrain_mesh_from_heightmap(
            (0.0, 0.0, 0.0, 0.0), width=2, height=2, size_m=256.0
        )
        self.assertEqual(tuple(vertices[0:2]), (0.0, 0.0))

    def test_an_origin_moves_the_whole_sheet(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        vertices, _ = terrain_mesh_from_heightmap(
            (0.0, 0.0, 0.0, 0.0),
            width=2,
            height=2,
            size_m=256.0,
            origin=(0.0, 256.0),
        )
        corners = [tuple(vertices[i * 5 : i * 5 + 2]) for i in range(4)]
        self.assertEqual(
            corners, [(0.0, 256.0), (256.0, 256.0), (0.0, 512.0), (256.0, 512.0)]
        )

    def test_the_texture_coordinates_do_not_move_with_it(self) -> None:
        # Each region's ground textures tile across that region from its own
        # corner. Sliding the uv with the origin would shift the tiling of
        # every neighbour by a whole region.
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        moved, _ = terrain_mesh_from_heightmap(
            (1.0, 2.0, 3.0, 4.0), width=2, height=2, origin=(-256.0, 512.0)
        )
        still, _ = terrain_mesh_from_heightmap((1.0, 2.0, 3.0, 4.0), width=2, height=2)
        self.assertEqual(
            [tuple(moved[i * 5 + 3 : i * 5 + 5]) for i in range(4)],
            [tuple(still[i * 5 + 3 : i * 5 + 5]) for i in range(4)],
        )

    def test_the_heights_are_untouched_by_the_origin(self) -> None:
        from vibestorm.viewer3d.perspective import terrain_mesh_from_heightmap

        vertices, _ = terrain_mesh_from_heightmap(
            (5.0, 6.0, 7.0, 8.0), width=2, height=2, origin=(64.0, -64.0)
        )
        self.assertEqual([vertices[i * 5 + 2] for i in range(4)], [5.0, 6.0, 7.0, 8.0])


class CoarseSampleTests(unittest.TestCase):
    def test_it_keeps_both_edges(self) -> None:
        # A grid that stopped a sample short of the far edge would leave a
        # strip of sky along the border two regions share.
        from vibestorm.viewer3d.perspective import coarse_terrain_samples

        samples = coarse_terrain_samples(_ramp_heightmap(0.0, 100.0), count=5)
        self.assertEqual(len(samples), 25)
        self.assertAlmostEqual(samples[0], 0.0, places=5)
        self.assertAlmostEqual(samples[-1], 100.0, places=5)

    def test_it_reads_the_ground_in_between(self) -> None:
        from vibestorm.viewer3d.perspective import coarse_terrain_samples

        samples = coarse_terrain_samples(_ramp_heightmap(0.0, 100.0), count=5)
        middle_row = samples[2 * 5 : 3 * 5]
        for value in middle_row:
            self.assertAlmostEqual(value, 50.0, places=4)

    def test_a_grid_of_one_is_not_a_grid(self) -> None:
        from vibestorm.viewer3d.perspective import coarse_terrain_samples

        with self.assertRaises(ValueError):
            coarse_terrain_samples(_flat_heightmap(0.0), count=1)

    def test_the_default_is_far_coarser_than_the_wire(self) -> None:
        # The point of resampling: a neighbour is never closer than a region
        # away, and there can be eight of them.
        from vibestorm.viewer3d.perspective import NEIGHBOUR_TERRAIN_SAMPLES

        self.assertLess(NEIGHBOUR_TERRAIN_SAMPLES, 256)


class SceneRefreshTests(unittest.TestCase):
    """What the scene takes from the session, and what it refuses to take."""

    def _session(self, circuits: dict):
        class _Session:
            region_handle = (256000 << 32) | 256256
            neighbours = circuits
            texture_paths: dict = {}

        return _Session()

    #: A real circuit, not a stand-in. The scene reads half a dozen
    #: attributes off one, and a stub that grew them by hand would go on
    #: passing after the circuit stopped having them.
    @classmethod
    def setUpClass(cls) -> None:
        from vibestorm.udp.dispatch import MessageDispatcher

        cls.dispatcher = MessageDispatcher.from_repo_root(
            Path(__file__).resolve().parents[1]
        )

    def _circuit(self, handle: int, heightmap, name: str = "North"):
        from uuid import UUID

        from vibestorm.udp.neighbour import NeighbourCircuit

        circuit = NeighbourCircuit(
            handle=handle,
            address=("127.0.0.1", 9001),
            agent_id=UUID(int=1),
            session_id=UUID(int=2),
            circuit_code=7,
            dispatcher=self.dispatcher,
            heightmap=heightmap,
        )
        circuit.region_name = name
        return circuit

    def test_a_neighbour_with_ground_reaches_the_scene(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        handle = (256000 << 32) | 256512
        scene = Scene()
        scene.refresh_neighbours(
            self._session({handle: self._circuit(handle, _flat_heightmap(21.0))})
        )
        self.assertEqual(len(scene.neighbour_terrain), 1)
        self.assertEqual(scene.neighbour_terrain[0].offset, (0.0, 256.0))
        self.assertEqual(scene.neighbour_terrain[0].handle, handle)

    def test_a_neighbour_whose_ground_has_not_arrived_is_not_drawn(self) -> None:
        # The circuit opens a second or two before the first patch lands.
        # Drawing it then paints a flat sheet at zero metres over the sea,
        # which looks far more broken than the sea did.
        from vibestorm.viewer3d.scene import Scene
        from vibestorm.world.terrain import RegionHeightmap

        handle = (256000 << 32) | 256512
        scene = Scene()
        scene.refresh_neighbours(
            self._session({handle: self._circuit(handle, RegionHeightmap())})
        )
        self.assertEqual(scene.neighbour_terrain, ())

    def test_no_session_means_no_neighbours(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.refresh_neighbours(None)
        self.assertEqual(scene.neighbour_terrain, ())

    def test_a_neighbour_that_went_away_is_dropped(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        handle = (256000 << 32) | 256512
        scene = Scene()
        scene.refresh_neighbours(
            self._session({handle: self._circuit(handle, _flat_heightmap(21.0))})
        )
        scene.refresh_neighbours(self._session({}))
        self.assertEqual(scene.neighbour_terrain, ())

    def test_the_region_s_own_ground_textures_reach_the_scene(self) -> None:
        # They are fetched through this region's capability and land in the
        # session's texture cache, keyed by asset id like every other
        # texture. The scene's job is only to look them up.
        from vibestorm.viewer3d.scene import Scene

        handle = (256000 << 32) | 256512
        circuit = self._circuit(handle, _flat_heightmap(21.0))
        ids = tuple(UUID(int=0xB0 + index) for index in range(4))
        circuit.terrain_detail = ids
        circuit.terrain_start_height = (1.0, 2.0, 3.0, 4.0)
        circuit.terrain_height_range = (5.0, 6.0, 7.0, 8.0)

        session = self._session({handle: circuit})
        session.texture_paths = {
            texture_id: Path(f"/tmp/ground-{index}.png")
            for index, texture_id in enumerate(ids)
        }
        scene = Scene()
        scene.refresh_neighbours(session)

        entry = scene.neighbour_terrain[0]
        self.assertEqual(
            entry.texture_paths,
            tuple(Path(f"/tmp/ground-{index}.png") for index in range(4)),
        )
        self.assertEqual(entry.start_height, (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(entry.height_range, (5.0, 6.0, 7.0, 8.0))

    def test_a_texture_that_has_not_arrived_reads_as_missing(self) -> None:
        from vibestorm.viewer3d.scene import Scene

        handle = (256000 << 32) | 256512
        circuit = self._circuit(handle, _flat_heightmap(21.0))
        circuit.terrain_detail = tuple(UUID(int=0xC0 + index) for index in range(4))
        scene = Scene()
        scene.refresh_neighbours(self._session({handle: circuit}))
        self.assertEqual(scene.neighbour_terrain[0].texture_paths, (None,) * 4)

    def test_the_heightmap_is_held_by_reference(self) -> None:
        # Patches keep arriving after the first frame that draws one, and the
        # renderer rebuilds off `revision`. A copy would freeze the neighbour
        # at whatever had arrived when it was first seen.
        from vibestorm.viewer3d.scene import Scene

        handle = (256000 << 32) | 256512
        heightmap = _flat_heightmap(21.0)
        scene = Scene()
        scene.refresh_neighbours(self._session({handle: self._circuit(handle, heightmap)}))
        self.assertIs(scene.neighbour_terrain[0].heightmap, heightmap)


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        return moderngl.create_standalone_context(), None
    except Exception as exc:  # noqa: BLE001 - any GL failure is a skip
        return None, f"standalone GL context unavailable: {exc}"


class NeighbourTerrainGLTests(unittest.TestCase):
    """Render it and look at it: is there ground where the neighbour is?"""

    FBO_SIZE = (64, 64)

    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional viewer extra
            self.skipTest(f"pygame unavailable: {exc}")
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
        self._tiles: list[Path] = []

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self._depth_rb.release()
        self.ctx.release()
        for path in self._tiles:
            path.unlink(missing_ok=True)

    def _draw(self, renderer, scene) -> None:
        # The depth buffer is not cleared by the renderer -- the app clears
        # the frame before calling it -- so a test that renders twice into
        # one framebuffer has to, or the second frame is depth-tested against
        # the first and nothing new is ever drawn.
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0, depth=1.0)
        renderer.render_gl(scene, aspect=1.0)

    def _read_pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        data = self.fbo.read(components=4)
        width, height = self.FBO_SIZE
        offset = (((height - 1) - y) * width + x) * 4
        return tuple(data[offset : offset + 4])

    def _scene(self, *, with_neighbour: bool):
        """Flat ground at zero, and a neighbour 40 m proud of it to the north."""
        from vibestorm.viewer3d.scene import NeighbourTerrain, Scene

        scene = Scene()
        scene.render_water = False
        scene.render_objects = False
        scene.terrain_heightmap = _flat_heightmap(0.0)
        if with_neighbour:
            scene.neighbour_terrain = (
                NeighbourTerrain(
                    handle=(256000 << 32) | 256512,
                    offset=(0.0, 256.0),
                    heightmap=_flat_heightmap(40.0, revision=3),
                    region_name="North",
                ),
            )
        return scene

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        # Above the north edge of the home region, looking down and north
        # across the border. The centre ray leaves the home region's ground
        # well clear of it and comes down at (128, 350) -- which is inside the
        # region next door and nowhere else, so the middle pixel is that
        # region's ground if it was drawn and sky if it was not.
        camera = Camera3D(
            eye_position=(128.0, 200.0, 100.0), target=(128.0, 350.0, 40.0)
        )
        camera.set_mode("eye")
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def test_the_region_next_door_is_drawn(self) -> None:
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=False))
            without = self._read_pixel(32, 32)
            self._draw(renderer, self._scene(with_neighbour=True))
            with_it = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()

        self.assertNotEqual(
            without[:3], with_it[:3], "the neighbour's ground never reached the picture"
        )
        # Ground, not sky: the fill shader is green-dominant and the sky
        # ahead of the camera is not.
        self.assertGreater(with_it[1], with_it[2], "what was drawn is not ground")

    def test_it_is_drawn_where_the_neighbour_is_and_not_on_top_of_us(self) -> None:
        # The failure this guards is quiet: a sheet built without its origin
        # lands on the region the avatar is standing in, which reads as the
        # ground changing colour rather than as a bug.
        from vibestorm.viewer3d.scene import NeighbourTerrain

        scene = self._scene(with_neighbour=True)
        renderer = self._renderer()
        try:
            self._draw(renderer, scene)
            correct = self._read_pixel(32, 32)

            scene.neighbour_terrain = (
                NeighbourTerrain(
                    handle=scene.neighbour_terrain[0].handle,
                    offset=(0.0, 0.0),
                    heightmap=scene.neighbour_terrain[0].heightmap,
                ),
            )
            self._draw(renderer, scene)
            underfoot = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()

        self.assertNotEqual(correct[:3], underfoot[:3])

    def test_a_neighbour_that_goes_away_takes_its_mesh_with_it(self) -> None:
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=True))
            self.assertEqual(len(renderer._neighbour_meshes), 1)
            self._draw(renderer, self._scene(with_neighbour=False))
            self.assertEqual(renderer._neighbour_meshes, {})
        finally:
            renderer.clear_caches()

    def test_the_mesh_is_not_rebuilt_while_nothing_changes(self) -> None:
        # A 65x65 sheet per neighbour, rebuilt every frame for eight of them,
        # is the kind of cost that only shows up as a lower framerate.
        renderer = self._renderer()
        scene = self._scene(with_neighbour=True)
        try:
            self._draw(renderer, scene)
            first = renderer._neighbour_meshes[scene.neighbour_terrain[0].handle]
            self._draw(renderer, scene)
            second = renderer._neighbour_meshes[scene.neighbour_terrain[0].handle]
            self.assertIs(first, second)

            scene.neighbour_terrain[0].heightmap.revision += 1
            self._draw(renderer, scene)
            self.assertIsNot(
                renderer._neighbour_meshes[scene.neighbour_terrain[0].handle], second
            )
        finally:
            renderer.clear_caches()

    def _textured_scene(self, color: tuple[int, int, int]):
        """The same neighbour, with four ground textures of one flat colour."""
        from vibestorm.viewer3d.scene import NeighbourTerrain

        scene = self._scene(with_neighbour=True)
        entry = scene.neighbour_terrain[0]
        paths = tuple(_solid_tile(color) for _ in range(4))
        self._tiles.extend(paths)
        scene.neighbour_terrain = (
            NeighbourTerrain(
                handle=entry.handle,
                offset=entry.offset,
                heightmap=entry.heightmap,
                texture_paths=paths,
                start_height=(10.0, 10.0, 10.0, 10.0),
                height_range=(60.0, 60.0, 60.0, 60.0),
            ),
        )
        return scene

    def test_a_neighbour_with_its_own_ground_textures_is_drawn_with_them(self) -> None:
        # A region's four ground textures are named in its own handshake and
        # are ordinary asset ids, so this region's GetTexture capability
        # fetches them. Until they arrive the neighbour is shaded ground; the
        # moment they do it should be textured ground, and the two do not
        # look alike.
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene(with_neighbour=True))
            shaded = self._read_pixel(32, 32)
            self._draw(renderer, self._textured_scene((220, 30, 30)))
            textured = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()

        self.assertNotEqual(shaded[:3], textured[:3])
        self.assertGreater(textured[0], textured[1], f"not the red ground: {textured}")

    def test_the_texture_set_is_shared_between_regions_that_match(self) -> None:
        # Neighbours on one grid usually share a ground palette, and eight
        # copies of the same four images is VRAM for nothing.
        renderer = self._renderer()
        try:
            self._draw(renderer, self._textured_scene((30, 30, 220)))
            self.assertEqual(len(renderer._neighbour_texture_sets), 1)
            self._draw(renderer, self._textured_scene((30, 30, 220)))
            # Different files, same colour: keyed by path, so two sets.
            self.assertEqual(len(renderer._neighbour_texture_sets), 2)
        finally:
            renderer.clear_caches()

    def test_the_same_ground_is_uploaded_once(self) -> None:
        # Frame after frame, the same four files. Re-uploading them every
        # frame is four texture uploads a frame per neighbour.
        renderer = self._renderer()
        try:
            scene = self._textured_scene((30, 30, 220))
            paths = scene.neighbour_terrain[0].texture_paths
            self._draw(renderer, scene)
            first = renderer._neighbour_texture_sets[paths]
            self._draw(renderer, scene)
            self.assertIs(renderer._neighbour_texture_sets[paths], first)
        finally:
            renderer.clear_caches()

    def test_each_region_blends_against_its_own_bands(self) -> None:
        # Two neighbours, two handshakes, two sets of elevation bands. Set
        # once for the whole pass and the second region gets the first one's
        # sand where its grass should be.
        from vibestorm.viewer3d.scene import NeighbourTerrain

        renderer = self._renderer()
        try:
            scene = self._textured_scene((220, 30, 30))
            near = scene.neighbour_terrain[0]
            far_paths = tuple(_solid_tile((30, 220, 30)) for _ in range(4))
            self._tiles.extend(far_paths)
            far = NeighbourTerrain(
                handle=near.handle + 1,
                offset=(256.0, 256.0),
                heightmap=near.heightmap,
                texture_paths=far_paths,
                start_height=(90.0, 91.0, 92.0, 93.0),
                height_range=(40.0, 41.0, 42.0, 43.0),
            )
            scene.neighbour_terrain = (near, far)
            self._draw(renderer, scene)
            program = renderer._terrain_texture_program
            # Drawn in order, so the uniform still holds the last region's.
            self.assertEqual(program["u_start_height"].value, far.start_height)
            self.assertEqual(program["u_height_range"].value, far.height_range)
        finally:
            renderer.clear_caches()

    def test_clearing_the_caches_gives_the_ground_textures_back(self) -> None:
        renderer = self._renderer()
        self._draw(renderer, self._textured_scene((220, 30, 30)))
        self.assertEqual(len(renderer._neighbour_texture_sets), 1)
        renderer.clear_caches()
        self.assertEqual(renderer._neighbour_texture_sets, {})

    def test_a_half_arrived_texture_set_falls_back_to_shading(self) -> None:
        # Three textures out of four would blend against whatever the last
        # region left in the fourth unit.
        from vibestorm.viewer3d.scene import NeighbourTerrain

        renderer = self._renderer()
        try:
            scene = self._textured_scene((220, 30, 30))
            entry = scene.neighbour_terrain[0]
            self._draw(renderer, scene)
            textured = self._read_pixel(32, 32)

            scene.neighbour_terrain = (
                NeighbourTerrain(
                    handle=entry.handle,
                    offset=entry.offset,
                    heightmap=entry.heightmap,
                    texture_paths=(*entry.texture_paths[:3], None),
                    start_height=entry.start_height,
                    height_range=entry.height_range,
                ),
            )
            self._draw(renderer, scene)
            partial = self._read_pixel(32, 32)

            self._draw(renderer, self._scene(with_neighbour=True))
            shaded = self._read_pixel(32, 32)
        finally:
            renderer.clear_caches()

        self.assertEqual(partial[:3], shaded[:3])
        self.assertNotEqual(partial[:3], textured[:3])

    def test_textures_arriving_late_do_not_rebuild_the_ground(self) -> None:
        # The heightmap arrives seconds before the textures do. Rebuilding
        # sixty-five hundred vertices because a texture finally downloaded
        # would be a stutter for nothing.
        renderer = self._renderer()
        try:
            scene = self._scene(with_neighbour=True)
            self._draw(renderer, scene)
            before = renderer._neighbour_meshes[scene.neighbour_terrain[0].handle]

            textured = self._textured_scene((30, 220, 30))
            self._draw(renderer, textured)
            after = renderer._neighbour_meshes[textured.neighbour_terrain[0].handle]
        finally:
            renderer.clear_caches()

        self.assertIs(before, after)
        self.assertEqual(after.start_height, (10.0, 10.0, 10.0, 10.0))

    def test_a_neighbour_that_moves_relative_to_us_is_rebuilt(self) -> None:
        # Region handles do not change, but the frame they are measured in
        # does: cross the border and the region you came from becomes the
        # neighbour to the south. Every offset shifts by a region while every
        # handle and every revision stays exactly as it was.
        from vibestorm.viewer3d.scene import NeighbourTerrain

        renderer = self._renderer()
        scene = self._scene(with_neighbour=True)
        entry = scene.neighbour_terrain[0]
        try:
            self._draw(renderer, scene)
            before = renderer._neighbour_meshes[entry.handle]

            scene.neighbour_terrain = (
                NeighbourTerrain(
                    handle=entry.handle,
                    offset=(0.0, -256.0),
                    heightmap=entry.heightmap,
                ),
            )
            self._draw(renderer, scene)
            after = renderer._neighbour_meshes[entry.handle]
        finally:
            renderer.clear_caches()

        self.assertIsNot(before, after)
        self.assertEqual(after.offset, (0.0, -256.0))

    def test_the_render_flag_turns_them_off(self) -> None:
        renderer = self._renderer()
        scene = self._scene(with_neighbour=True)
        try:
            self._draw(renderer, scene)
            self.assertEqual(len(renderer._neighbour_meshes), 1)
            scene.render_neighbours = False
            self._draw(renderer, scene)
            self.assertEqual(renderer._neighbour_meshes, {})
        finally:
            renderer.clear_caches()


if __name__ == "__main__":
    unittest.main()


class _RecordingBuffer:
    def __init__(self, data) -> None:
        self.data = data
        self.released = False

    def release(self) -> None:
        self.released = True


class _RecordingVertexArray:
    def __init__(self, index_buffer) -> None:
        self.index_buffer = index_buffer
        self.released = False

    def release(self) -> None:
        self.released = True


class _RecordingContext:
    """Just enough moderngl to watch what goes into the buffers."""

    def __init__(self) -> None:
        self.buffers: list[_RecordingBuffer] = []

    def buffer(self, data, **_kwargs):
        made = _RecordingBuffer(data)
        self.buffers.append(made)
        return made

    def vertex_array(self, _program, _content, *, index_buffer=None, **_kwargs):
        return _RecordingVertexArray(index_buffer)


class NeighbourSheetIndexesItsOwnVerticesTests(unittest.TestCase):
    """A sheet drawn with somebody else's indices reads off the end of it.

    A neighbour's ground is 65 samples a side; the region underfoot is 256.
    Handing the neighbour's 65x65 vertex buffer the 256x256 index array is a
    change no behavioural test noticed -- it survived a mutation battery --
    and what it does at the GPU is read four thousand vertices past the end of
    the buffer.

    The invariant is the one worth asserting whatever the numbers become:
    every index must address a vertex the sheet actually has.
    """

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        renderer = PerspectiveRenderer(Camera3D(), ctx=None)
        # The upload returns early without one, and a real program needs a GL
        # context; nothing in this path calls it.
        renderer._terrain_fill_program = object()
        renderer._terrain_texture_program = None
        return renderer

    def _scene(self):
        from vibestorm.viewer3d.scene import NeighbourTerrain, Scene

        scene = Scene()
        scene.neighbour_terrain = (
            NeighbourTerrain(
                handle=(256000 << 32) | 256512,
                offset=(0.0, 256.0),
                heightmap=_ramp_heightmap(10.0, 40.0, revision=2),
                region_name="North",
            ),
        )
        return scene

    def _upload(self):
        renderer = self._renderer()
        ctx = _RecordingContext()
        renderer._upload_neighbour_terrain(ctx, self._scene())
        return renderer, ctx

    def test_every_index_addresses_a_vertex_the_sheet_has(self):
        from vibestorm.viewer3d.perspective import NEIGHBOUR_TERRAIN_SAMPLES

        _renderer, ctx = self._upload()
        vertices, indices = (buffer.data for buffer in ctx.buffers[:2])
        self.assertEqual(indices.typecode, "I")
        vertex_count = len(vertices) // 5
        self.assertEqual(vertex_count, NEIGHBOUR_TERRAIN_SAMPLES**2)
        self.assertLess(max(indices), vertex_count)

    def test_the_indices_are_the_ones_for_its_own_grid(self):
        from vibestorm.viewer3d.perspective import (
            NEIGHBOUR_TERRAIN_SAMPLES,
            _grid_triangle_indices,
        )

        _renderer, ctx = self._upload()
        self.assertIs(
            ctx.buffers[1].data,
            _grid_triangle_indices(NEIGHBOUR_TERRAIN_SAMPLES, NEIGHBOUR_TERRAIN_SAMPLES),
        )

    def test_the_recorded_index_count_matches_the_buffer(self):
        """`index_count` is what the draw call passes to `render`, so a count
        that disagrees with the buffer draws the wrong number of triangles."""
        renderer, ctx = self._upload()
        (mesh,) = renderer._neighbour_meshes.values()
        self.assertEqual(mesh.index_count, len(ctx.buffers[1].data))
