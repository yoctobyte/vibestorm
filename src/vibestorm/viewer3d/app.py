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
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from vibestorm.bus import BusDeliveryError, BusError
from vibestorm.bus.commands import (
    RequestAssetData,
    RequestObjectInventory,
    SendChat,
    TeleportLocation,
)
from vibestorm.bus.events import (
    AssetDataReady,
    AttachedSoundGainChanged,
    AttachedSoundReceived,
    AvatarAnimationReceived,
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    EventQueueEventReceived,
    InventorySnapshotReady,
    LayerDataReceived,
    MeshAssetReady,
    ObjectAnimationReceived,
    ObjectInventorySnapshotReady,
    ParcelOverlayReceived,
    ParcelPropertiesReceived,
    RegionChanged,
    RegionMapTileReady,
    SoundTriggered,
    TextureAssetReady,
)
from vibestorm.caps.asset_upload_client import (
    AssetUploadClient,
    AssetUploadError,
    NewFileInventoryRequest,
)
from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.caps.inventory_client import (
    InventoryCapabilityClient,
    InventoryCapabilityError,
    InventoryFolderRequest,
    merge_inventory_fetch_snapshots,
    parse_inventory_descendents_payload,
    snapshot_with_loaded_empty_folder,
)
from vibestorm.login.client import LoginError
from vibestorm.sync.engine import (
    pull_object_to_folder,
    push_folder_to_object,
    resolve_sync_caps,
)
from vibestorm.sync.naming import (
    asset_file_suffix,
    safe_filename,
    upload_kind_for_path,
)
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient, WorldClientError
from vibestorm.viewer3d.camera import Camera, CameraPreset
from vibestorm.viewer3d.gl_compositor import GLCompositor
from vibestorm.viewer3d.hud import HUD, ObjectAssetSelection
from vibestorm.viewer3d.input import handle_event
from vibestorm.viewer3d.perspective import PerspectiveRenderer
from vibestorm.viewer3d.render import clear_tile_cache
from vibestorm.viewer3d.renderer import TopDownRenderer, ViewerRenderer
from vibestorm.viewer3d.scene import Scene

if TYPE_CHECKING:
    import moderngl
    import pygame


#: Naming and matching live in vibestorm.sync so the CLI sync and the
#: viewer cannot drift apart on what a pulled file is called.
_safe_filename = safe_filename
_asset_file_suffix = asset_file_suffix
_upload_kind_for_path = upload_kind_for_path
DEFAULT_ASSET_DOWNLOAD_DIR = Path("local/asset-downloads")
DEFAULT_ASSET_UPLOAD_DIR = Path("local/upload")

@dataclass(slots=True, frozen=True)
class PendingAssetSave:
    selection: ObjectAssetSelection
    target_path: Path


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


def save_screenshot(ctx, size: tuple[int, int], path: Path) -> None:
    """Write the current framebuffer to a PNG.

    Read back through GL rather than from pygame: in an OPENGL display the
    surface pygame hands out is not the one the driver drew into, so saving it
    produces a black image and a confident report that it worked.

    GL's origin is bottom-left, so the rows come back upside down.
    """
    import pygame

    width, height = size
    raw = ctx.screen.read(components=3, alignment=1)
    surface = pygame.image.frombytes(raw, (width, height), "RGB", True)
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surface, str(path))


def redraw_hud(compositor: GLCompositor, hud_surface: pygame.Surface, hud) -> bool:
    """Repaint and re-upload the HUD, but only when it would look different.

    Returns whether it repainted. The clear, the per-element blits and the
    full-screen upload together cost 17 ms of a 1920x1080 frame on a GTX 1660
    SUPER -- the upload alone is 6.4 ms -- and the HUD's content changes a few
    times a second. The texture already on the GPU stays valid in between, so a
    skipped frame still draws the same pixels.

    The texture is checked for as well as the dirty flag: the compositor may
    have been released and rebuilt (a resize) while the HUD believed itself
    freshly drawn, and drawing a name that has no texture is not a thing to
    find out about at 60 Hz.
    """
    if not hud.needs_redraw() and compositor.has_texture("hud"):
        return False
    hud_surface.fill((0, 0, 0, 0))
    hud.draw(hud_surface)
    compositor.upload_surface("hud", hud_surface)
    hud.mark_drawn()
    return True


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


def _camera_avatar_entity(scene: Scene):
    if scene.avatar_entities:
        if scene.avatar_position is not None:
            ax, ay, az = scene.avatar_position
            return min(
                scene.avatar_entities.values(),
                key=lambda entity: (
                    (entity.position[0] - ax) ** 2
                    + (entity.position[1] - ay) ** 2
                    + (entity.position[2] - az) ** 2
                ),
            )
        return next(iter(scene.avatar_entities.values()))
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibestorm-viewer3d")
    parser.add_argument("--login-uri")
    parser.add_argument("--first")
    parser.add_argument("--last")
    parser.add_argument("--password")
    parser.add_argument("--start", default="last")
    # 10 Hz, as an interactive viewer needs: at the old 1 Hz default a keypress
    # waited up to a second to reach the simulator, and releasing it waited
    # another second to stop.
    parser.add_argument("--agent-update-interval", type=float, default=0.1)
    parser.add_argument("--camera-sweep", action="store_true")
    parser.add_argument(
        "--screenshot",
        metavar="PATH",
        help=(
            "Write one PNG of the rendered frame and exit. Runs happily under "
            "Xvfb, so it is the way to see what the viewer draws without "
            "opening a window on anyone's desktop."
        ),
    )
    parser.add_argument(
        "--screenshot-after",
        type=float,
        default=25.0,
        help=(
            "Seconds to let the region load before the screenshot. Terrain, "
            "object updates and textures all arrive over several seconds, so a "
            "shot taken too early is a picture of a loading screen."
        ),
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help=(
            "Open the diagnostics panel at startup. It rebuilds a pygame_gui "
            "text box every second, which costs about 49 ms and drops three "
            "frames; the framerate it reports is in the status bar either way."
        ),
    )
    parser.add_argument(
        "--camera",
        choices=("avatar_behind", "avatar_eye", "sim"),
        default="avatar_behind",
        help="Camera to start in. The default follows the avatar; 'sim' frames the region.",
    )
    parser.add_argument(
        "--no-auto-bake-upload",
        action="store_true",
        help="Do not automatically upload baked appearance textures during session setup.",
    )
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


class _PhaseStats:
    """Temporary per-phase frame timing, enabled by VIBESTORM_PROFILE_FRAMES."""

    def __init__(self, report_every: int = 60) -> None:
        self.report_every = report_every
        self.n = 0
        self.totals: dict[str, float] = {}

    def add(self, **phases: float) -> None:
        self.n += 1
        for k, v in phases.items():
            self.totals[k] = self.totals.get(k, 0.0) + v
        if self.n % self.report_every == 0:
            total = sum(self.totals.values())
            parts = " ".join(
                f"{k}={self.totals[k] / self.n * 1000:.1f}ms"
                for k in sorted(self.totals, key=lambda x: -self.totals[x])
            )
            print(
                f"PHASE frames={self.n} avg_total={total / self.n * 1000:.1f}ms "
                f"({self.n / max(total, 1e-9):.1f} fps if unbounded) {parts}",
                flush=True,
            )


_PHASE_STATS = _PhaseStats() if os.environ.get("VIBESTORM_PROFILE_FRAMES") else None


async def run_viewer(args: argparse.Namespace) -> int:
    import moderngl
    import pygame

    from vibestorm.viewer.login_screen import LoginScreen

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

    login_screen = LoginScreen(screen_size, ui_scale=ui_scale, args=args)
    login_clock = pygame.time.Clock()

    bootstrap = None
    while bootstrap is None:
        dt = login_clock.tick(60) / 1000.0
        for event in pygame.event.get():
            login_screen.process_event(event)
            if event.type == pygame.QUIT:
                login_screen.quit_requested = True
            elif event.type == pygame.VIDEORESIZE:
                screen_size = (max(1, event.w), max(1, event.h))
                pygame.display.set_mode(screen_size, display_flags)
                ctx.viewport = (0, 0, *screen_size)
                world_surface, hud_surface = allocate_frame_surfaces(pygame, screen_size)
                login_screen.resize(screen_size)

        if login_screen.quit_requested:
            pygame.quit()
            return 0

        login_screen.update(dt)

        world_surface.fill((0, 0, 0))
        login_screen.draw(world_surface)

        compositor.clear((0.0, 0.0, 0.0, 1.0))
        composite_world(compositor, world_surface)
        pygame.display.flip()

        if login_screen.bootstrap:
            bootstrap = login_screen.bootstrap
            break

        await asyncio.sleep(0.005)

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
    pending_asset_saves: dict[UUID, list[PendingAssetSave]] = {}

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

    def on_view_asset(
        asset_id: UUID,
        asset_type: int,
        task_id: UUID | None = None,
        item_id: UUID | None = None,
    ) -> None:
        try:
            client.bus.dispatch(
                RequestAssetData(
                    asset_id=asset_id,
                    asset_type=asset_type,
                    task_id=task_id,
                    item_id=item_id,
                )
            )
        except (BusError, BusDeliveryError, RuntimeError, ValueError) as exc:
            scene.apply_chat_alert(
                ChatAlert(region_handle=client.current_handle or 0, message=str(exc))
            )

    def on_save_asset(selection: ObjectAssetSelection, target_path: Path | None = None) -> None:
        queue_asset_save(selection, target_path=target_path)
        on_view_asset(
            selection.asset_id,
            selection.asset_type,
            selection.task_id,
            selection.item_id,
        )

    def on_sync_object_to_folder(task_id: UUID, local_id: int, path: Path | None = None) -> None:
        asyncio.create_task(sync_object_to_folder(task_id, local_id, path))

    async def sync_object_to_folder(
        task_id: UUID,
        local_id: int,
        path: Path | None,
    ) -> None:
        """Write an object's whole contents to a folder, through the engine.

        This used to queue the *text* rows the inspector panel happened to be
        showing through the asset viewer, one at a time. Everything else in
        the object -- textures, sounds, animations, body parts -- had no way
        out of the window at all, though ``--all-assets`` had covered them
        from the CLI for weeks.

        Going through :func:`pull_object_to_folder` closes that and three
        smaller gaps with it: the engine re-reads the inventory itself rather
        than trusting the panel, it records the bindings a later push needs,
        and it names the files by the one shared rule, so pushing the folder
        back updates the rows it came from instead of creating new ones.
        """
        session = client.current
        handle = client.current_handle or 0

        def report(message: str) -> None:
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message=f"Sync: {message}"))

        if session is None:
            report("not connected.")
            return

        folder = sync_folder_for_task(task_id, path)
        outcome = await pull_object_to_folder(
            client,
            task_id=task_id,
            local_id=local_id,
            folder=folder,
            # Everything, not only what this client can author. The rest comes
            # out read-only -- push refuses to send it back -- which is what
            # makes "save all" safe to mean all.
            include_binary=True,
            on_progress=report,
        )
        for name, reason in outcome.conflicts:
            report(f"conflict on {name}: {reason}")
        for name, reason in outcome.failed:
            report(f"{name} failed: {reason}")
        report(f"saved to {folder}: {outcome.summary()}")

    def queue_asset_save(
        selection: ObjectAssetSelection,
        *,
        target_path: Path | None = None,
        target_dir: Path | None = None,
    ) -> None:
        if target_path is None:
            target_path = _download_path_for_selection(selection, target_dir=target_dir)
        pending_asset_saves.setdefault(selection.asset_id, []).append(
            PendingAssetSave(selection=selection, target_path=target_path)
        )
        scene.apply_chat_alert(
            ChatAlert(
                region_handle=client.current_handle or 0,
                message=f"Saving {selection.item_name} to {target_path}",
            )
        )

    def on_upload_files(path: Path | None = None) -> None:
        asyncio.create_task(upload_files_from_path(path))

    async def upload_files_from_path(path: Path | None = None) -> None:
        session = client.current
        handle = client.current_handle or 0
        if session is None:
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message="Upload is not connected."))
            return
        root_folder_id = session.bootstrap.inventory_root_folder_id
        if root_folder_id is None:
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message="Upload needs an inventory root folder.")
            )
            return
        upload_path = _resolve_user_path(path or DEFAULT_ASSET_UPLOAD_DIR)
        if upload_path.is_dir():
            upload_path.mkdir(parents=True, exist_ok=True)
            files = tuple(path for path in sorted(upload_path.iterdir()) if path.is_file())
        elif upload_path.is_file():
            files = (upload_path,)
        else:
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            files = ()
            if path is None:
                upload_path.mkdir(parents=True, exist_ok=True)
        files = tuple(path for path in files if _upload_kind_for_path(path) is not None)
        if not files:
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=handle,
                    message=f"No uploadable files at {upload_path} (.lsl, .txt, .nc).",
                )
            )
            return
        try:
            caps = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
                session.bootstrap.seed_capability,
                ["NewFileAgentInventory"],
                udp_listen_port=session.caps_udp_listen_port,
                user_agent="Vibestorm",
            )
        except CapabilityError as exc:
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message=str(exc)))
            return
        upload_url = caps.get("NewFileAgentInventory")
        if not upload_url:
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message="NewFileAgentInventory is not available.")
            )
            return
        uploader = AssetUploadClient(timeout_seconds=20.0)
        uploaded = 0
        for path in files:
            kind = _upload_kind_for_path(path)
            if kind is None:
                continue
            asset_type, inventory_type = kind
            try:
                result = await uploader.upload_new_file(
                    upload_url,
                    NewFileInventoryRequest(
                        folder_id=root_folder_id,
                        name=path.name,
                        description=f"Uploaded by Vibestorm from {path}",
                        asset_type=asset_type,
                        inventory_type=inventory_type,
                    ),
                    path.read_bytes(),
                    udp_listen_port=session.caps_udp_listen_port,
                    user_agent="Vibestorm",
                )
            except (AssetUploadError, OSError) as exc:
                scene.apply_chat_alert(
                    ChatAlert(region_handle=handle, message=f"Upload failed for {path.name}: {exc}")
                )
                continue
            uploaded += 1
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=handle,
                    message=(
                        f"Uploaded {path.name}: asset={result.new_asset_id} "
                        f"item={result.new_inventory_item_id}"
                    ),
                )
            )
        scene.apply_chat_alert(
            ChatAlert(region_handle=handle, message=f"Uploaded {uploaded}/{len(files)} file(s).")
        )

    def on_upload_object_files(
        task_id: UUID,
        asset_rows: dict[str, ObjectAssetSelection],
        path: Path | None = None,
    ) -> None:
        # ``asset_rows`` is what the inspector panel happens to be showing.
        # The engine re-reads the object's inventory for itself, which is the
        # point: it then sees rows the panel never listed, and the bindings
        # recorded in the folder outrank every name on either side.
        asyncio.create_task(sync_files_to_object_task_inventory(task_id, path))

    async def sync_files_to_object_task_inventory(
        task_id: UUID,
        path: Path | None,
    ) -> None:
        """Push a folder into an object, through the shipped sync engine.

        This used to be ~180 lines of its own: its own name matching, its own
        row creation, its own upload loop. All of that now lives in
        ``vibestorm.sync`` and is exercised headlessly against a live
        simulator, so the button and the CLI cannot drift apart -- and the
        button inherits what the engine learned. In particular it stops
        matching on the file's stem, which misses whenever an in-world item is
        named with its suffix already (they routinely are), and a miss here
        does not fail: it creates a *second* row beside the one the file came
        from.
        """
        session = client.current
        handle = client.current_handle or 0

        def report(message: str) -> None:
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message=f"Sync: {message}")
            )

        if session is None:
            report("not connected.")
            return
        obj = session.world_view.objects.get(task_id)
        if obj is None:
            report("the object is not in view, so its inventory cannot be read.")
            return

        folder = sync_folder_for_task(task_id, path)
        if not folder.is_dir():
            report(f"folder not found: {folder}")
            return

        try:
            caps = await resolve_sync_caps(session)
        except CapabilityError as exc:
            report(f"caps: {exc}")
            return
        if not caps.script and not caps.notecard:
            report("no task inventory capabilities are available.")
            return

        outcome = await push_folder_to_object(
            client,
            session,
            handle=handle,
            task_id=task_id,
            local_id=obj.local_id,
            folder=folder,
            script_cap=caps.script,
            notecard_cap=caps.notecard,
            notecard_agent_cap=caps.notecard_agent,
            agent_folder_id=session.bootstrap.inventory_root_folder_id,
            on_progress=report,
        )
        for name, reason in outcome.conflicts:
            report(f"conflict on {name}: {reason}")
        for name, reason in outcome.skipped:
            report(f"skipped {name} ({reason})")
        for name, reason in outcome.failed:
            report(f"{name} failed: {reason}")
        report(f"complete: {outcome.summary()}")

    def on_render_mode_change(mode: str) -> None:
        nonlocal renderer
        if mode == "3d":
            camera.set_sim_overview()
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
            "render_sky",
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
        on_view_asset=on_view_asset,
        on_save_asset=on_save_asset,
        on_sync_object_to_folder=on_sync_object_to_folder,
        on_upload_files=on_upload_files,
        on_upload_object_files=on_upload_object_files,
        on_render_mode_change=on_render_mode_change,
        on_render_setting_change=on_render_setting_change,
        initial_render_mode=initial_mode,
        show_diagnostics=bool(getattr(args, "diagnostics", False)),
        help_text=_load_viewer_help(),
        theme_path=Path(__file__).parent / "theme.json",
        ui_scale=ui_scale,
    )

    stop_event = asyncio.Event()

    def on_session_event(event) -> None:
        interesting_prefixes = (
            "task_inventory.",
            "xfer.",
            "transfer.",
        )
        if event.kind.startswith(interesting_prefixes):
            print(
                f"[viewer3d {event.at_seconds:8.3f}] {event.kind} {event.detail}",
                flush=True,
            )

    # Wire HUD-level subscriptions that need the hud instance.
    def _on_object_inventory_snapshot_ready(event: ObjectInventorySnapshotReady) -> None:
        # Let the scene update first (already subscribed), then register for view
        hud.register_inventory_snapshot_for_view(event.snapshot)

    client.bus.subscribe(AssetDataReady, _make_asset_data_ready_handler(hud, pending_asset_saves))
    client.bus.subscribe(ObjectInventorySnapshotReady, _on_object_inventory_snapshot_ready)

    session_task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(
                duration_seconds=86400.0,
                agent_update_interval_seconds=args.agent_update_interval,
                camera_sweep=args.camera_sweep,
                auto_upload_bakes=not args.no_auto_bake_upload,
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
    active_camera_preset: CameraPreset = args.camera

    def apply_camera_preset(preset: CameraPreset) -> None:
        nonlocal active_camera_preset
        active_camera_preset = preset
        if preset == "sim":
            camera.set_sim_overview()
            return
        avatar = _camera_avatar_entity(scene)
        if avatar is None:
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=client.current_handle or 0,
                    message="Avatar camera preset unavailable until an avatar update arrives.",
                )
            )
            return
        if preset == "avatar_behind":
            camera.set_avatar_behind(avatar.position, avatar.rotation)
        elif preset == "avatar_eye":
            camera.set_avatar_eye(avatar.position, avatar.rotation)

    def refresh_avatar_camera_preset() -> None:
        if active_camera_preset in ("avatar_behind", "avatar_eye"):
            avatar = _camera_avatar_entity(scene)
            if avatar is None:
                return
            if active_camera_preset == "avatar_behind":
                camera.set_avatar_behind(avatar.position, avatar.rotation)
            else:
                camera.set_avatar_eye(avatar.position, avatar.rotation)

    screenshot_path = Path(args.screenshot) if getattr(args, "screenshot", None) else None
    elapsed_s = 0.0
    try:
        while running and not session_task.done():
            dt = clock.tick(frame_cap) / 1000.0
            elapsed_s += dt
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
                except (BusError, WorldClientError) as exc:
                    # A movement key pressed while the circuit is coming up or
                    # tearing down reaches a WorldClient with no current session,
                    # which raises WorldClientError -- a RuntimeError, not a
                    # BusError, so it went straight past this handler and killed
                    # the viewer. Input arriving at any moment is normal; dying
                    # from it is not.
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
                if intent.camera_preset is not None:
                    apply_camera_preset(intent.camera_preset)

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

            _t = time.perf_counter
            _m0 = _t()
            scene.refresh_from_world_view(client.world_view())
            # After the refresh: the gait is derived from how far each avatar
            # moved since the last frame, so it needs this frame's positions.
            scene.advance_avatar_poses(dt)
            refresh_avatar_camera_preset()
            _m1 = _t()
            renderer.update(dt, scene)
            _m2 = _t()
            # A renderer with a flat backdrop skips the world surface: see
            # ViewerRenderer.world_background.
            background = renderer.world_background()
            if background is None:
                renderer.render(world_surface, scene)
            _m3 = _t()
            hud.update(dt, scene, client.world_view())
            _m4 = _t()
            redraw_hud(compositor, hud_surface, hud)
            _m5 = _t()

            compositor.clear(background or (0.0, 0.0, 0.0, 1.0))
            if background is None:
                composite_world(compositor, world_surface)
            _m6 = _t()
            aspect = screen_size[0] / max(1, screen_size[1])
            renderer.render_gl(scene, aspect=aspect)
            _m7 = _t()
            compositor.draw("hud", alpha=True)
            pygame.display.flip()
            _m8 = _t()
            if screenshot_path is not None and elapsed_s >= args.screenshot_after:
                save_screenshot(ctx, screen_size, screenshot_path)
                print(f"screenshot={screenshot_path}", flush=True)
                running = False
            if _PHASE_STATS is not None:
                _PHASE_STATS.add(
                    scene_refresh=_m1 - _m0,
                    renderer_update=_m2 - _m1,
                    renderer_render_sw=_m3 - _m2,
                    hud_update=_m4 - _m3,
                    # clear + blits + upload, or nothing when unchanged
                    hud_redraw=_m5 - _m4,
                    composite_world=_m6 - _m5,
                    render_gl=_m7 - _m6,
                    hud_quad_flip=_m8 - _m7,
                )
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
    client.bus.subscribe(MeshAssetReady, scene.apply_mesh_asset_ready)
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
    client.bus.subscribe(ParcelPropertiesReceived, scene.apply_parcel_properties)
    client.bus.subscribe(ParcelOverlayReceived, scene.apply_parcel_overlay)
    client.bus.subscribe(EventQueueEventReceived, scene.apply_event_queue_event)
    client.bus.subscribe(AvatarAnimationReceived, scene.apply_avatar_animation)
    client.bus.subscribe(ObjectAnimationReceived, scene.apply_object_animation)
    client.bus.subscribe(AttachedSoundReceived, scene.apply_attached_sound)
    client.bus.subscribe(AttachedSoundGainChanged, scene.apply_attached_sound_gain_change)
    client.bus.subscribe(SoundTriggered, scene.apply_sound_trigger)


def _make_asset_data_ready_handler(
    hud,
    pending_asset_saves: dict[UUID, list[PendingAssetSave]] | None = None,
):
    def _on_asset_data_ready(event: AssetDataReady) -> None:
        # Find item_name from hud's known asset map (best-effort). Entries are:
        # (asset_id, asset_type, item_name, task_id, item_id).
        item_name = ""
        for entry in hud._inspector_item_asset_map.values():
            aid, _atype, name, _task_id, _item_id = entry
            if aid == event.asset_id:
                item_name = name
                break
        if pending_asset_saves is not None:
            for pending_save in pending_asset_saves.pop(event.asset_id, []):
                _write_asset_save(pending_save.target_path, event.data)
                print(
                    "[viewer3d] asset.save "
                    f"name={pending_save.selection.item_name!r} path={pending_save.target_path}",
                    flush=True,
                )
        hud.show_asset_data(
            event.asset_id,
            event.asset_type,
            event.data,
            item_name=item_name,
        )

    return _on_asset_data_ready


def _with_render_cache_clear(handler):
    def _wrapped(event):
        clear_tile_cache()
        handler(event)

    return _wrapped


def _download_path_for_selection(
    selection: ObjectAssetSelection,
    *,
    target_dir: Path | None = None,
) -> Path:
    if target_dir is None:
        base_dir = Path.cwd() / DEFAULT_ASSET_DOWNLOAD_DIR
        object_label = (
            _safe_filename(str(selection.task_id)) if selection.task_id is not None else "agent-assets"
        )
        directory = base_dir / object_label
    else:
        directory = _resolve_user_path(target_dir)
    name = _safe_filename(selection.item_name or str(selection.asset_id))
    suffix = _asset_file_suffix(selection.asset_type)
    if not name.lower().endswith(suffix):
        name = f"{name}{suffix}"
    return directory / name


def _write_asset_save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def sync_folder_for_task(task_id: UUID, path: Path | None) -> Path:
    """The folder a sync of ``task_id`` works in.

    ``path`` comes from the inspector panel and can be any of four things,
    which is why this is not a one-liner: absent, meaning the object's own
    download folder; a directory, used as given; a *file* inside the folder
    the user meant, which is what a file picker hands back; or something that
    is not there at all.

    That last case is why "not a directory" is not enough to justify taking
    the parent. A mistyped folder name would then quietly resolve to the
    folder above it and push whatever *that* holds into the object. It is
    returned unchanged instead, so the caller reports it missing under the
    name the user actually gave.
    """
    if path is None:
        return _resolve_user_path(DEFAULT_ASSET_DOWNLOAD_DIR / _safe_filename(str(task_id)))
    if path.is_file():
        return _resolve_user_path(path.parent)
    return _resolve_user_path(path)


def _resolve_user_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


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
    try:
        raise SystemExit(main())
    except LoginError as exc:
        print(f"login_error={exc}")
        raise SystemExit(10) from exc
