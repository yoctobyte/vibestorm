"""Mode-aware camera for the viewer3d fork.

The 2D top-down map view is one of several rendering modes; the camera
needs to support all of them through a single object so the renderer
and input layers don't fork on mode. ``mode = "map"`` is orthographic
top-down with pan/zoom — exactly what the prior 2D ``Camera`` did.

The 3D modes (``orbit``, ``eye``, ``free``) build a 4x4 view matrix
through ``view_matrix()``, paired with ``projection_matrix(aspect)``
for a standard right-handed perspective. Step 6 wires only ``orbit``
end-to-end; ``eye`` and ``free`` reuse ``eye_position`` and ``target``
directly so they will work as soon as input handlers move those
fields.

World axes follow SL convention: X east, Y north, Z up. ``up`` for
the view matrix is ``(0, 0, 1)``. Screen y runs top-to-bottom
(pygame); ``world_to_screen`` flips Y so north is up. The 3D renderer
gets matrices in column-major order — the GLSL convention — so
moderngl can write them straight to a uniform.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Literal

REGION_SIZE_METERS: float = 256.0

CameraMode = Literal["map", "orbit", "eye", "free"]

DEFAULT_FOV_Y_RADIANS: float = math.radians(60.0)
DEFAULT_NEAR_PLANE_M: float = 0.1
DEFAULT_FAR_PLANE_M: float = 1024.0
DEFAULT_UP: tuple[float, float, float] = (0.0, 0.0, 1.0)


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length == 0.0:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def look_at(
    eye: tuple[float, float, float],
    target: tuple[float, float, float],
    up: tuple[float, float, float] = DEFAULT_UP,
) -> tuple[float, ...]:
    """Right-handed view matrix in column-major order (16 floats).

    The camera's local -Z aligns with ``target - eye``. Multiplying
    this matrix with a world-space point ``(eye, 1)`` yields the
    origin in camera space.
    """
    forward = _normalize(_sub(target, eye))
    side = _normalize(_cross(forward, up))
    upward = _cross(side, forward)
    return (
        side[0], upward[0], -forward[0], 0.0,
        side[1], upward[1], -forward[1], 0.0,
        side[2], upward[2], -forward[2], 0.0,
        -_dot(side, eye), -_dot(upward, eye), _dot(forward, eye), 1.0,
    )


def perspective(
    fov_y_radians: float, aspect: float, near: float, far: float
) -> tuple[float, ...]:
    """Right-handed perspective matrix in column-major order (16 floats).

    NDC depth is [-1, 1]; the projection follows the standard glm
    formulation. ``aspect`` is screen width / screen height.
    """
    if fov_y_radians <= 0.0:
        raise ValueError("fov_y_radians must be > 0")
    if aspect <= 0.0:
        raise ValueError("aspect must be > 0")
    if not 0.0 < near < far:
        raise ValueError("require 0 < near < far")
    t = math.tan(fov_y_radians / 2.0)
    return (
        1.0 / (aspect * t), 0.0, 0.0, 0.0,
        0.0, 1.0 / t, 0.0, 0.0,
        0.0, 0.0, -(far + near) / (far - near), -1.0,
        0.0, 0.0, -(2.0 * far * near) / (far - near), 0.0,
    )


def pack_mat4(m: tuple[float, ...]) -> bytes:
    """Pack a 16-float column-major matrix as little-endian float32 bytes."""
    if len(m) != 16:
        raise ValueError(f"expected 16 floats, got {len(m)}")
    return struct.pack("16f", *m)


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
        """Switch camera mode. ``map`` keeps using ``world_to_screen`` /
        ``screen_to_world`` for the top-down draw; the 3D modes drive
        ``view_matrix()`` instead."""
        self.mode = mode

    # ----- 3D matrices -----------------------------------------------------

    def orbit_eye(self) -> tuple[float, float, float]:
        """World-space eye for the orbit camera, derived from yaw/pitch/distance/target.

        ``yaw=0`` puts the camera east of the target; ``yaw=π/2`` puts
        it north. ``pitch=0`` is level with the target; ``pitch=π/2``
        places the camera directly above. The eye stays a fixed
        ``distance`` metres from ``target``.
        """
        cos_pitch = math.cos(self.pitch)
        sin_pitch = math.sin(self.pitch)
        cos_yaw = math.cos(self.yaw)
        sin_yaw = math.sin(self.yaw)
        offset = (
            self.distance * cos_pitch * cos_yaw,
            self.distance * cos_pitch * sin_yaw,
            self.distance * sin_pitch,
        )
        tx, ty, tz = self.target
        return (tx + offset[0], ty + offset[1], tz + offset[2])

    def view_matrix(self) -> tuple[float, ...]:
        """4x4 column-major view matrix for the active 3D mode.

        - ``orbit``: eye is computed from yaw/pitch/distance around target.
        - ``eye`` / ``free``: ``eye_position`` and ``target`` are used
          directly. Step 6 doesn't yet wire input for those, but the
          matrix still works once they are driven.
        - ``map``: callers should not draw 3D in map mode, but for
          completeness this returns a top-down view from above the
          target, matching the current ortho framing.
        """
        if self.mode == "orbit":
            eye = self.orbit_eye()
            return look_at(eye, self.target)
        if self.mode in ("eye", "free"):
            return look_at(self.eye_position, self.target)
        # "map" — top-down from above target
        tx, ty, tz = self.target
        return look_at((tx, ty, tz + max(self.distance, 1.0)), self.target, up=(0.0, 1.0, 0.0))

    def projection_matrix(
        self,
        aspect: float,
        *,
        fov_y_radians: float = DEFAULT_FOV_Y_RADIANS,
        near: float = DEFAULT_NEAR_PLANE_M,
        far: float = DEFAULT_FAR_PLANE_M,
    ) -> tuple[float, ...]:
        """4x4 column-major perspective matrix; convenience wrapper."""
        return perspective(fov_y_radians, aspect, near, far)


# Backwards-compatible alias. Existing call sites and tests under viewer3d/
# can continue to import ``Camera``; new code should prefer ``Camera3D``.
Camera = Camera3D


__all__ = ["Camera", "Camera3D", "CameraMode", "REGION_SIZE_METERS"]
