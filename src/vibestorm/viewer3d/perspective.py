"""3D perspective renderer for the viewer3d fork.

Pipeline:

- Software ``render(world_surface, scene)`` blits the cached map tile
  so the compositor still has a world quad even before the GL pass
  paints over it.
- ``render_gl(scene, aspect)`` draws geometry directly to the GL
  framebuffer: a textured ground floor at Z=0 (step 6b) followed by
  per-shape instanced primitive draws — one VAO per shape from
  ``vibestorm.viewer3d.meshes`` (step 7) keyed by ``SceneEntity.shape``.
  Each draw call uses the shared instance VBO, so an entity's
  ``position``/``scale``/``rotation``/``tint`` flows through model
  matrices regardless of mesh type.

Current limits:

- Lighting is first-pass directional + ambient only. Primitive normals
  are approximated from local vertex position until the mesh format
  carries authored normals.
- Per-face textures, mesh, sculpt geometry. Each is its own pipeline.
- Avatar capsules / billboards. Avatars currently fall through to
  the cube fallback (no shape classification on PCODE_AVATAR).

The class accepts an optional ``moderngl.Context``. When ``ctx`` is
``None`` (e.g. unit tests with no GL available) ``render_gl`` is a
no-op — ``render`` still draws the placeholder background and labels
so swap-mechanism tests keep working without GL.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    import moderngl
    import pygame

    from vibestorm.viewer3d.camera import Camera3D
    from vibestorm.viewer3d.scene import Scene, SceneEntity


# Sky colour used when the perspective renderer is asked for a 2D
# world surface. The fullscreen quad uploaded by the compositor sits
# under the GL pass, so this fills the sky above the horizon (and
# anywhere the 3D ground/cubes don't draw).
SKY_COLOR: tuple[int, int, int] = (60, 110, 160)


_FLOATS_PER_INSTANCE = 16 + 3  # mat4 + vec3 tint
_BYTES_PER_INSTANCE = _FLOATS_PER_INSTANCE * 4
_INITIAL_INSTANCE_CAPACITY = 1024

# Mesh used when ``SceneEntity.shape`` is ``None`` (avatars, trees with
# no shape classification, future entity kinds). Cubes are forgiving
# — wrong size is obvious, wrong shape is not catastrophic.
_DEFAULT_SHAPE_KEY: str = "cube"

# Aliases from PrimShape values to the underlying mesh used. Tube/ring
# don't have purpose-built meshes yet — fall back to the closest match.
_SHAPE_ALIASES: dict[str, str] = {
    "tube": "cube",
    "ring": "torus",
}

DEFAULT_SUN_DIRECTION: tuple[float, float, float] = (0.35, -0.55, 0.76)
AMBIENT_LIGHT: float = 0.78
DIFFUSE_LIGHT: float = 0.34


_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec3 u_sun_dir;
uniform float u_ambient_light;
uniform float u_diffuse_light;
in vec3 in_pos;
in mat4 in_model;
in vec3 in_tint;

out vec3 v_tint;
out float v_light;
out vec3 v_local_pos;
out vec3 v_local_normal;

void main() {
    vec3 local_normal = normalize(in_pos);
    vec3 world_normal = normalize(mat3(in_model) * local_normal);
    float diffuse = max(dot(world_normal, normalize(u_sun_dir)), 0.0);
    v_light = clamp(u_ambient_light + diffuse * u_diffuse_light, 0.0, 1.15);
    v_tint = in_tint;
    v_local_pos = in_pos;
    v_local_normal = local_normal;
    gl_Position = u_proj * u_view * in_model * vec4(in_pos, 1.0);
}
"""

_FRAGMENT_SHADER = """
#version 330

uniform bool u_use_texture;
uniform sampler2D u_texture;

in vec3 v_tint;
in float v_light;
in vec3 v_local_pos;
in vec3 v_local_normal;
out vec4 frag_color;

vec2 generated_uv(vec3 pos, vec3 normal) {
    vec3 axis = abs(normal);
    vec2 uv;
    if (axis.x >= axis.y && axis.x >= axis.z) {
        uv = vec2(normal.x >= 0.0 ? -pos.y : pos.y, pos.z);
    } else if (axis.y >= axis.x && axis.y >= axis.z) {
        uv = vec2(normal.y >= 0.0 ? pos.x : -pos.x, pos.z);
    } else {
        uv = vec2(pos.x, normal.z >= 0.0 ? pos.y : -pos.y);
    }
    return clamp(uv + vec2(0.5, 0.5), 0.0, 1.0);
}

void main() {
    vec3 base_color = v_tint;
    if (u_use_texture) {
        base_color = texture(u_texture, generated_uv(v_local_pos, v_local_normal)).rgb;
    }
    frag_color = vec4(base_color * v_light, 1.0);
}
"""


_GROUND_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;
in vec2 in_uv;

out vec2 v_uv;

void main() {
    v_uv = in_uv;
    gl_Position = u_proj * u_view * vec4(in_pos, 1.0);
}
"""

_GROUND_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_texture;

in vec2 v_uv;

out vec4 frag_color;

void main() {
    frag_color = vec4(texture(u_texture, v_uv).rgb, 1.0);
}
"""

_TERRAIN_LINE_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;

out float v_height;
out vec3 v_world_pos;

void main() {
    v_height = in_pos.z;
    v_world_pos = in_pos;
    gl_Position = u_proj * u_view * vec4(in_pos, 1.0);
}
"""

_TERRAIN_FILL_FRAGMENT_SHADER = """
#version 330

uniform vec4 u_color;
uniform float u_height_min;
uniform float u_height_max;
uniform vec3 u_sun_dir;
uniform float u_ambient_light;
uniform float u_diffuse_light;

in float v_height;
in vec3 v_world_pos;

out vec4 frag_color;

void main() {
    float span = max(0.001, u_height_max - u_height_min);
    float t = clamp((v_height - u_height_min) / span, 0.0, 1.0);
    vec3 low = vec3(0.12, 0.30, 0.12);
    vec3 mid = u_color.rgb;
    vec3 high = vec3(0.78, 0.70, 0.42);
    vec3 rgb = mix(low, mid, smoothstep(0.0, 0.55, t));
    rgb = mix(rgb, high, smoothstep(0.55, 1.0, t));
    vec3 dx = dFdx(v_world_pos);
    vec3 dy = dFdy(v_world_pos);
    vec3 normal = normalize(cross(dx, dy));
    if (normal.z < 0.0) {
        normal = -normal;
    }
    float diffuse = max(dot(normal, normalize(u_sun_dir)), 0.0);
    float light = clamp(u_ambient_light + diffuse * u_diffuse_light, 0.0, 1.15);
    rgb *= light;
    frag_color = vec4(rgb, u_color.a);
}
"""

_TERRAIN_LINE_FRAGMENT_SHADER = """
#version 330

uniform vec4 u_color;

out vec4 frag_color;

void main() {
    frag_color = u_color;
}
"""


# Region floor: flat 256x256 m quad at Z=0. UV mapping puts the map
# tile's row 0 (north of the region by SL convention) at world Y=256
# and its column 0 (west) at world X=0, so the texture lands in the
# same orientation the 2D top-down view shows.
REGION_GROUND_SIZE_M: float = 256.0

_GROUND_VERTICES: tuple[float, ...] = (
    # x,                   y,                   z,    u,   v
      0.0,                  0.0,                 0.0,  0.0, 1.0,  # SW
    REGION_GROUND_SIZE_M,   0.0,                 0.0,  1.0, 1.0,  # SE
    REGION_GROUND_SIZE_M,   REGION_GROUND_SIZE_M, 0.0, 1.0, 0.0,  # NE
      0.0,                  REGION_GROUND_SIZE_M, 0.0, 0.0, 0.0,  # NW
)

_GROUND_INDICES: tuple[int, ...] = (
    0, 1, 2,  # SW, SE, NE
    0, 2, 3,  # SW, NE, NW
)


# Region water: flat translucent quad. The default comes from SL/OpenSim's
# usual 20 m setting, but live scenes override it from RegionHandshake.
WATER_LEVEL_M: float = 20.0
WATER_TINT_RGB: tuple[float, float, float] = (0.18, 0.36, 0.55)
WATER_NOISE_STRENGTH: float = 0.08
TERRAIN_FILL_RGBA: tuple[float, float, float, float] = (0.28, 0.58, 0.22, 1.0)
TERRAIN_LINE_RGBA: tuple[float, float, float, float] = (0.05, 1.0, 0.20, 0.85)
GROUND_FALLBACK_RGBA: bytes = bytes((80, 120, 70, 255))

_WATER_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;

out vec2 v_world_xy;

void main() {
    v_world_xy = in_pos.xy;
    gl_Position = u_proj * u_view * vec4(in_pos, 1.0);
}
"""

_WATER_FRAGMENT_SHADER = """
#version 330

uniform vec4 u_color;

in vec2 v_world_xy;
out vec4 frag_color;

void main() {
    float wave = sin(v_world_xy.x * 0.23) * sin(v_world_xy.y * 0.19);
    float fine = sin((v_world_xy.x + v_world_xy.y) * 0.61);
    float noise = (wave * 0.65 + fine * 0.35) * 0.5 + 0.5;
    vec3 rgb = u_color.rgb + (noise - 0.5) * __WATER_NOISE_STRENGTH__;
    frag_color = vec4(clamp(rgb, 0.0, 1.0), u_color.a);
}
""".replace("__WATER_NOISE_STRENGTH__", f"{WATER_NOISE_STRENGTH:f}")

_WATER_INDICES: tuple[int, ...] = (
    0, 1, 2,
    0, 2, 3,
)


def _water_vertices(water_height: float) -> tuple[float, ...]:
    return (
        0.0,                   0.0,                  water_height,
        REGION_GROUND_SIZE_M,  0.0,                  water_height,
        REGION_GROUND_SIZE_M,  REGION_GROUND_SIZE_M, water_height,
        0.0,                   REGION_GROUND_SIZE_M, water_height,
    )


def lighting_direction(scene: Scene) -> tuple[float, float, float]:
    """Return a normalized world-space light direction for the scene."""
    raw = getattr(scene, "sun_direction", None)
    if raw is None:
        raw = _sun_direction_from_phase(getattr(scene, "sun_phase", None))
    return _normalize_vec3(raw, fallback=DEFAULT_SUN_DIRECTION)


def _sun_direction_from_phase(phase: float | None) -> tuple[float, float, float]:
    if phase is None:
        return DEFAULT_SUN_DIRECTION
    # The simulator's explicit SunDirection is preferred. This fallback
    # only needs to keep debug/synthetic scenes shaded consistently.
    azimuth = float(phase)
    elevation = 0.45 + 0.35 * math.sin(azimuth)
    return (math.cos(azimuth), math.sin(azimuth), max(0.18, elevation))


def _normalize_vec3(
    value: tuple[float, float, float], *, fallback: tuple[float, float, float]
) -> tuple[float, float, float]:
    try:
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, IndexError):
        x, y, z = fallback
    length = math.sqrt((x * x) + (y * y) + (z * z))
    if not math.isfinite(length) or length <= 0.000001:
        x, y, z = fallback
        length = math.sqrt((x * x) + (y * y) + (z * z))
    return (x / length, y / length, z / length)


def generated_texture_uv(
    position: tuple[float, float, float], normal: tuple[float, float, float]
) -> tuple[float, float]:
    """Generated per-face UV projection used by the object texture shader."""
    px, py, pz = position
    nx, ny, nz = normal
    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        u, v = (-py if nx >= 0.0 else py), pz
    elif ay >= ax and ay >= az:
        u, v = (px if ny >= 0.0 else -px), pz
    else:
        u, v = px, (py if nz >= 0.0 else -py)
    return (max(0.0, min(1.0, u + 0.5)), max(0.0, min(1.0, v + 0.5)))


def model_matrix(
    position: tuple[float, float, float],
    scale: tuple[float, float, float],
    rotation_quat: tuple[float, float, float, float],
) -> tuple[float, ...]:
    """Build a column-major 4x4 model matrix M = T * R * S.

    Quaternion order is ``(x, y, z, w)`` with w real, matching the
    on-the-wire convention from ``ObjectUpdate``. The result is 16
    floats ready for ``struct.pack`` into an instance buffer.
    """
    px, py, pz = position
    sx, sy, sz = scale
    qx, qy, qz, qw = rotation_quat

    xx = qx * qx
    yy = qy * qy
    zz = qz * qz
    xy = qx * qy
    xz = qx * qz
    yz = qy * qz
    wx = qw * qx
    wy = qw * qy
    wz = qw * qz

    r00 = 1.0 - 2.0 * (yy + zz)
    r01 = 2.0 * (xy - wz)
    r02 = 2.0 * (xz + wy)
    r10 = 2.0 * (xy + wz)
    r11 = 1.0 - 2.0 * (xx + zz)
    r12 = 2.0 * (yz - wx)
    r20 = 2.0 * (xz - wy)
    r21 = 2.0 * (yz + wx)
    r22 = 1.0 - 2.0 * (xx + yy)

    return (
        r00 * sx, r10 * sx, r20 * sx, 0.0,  # column 0
        r01 * sy, r11 * sy, r21 * sy, 0.0,  # column 1
        r02 * sz, r12 * sz, r22 * sz, 0.0,  # column 2
        px, py, pz, 1.0,                    # column 3
    )


def _quat_rotate(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate vector v by quaternion q = (x, y, z, w)."""
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + qy * tz - qz * ty,
        vy + qw * ty + qz * tx - qx * tz,
        vz + qw * tz + qx * ty - qy * tx,
    )


@dataclass(slots=True)
class _ShapeMesh:
    """GL resources for one primitive mesh.

    The VBO/IBO are owned for the lifetime of the renderer; the VAO is
    rebuilt whenever the shared instance buffer is reallocated (its
    binding is recorded inside the VAO at construction time).
    """

    vbo: object  # moderngl.Buffer
    ibo: object  # moderngl.Buffer
    vao: object  # moderngl.VertexArray
    index_count: int


def terrain_mesh_from_heightmap(
    samples: list[float] | tuple[float, ...],
    *,
    width: int,
    height: int,
    size_m: float = REGION_GROUND_SIZE_M,
    z_scale: float = 1.0,
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Build a textured terrain grid from row-major height samples."""
    if width < 2 or height < 2:
        raise ValueError("terrain mesh needs at least a 2x2 heightmap")
    if len(samples) != width * height:
        raise ValueError(
            f"height sample count {len(samples)} does not match {width}x{height}"
        )

    vertices: list[float] = []
    for row in range(height):
        y = (float(row) / float(height - 1)) * size_m
        v = 1.0 - (float(row) / float(height - 1))
        for col in range(width):
            x = (float(col) / float(width - 1)) * size_m
            u = float(col) / float(width - 1)
            vertices.extend((x, y, float(samples[row * width + col]) * z_scale, u, v))

    indices: list[int] = []
    for row in range(height - 1):
        for col in range(width - 1):
            sw = row * width + col
            se = sw + 1
            nw = sw + width
            ne = nw + 1
            indices.extend((sw, se, ne, sw, ne, nw))

    return tuple(vertices), tuple(indices)


def terrain_line_indices(width: int, height: int) -> tuple[int, ...]:
    """Build grid-line indices for a row-major terrain vertex grid."""
    if width < 2 or height < 2:
        raise ValueError("terrain lines need at least a 2x2 heightmap")
    indices: list[int] = []
    for row in range(height):
        base = row * width
        for col in range(width - 1):
            indices.extend((base + col, base + col + 1))
    for row in range(height - 1):
        base = row * width
        next_base = (row + 1) * width
        for col in range(width):
            indices.extend((base + col, next_base + col))
    return tuple(indices)


class PerspectiveRenderer:
    """3D renderer. Software map-tile background + native GL geometry."""

    def __init__(self, camera: Camera3D, *, ctx: moderngl.Context | None = None) -> None:
        self.camera = camera
        self.ctx = ctx

        # GL resources are allocated lazily so renderer construction
        # stays cheap when ctx is None (test harnesses without GL).
        self._program = None  # type: moderngl.Program | None
        self._instance_vbo = None  # type: moderngl.Buffer | None
        self._shape_meshes: dict[str, _ShapeMesh] = {}
        self._cube_face_meshes: dict[int, _ShapeMesh] = {}
        self._instance_capacity = 0
        # Ground (region floor) — separate program because the cubes are
        # flat-tinted while the ground samples a texture.
        self._ground_program = None  # type: moderngl.Program | None
        self._ground_vbo = None  # type: moderngl.Buffer | None
        self._ground_ibo = None  # type: moderngl.Buffer | None
        self._ground_vao = None  # type: moderngl.VertexArray | None
        self._ground_texture = None  # type: moderngl.Texture | None
        self._ground_texture_path: Path | None = None
        self._object_textures: dict[UUID, object] = {}
        self._object_texture_paths: dict[UUID, Path] = {}
        self._terrain_vbo = None  # type: moderngl.Buffer | None
        self._terrain_ibo = None  # type: moderngl.Buffer | None
        self._terrain_fill_program = None  # type: moderngl.Program | None
        self._terrain_fill_vao = None  # type: moderngl.VertexArray | None
        self._terrain_vao = None  # type: moderngl.VertexArray | None
        self._terrain_line_program = None  # type: moderngl.Program | None
        self._terrain_line_ibo = None  # type: moderngl.Buffer | None
        self._terrain_line_vao = None  # type: moderngl.VertexArray | None
        self._terrain_line_index_count: int = 0
        self._terrain_revision: int | None = None
        self._terrain_z_scale: float = 1.0
        self._terrain_height_range: tuple[float, float] = (0.0, 1.0)
        # Water plane at SL's default sea level. Solid translucent fill
        # for v1; lighting/sun reflections move with step 8.
        self._water_program = None  # type: moderngl.Program | None
        self._water_vbo = None  # type: moderngl.Buffer | None
        self._water_ibo = None  # type: moderngl.Buffer | None
        self._water_vao = None  # type: moderngl.VertexArray | None
        self._water_height: float | None = None
        if ctx is not None:
            self._setup_gl(ctx)

    # -------------------------------------------------------------- pygame

    def update(self, dt: float, scene: Scene) -> None:
        del dt, scene

    def render(self, surface: pygame.Surface, scene: Scene) -> None:
        """Fill the world surface with sky.

        The map tile is drawn by the 3D pass as a textured ground
        quad (step 6b); the world surface only provides the sky/skyline
        backdrop above the horizon. Painting the map tile here as a
        fullscreen 2D image hid the actual 3D ground behind a look-alike
        quad, which is why the surface mesh appeared invisible despite
        rendering correctly.
        """
        del scene
        surface.fill(SKY_COLOR)

    # -------------------------------------------------------------- GL pass

    def render_gl(self, scene: Scene, *, aspect: float) -> None:
        """Draw the region ground + primitives + water.

        Entities are grouped by ``SceneEntity.shape`` so each primitive
        shape is drawn with one instanced draw call against its own
        mesh. Order: ground floor, primitives, water — all under depth
        test. Water draws last with alpha blending so submerged ground
        and below-water primitive fragments tint through it; primitives
        above water still occlude water at the depth test.
        """
        ctx = self.ctx
        if ctx is None or self._program is None or not self._shape_meshes:
            return
        if aspect <= 0.0:
            return

        view = self.camera.view_matrix()
        proj = self.camera.projection_matrix(aspect)
        view_data = struct.pack("16f", *view)
        proj_data = struct.pack("16f", *proj)
        sun_direction = lighting_direction(scene)

        self._upload_ground_texture(ctx, scene)
        shape_groups = self._group_entities_by_shape(scene)
        cube_entities = shape_groups.pop("cube", [])
        groups = self._group_entities_for_draw(scene, shape_groups=shape_groups)

        ctx.enable(ctx.DEPTH_TEST)
        try:
            if scene.render_terrain:
                self._upload_terrain_mesh(ctx, scene)
            else:
                self._release_terrain_mesh()
            if scene.render_terrain and self._terrain_vao is not None:
                if self._ground_texture is not None and self._ground_texture_path is not None:
                    assert self._ground_program is not None
                    self._ground_program["u_view"].write(view_data)
                    self._ground_program["u_proj"].write(proj_data)
                    self._ground_texture.use(location=0)
                    self._terrain_vao.render()
                else:
                    self._render_terrain_fill(
                        view_data, proj_data, sun_direction=sun_direction
                    )
                if scene.render_terrain_lines:
                    self._render_terrain_lines(ctx, view_data, proj_data)
            elif self._ground_texture is not None and self._ground_program is not None:
                self._ground_program["u_view"].write(view_data)
                self._ground_program["u_proj"].write(proj_data)
                self._ground_texture.use(location=0)
                assert self._ground_vao is not None
                self._ground_vao.render()

            if scene.render_objects and (groups or cube_entities):
                self._program["u_view"].write(view_data)
                self._program["u_proj"].write(proj_data)
                self._program["u_sun_dir"].value = sun_direction
                self._program["u_ambient_light"].value = AMBIENT_LIGHT
                self._program["u_diffuse_light"].value = DIFFUSE_LIGHT
                if "u_texture" in self._program:
                    self._program["u_texture"].value = 0
                if cube_entities:
                    self._render_cube_faces(ctx, scene, cube_entities)
                for (shape_key, texture_id), entities in groups.items():
                    mesh = self._shape_meshes[shape_key]
                    texture = (
                        self._upload_object_texture(ctx, scene, texture_id)
                        if texture_id is not None
                        else None
                    )
                    if texture is not None:
                        self._program["u_use_texture"].value = True
                        texture.use(location=0)
                    else:
                        self._program["u_use_texture"].value = False
                    self._upload_instances_for(ctx, entities)
                    mesh.vao.render(instances=len(entities))

            if (
                scene.render_water
                and self._water_program is not None
                and self._water_vao is not None
            ):
                self._upload_water_mesh(ctx, scene.water_height)
                self._water_program["u_view"].write(view_data)
                self._water_program["u_proj"].write(proj_data)
                alpha = max(0.0, min(1.0, float(getattr(scene, "water_alpha", 0.72))))
                self._water_program["u_color"].value = (*WATER_TINT_RGB, alpha)
                ctx.enable(ctx.BLEND)
                ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
                try:
                    self._water_vao.render()
                finally:
                    ctx.disable(ctx.BLEND)
        finally:
            # Leave the depth state predictable for the HUD overlay
            # quad and the next frame's compositor draws.
            ctx.disable(ctx.DEPTH_TEST)

    def pick(self, x: int, y: int, scene: Scene, *, aspect: float) -> int | None:
        if aspect <= 0.0:
            return None

        # Unproject screen to ray
        camera = self.camera
        if camera.mode == "orbit":
            eye = camera.orbit_eye()
        else:
            eye = camera.eye_position

        # Reconstruct camera frame
        from vibestorm.viewer3d.camera import _sub, _cross, _normalize, DEFAULT_UP, DEFAULT_FOV_Y_RADIANS
        forward = _normalize(_sub(camera.target, eye))
        side = _normalize(_cross(forward, DEFAULT_UP))
        upward = _cross(side, forward)

        sw, sh = camera.screen_size
        if sw == 0 or sh == 0:
            return None

        ndc_x = (2.0 * x / sw) - 1.0
        ndc_y = 1.0 - (2.0 * y / sh)
        tan_half_fov = math.tan(DEFAULT_FOV_Y_RADIANS / 2.0)
        view_dir_x = ndc_x * aspect * tan_half_fov
        view_dir_y = ndc_y * tan_half_fov

        ray_dir = _normalize((
            view_dir_x * side[0] + view_dir_y * upward[0] + forward[0],
            view_dir_x * side[1] + view_dir_y * upward[1] + forward[1],
            view_dir_x * side[2] + view_dir_y * upward[2] + forward[2],
        ))

        # Ray-OBB intersection
        best_id = None
        best_dist = float('inf')

        for entity in scene.object_entities.values():
            if entity.scale is None or entity.position is None:
                continue

            # Inverse transform ray to local space
            # Translate
            lx = eye[0] - entity.position[0]
            ly = eye[1] - entity.position[1]
            lz = eye[2] - entity.position[2]

            # Rotate
            qx, qy, qz, qw = entity.rotation if entity.rotation is not None else (0.0, 0.0, 0.0, 1.0)
            inv_q = (-qx, -qy, -qz, qw)
            local_origin = _quat_rotate(inv_q, (lx, ly, lz))
            local_dir = _quat_rotate(inv_q, ray_dir)

            tmin = 0.0
            tmax = float('inf')
            hit = True
            for i in range(3):
                half_extent = entity.scale[i] / 2.0
                if abs(local_dir[i]) < 1e-6:
                    if abs(local_origin[i]) > half_extent:
                        hit = False
                        break
                else:
                    ood = 1.0 / local_dir[i]
                    t1 = (-half_extent - local_origin[i]) * ood
                    t2 = (half_extent - local_origin[i]) * ood
                    if t1 > t2:
                        t1, t2 = t2, t1
                    if t1 > tmin: tmin = t1
                    if t2 < tmax: tmax = t2
                    if tmin > tmax:
                        hit = False
                        break

            if hit and tmin > 0.0 and tmin < best_dist:
                best_dist = tmin
                best_id = entity.local_id

        return best_id

    # -------------------------------------------------------------- caches

    def clear_caches(self) -> None:
        """Release GL resources.

        Called by the app on render-mode swap and on shutdown. After
        ``clear_caches`` the renderer is no longer usable; build a new
        instance to render again.
        """
        for mesh in self._shape_meshes.values():
            mesh.vao.release()
            mesh.ibo.release()
            mesh.vbo.release()
        self._shape_meshes.clear()
        for mesh in self._cube_face_meshes.values():
            mesh.vao.release()
            mesh.ibo.release()
            mesh.vbo.release()
        self._cube_face_meshes.clear()
        for texture in self._object_textures.values():
            texture.release()
        self._object_textures.clear()
        self._object_texture_paths.clear()
        for resource in (
            self._instance_vbo,
            self._program,
            self._ground_vao,
            self._ground_ibo,
            self._ground_vbo,
            self._ground_program,
            self._ground_texture,
            self._terrain_vao,
            self._terrain_ibo,
            self._terrain_fill_vao,
            self._terrain_fill_program,
            self._terrain_line_vao,
            self._terrain_line_ibo,
            self._terrain_line_program,
            self._terrain_vbo,
            self._water_vao,
            self._water_ibo,
            self._water_vbo,
            self._water_program,
        ):
            if resource is not None:
                resource.release()
        self._instance_vbo = None
        self._program = None
        self._cube_face_meshes = {}
        self._instance_capacity = 0
        self._ground_vao = None
        self._ground_ibo = None
        self._ground_vbo = None
        self._ground_program = None
        self._ground_texture = None
        self._ground_texture_path = None
        self._terrain_vao = None
        self._terrain_ibo = None
        self._terrain_fill_vao = None
        self._terrain_fill_program = None
        self._terrain_line_vao = None
        self._terrain_line_ibo = None
        self._terrain_line_program = None
        self._terrain_line_index_count = 0
        self._terrain_vbo = None
        self._terrain_revision = None
        self._terrain_z_scale = 1.0
        self._terrain_height_range = (0.0, 1.0)
        self._water_vao = None
        self._water_ibo = None
        self._water_vbo = None
        self._water_program = None
        self._water_height = None

    # -------------------------------------------------------------- helpers

    def _setup_gl(self, ctx: moderngl.Context) -> None:
        from vibestorm.viewer3d import meshes

        self._program = ctx.program(
            vertex_shader=_VERTEX_SHADER,
            fragment_shader=_FRAGMENT_SHADER,
        )
        if "u_texture" in self._program:
            self._program["u_texture"].value = 0
        self._instance_capacity = _INITIAL_INSTANCE_CAPACITY
        self._instance_vbo = ctx.buffer(
            reserve=self._instance_capacity * _BYTES_PER_INSTANCE,
            dynamic=True,
        )

        shape_authors = {
            "cube": meshes.cube_mesh,
            "sphere": meshes.sphere_mesh,
            "cylinder": meshes.cylinder_mesh,
            "torus": meshes.torus_mesh,
            "prism": meshes.prism_mesh,
        }
        for shape_key, author in shape_authors.items():
            verts, indices = author()
            vbo = ctx.buffer(struct.pack(f"{len(verts)}f", *verts))
            ibo = ctx.buffer(struct.pack(f"{len(indices)}I", *indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f", "in_pos"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(indices)
            )
        cube_vertices, cube_indices = meshes.cube_mesh()
        for face_index in range(6):
            face_indices = cube_indices[face_index * 6 : (face_index + 1) * 6]
            vbo = ctx.buffer(struct.pack(f"{len(cube_vertices)}f", *cube_vertices))
            ibo = ctx.buffer(struct.pack(f"{len(face_indices)}I", *face_indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f", "in_pos"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._cube_face_meshes[face_index] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(face_indices)
            )

        self._ground_program = ctx.program(
            vertex_shader=_GROUND_VERTEX_SHADER,
            fragment_shader=_GROUND_FRAGMENT_SHADER,
        )
        if "u_texture" in self._ground_program:
            self._ground_program["u_texture"].value = 0
        self._ground_vbo = ctx.buffer(
            struct.pack(f"{len(_GROUND_VERTICES)}f", *_GROUND_VERTICES)
        )
        self._ground_ibo = ctx.buffer(
            struct.pack(f"{len(_GROUND_INDICES)}I", *_GROUND_INDICES)
        )
        self._ground_vao = ctx.vertex_array(
            self._ground_program,
            [(self._ground_vbo, "3f 2f", "in_pos", "in_uv")],
            index_buffer=self._ground_ibo,
            index_element_size=4,
        )

        self._terrain_line_program = ctx.program(
            vertex_shader=_TERRAIN_LINE_VERTEX_SHADER,
            fragment_shader=_TERRAIN_LINE_FRAGMENT_SHADER,
        )
        self._terrain_fill_program = ctx.program(
            vertex_shader=_TERRAIN_LINE_VERTEX_SHADER,
            fragment_shader=_TERRAIN_FILL_FRAGMENT_SHADER,
        )

        self._water_program = ctx.program(
            vertex_shader=_WATER_VERTEX_SHADER,
            fragment_shader=_WATER_FRAGMENT_SHADER,
        )
        vertices = _water_vertices(WATER_LEVEL_M)
        self._water_vbo = ctx.buffer(struct.pack(f"{len(vertices)}f", *vertices))
        self._water_ibo = ctx.buffer(
            struct.pack(f"{len(_WATER_INDICES)}I", *_WATER_INDICES)
        )
        self._water_vao = ctx.vertex_array(
            self._water_program,
            [(self._water_vbo, "3f", "in_pos")],
            index_buffer=self._water_ibo,
            index_element_size=4,
        )
        self._water_height = WATER_LEVEL_M

    def _group_entities_by_shape(
        self, scene: Scene
    ) -> dict[str, list[SceneEntity]]:
        """Bucket scene entities by mesh key, applying aliases / fallback.

        Avatars and entities whose ``shape`` is ``None`` route to the
        cube fallback. Tube/ring fall back to cube/torus per
        ``_SHAPE_ALIASES``. Order is preserved within each bucket so
        the on-screen layout is deterministic frame to frame.
        """
        groups: dict[str, list[SceneEntity]] = {}
        for entity in (
            *scene.object_entities.values(),
            *scene.avatar_entities.values(),
        ):
            raw = entity.shape
            shape_key = _SHAPE_ALIASES.get(raw, raw) if raw is not None else _DEFAULT_SHAPE_KEY
            if shape_key not in self._shape_meshes:
                shape_key = _DEFAULT_SHAPE_KEY
            groups.setdefault(shape_key, []).append(entity)
        return groups

    def _group_entities_for_draw(
        self,
        scene: Scene,
        *,
        shape_groups: dict[str, list[SceneEntity]] | None = None,
    ) -> dict[tuple[str, UUID | None], list[SceneEntity]]:
        groups: dict[tuple[str, UUID | None], list[SceneEntity]] = {}
        source_groups = shape_groups if shape_groups is not None else self._group_entities_by_shape(scene)
        for shape_key, entities in source_groups.items():
            for entity in entities:
                texture_id = self._texture_id_for_entity_face(scene, entity, None)
                groups.setdefault((shape_key, texture_id), []).append(entity)
        return groups

    def _texture_id_for_entity_face(
        self, scene: Scene, entity: SceneEntity, face_index: int | None
    ) -> UUID | None:
        texture_id = entity.default_texture_id
        if face_index is not None and entity.texture_entry is not None:
            texture_id = entity.texture_entry.texture_for_face(face_index)
        if texture_id is not None and texture_id not in scene.texture_paths:
            return None
        return texture_id

    def _render_cube_faces(
        self,
        ctx: moderngl.Context,
        scene: Scene,
        entities: list[SceneEntity],
    ) -> None:
        for face_index, mesh in self._cube_face_meshes.items():
            face_groups: dict[UUID | None, list[SceneEntity]] = {}
            for entity in entities:
                texture_id = self._texture_id_for_entity_face(scene, entity, face_index)
                face_groups.setdefault(texture_id, []).append(entity)
            for texture_id, face_entities in face_groups.items():
                texture = (
                    self._upload_object_texture(ctx, scene, texture_id)
                    if texture_id is not None
                    else None
                )
                if texture is not None:
                    self._program["u_use_texture"].value = True
                    texture.use(location=0)
                else:
                    self._program["u_use_texture"].value = False
                self._upload_instances_for(ctx, face_entities)
                mesh.vao.render(instances=len(face_entities))

    def _upload_instances_for(
        self, ctx: moderngl.Context, entities: list[SceneEntity]
    ) -> None:
        """Pack ``entities`` into the shared instance buffer.

        The buffer is shared by every shape's VAO; uploads happen once
        per shape group per frame. ``orphan`` requests fresh storage so
        the GPU isn't stalled by the previous frame's reads.
        """
        if len(entities) > self._instance_capacity:
            self._grow_instance_buffer(ctx, len(entities))

        floats: list[float] = []
        for entity in entities:
            quat = entity.rotation if entity.rotation is not None else (0.0, 0.0, 0.0, 1.0)
            floats.extend(model_matrix(entity.position, entity.scale, quat))
            r, g, b = entity.tint
            floats.append(r / 255.0)
            floats.append(g / 255.0)
            floats.append(b / 255.0)

        data = struct.pack(f"{len(floats)}f", *floats)
        assert self._instance_vbo is not None
        self._instance_vbo.orphan(size=len(data))
        self._instance_vbo.write(data)

    def _upload_ground_texture(self, ctx: moderngl.Context, scene: Scene) -> None:
        """Lazily upload ``scene.map_tile_path`` as the ground texture.

        Re-upload only when the path changes; otherwise the cached
        ``moderngl.Texture`` is kept. ``convert_alpha`` is intentionally
        skipped — it requires an active pygame display, which the GL
        test harness does not have, and ``tobytes(..., "RGBA")`` already
        normalises the pixel format.
        """
        path = scene.map_tile_path
        if path is None:
            if scene.terrain_heightmap is None:
                if self._ground_texture is not None:
                    self._ground_texture.release()
                    self._ground_texture = None
                    self._ground_texture_path = None
                return
            if self._ground_texture is None or self._ground_texture_path is not None:
                if self._ground_texture is not None:
                    self._ground_texture.release()
                tex = ctx.texture((1, 1), components=4, data=GROUND_FALLBACK_RGBA)
                tex.filter = (ctx.NEAREST, ctx.NEAREST)
                tex.repeat_x = True
                tex.repeat_y = True
                self._ground_texture = tex
                self._ground_texture_path = None
            return
        if path == self._ground_texture_path and self._ground_texture is not None:
            return

        import pygame

        try:
            surface = pygame.image.load(str(path))
        except (pygame.error, FileNotFoundError, OSError):
            return

        size = surface.get_size()
        pixels = pygame.image.tobytes(surface, "RGBA")

        existing = self._ground_texture
        if existing is not None and existing.size == size:
            existing.write(pixels)
        else:
            if existing is not None:
                existing.release()
            tex = ctx.texture(size, components=4, data=pixels)
            tex.filter = (ctx.LINEAR, ctx.LINEAR)
            tex.repeat_x = False
            tex.repeat_y = False
            self._ground_texture = tex
        self._ground_texture_path = path

    def _upload_object_texture(
        self, ctx: moderngl.Context, scene: Scene, texture_id: UUID
    ) -> object | None:
        path = scene.texture_paths.get(texture_id)
        if path is None:
            return None
        cached = self._object_textures.get(texture_id)
        if cached is not None and self._object_texture_paths.get(texture_id) == path:
            return cached

        import pygame

        try:
            surface = pygame.image.load(str(path))
        except (pygame.error, FileNotFoundError, OSError):
            return None

        pixels = pygame.image.tobytes(surface, "RGBA")
        texture = self._object_textures.pop(texture_id, None)
        if texture is not None:
            texture.release()
        texture = ctx.texture(surface.get_size(), components=4, data=pixels)
        texture.filter = (ctx.LINEAR, ctx.LINEAR)
        texture.repeat_x = True
        texture.repeat_y = True
        self._object_textures[texture_id] = texture
        self._object_texture_paths[texture_id] = path
        return texture

    def _upload_terrain_mesh(self, ctx: moderngl.Context, scene: Scene) -> None:
        heightmap = scene.terrain_heightmap
        if heightmap is None:
            self._release_terrain_mesh()
            return
        z_scale = float(getattr(scene, "terrain_z_scale", 1.0))
        if (
            self._terrain_revision == heightmap.revision
            and abs(self._terrain_z_scale - z_scale) < 0.001
            and self._terrain_vao is not None
        ):
            return
        if self._ground_program is None:
            return

        vertices, indices = terrain_mesh_from_heightmap(
            heightmap.samples,
            width=heightmap.width,
            height=heightmap.height,
            z_scale=z_scale,
        )
        line_indices = terrain_line_indices(heightmap.width, heightmap.height)
        self._release_terrain_mesh()
        self._terrain_vbo = ctx.buffer(struct.pack(f"{len(vertices)}f", *vertices))
        self._terrain_ibo = ctx.buffer(struct.pack(f"{len(indices)}I", *indices))
        self._terrain_vao = ctx.vertex_array(
            self._ground_program,
            [(self._terrain_vbo, "3f 2f", "in_pos", "in_uv")],
            index_buffer=self._terrain_ibo,
            index_element_size=4,
        )
        if self._terrain_fill_program is not None:
            self._terrain_fill_vao = ctx.vertex_array(
                self._terrain_fill_program,
                [(self._terrain_vbo, "3f 2x4", "in_pos")],
                index_buffer=self._terrain_ibo,
                index_element_size=4,
            )
        if self._terrain_line_program is not None:
            self._terrain_line_ibo = ctx.buffer(
                struct.pack(f"{len(line_indices)}I", *line_indices)
            )
            self._terrain_line_vao = ctx.vertex_array(
                self._terrain_line_program,
                [(self._terrain_vbo, "3f 2x4", "in_pos")],
                index_buffer=self._terrain_line_ibo,
                index_element_size=4,
            )
            self._terrain_line_index_count = len(line_indices)
        self._terrain_revision = heightmap.revision
        self._terrain_z_scale = z_scale
        raw_min = heightmap.sample_min if heightmap.sample_min is not None else 0.0
        raw_max = heightmap.sample_max if heightmap.sample_max is not None else 1.0
        self._terrain_height_range = (raw_min * z_scale, raw_max * z_scale)

    def _release_terrain_mesh(self) -> None:
        for resource in (
            self._terrain_vao,
            self._terrain_ibo,
            self._terrain_fill_vao,
            self._terrain_line_vao,
            self._terrain_line_ibo,
            self._terrain_vbo,
        ):
            if resource is not None:
                resource.release()
        self._terrain_vao = None
        self._terrain_ibo = None
        self._terrain_fill_vao = None
        self._terrain_line_vao = None
        self._terrain_line_ibo = None
        self._terrain_line_index_count = 0
        self._terrain_vbo = None
        self._terrain_revision = None
        self._terrain_z_scale = 1.0
        self._terrain_height_range = (0.0, 1.0)

    def _render_terrain_fill(
        self,
        view_data: bytes,
        proj_data: bytes,
        *,
        sun_direction: tuple[float, float, float],
    ) -> None:
        if self._terrain_fill_program is None or self._terrain_fill_vao is None:
            # The textured terrain VAO is kept for future texture work,
            # but current debug rendering should be visible without it.
            if self._terrain_vao is not None:
                self._terrain_vao.render()
            return
        self._terrain_fill_program["u_view"].write(view_data)
        self._terrain_fill_program["u_proj"].write(proj_data)
        self._terrain_fill_program["u_color"].value = TERRAIN_FILL_RGBA
        self._terrain_fill_program["u_height_min"].value = self._terrain_height_range[0]
        self._terrain_fill_program["u_height_max"].value = self._terrain_height_range[1]
        self._terrain_fill_program["u_sun_dir"].value = sun_direction
        self._terrain_fill_program["u_ambient_light"].value = AMBIENT_LIGHT
        self._terrain_fill_program["u_diffuse_light"].value = DIFFUSE_LIGHT
        self._terrain_fill_vao.render()

    def _render_terrain_lines(
        self, ctx: moderngl.Context, view_data: bytes, proj_data: bytes
    ) -> None:
        if (
            self._terrain_line_program is None
            or self._terrain_line_vao is None
            or self._terrain_line_index_count <= 0
        ):
            return
        self._terrain_line_program["u_view"].write(view_data)
        self._terrain_line_program["u_proj"].write(proj_data)
        self._terrain_line_program["u_color"].value = TERRAIN_LINE_RGBA
        ctx.enable(ctx.BLEND)
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
        try:
            self._terrain_line_vao.render(mode=ctx.LINES)
        finally:
            ctx.disable(ctx.BLEND)

    def _upload_water_mesh(self, ctx: moderngl.Context, water_height: float) -> None:
        if self._water_vbo is None or self._water_program is None:
            return
        if self._water_height is not None and abs(self._water_height - water_height) < 0.001:
            return
        vertices = _water_vertices(water_height)
        self._water_vbo.write(struct.pack(f"{len(vertices)}f", *vertices))
        self._water_height = water_height

    def _grow_instance_buffer(self, ctx: moderngl.Context, required: int) -> None:
        """Reallocate the shared instance buffer and rebind every shape VAO.

        Each VAO records the buffer it draws from at construction
        time, so growing the buffer means tearing down and rebuilding
        every per-shape VAO against the new buffer. VBO/IBO are kept.
        """
        new_capacity = max(self._instance_capacity * 2, required)
        assert self._instance_vbo is not None and self._program is not None
        self._instance_vbo.release()
        self._instance_vbo = ctx.buffer(
            reserve=new_capacity * _BYTES_PER_INSTANCE,
            dynamic=True,
        )
        for shape_key, mesh in self._shape_meshes.items():
            mesh.vao.release()
            mesh.vao = ctx.vertex_array(
                self._program,
                [
                    (mesh.vbo, "3f", "in_pos"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=mesh.ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = mesh
        for face_index, mesh in self._cube_face_meshes.items():
            mesh.vao.release()
            mesh.vao = ctx.vertex_array(
                self._program,
                [
                    (mesh.vbo, "3f", "in_pos"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=mesh.ibo,
                index_element_size=4,
            )
            self._cube_face_meshes[face_index] = mesh
        self._instance_capacity = new_capacity

__all__ = [
    "DEFAULT_SUN_DIRECTION",
    "PerspectiveRenderer",
    "generated_texture_uv",
    "lighting_direction",
    "model_matrix",
    "terrain_line_indices",
    "terrain_mesh_from_heightmap",
]
