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
from vibestorm.caps.task_inventory_upload_client import (
    TaskInventoryUploadClient,
    TaskInventoryUploadError,
)
from vibestorm.login.client import LoginError
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import DEFAULT_SCRIPT_ASSET_ID
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient, WorldClientError
from vibestorm.sync.naming import (
    TEXT_ASSET_TYPES,
    asset_file_suffix,
    match_files_to_rows,
    safe_filename,
    upload_kind_for_path,
)
from vibestorm.viewer3d.camera import Camera, CameraPreset
from vibestorm.viewer3d.gl_compositor import GLCompositor
from vibestorm.viewer3d.hud import HUD, ObjectAssetSelection
from vibestorm.viewer3d.input import handle_event
from vibestorm.viewer3d.perspective import PerspectiveRenderer
from vibestorm.viewer3d.render import clear_tile_cache
from vibestorm.viewer3d.renderer import TopDownRenderer, ViewerRenderer
from vibestorm.viewer3d.scene import Scene
from vibestorm.world.object_inventory import ObjectInventorySnapshot

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

#: Both names OpenSim registers for updating a script in object inventory,
#: current one first. ``BunchOfCaps`` registers ``UpdateScriptTask`` and then
#: ``UpdateScriptTaskInventory`` against the same handler, with the second
#: marked ``//legacy`` in the source. Asking only for the legacy alias works
#: today but leaves the client depending on the name OpenSim has already
#: labelled as the one it keeps for compatibility.
SCRIPT_TASK_CAP_NAMES = ["UpdateScriptTask", "UpdateScriptTaskInventory"]


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

    def on_save_object_text_assets(
        selections: tuple[ObjectAssetSelection, ...],
        target_dir: Path | None = None,
    ) -> None:
        queued = 0
        for selection in selections:
            if selection.asset_type not in TEXT_ASSET_TYPES or selection.asset_id.int == 0:
                continue
            queue_asset_save(selection, target_dir=target_dir)
            on_view_asset(
                selection.asset_id,
                selection.asset_type,
                selection.task_id,
                selection.item_id,
            )
            queued += 1
        scene.apply_chat_alert(
            ChatAlert(
                region_handle=client.current_handle or 0,
                message=f"Queued {queued} object text asset download(s).",
            )
        )

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
        asyncio.create_task(sync_files_to_object_task_inventory(task_id, asset_rows, path))

    async def sync_files_to_object_task_inventory(
        task_id: UUID,
        asset_rows: dict[str, ObjectAssetSelection],
        path: Path | None,
    ) -> None:
        session = client.current
        handle = client.current_handle or 0
        if session is None:
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message="Sync: not connected."))
            return
        try:
            caps = await CapabilityClient(timeout_seconds=10.0).resolve_seed_caps(
                session.bootstrap.seed_capability,
                SCRIPT_TASK_CAP_NAMES + ["UpdateNotecardTaskInventory"],
                udp_listen_port=session.caps_udp_listen_port,
                user_agent="Vibestorm",
            )
        except CapabilityError as exc:
            scene.apply_chat_alert(ChatAlert(region_handle=handle, message=f"Sync caps: {exc}"))
            return
        script_cap = _first_resolved(caps, SCRIPT_TASK_CAP_NAMES)
        notecard_cap = caps.get("UpdateNotecardTaskInventory")
        if not script_cap and not notecard_cap:
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message="Sync: no task inventory caps available.")
            )
            return
        safe_task = _safe_filename(str(task_id))
        if path is None:
            upload_dir = _resolve_user_path(DEFAULT_ASSET_DOWNLOAD_DIR / safe_task)
        elif path.is_dir():
            upload_dir = _resolve_user_path(path)
        else:
            upload_dir = _resolve_user_path(path.parent)
        if not upload_dir.is_dir():
            scene.apply_chat_alert(
                ChatAlert(region_handle=handle, message=f"Sync: folder not found: {upload_dir}")
            )
            return
        matched, unmatched = _match_files_to_task_selections(upload_dir, asset_rows)

        # Files with no row yet: make the row, then treat them like any other
        # match. Without this step a whole-folder sync can only ever update
        # what the object already contains, which is not what "upload a folder"
        # means. Only scripts can be created this way -- RezScript is
        # script-specific, and there is no equivalent create for notecards.
        skipped: list[tuple[Path, str]] = []
        if unmatched:
            to_create = [f for f in unmatched if f.suffix.lower() == ".lsl"]
            skipped.extend(
                (f, "no matching inventory item, and only .lsl rows can be created")
                for f in unmatched
                if f.suffix.lower() != ".lsl"
            )
            if to_create and not script_cap:
                skipped.extend((f, "no script task cap to fill it in") for f in to_create)
                to_create = []
            if to_create:
                obj = session.world_view.objects.get(task_id)
                if obj is None:
                    skipped.extend((f, "object not in view; cannot create") for f in to_create)
                else:
                    created, create_skipped = await _create_task_script_rows(
                        client,
                        session,
                        scene,
                        handle=handle,
                        task_id=task_id,
                        local_id=obj.local_id,
                        files=to_create,
                    )
                    matched.extend(created)
                    skipped.extend(create_skipped)

        if not matched:
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=handle,
                    message=f"Sync: no file names match inventory items in {upload_dir}",
                )
            )
            for file_path, reason in skipped:
                scene.apply_chat_alert(
                    ChatAlert(
                        region_handle=handle,
                        message=f"Sync: skipped {file_path.name} ({reason})",
                    )
                )
            return
        for file_path, reason in skipped:
            scene.apply_chat_alert(
                ChatAlert(
                    region_handle=handle,
                    message=f"Sync: skipped {file_path.name} ({reason})",
                )
            )
        created_count = sum(1 for _f, sel in matched if sel.item_key == _CREATED_ROW_KEY)
        uploader = TaskInventoryUploadClient(timeout_seconds=20.0)
        uploaded = 0
        failed = 0
        for file_path, selection in matched:
            if selection.item_id is None:
                scene.apply_chat_alert(
                    ChatAlert(
                        region_handle=handle,
                        message=f"Sync: skipped {file_path.name} (item_id unknown)",
                    )
                )
                continue
            try:
                data = file_path.read_bytes()
                if selection.asset_type == 10:
                    if not script_cap:
                        scene.apply_chat_alert(
                            ChatAlert(
                                region_handle=handle,
                                message=f"Sync: skipped {file_path.name} (no script task cap)",
                            )
                        )
                        continue
                    result = await uploader.upload_task_script(
                        script_cap,
                        item_id=selection.item_id,
                        task_id=task_id,
                        script_bytes=data,
                        udp_listen_port=session.caps_udp_listen_port,
                    )
                    if result.compiled:
                        msg = f"Sync: {file_path.name} → compiled OK (item={result.new_item_id})"
                    else:
                        errs = "; ".join(str(e) for e in result.errors[:3])
                        msg = f"Sync: {file_path.name} → compile errors: {errs}"
                    scene.apply_chat_alert(ChatAlert(region_handle=handle, message=msg))
                else:
                    if not notecard_cap:
                        scene.apply_chat_alert(
                            ChatAlert(
                                region_handle=handle,
                                message=f"Sync: skipped {file_path.name} (UpdateNotecardTaskInventory not available)",
                            )
                        )
                        continue
                    result = await uploader.upload_task_notecard(
                        notecard_cap,
                        item_id=selection.item_id,
                        task_id=task_id,
                        notecard_bytes=data,
                        udp_listen_port=session.caps_udp_listen_port,
                    )
                    scene.apply_chat_alert(
                        ChatAlert(
                            region_handle=handle,
                            message=f"Sync: {file_path.name} → OK (item={result.new_item_id})",
                        )
                    )
                uploaded += 1
            except (TaskInventoryUploadError, OSError) as exc:
                scene.apply_chat_alert(
                    ChatAlert(
                        region_handle=handle, message=f"Sync: {file_path.name} failed: {exc}"
                    )
                )
                failed += 1
        scene.apply_chat_alert(
            ChatAlert(
                region_handle=handle,
                message=(
                    f"Sync complete: {uploaded} uploaded, {created_count} created, "
                    f"{len(skipped)} skipped, {failed} failed."
                ),
            )
        )

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
        on_save_object_text_assets=on_save_object_text_assets,
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


#: Marks a selection this session created rather than read off the object, so
#: the completion line can report creates separately from updates.
_CREATED_ROW_KEY = "__created__"

#: How long to wait for the object's inventory to come back after creating
#: rows. The reply is a RequestXfer round trip, not a single packet.
_TASK_INVENTORY_REFRESH_TIMEOUT = 15.0


async def _await_object_inventory(
    client: WorldClient,
    local_id: int,
    *,
    timeout: float = _TASK_INVENTORY_REFRESH_TIMEOUT,
) -> ObjectInventorySnapshot | None:
    """Request one object's task inventory and wait for the snapshot.

    Subscribes *before* dispatching the request: the snapshot can arrive while
    this coroutine is still being set up, and a subscription taken afterwards
    would miss it and then wait out the full timeout.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[ObjectInventorySnapshot] = loop.create_future()

    def _on_ready(event: ObjectInventorySnapshotReady) -> None:
        if event.snapshot.local_id == local_id and not future.done():
            future.set_result(event.snapshot)

    subscription = client.bus.subscribe(ObjectInventorySnapshotReady, _on_ready)
    try:
        client.bus.dispatch(RequestObjectInventory(local_id))
        return await asyncio.wait_for(future, timeout)
    except (TimeoutError, BusError):
        return None
    finally:
        subscription.cancel()


async def _create_task_script_rows(
    client: WorldClient,
    session: object,
    scene: Scene,
    *,
    handle: int,
    task_id: UUID,
    local_id: int,
    files: list[Path],
    timeout: float = _TASK_INVENTORY_REFRESH_TIMEOUT,
) -> tuple[list[tuple[Path, ObjectAssetSelection]], list[tuple[Path, str]]]:
    """Create one empty script per file, then resolve the rows they became.

    Returns the files that now have a row to upload onto, plus the ones that
    did not get one and why.

    The rows are created in a batch and the inventory is re-read **once**. A
    read per file would be correct too, but each read is an Xfer round trip, so
    a ten-file folder would spend ten of them to learn what one tells us.

    New rows are identified by diffing item ids against a baseline rather than
    by looking for the name: an object may already hold a script called
    ``foo.lsl`` while the file ``foo.lsl`` failed to match for some other
    reason, and then matching on name would upload over the wrong row.
    """
    before = await _await_object_inventory(client, local_id, timeout=timeout)
    if before is None:
        return [], [(f, "could not read object inventory before creating") for f in files]
    baseline = {item.item_id for item in before.items if item.item_id is not None}

    for file_path in files:
        packet = session.build_rez_script_packet(  # type: ignore[attr-defined]
            part_id=task_id,
            local_id=local_id,
            name=file_path.stem,
            description="created by Vibestorm folder sync",
        )
        client.queue_outbound_packet(handle, packet)
        scene.apply_chat_alert(
            ChatAlert(region_handle=handle, message=f"Sync: creating {file_path.name}")
        )

    after = await _await_object_inventory(client, local_id, timeout=timeout)
    if after is None:
        return [], [(f, "created, but the object inventory did not come back") for f in files]

    added = [
        item
        for item in after.items
        if item.item_id is not None and item.item_id not in baseline
    ]
    by_name = {item.name: item for item in added}

    created: list[tuple[Path, ObjectAssetSelection]] = []
    skipped: list[tuple[Path, str]] = []
    for file_path in files:
        item = by_name.pop(file_path.stem, None)
        if item is None:
            skipped.append((file_path, "the sim did not create a row for it"))
            continue
        created.append(
            (
                file_path,
                ObjectAssetSelection(
                    item_key=_CREATED_ROW_KEY,
                    asset_id=item.asset_id or DEFAULT_SCRIPT_ASSET_ID,
                    asset_type=10,
                    item_name=item.name,
                    task_id=task_id,
                    item_id=item.item_id,
                ),
            )
        )
    return created, skipped


def _match_files_to_task_selections(
    upload_dir: Path,
    asset_rows: dict[str, ObjectAssetSelection],
) -> tuple[list[tuple[Path, ObjectAssetSelection]], list[Path]]:
    """Match uploadable files in upload_dir to task inventory asset rows by name.

    Delegates the rule itself to ``vibestorm.sync.naming`` so the pull
    direction writes the names this matches back.
    """
    rows = [
        selection
        for selection in asset_rows.values()
        if selection.asset_type in TEXT_ASSET_TYPES
    ]
    files = [
        path
        for path in upload_dir.iterdir()
        if path.is_file() and _upload_kind_for_path(path) is not None
    ]
    return match_files_to_rows(
        files,
        rows,
        name_of=lambda selection: selection.item_name or "",
        asset_type_of=lambda selection: selection.asset_type,
    )


def _first_resolved(caps: dict[str, str], names: list[str]) -> str | None:
    """The first of ``names`` the simulator actually resolved.

    Order is preference, not fallback ranking: a sim that offers both a
    current and a legacy name for one handler should be talked to by its
    current name.
    """
    for name in names:
        url = caps.get(name)
        if url:
            return url
    return None


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
