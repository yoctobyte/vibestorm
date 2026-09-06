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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

REGION_SIZE_METERS: float = 256.0

CameraMode = Literal["map", "orbit", "eye", "free"]
CameraPreset = Literal["sim", "avatar_behind", "avatar_eye"]

DEFAULT_FOV_Y_RADIANS: float = math.radians(60.0)
DEFAULT_NEAR_PLANE_M: float = 0.1
DEFAULT_FAR_PLANE_M: float = 1024.0
DEFAULT_UP: tuple[float, float, float] = (0.0, 0.0, 1.0)

#: How high the camera is held over the ground it would otherwise be inside.
#:
#: The near plane is 0.1 m, so anything below about that lets the hillside the
#: camera is standing in clip through the middle of the picture. Half a metre
#: leaves the ground where it belongs -- underfoot -- without the camera
#: visibly floating when it is pulled in against a slope.
GROUND_CLEARANCE_M: float = 0.5

#: How high the ground is at a world point, or `None` where there is none.
#:
#: A callable rather than a heightmap so the camera keeps knowing nothing about
#: terrain decoding, and so the answer can be `None`: the drawn terrain stops
#: at the region's edge, and a camera out over the void is not inside anything.
GroundHeight = Callable[[float, float], "float | None"]


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


def eye_clear_of_the_ground(
    eye: tuple[float, float, float],
    target: tuple[float, float, float],
    ground_height: GroundHeight,
    *,
    clearance_m: float = GROUND_CLEARANCE_M,
    step_m: float = 1.0,
) -> tuple[float, float, float]:
    """`eye`, pulled in towards `target` until it is out of the ground.

    Pulled in rather than lifted: shortening the distance keeps the direction
    the viewer is looking from, which is what they asked for, where raising the
    eye silently changes the angle. It is also what a camera in any viewer does
    when you back it into a wall.

    The march starts at the target and stops at the first blocked sample, which
    is a ray cast and not a bisection: a heightfield along a line is not
    monotone, and a bisection would happily jump a ridge and put the camera on
    the far side of it. `step_m` is a metre because the samples are a metre
    apart and a finer march would only be reading the same four samples again;
    the bisection afterwards is what makes the result smooth enough not to pop
    as the camera turns.

    If the target itself is underground there is nothing to pull back to -- the
    avatar is already inside the hill -- so the eye is lifted straight up
    instead, which at least draws the world from outside it.
    """

    def blocked(point: tuple[float, float, float]) -> bool:
        ground = ground_height(point[0], point[1])
        return ground is not None and point[2] < ground + clearance_m

    if not blocked(eye):
        return eye

    dx = eye[0] - target[0]
    dy = eye[1] - target[1]
    dz = eye[2] - target[2]
    span = math.sqrt(dx * dx + dy * dy + dz * dz)

    def along(t: float) -> tuple[float, float, float]:
        return (target[0] + dx * t, target[1] + dy * t, target[2] + dz * t)

    if span < 1e-6 or blocked(target):
        ground = ground_height(eye[0], eye[1])
        if ground is None:
            return eye
        return (eye[0], eye[1], ground + clearance_m)

    # The eye is known to be blocked, so t=1 is the far end of the bracket
    # whether or not the coarse march finds something nearer.
    clear_t = 0.0
    blocked_t = 1.0
    steps = max(2, int(span / max(step_m, 1e-3)))
    for i in range(1, steps):
        t = i / steps
        if blocked(along(t)):
            blocked_t = t
            break
        clear_t = t

    for _ in range(8):
        middle = 0.5 * (clear_t + blocked_t)
        if blocked(along(middle)):
            blocked_t = middle
        else:
            clear_t = middle
    return along(clear_t)


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

    #: The ground under this camera, if anyone has told it. Set by whoever has
    #: the region's terrain in hand -- the renderer, once a frame -- because a
    #: camera that imported the heightmap would be a camera that knew how to
    #: decode LayerData. `None` means nobody has said, and then the camera goes
    #: wherever it is put.
    ground_height: GroundHeight | None = field(
        default=None, compare=False, repr=False
    )

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

    def orbit_rotate(self, dx_px: float, dy_px: float, *, sensitivity: float = 0.01) -> None:
        """Rotate the orbit camera from a mouse-drag delta."""
        self.yaw -= dx_px * sensitivity
        limit = math.radians(89.0)
        self.pitch = max(-limit, min(limit, self.pitch + dy_px * sensitivity))

    def orbit_zoom(self, steps: float, *, factor_per_step: float = 1.12) -> None:
        """Move the orbit camera closer/farther from its target."""
        if factor_per_step <= 1.0:
            raise ValueError("factor_per_step must be > 1")
        if steps > 0:
            self.distance /= factor_per_step ** steps
        elif steps < 0:
            self.distance *= factor_per_step ** abs(steps)
        self.distance = max(2.0, min(512.0, self.distance))

    def orbit_pan(self, dx_px: float, dy_px: float, *, sensitivity: float = 0.08) -> None:
        """Pan the orbit target along world X/Y from a mouse-drag delta."""
        tx, ty, tz = self.target
        scale = sensitivity * max(1.0, self.distance / 50.0)
        self.target = (tx - dx_px * scale, ty + dy_px * scale, tz)

    def orbit_lift(self, dz_m: float) -> None:
        """Move the orbit target vertically."""
        tx, ty, tz = self.target
        self.target = (tx, ty, tz + dz_m)

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

    def set_sim_overview(self) -> None:
        """Frame the whole region from the current orbit camera."""
        self.set_mode("orbit")
        self.target = (128.0, 128.0, 24.0)
        self.distance = 190.0
        self.yaw = math.radians(45.0)
        self.pitch = math.radians(58.0)

    def set_avatar_behind(
        self,
        position: tuple[float, float, float],
        rotation: tuple[float, float, float, float] | None,
        *,
        distance_m: float = 10.0,
    ) -> None:
        """Third-person camera behind the avatar, looking over the head."""
        forward = _avatar_forward(rotation)
        px, py, pz = position
        eye = (
            px - forward[0] * distance_m,
            py - forward[1] * distance_m,
            pz + 3.2,
        )
        target = (px + forward[0] * 1.8, py + forward[1] * 1.8, pz + 1.8)
        self.set_mode("free")
        self.eye_position = eye
        self.target = target

    def set_avatar_eye(
        self,
        position: tuple[float, float, float],
        rotation: tuple[float, float, float, float] | None,
        *,
        eye_height_m: float = 1.65,
    ) -> None:
        """First-person eye camera from the avatar's current rotation."""
        forward = _avatar_forward(rotation)
        px, py, pz = position
        eye = (px, py, pz + eye_height_m)
        target = (
            eye[0] + forward[0] * 8.0,
            eye[1] + forward[1] * 8.0,
            eye[2] + forward[2] * 8.0,
        )
        self.set_mode("eye")
        self.eye_position = eye
        self.target = target

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

    def eye(self) -> tuple[float, float, float]:
        """Where the camera actually is, in the active mode.

        - ``orbit``: derived from yaw/pitch/distance around target.
        - ``eye`` / ``free``: ``eye_position``, as set.
        - ``map``: above the target, matching the ortho framing.

        Held clear of the ground where `ground_height` says there is any, in
        the two modes that look *at* something: orbit, and the ``free`` the
        avatar-behind preset uses. Not in ``eye``, which is first person -- the
        eye there is the avatar's own head, and where that goes is the
        simulator's business, not this camera's.

        One method rather than the same three-way branch spelled out at each
        call site. The renderer wants the eye for the water pass, `pick` wants
        it for the ray, and `view_matrix` wants it for the picture; when they
        were three copies, a camera held off the ground in the picture would
        still have been underground in the ray.
        """
        if self.mode == "orbit":
            raw = self.orbit_eye()
        elif self.mode in ("eye", "free"):
            raw = self.eye_position
        else:  # "map" -- top-down from above target
            tx, ty, tz = self.target
            return (tx, ty, tz + max(self.distance, 1.0))
        if self.ground_height is None or self.mode == "eye":
            return raw
        return eye_clear_of_the_ground(raw, self.target, self.ground_height)

    def view_matrix(self) -> tuple[float, ...]:
        """4x4 column-major view matrix for the active 3D mode.

        Callers should not draw 3D in map mode, but for completeness that
        branch returns a top-down view from above the target.
        """
        if self.mode == "map":
            return look_at(self.eye(), self.target, up=(0.0, 1.0, 0.0))
        return look_at(self.eye(), self.target)

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


def _avatar_forward(
    rotation: tuple[float, float, float, float] | None,
) -> tuple[float, float, float]:
    if rotation is None:
        return (1.0, 0.0, 0.0)
    qx, qy, qz, qw = rotation
    # Rotate local +X by quaternion q=(x,y,z,w).
    vx, vy, vz = (1.0, 0.0, 0.0)
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return _normalize(
        (
            vx + qw * tx + qy * tz - qz * ty,
            vy + qw * ty + qz * tx - qx * tz,
            vz + qw * tz + qx * ty - qy * tx,
        )
    )


__all__ = [
    "Camera",
    "Camera3D",
    "CameraMode",
    "CameraPreset",
    "REGION_SIZE_METERS",
]
