"""Tests for the viewer3d app's GL compositor wiring (step 5b-ii).

The full ``run_viewer`` coroutine needs a real pygame OpenGL window and
a live login; it is not unit-testable. The pieces that are unit
testable are the small module-level helpers ``allocate_frame_surfaces``
and ``composite_frame``, plus the ``PerspectiveRenderer`` placeholder's
new map-tile background path. These tests cover those.

The compositor tests use ``moderngl.create_standalone_context()`` and
skip if no GL is available.
"""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as exc:
        return None, f"standalone GL context unavailable: {exc}"
    return ctx, None


class AllocateFrameSurfacesTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((1, 1))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_returns_two_surfaces_at_requested_size(self) -> None:
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        world, hud = allocate_frame_surfaces(self.pygame, (640, 480))

        self.assertEqual(world.get_size(), (640, 480))
        self.assertEqual(hud.get_size(), (640, 480))

    def test_hud_surface_has_per_pixel_alpha(self) -> None:
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        _, hud = allocate_frame_surfaces(self.pygame, (320, 200))
        flags = hud.get_flags()

        self.assertTrue(flags & self.pygame.SRCALPHA, f"hud flags={flags:#x}")

    def test_world_surface_is_opaque_format(self) -> None:
        # The world surface is opaque — the compositor draws it without
        # blending. SRCALPHA is the bug we want to avoid here.
        from vibestorm.viewer3d.app import allocate_frame_surfaces

        world, _ = allocate_frame_surfaces(self.pygame, (320, 200))
        flags = world.get_flags()

        self.assertFalse(flags & self.pygame.SRCALPHA, f"world flags={flags:#x}")


class AssetDataReadyHandlerTests(unittest.TestCase):
    def test_uses_five_field_asset_map_entry(self) -> None:
        from vibestorm.bus.events import AssetDataReady
        from vibestorm.viewer3d.app import _make_asset_data_ready_handler

        asset_id = UUID("11111111-1111-4111-8111-111111111111")

        class FakeHud:
            _inspector_item_asset_map = {
                "Script [lsltext]": (
                    asset_id,
                    10,
                    "Script",
                    UUID("22222222-2222-4222-8222-222222222222"),
                    UUID("33333333-3333-4333-8333-333333333333"),
                )
            }

            def __init__(self) -> None:
                self.calls = []

            def show_asset_data(self, asset_id, asset_type, data, *, item_name=""):  # type: ignore[no-untyped-def]
                self.calls.append((asset_id, asset_type, data, item_name))

        hud = FakeHud()
        handler = _make_asset_data_ready_handler(hud)

        handler(
            AssetDataReady(
                region_handle=1,
                asset_id=asset_id,
                asset_type=10,
                data=b"default {}",
            )
        )

        self.assertEqual(hud.calls, [(asset_id, 10, b"default {}", "Script")])

    def test_writes_pending_asset_saves(self) -> None:
        from vibestorm.bus.events import AssetDataReady
        from vibestorm.viewer3d.app import PendingAssetSave, _make_asset_data_ready_handler
        from vibestorm.viewer3d.hud import ObjectAssetSelection

        asset_id = UUID("11111111-1111-4111-8111-111111111111")

        class FakeHud:
            _inspector_item_asset_map = {}

            def show_asset_data(self, asset_id, asset_type, data, *, item_name=""):  # type: ignore[no-untyped-def]
                pass

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "scripts" / "Main.lsl"
            pending = {
                asset_id: [
                    PendingAssetSave(
                        selection=ObjectAssetSelection(
                            item_key="Main [lsltext]",
                            asset_id=asset_id,
                            asset_type=10,
                            item_name="Main",
                        ),
                        target_path=target,
                    )
                ]
            }
            handler = _make_asset_data_ready_handler(FakeHud(), pending)

            handler(
                AssetDataReady(
                    region_handle=1,
                    asset_id=asset_id,
                    asset_type=10,
                    data=b"default { state_entry() {} }",
                )
            )

            self.assertEqual(target.read_bytes(), b"default { state_entry() {} }")
            self.assertEqual(pending, {})


class CompositeFrameTests(unittest.TestCase):
    FBO_SIZE = (16, 16)

    def setUp(self) -> None:
        try:
            import pygame  # noqa: F401
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        ctx, err = _try_create_context()
        if ctx is None:
            self.skipTest(err)
        self.ctx = ctx
        self._color_tex = ctx.texture(self.FBO_SIZE, components=4)
        self.fbo = ctx.framebuffer(color_attachments=[self._color_tex])
        self.fbo.use()
        ctx.viewport = (0, 0, *self.FBO_SIZE)

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self.ctx.release()

    def _read_center_pixel(self) -> tuple[int, int, int, int]:
        data = self.fbo.read(components=4)
        w, h = self.FBO_SIZE
        x = w // 2
        y = h // 2
        offset = (y * w + x) * 4
        return tuple(data[offset : offset + 4])

    def test_draws_world_then_hud(self) -> None:
        # World = solid red, HUD = transparent middle, opaque green
        # band. composite_frame should leave the center as pure red
        # (HUD's transparent center doesn't change anything).
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            world.fill((255, 0, 0))

            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
            hud.fill((0, 0, 0, 0))  # fully transparent everywhere

            composite_frame(compositor, world, hud)

            r, g, b, _a = self._read_center_pixel()
            self.assertGreater(r, 240)
            self.assertLess(g, 10)
            self.assertLess(b, 10)
        finally:
            compositor.release()

    def test_hud_alpha_overlays_world(self) -> None:
        # HUD = opaque blue everywhere. Center pixel after composite
        # should be blue, not red.
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            world.fill((255, 0, 0))
            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)
            hud.fill((0, 0, 220, 255))

            composite_frame(compositor, world, hud)

            r, g, b, _a = self._read_center_pixel()
            self.assertLess(r, 20)
            self.assertLess(g, 20)
            self.assertGreater(b, 200)
        finally:
            compositor.release()

    def test_uploads_under_world_and_hud_names(self) -> None:
        import pygame

        from vibestorm.viewer3d.app import composite_frame
        from vibestorm.viewer3d.gl_compositor import GLCompositor

        compositor = GLCompositor(self.ctx)
        try:
            world = pygame.Surface(self.FBO_SIZE)
            hud = pygame.Surface(self.FBO_SIZE, pygame.SRCALPHA)

            composite_frame(compositor, world, hud)

            self.assertTrue(compositor.has_texture("world"))
            self.assertTrue(compositor.has_texture("hud"))
        finally:
            compositor.release()


class PerspectiveSkyBackgroundTests(unittest.TestCase):
    """The 3D renderer fills the world surface with a sky colour and
    leaves the map tile to the GL ground quad — earlier versions blitted
    the tile fullscreen, which hid the actual 3D ground behind a
    look-alike."""

    def setUp(self) -> None:
        try:
            import pygame
        except ImportError as exc:
            self.skipTest(f"pygame unavailable: {exc}")
        self.pygame = pygame
        pygame.init()
        pygame.display.set_mode((1, 1))

    def tearDown(self) -> None:
        self.pygame.quit()

    def test_world_surface_is_sky_when_no_tile(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import SKY_COLOR, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        renderer = PerspectiveRenderer(Camera3D())
        surface = self.pygame.Surface((64, 64))

        renderer.render(surface, Scene())

        corner = surface.get_at((1, 1))
        self.assertEqual((corner.r, corner.g, corner.b), SKY_COLOR)

    def test_world_surface_stays_sky_even_when_map_tile_path_set(self) -> None:
        # The map tile lives on the GL ground from step 6b; the world
        # surface must NOT blit it as a 2D background anymore.
        import tempfile
        from pathlib import Path

        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import SKY_COLOR, PerspectiveRenderer
        from vibestorm.viewer3d.scene import Scene

        tile = self.pygame.Surface((32, 32))
        tile.fill((30, 200, 90))
        with tempfile.TemporaryDirectory() as tmp:
            tile_path = Path(tmp) / "region.png"
            self.pygame.image.save(tile, str(tile_path))

            scene = Scene()
            scene.map_tile_path = tile_path

            renderer = PerspectiveRenderer(Camera3D())
            surface = self.pygame.Surface((64, 64))

            renderer.render(surface, scene)

            corner = surface.get_at((1, 1))
            self.assertEqual((corner.r, corner.g, corner.b), SKY_COLOR)


class SyncFolderForTaskTests(unittest.TestCase):
    """Where the viewer's Upload button decides to sync from.

    The button is the one sync entry point with no headless verification --
    driving it needs a window and a live simulator -- so the part of it that
    is not a guard clause or a call into the engine is pulled out here and
    tested directly.
    """

    TASK = UUID("d7f47f7e-4328-4d17-a665-19feaec7b1e9")

    def setUp(self) -> None:
        self._cwd = Path.cwd()

    def test_no_path_means_the_object_s_own_download_folder(self) -> None:
        from vibestorm.viewer3d.app import DEFAULT_ASSET_DOWNLOAD_DIR, sync_folder_for_task

        folder = sync_folder_for_task(self.TASK, None)

        self.assertTrue(folder.is_absolute())
        self.assertEqual(folder.name, str(self.TASK))
        self.assertEqual(folder.parent.name, DEFAULT_ASSET_DOWNLOAD_DIR.name)

    def test_a_directory_is_used_as_given(self) -> None:
        from vibestorm.viewer3d.app import sync_folder_for_task

        with tempfile.TemporaryDirectory() as tmp:
            folder = sync_folder_for_task(self.TASK, Path(tmp))

            self.assertEqual(folder, Path(tmp))

    def test_a_file_means_the_folder_it_is_in(self) -> None:
        # A file picker hands back a file. Taking it literally would sync a
        # folder that does not exist and report the user's folder missing.
        from vibestorm.viewer3d.app import sync_folder_for_task

        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "greeter.lsl"
            chosen.write_text("default {}")

            self.assertEqual(sync_folder_for_task(self.TASK, chosen), Path(tmp))

    def test_a_relative_path_is_anchored_to_the_working_directory(self) -> None:
        from vibestorm.viewer3d.app import sync_folder_for_task

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "work").mkdir()
            os.chdir(tmp)
            try:
                folder = sync_folder_for_task(self.TASK, Path("work"))
            finally:
                os.chdir(self._cwd)

            self.assertEqual(folder, Path(tmp).resolve() / "work")

    def test_a_folder_that_is_not_there_is_not_quietly_replaced_by_its_parent(self) -> None:
        # A mistyped folder name resolving to the folder above it would push
        # whatever that holds into the object. The caller checks is_dir() and
        # reports it missing; that only works if the name survives to it.
        from vibestorm.viewer3d.app import sync_folder_for_task

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "scrpits"

            self.assertEqual(sync_folder_for_task(self.TASK, missing), missing)



class StopSessionTaskTests(unittest.IsolatedAsyncioTestCase):
    """What the viewer does with a session task that ends badly.

    `run_viewer`'s frame loop stops when the session task finishes, and the
    `finally` after it is the teardown: the last soak sample, closing the soak
    log, releasing the GL caches, `pygame.quit()`. That teardown used to sit
    behind a bare `asyncio.wait_for`, which re-raises -- so a session that
    ended by *raising* skipped every part of it. A run that ends on a session
    crash is exactly the run whose final sample and flushed log someone wants
    to read, and it was the only run that never got one.

    The fix is to hand the exception back instead, and let the caller re-raise
    it once the teardown has run. These cover the three ways the task can end,
    because the reason `wait_for` was there at all -- a session that will not
    stop -- still has to work.
    """

    async def test_a_session_that_raised_is_handed_back_rather_than_raised(self) -> None:
        import asyncio

        from vibestorm.viewer3d.app import stop_session_task

        boom = RuntimeError("circuit exploded")

        async def failing() -> None:
            raise boom

        task = asyncio.ensure_future(failing())
        error = await stop_session_task(task, asyncio.Event())
        self.assertIs(error, boom)

    async def test_a_clean_session_hands_back_nothing(self) -> None:
        import asyncio

        from vibestorm.viewer3d.app import stop_session_task

        async def clean() -> None:
            return None

        task = asyncio.ensure_future(clean())
        self.assertIsNone(await stop_session_task(task, asyncio.Event()))

    async def test_the_stop_event_is_what_asks_it_to_finish(self) -> None:
        """A session ends when the event is set, not when the task is killed.

        Cancelling is the fallback for one that ignores the ask, and it costs
        the logout: `build_shutdown_packets` never runs, so the simulator is
        left holding a circuit until it times out.
        """
        import asyncio

        from vibestorm.viewer3d.app import stop_session_task

        stop_event = asyncio.Event()
        stopped = asyncio.Event()

        async def polite() -> None:
            await stop_event.wait()
            stopped.set()

        task = asyncio.ensure_future(polite())
        self.assertIsNone(await stop_session_task(task, stop_event))
        self.assertTrue(stopped.is_set())
        self.assertFalse(task.cancelled())

    async def test_a_session_that_will_not_stop_is_cancelled(self) -> None:
        import asyncio

        from vibestorm.viewer3d.app import stop_session_task

        async def stuck() -> None:
            await asyncio.Event().wait()

        task = asyncio.ensure_future(stuck())
        error = await stop_session_task(task, asyncio.Event(), timeout=0.05)
        self.assertIsNone(error)
        self.assertTrue(task.cancelling() or task.cancelled() or task.done())



class OrbitYawTests(unittest.TestCase):
    """The soak's camera, and why it is not the other camera.

    A viewer parked in one spot rebuilt its entity list ten times in a
    two-hour soak: the local region is still and the camera stiller, so
    everything that runs when the *view* changes -- culling, sorting, every
    cache keyed on where the camera is -- went two hours without being asked
    to. `--camera-sweep` does not help; it moves the camera this client
    reports to the simulator for interest management, not the one it draws
    from.
    """

    def test_a_full_turn_takes_one_period(self) -> None:
        import math

        from vibestorm.viewer3d.app import orbit_yaw

        self.assertAlmostEqual(orbit_yaw(0.0, 0.0, 60.0), 0.0)
        self.assertAlmostEqual(orbit_yaw(0.0, 15.0, 60.0), math.pi / 2)
        self.assertAlmostEqual(orbit_yaw(0.0, 30.0, 60.0), math.pi)
        self.assertAlmostEqual(orbit_yaw(0.0, 60.0, 60.0), 0.0)

    def test_it_turns_from_where_the_camera_started(self) -> None:
        """The offset is the yaw the run began at, so the flag does not also
        silently reset the view a preset or the user had chosen."""
        import math

        from vibestorm.viewer3d.app import orbit_yaw

        self.assertAlmostEqual(orbit_yaw(1.25, 0.0, 60.0), 1.25)
        self.assertAlmostEqual(orbit_yaw(1.25, 30.0, 60.0), 1.25 + math.pi)

    def test_a_period_of_zero_leaves_the_camera_alone(self) -> None:
        """Which is the flag's default, so the off switch is in one place."""
        from vibestorm.viewer3d.app import orbit_yaw

        self.assertEqual(orbit_yaw(1.25, 999.0, 0.0), 1.25)
        self.assertEqual(orbit_yaw(1.25, 999.0, -5.0), 1.25)

    def test_the_orbit_wins_over_the_camera_preset(self) -> None:
        """The order, which is the only thing `apply_frame_camera` is for.

        The default camera preset is `avatar_behind`, and it sets the yaw from
        the avatar's rotation on every single frame. An orbit applied before
        it is overwritten before anything is drawn: the flag does nothing, the
        run still finishes, and the report still looks healthy. This is what
        that looks like as a test, with a stand-in preset that writes a yaw
        nobody asked for.
        """
        import math
        from dataclasses import dataclass

        from vibestorm.viewer3d.app import apply_frame_camera

        @dataclass
        class _Camera:
            mode: str = "orbit"
            yaw: float = 0.0

        camera = _Camera()

        def preset() -> None:
            camera.yaw = 99.0

        apply_frame_camera(camera, preset, start_yaw=0.0, elapsed_s=15.0, orbit_seconds=60.0)
        self.assertAlmostEqual(camera.yaw, math.pi / 2)

    def test_the_preset_still_runs_when_the_orbit_is_off(self) -> None:
        """Turning the flag off must not also turn the camera off."""
        from dataclasses import dataclass

        from vibestorm.viewer3d.app import apply_frame_camera

        @dataclass
        class _Camera:
            mode: str = "orbit"
            yaw: float = 0.0

        camera = _Camera()

        def preset() -> None:
            camera.yaw = 99.0

        apply_frame_camera(camera, preset, start_yaw=0.0, elapsed_s=15.0, orbit_seconds=0.0)
        self.assertEqual(camera.yaw, 99.0)

    def test_the_map_camera_is_left_alone(self) -> None:
        """A top-down orthographic view has no yaw to turn."""
        from dataclasses import dataclass

        from vibestorm.viewer3d.app import apply_frame_camera

        @dataclass
        class _Camera:
            mode: str = "map"
            yaw: float = 7.0

        camera = _Camera()
        apply_frame_camera(camera, lambda: None, start_yaw=0.0, elapsed_s=15.0, orbit_seconds=60.0)
        self.assertEqual(camera.yaw, 7.0)

    def test_the_flag_parses_and_is_off_by_default(self) -> None:
        from vibestorm.viewer3d.app import build_parser

        self.assertEqual(build_parser().parse_args([]).camera_orbit_seconds, 0.0)
        self.assertEqual(
            build_parser().parse_args(["--camera-orbit-seconds", "45"]).camera_orbit_seconds,
            45.0,
        )

    def test_it_is_not_the_same_flag_as_camera_sweep(self) -> None:
        """They move different cameras and one is not a spelling of the other.

        `--camera-sweep` circles the camera this client *reports* to the
        simulator, which is interest management; this one turns the camera it
        draws from. A run that asked for one and got the other looks healthy
        and measures nothing.
        """
        from vibestorm.viewer3d.app import build_parser

        args = build_parser().parse_args(["--camera-sweep"])
        self.assertTrue(args.camera_sweep)
        self.assertEqual(args.camera_orbit_seconds, 0.0)

    def test_it_keeps_turning_over_a_long_run(self) -> None:
        """Four hours at 30 fps is 432,000 frames. A yaw that accumulated
        would be a number with no precision left in it by the end; this is a
        function of elapsed time, so hour four is as exact as hour one."""
        import math

        from vibestorm.viewer3d.app import orbit_yaw

        self.assertAlmostEqual(orbit_yaw(0.0, 4 * 3600.0 + 15.0, 60.0), math.pi / 2)


if __name__ == "__main__":
    unittest.main()
