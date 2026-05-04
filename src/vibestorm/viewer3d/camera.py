"""2D camera transform: world meters ↔ screen pixels.

Pygame-free. World axes follow SL convention (x east, y north, z up); the
viewer is a top-down projection so we only care about (x, y). Screen y
runs top-to-bottom, so the transform flips y to keep north pointing up.
"""

from __future__ import annotations

from dataclasses import dataclass

REGION_SIZE_METERS: float = 256.0


@dataclass(slots=True)
class Camera:
    world_center: tuple[float, float]  # meters; the world point at screen center
    zoom: float                         # pixels per meter
    screen_size: tuple[int, int]        # (width, height) in pixels

    # ----- transform --------------------------------------------------------

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

    # ----- mutations --------------------------------------------------------

    def center_on(self, world_x: float, world_y: float) -> None:
        self.world_center = (float(world_x), float(world_y))

    def pan_screen(self, dx_px: float, dy_px: float) -> None:
        """Pan by (dx, dy) screen pixels (positive dx = move view right)."""
        cx, cy = self.world_center
        self.world_center = (cx - dx_px / self.zoom, cy + dy_px / self.zoom)

    def zoom_at_screen(self, screen_x: float, screen_y: float, factor: float) -> None:
        """Zoom around a screen anchor point. The world point under the cursor stays put."""
        if factor <= 0:
            raise ValueError("zoom factor must be positive")
        anchor_world = self.screen_to_world(screen_x, screen_y)
        self.zoom *= factor
        # After changing zoom, recenter so anchor_world maps back to (screen_x, screen_y).
        cx_old, cy_old = self.world_center
        sx_after, sy_after = self.world_to_screen(*anchor_world)
        # The drift in screen coords after the zoom: subtract the anchor.
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


__all__ = ["Camera", "REGION_SIZE_METERS"]
