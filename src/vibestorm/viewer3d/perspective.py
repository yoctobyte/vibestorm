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

Deliberate omissions:

- Lighting / shading. Cubes render flat-tinted; ``Scene.sun_phase``
  feeds in step 8.
- Per-face textures, mesh, sculpt geometry. Each is its own pipeline.
- Avatar capsules / billboards. Avatars currently fall through to
  the cube fallback (no shape classification on PCODE_AVATAR).

The class accepts an optional ``moderngl.Context``. When ``ctx`` is
``None`` (e.g. unit tests with no GL available) ``render_gl`` is a
no-op — ``render`` still draws the placeholder background and labels
so swap-mechanism tests keep working without GL.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

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


_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;
in mat4 in_model;
in vec3 in_tint;

out vec3 v_color;

void main() {
    v_color = in_tint;
    gl_Position = u_proj * u_view * in_model * vec4(in_pos, 1.0);
}
"""

_FRAGMENT_SHADER = """
#version 330

in vec3 v_color;
out vec4 frag_color;

void main() {
    frag_color = vec4(v_color, 1.0);
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


# Region water: flat translucent quad at SL's default sea level (20 m).
# Shaders are minimal — single solid colour with alpha; sun_phase-driven
# tinting and reflection land in step 8.
WATER_LEVEL_M: float = 20.0
WATER_TINT_RGBA: tuple[float, float, float, float] = (0.18, 0.36, 0.55, 0.55)

_WATER_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;

void main() {
    gl_Position = u_proj * u_view * vec4(in_pos, 1.0);
}
"""

_WATER_FRAGMENT_SHADER = """
#version 330

uniform vec4 u_color;

out vec4 frag_color;

void main() {
    frag_color = u_color;
}
"""

_WATER_VERTICES: tuple[float, ...] = (
    0.0,                   0.0,                  WATER_LEVEL_M,
    REGION_GROUND_SIZE_M,  0.0,                  WATER_LEVEL_M,
    REGION_GROUND_SIZE_M,  REGION_GROUND_SIZE_M, WATER_LEVEL_M,
    0.0,                   REGION_GROUND_SIZE_M, WATER_LEVEL_M,
)

_WATER_INDICES: tuple[int, ...] = (
    0, 1, 2,
    0, 2, 3,
)


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
        self._instance_capacity = 0
        # Ground (region floor) — separate program because the cubes are
        # flat-tinted while the ground samples a texture.
        self._ground_program = None  # type: moderngl.Program | None
        self._ground_vbo = None  # type: moderngl.Buffer | None
        self._ground_ibo = None  # type: moderngl.Buffer | None
        self._ground_vao = None  # type: moderngl.VertexArray | None
        self._ground_texture = None  # type: moderngl.Texture | None
        self._ground_texture_path: Path | None = None
        # Water plane at SL's default sea level. Solid translucent fill
        # for v1; lighting/sun reflections move with step 8.
        self._water_program = None  # type: moderngl.Program | None
        self._water_vbo = None  # type: moderngl.Buffer | None
        self._water_ibo = None  # type: moderngl.Buffer | None
        self._water_vao = None  # type: moderngl.VertexArray | None
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

        self._upload_ground_texture(ctx, scene)
        groups = self._group_entities_by_shape(scene)

        ctx.enable(ctx.DEPTH_TEST)
        try:
            if self._ground_texture is not None and self._ground_program is not None:
                self._ground_program["u_view"].write(view_data)
                self._ground_program["u_proj"].write(proj_data)
                self._ground_texture.use(location=0)
                assert self._ground_vao is not None
                self._ground_vao.render()

            if groups:
                self._program["u_view"].write(view_data)
                self._program["u_proj"].write(proj_data)
                for shape_key, entities in groups.items():
                    mesh = self._shape_meshes[shape_key]
                    self._upload_instances_for(ctx, entities)
                    mesh.vao.render(instances=len(entities))

            if self._water_program is not None and self._water_vao is not None:
                self._water_program["u_view"].write(view_data)
                self._water_program["u_proj"].write(proj_data)
                self._water_program["u_color"].value = WATER_TINT_RGBA
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
        for resource in (
            self._instance_vbo,
            self._program,
            self._ground_vao,
            self._ground_ibo,
            self._ground_vbo,
            self._ground_program,
            self._ground_texture,
            self._water_vao,
            self._water_ibo,
            self._water_vbo,
            self._water_program,
        ):
            if resource is not None:
                resource.release()
        self._instance_vbo = None
        self._program = None
        self._instance_capacity = 0
        self._ground_vao = None
        self._ground_ibo = None
        self._ground_vbo = None
        self._ground_program = None
        self._ground_texture = None
        self._ground_texture_path = None
        self._water_vao = None
        self._water_ibo = None
        self._water_vbo = None
        self._water_program = None

    # -------------------------------------------------------------- helpers

    def _setup_gl(self, ctx: moderngl.Context) -> None:
        from vibestorm.viewer3d import meshes

        self._program = ctx.program(
            vertex_shader=_VERTEX_SHADER,
            fragment_shader=_FRAGMENT_SHADER,
        )
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

        self._water_program = ctx.program(
            vertex_shader=_WATER_VERTEX_SHADER,
            fragment_shader=_WATER_FRAGMENT_SHADER,
        )
        self._water_vbo = ctx.buffer(
            struct.pack(f"{len(_WATER_VERTICES)}f", *_WATER_VERTICES)
        )
        self._water_ibo = ctx.buffer(
            struct.pack(f"{len(_WATER_INDICES)}I", *_WATER_INDICES)
        )
        self._water_vao = ctx.vertex_array(
            self._water_program,
            [(self._water_vbo, "3f", "in_pos")],
            index_buffer=self._water_ibo,
            index_element_size=4,
        )

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
            if self._ground_texture is not None:
                self._ground_texture.release()
                self._ground_texture = None
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
        self._instance_capacity = new_capacity

__all__ = ["PerspectiveRenderer", "model_matrix"]
