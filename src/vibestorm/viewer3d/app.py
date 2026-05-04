"""Runnable pygame bird's-eye viewer."""

from __future__ import annotations

import argparse
import asyncio
import platform
from pathlib import Path

from vibestorm import __version__
from vibestorm.bus import BusDeliveryError, BusError
from vibestorm.bus.commands import SendChat, TeleportLocation
from vibestorm.bus.events import (
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    InventorySnapshotReady,
    RegionChanged,
    RegionMapTileReady,
)
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient
from vibestorm.viewer3d.camera import Camera
from vibestorm.viewer3d.hud import HUD
from vibestorm.viewer3d.input import handle_event
from vibestorm.viewer3d.render import clear_tile_cache, render_scene
from vibestorm.viewer3d.scene import Scene


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibestorm-viewer3d")
    parser.add_argument("--login-uri", required=True)
    parser.add_argument("--first", required=True)
    parser.add_argument("--last", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--start", default="last")
    parser.add_argument("--agent-update-interval", type=float, default=1.0)
    parser.add_argument("--camera-sweep", action="store_true")
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
        float(args.ui_scale)
        if args.ui_scale and args.ui_scale > 0
        else _auto_ui_scale(pygame)
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
    screen = pygame.display.set_mode(screen_size, pygame.RESIZABLE)
    clock = pygame.time.Clock()

    client = WorldClient()
    scene = Scene()
    camera = Camera(world_center=(128.0, 128.0), zoom=1.0, screen_size=screen_size)
    camera.fit_region(padding_px=56)

    _wire_scene(client, scene)

    def center_on_avatar() -> None:
        world = client.world_view()
        if world is not None:
            for coarse in world.coarse_agents:
                if coarse.is_you:
                    camera.center_on(float(coarse.x), float(coarse.y))
                    return
        marker = next(iter(scene.avatar_markers.values()), None)
        if marker is not None:
            camera.center_on(marker.position[0], marker.position[1])

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
        help_text=_load_viewer_help(),
        ui_scale=ui_scale,
    )

    stop_event = asyncio.Event()
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
        )
    )

    running = True
    try:
        while running and not session_task.done():
            dt = clock.tick(60) / 1000.0
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
                if event.type == pygame.VIDEORESIZE:
                    screen_size = (max(1, event.w), max(1, event.h))
                    screen = pygame.display.set_mode(screen_size, pygame.RESIZABLE)
                    camera.set_screen_size(screen_size)
                    hud.resize(screen_size)

            scene.refresh_from_world_view(client.world_view())
            render_scene(screen, camera, scene)
            hud.update(dt, scene)
            hud.draw(screen)
            pygame.display.flip()
            await asyncio.sleep(0)
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(session_task, timeout=2.0)
        except TimeoutError:
            session_task.cancel()
        clear_tile_cache()
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
    client.bus.subscribe(ChatLocal, scene.apply_chat_local)
    client.bus.subscribe(ChatIM, scene.apply_chat_im)
    client.bus.subscribe(ChatAlert, scene.apply_chat_alert)
    client.bus.subscribe(ChatOutbound, scene.apply_chat_outbound)
    client.bus.subscribe(InventorySnapshotReady, scene.apply_inventory_snapshot_ready)


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
