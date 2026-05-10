"""Runnable pygame bird's-eye viewer.

Display pipeline (step 5b-ii):

- The pygame window opens with ``OPENGL | DOUBLEBUF | RESIZABLE``;
  the screen surface is the GL default framebuffer.
- The active ``ViewerRenderer`` draws into a software
  ``world_surface``; the HUD's ``UIManager`` draws into a separate
  per-pixel-alpha ``hud_surface``.
- Each frame the ``GLCompositor`` uploads both surfaces as textures
  and draws them as fullscreen quads — world opaque, HUD with alpha
  blending — then ``pygame.display.flip()`` swaps the framebuffer.

Step 6 replaces the ``PerspectiveRenderer`` body with native GL
geometry that targets the same default framebuffer.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from vibestorm import __version__
from vibestorm.bus import BusDeliveryError, BusError
from vibestorm.bus.commands import RequestObjectInventory, SendChat, TeleportLocation
from vibestorm.bus.events import (
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    InventorySnapshotReady,
    LayerDataReceived,
    ObjectInventorySnapshotReady,
    RegionChanged,
    RegionMapTileReady,
    TextureAssetReady,
)
from vibestorm.caps.inventory_client import (
    InventoryCapabilityClient,
    InventoryCapabilityError,
    InventoryFolderRequest,
    merge_inventory_fetch_snapshots,
    parse_inventory_descendents_payload,
    snapshot_with_loaded_empty_folder,
)
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.camera import Camera
from vibestorm.viewer3d.gl_compositor import GLCompositor
from vibestorm.viewer3d.hud import HUD
from vibestorm.viewer3d.input import handle_event
from vibestorm.viewer3d.perspective import PerspectiveRenderer
from vibestorm.viewer3d.render import clear_tile_cache
from vibestorm.viewer3d.renderer import TopDownRenderer, ViewerRenderer
from vibestorm.viewer3d.scene import Scene

if TYPE_CHECKING:
    import moderngl
    import pygame


def build_renderer(
    mode: str, camera: Camera, *, ctx: moderngl.Context | None = None
) -> ViewerRenderer:
    """Pick a ``ViewerRenderer`` for the given HUD render-mode string.

    Both renderers draw the world background into a software pygame
    surface (uploaded as a fullscreen quad by ``GLCompositor``). The
    perspective renderer additionally draws native GL geometry on
    top of that quad in ``render_gl`` — that's why it needs the
    moderngl context. Tests that don't have a GL context can omit
    ``ctx`` and the perspective renderer will skip the native pass.
    """
    if mode == "3d":
        return PerspectiveRenderer(camera, ctx=ctx)
    return TopDownRenderer(camera)


def allocate_frame_surfaces(
    pygame_module, size: tuple[int, int]
) -> tuple[pygame.Surface, pygame.Surface]:
    """Allocate paired world (RGB) + HUD (SRCALPHA) draw targets.

    The world surface is opaque — the renderer fills every pixel.
    The HUD surface uses per-pixel alpha so empty UI space stays
    transparent; the compositor uses source-over blending to
    overlay it on the world quad.
    """
    world = pygame_module.Surface(size)
    hud = pygame_module.Surface(size, pygame_module.SRCALPHA)
    return world, hud


def composite_world(compositor: GLCompositor, world_surface: pygame.Surface) -> None:
    """Upload the world surface and draw it as the opaque background quad."""
    compositor.upload_surface("world", world_surface)
    compositor.draw("world", alpha=False)


def composite_hud(compositor: GLCompositor, hud_surface: pygame.Surface) -> None:
    """Upload the HUD surface and draw it as the alpha-blended overlay quad."""
    compositor.upload_surface("hud", hud_surface)
    compositor.draw("hud", alpha=True)


def composite_frame(
    compositor: GLCompositor,
    world_surface: pygame.Surface,
    hud_surface: pygame.Surface,
    *,
    clear_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
) -> None:
    """Convenience wrapper: clear, draw world quad, draw HUD quad.

    The frame loop in ``run_viewer`` doesn't use this — it inlines the
    sequence so ``renderer.render_gl(scene, aspect)`` can run between
    the world and HUD passes. The wrapper is kept for tests that
    exercise the world/HUD compositing path without a 3D renderer.
    """
    compositor.clear(clear_color)
    composite_world(compositor, world_surface)
    composite_hud(compositor, hud_surface)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibestorm-viewer3d")
    parser.add_argument("--login-uri", required=True)
    parser.add_argument("--first", required=True)
    parser.add_argument("--last", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--start", default="last")
    parser.add_argument("--agent-update-interval", type=float, default=1.0)
    parser.add_argument("--camera-sweep", action="store_true")
    parser.add_argument(
        "--render-mode",
        choices=("2d-map", "3d"),
        default="3d",
        help="Initial renderer mode. Defaults to 3d.",
    )
    parser.add_argument(
        "--max-fps",
        type=float,
        default=20.0,
        help="Frame-rate cap for the viewer loop. Use 0 to disable.",
    )
    parser.add_argument(
        "--debug-terrain",
        choices=("off", "synthetic"),
        default="off",
        help="Override live terrain with a deterministic debug heightmap.",
    )
    parser.add_argument(
        "--terrain-z-scale",
        type=float,
        default=1.0,
        help="Vertical scale applied to rendered terrain. Use values above 1 for debugging.",
    )
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument(
        "--ui-scale",
        type=float,
        default=0.0,
        help="UI scale factor. Defaults to auto based on desktop size.",
    )
    return parser


async def run_viewer(args: argparse.Namespace) -> int:
    import moderngl
    import pygame

    request = LoginRequest(
        login_uri=args.login_uri,
        credentials=LoginCredentials(first=args.first, last=args.last, password=args.password),
        start=args.start,
        version=__version__,
        platform=platform.system(),
        platform_version=platform.platform(),
    )
    bootstrap = await LoginClient().login(request)

    pygame.init()
    pygame.display.set_caption("Vibestorm 3D Viewer")
    ui_scale = (
        float(args.ui_scale) if args.ui_scale and args.ui_scale > 0 else _auto_ui_scale(pygame)
    )
    desktop_w, desktop_h = _desktop_size(pygame)
    default_w = int(round(1180 * ui_scale))
    default_h = int(round(820 * ui_scale))
    requested_w = args.width if args.width is not None else default_w
    requested_h = args.height if args.height is not None else default_h
    screen_size = (
        max(640, min(requested_w, max(640, desktop_w - 80))),
        max(480, min(requested_h, max(480, desktop_h - 120))),
    )
    display_flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
    pygame.display.set_mode(screen_size, display_flags)
    ctx = moderngl.create_context()
    ctx.viewport = (0, 0, *screen_size)
    compositor = GLCompositor(ctx)
    world_surface, hud_surface = allocate_frame_surfaces(pygame, screen_size)
    clock = pygame.time.Clock()

    client = WorldClient()
    scene = Scene()
    scene.terrain_z_scale = max(0.01, float(args.terrain_z_scale))
    if args.debug_terrain == "synthetic":
        from vibestorm.world.terrain import synthetic_heightmap

        scene.terrain_heightmap = synthetic_heightmap()
        scene.debug_terrain_source = "synthetic"
    camera = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=screen_size)
    camera.fit_region(padding_px=56)
    initial_mode = args.render_mode
    if initial_mode == "3d":
        camera.set_mode("orbit")
        camera.pitch = 0.5
        camera.distance = 50.0
    else:
        camera.set_mode("map")
    renderer: ViewerRenderer = build_renderer(initial_mode, camera, ctx=ctx)

    _wire_scene(client, scene)

    def center_on_avatar() -> None:
        world = client.world_view()
        if world is not None:
            for coarse in world.coarse_agents:
                if coarse.is_you:
                    if camera.mode == "orbit":
                        camera.target = (float(coarse.x), float(coarse.y), float(coarse.z))
                        return
                    camera.center_on(float(coarse.x), float(coarse.y))
                    return
        entity = next(iter(scene.avatar_entities.values()), None)
        if entity is not None:
            if camera.mode == "orbit":
                camera.target = entity.position
                return
            camera.center_on(entity.position[0], entity.position[1])

    def on_chat_submit(text: str) -> None:
        try:
            client.bus.dispatch(SendChat(text))
        except (BusError, BusDeliveryError, RuntimeError) as exc:
            scene.apply_chat_alert(
                ChatAlert(region_handle=client.current_handle or 0, message=str(exc))
            )

    def on_teleport(position: tuple[float, float, float]) -> None:
        try:
            client.bus.dispatch(TeleportLocation(position=position))
        except (BusError, BusDeliveryError, RuntimeError, ValueError) as exc:
            scene.apply_chat_alert(
                ChatAlert(region_handle=client.current_handle or 0, message=str(exc))
            )

    pending_inventory_folders: set[UUID] = set()

    async def fetch_inventory_folder(folder_id: UUID) -> None:
        session = client.current
        handle = client.current_handle or 0
        if session is None:
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message="Inventory is not connected.")
            )
            return
        if folder_id in pending_inventory_folders:
            return
        url = session.fetch_inventory_descendents_url
        if not url:
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=handle,
                    message="FetchInventoryDescendents2 is not available.",
                )
            )
            return
        pending_inventory_folders.add(folder_id)
        session._record_event(
            time.monotonic(),
            "caps.inventory_folder.start",
            f"folder={folder_id}",
        )
        try:
            inventory_client = InventoryCapabilityClient(timeout_seconds=5.0)
            payload = await inventory_client.fetch_inventory_descendents(
                url,
                [
                    InventoryFolderRequest(
                        folder_id=folder_id,
                        owner_id=session.bootstrap.agent_id,
                    )
                ],
                udp_listen_port=session.caps_udp_listen_port,
            )
        except InventoryCapabilityError as exc:
            session._record_event(
                time.monotonic(),
                "caps.inventory_folder.error",
                f"folder={folder_id} error={str(exc)!r}",
            )
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message=str(exc)))
        else:
            update = parse_inventory_descendents_payload(
                payload,
                inventory_root_folder_id=session.bootstrap.inventory_root_folder_id,
                current_outfit_folder_id=session.bootstrap.current_outfit_folder_id,
            )
            if update.folder_by_id(folder_id) is None:
                update = snapshot_with_loaded_empty_folder(
                    update,
                    folder_id=folder_id,
                    owner_id=session.bootstrap.agent_id,
                    agent_id=session.bootstrap.agent_id,
                )
            session.latest_inventory_fetch = merge_inventory_fetch_snapshots(
                session.latest_inventory_fetch,
                update,
            )
            session._record_event(
                time.monotonic(),
                "caps.inventory",
                f"folder={folder_id} folders={session.latest_inventory_fetch.folder_count} "
                f"items={session.latest_inventory_fetch.total_item_count}",
            )
        finally:
            pending_inventory_folders.discard(folder_id)

    def on_inventory_open_folder(folder_id: UUID) -> None:
        asyncio.create_task(fetch_inventory_folder(folder_id))

    def on_object_inventory_request(local_id: int) -> None:
        try:
            client.bus.dispatch(RequestObjectInventory(local_id))
        except (BusError, BusDeliveryError, RuntimeError, ValueError) as exc:
            scene.apply_chat_alert(
                ChatAlert(region_handle=client.current_handle or 0, message=str(exc))
            )

    def on_render_mode_change(mode: str) -> None:
        nonlocal renderer
        if mode == "3d":
            # Tilt + back off the orbit camera so the region floor and
            # most cubes are framed at startup. Without orbit input
            # wired (step 9), the user otherwise lands at pitch=0
            # distance=8 which looks "into" objects with no ground.
            camera.set_mode("orbit")
            camera.pitch = 0.5
            camera.distance = 50.0
        else:
            camera.set_mode("map")
        renderer.clear_caches()
        renderer = build_renderer(mode, camera, ctx=ctx)

    def on_render_setting_change(name: str, value: object) -> None:
        if name in {
            "render_terrain",
            "render_terrain_lines",
            "render_water",
            "render_objects",
        }:
            setattr(scene, name, bool(value))
        elif name == "water_alpha":
            scene.water_alpha = max(0.1, min(1.0, float(value)))

    hud = HUD(
        screen_size,
        on_chat_submit=on_chat_submit,
        on_zoom_in=lambda: camera.zoom_at_screen(
            camera.screen_size[0] / 2, camera.screen_size[1] / 2, 1.2
        ),
        on_zoom_out=lambda: camera.zoom_at_screen(
            camera.screen_size[0] / 2, camera.screen_size[1] / 2, 1.0 / 1.2
        ),
        on_center=center_on_avatar,
        on_teleport=on_teleport,
        on_inventory_open_folder=on_inventory_open_folder,
        on_object_inventory_request=on_object_inventory_request,
        on_render_mode_change=on_render_mode_change,
        on_render_setting_change=on_render_setting_change,
        initial_render_mode=initial_mode,
        help_text=_load_viewer_help(),
        theme_path=Path(__file__).parent / "theme.json",
        ui_scale=ui_scale,
    )

    stop_event = asyncio.Event()

    def on_session_event(event) -> None:
        interesting_prefixes = (
            "task_inventory.",
            "xfer.",
        )
        if event.kind.startswith(interesting_prefixes):
            print(
                f"[viewer3d {event.at_seconds:8.3f}] {event.kind} {event.detail}",
                flush=True,
            )

    session_task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(
                duration_seconds=86400.0,
                agent_update_interval_seconds=args.agent_update_interval,
                camera_sweep=args.camera_sweep,
            ),
            stop_event=stop_event,
            world_client=client,
            on_event=on_session_event,
        )
    )

    running = True
    max_fps = float(args.max_fps)
    frame_cap = int(max(1.0, max_fps)) if max_fps > 0.0 else 0
    left_click_start_pos: tuple[int, int] | None = None
    left_click_start_time: float | None = None
    right_click_start_pos: tuple[int, int] | None = None
    right_click_start_time: float | None = None
    try:
        while running and not session_task.done():
            dt = clock.tick(frame_cap) / 1000.0
            for event in pygame.event.get():
                consumed_by_ui = hud.process_event(event)
                if hud.quit_requested:
                    running = False
                    break
                if consumed_by_ui or (
                    hud.is_text_entry_focused() and event.type in (pygame.KEYDOWN, pygame.KEYUP)
                ):
                    continue
                try:
                    intent = handle_event(event, camera, client.bus)
                except BusError as exc:
                    scene.apply_chat_alert(
                        ChatAlert(region_handle=client.current_handle or 0, message=str(exc))
                    )
                    continue
                if intent.quit_requested:
                    running = False
                if intent.chat_input_focus:
                    hud.focus_chat()
                if intent.request_center_on_avatar:
                    center_on_avatar()

                if event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                        left_click_start_pos = event.pos
                        left_click_start_time = time.monotonic()
                    elif event.button == 3:
                        right_click_start_pos = event.pos
                        right_click_start_time = time.monotonic()

                if event.type == pygame.MOUSEBUTTONUP:
                    if event.button == 1 and left_click_start_pos is not None:
                        dx = event.pos[0] - left_click_start_pos[0]
                        dy = event.pos[1] - left_click_start_pos[1]
                        dt_click = time.monotonic() - (left_click_start_time or 0.0)
                        if dt_click < 0.4 and (dx * dx + dy * dy) < 25:
                            aspect = screen_size[0] / max(1, screen_size[1])
                            local_id = renderer.pick(event.pos[0], event.pos[1], scene, aspect=aspect)
                            if local_id is not None:
                                scene.apply_chat_alert(ChatAlert(region_handle=client.current_handle or 0, message=f"Touched object {local_id} (ObjectGrab not yet implemented)"))
                        left_click_start_pos = None

                    elif event.button == 3 and right_click_start_pos is not None:
                        dx = event.pos[0] - right_click_start_pos[0]
                        dy = event.pos[1] - right_click_start_pos[1]
                        dt_click = time.monotonic() - (right_click_start_time or 0.0)
                        if dt_click < 0.4 and (dx * dx + dy * dy) < 25:
                            aspect = screen_size[0] / max(1, screen_size[1])
                            local_id = renderer.pick(event.pos[0], event.pos[1], scene, aspect=aspect)
                            if local_id is not None:
                                hud.select_inspector_object(local_id)
                        right_click_start_pos = None

                if event.type == pygame.VIDEORESIZE:
                    screen_size = (max(1, event.w), max(1, event.h))
                    pygame.display.set_mode(screen_size, display_flags)
                    ctx.viewport = (0, 0, *screen_size)
                    world_surface, hud_surface = allocate_frame_surfaces(pygame, screen_size)
                    camera.set_screen_size(screen_size)
                    hud.resize(screen_size)

            scene.refresh_from_world_view(client.world_view())
            renderer.update(dt, scene)
            renderer.render(world_surface, scene)
            hud_surface.fill((0, 0, 0, 0))
            hud.update(dt, scene, client.world_view())
            hud.draw(hud_surface)

            compositor.clear((0.0, 0.0, 0.0, 1.0))
            composite_world(compositor, world_surface)
            aspect = screen_size[0] / max(1, screen_size[1])
            renderer.render_gl(scene, aspect=aspect)
            composite_hud(compositor, hud_surface)
            pygame.display.flip()
            await asyncio.sleep(0)
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(session_task, timeout=2.0)
        except TimeoutError:
            session_task.cancel()
        renderer.clear_caches()
        compositor.release()
        pygame.quit()

    return 0


def _desktop_size(pygame_module) -> tuple[int, int]:
    try:
        sizes = pygame_module.display.get_desktop_sizes()
    except pygame_module.error:
        sizes = []
    if not sizes:
        return (1920, 1080)
    width, height = sizes[0]
    return (max(1, int(width)), max(1, int(height)))


def _auto_ui_scale(pygame_module) -> float:
    width, height = _desktop_size(pygame_module)
    raw = min(width / 1920.0, height / 1080.0)
    clamped = min(2.0, max(1.0, raw))
    return round(clamped * 4.0) / 4.0


def _wire_scene(client: WorldClient, scene: Scene) -> None:
    client.bus.subscribe(RegionChanged, _with_render_cache_clear(scene.apply_region_changed))
    client.bus.subscribe(RegionMapTileReady, scene.apply_map_tile_ready)
    client.bus.subscribe(TextureAssetReady, scene.apply_texture_asset_ready)
    client.bus.subscribe(ChatLocal, scene.apply_chat_local)
    client.bus.subscribe(ChatIM, scene.apply_chat_im)
    client.bus.subscribe(ChatAlert, scene.apply_chat_alert)
    client.bus.subscribe(ChatOutbound, scene.apply_chat_outbound)
    client.bus.subscribe(InventorySnapshotReady, scene.apply_inventory_snapshot_ready)
    client.bus.subscribe(
        ObjectInventorySnapshotReady,
        scene.apply_object_inventory_snapshot_ready,
    )
    client.bus.subscribe(LayerDataReceived, scene.apply_layer_data_received)


def _with_render_cache_clear(handler):
    def _wrapped(event):
        clear_tile_cache()
        handler(event)

    return _wrapped


def _load_viewer_help() -> str:
    path = Path("docs/viewer-help.md")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        from vibestorm.viewer3d.hud import DEFAULT_HELP_TEXT

        return DEFAULT_HELP_TEXT


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_viewer(args))


if __name__ == "__main__":
    raise SystemExit(main())
