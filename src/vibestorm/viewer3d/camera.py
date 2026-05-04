"""Mode-aware camera for the viewer3d fork.

The 2D top-down map view is one of several rendering modes; the camera
needs to support all of them through a single object so the renderer
and input layers don't fork on mode. Today's behavior corresponds to
``mode = "map"`` — orthographic top-down with pan/zoom — and is exactly
what the prior 2D ``Camera`` class did.

The 3D modes (``orbit``, ``eye``, ``free``) are introduced as fields here.
Their pan/zoom semantics are filled in later steps; for now their
values exist so they can be configured in advance and the camera-mode
menu has something to bind to.

World axes follow SL convention: X east, Y north, Z up. Screen y runs
top-to-bottom (pygame); ``world_to_screen`` flips Y so north is up. A
3D renderer remaps to GL's frame internally — that conversion lives in
the renderer, not here, so 2D consumers keep the SL frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

REGION_SIZE_METERS: float = 256.0

CameraMode = Literal["map", "orbit", "eye", "free"]


@dataclass(slots=True)
class Camera3D:
    """Mode-aware camera. Today only ``map`` mode is fully implemented.

    ``map`` mode behaves identically to the prior 2D ``Camera`` — pan,
    zoom, fit-region, all of it. The 3D mode fields below exist so the
    camera-mode menu and 3D renderer can configure them ahead of time
    and so future steps can fill in the math without changing this
    class's shape.
    """

    # Map mode (also the default state used by the renderer when 3D modes
    # need a "home" view).
    world_center: tuple[float, float] = (128.0, 128.0)  # metres
    zoom: float = 1.0                                   # pixels per metre
    screen_size: tuple[int, int] = (800, 600)
    mode: CameraMode = "map"

    # 3D mode state. yaw / pitch are radians; distance is metres.
    yaw: float = 0.0
    pitch: float = 0.0
    distance: float = 8.0
    eye_position: tuple[float, float, float] = (128.0, 128.0, 30.0)
    target: tuple[float, float, float] = (128.0, 128.0, 22.0)

    # ----- 2D transform (Map mode) -----------------------------------------

    def world_to_screen(self, world_x: float, world_y: float) -> tuple[float, float]:
        cx, cy = self.world_center
        sw, sh = self.screen_size
        sx = (world_x - cx) * self.zoom + sw / 2.0
        sy = sh / 2.0 - (world_y - cy) * self.zoom
        return sx, sy

    def screen_to_world(self, screen_x: float, screen_y: float) -> tuple[float, float]:
        cx, cy = self.world_center
        sw, sh = self.screen_size
        wx = cx + (screen_x - sw / 2.0) / self.zoom
        wy = cy + (sh / 2.0 - screen_y) / self.zoom
        return wx, wy

    # ----- mutations -------------------------------------------------------

    def center_on(self, world_x: float, world_y: float) -> None:
        self.world_center = (float(world_x), float(world_y))

    def pan_screen(self, dx_px: float, dy_px: float) -> None:
        """Pan by (dx, dy) screen pixels (positive dx = move view right)."""
        cx, cy = self.world_center
        self.world_center = (cx - dx_px / self.zoom, cy + dy_px / self.zoom)

    def zoom_at_screen(self, screen_x: float, screen_y: float, factor: float) -> None:
        """Zoom around a screen anchor. The world point under the cursor stays put."""
        if factor <= 0:
            raise ValueError("zoom factor must be positive")
        anchor_world = self.screen_to_world(screen_x, screen_y)
        self.zoom *= factor
        cx_old, cy_old = self.world_center
        sx_after, sy_after = self.world_to_screen(*anchor_world)
        drift_x = sx_after - screen_x
        drift_y = sy_after - screen_y
        self.world_center = (cx_old + drift_x / self.zoom, cy_old - drift_y / self.zoom)

    def fit_region(self, padding_px: int = 0) -> None:
        """Zoom so a 256m region fits within (screen - 2*padding)."""
        sw, sh = self.screen_size
        usable = max(1, min(sw, sh) - 2 * padding_px)
        self.zoom = usable / REGION_SIZE_METERS

    def set_screen_size(self, screen_size: tuple[int, int]) -> None:
        self.screen_size = (max(1, int(screen_size[0])), max(1, int(screen_size[1])))

    # ----- mode selection --------------------------------------------------

    def set_mode(self, mode: CameraMode) -> None:
        """Switch camera mode. State-only change today; 3D rendering wires
        the mode-specific projection in later steps."""
        self.mode = mode


# Backwards-compatible alias. Existing call sites and tests under viewer3d/
# can continue to import ``Camera``; new code should prefer ``Camera3D``.
Camera = Camera3D


__all__ = ["Camera", "Camera3D", "CameraMode", "REGION_SIZE_METERS"]
