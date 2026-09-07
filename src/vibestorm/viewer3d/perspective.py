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
- Mesh/sculpt fidelity is first-pass: high-LOD mesh Position/TriangleList
  and RGB sculpt-map displacement render, but normals/UV/materials are coarse.
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

from vibestorm.assets.sculpt import SculptDecodeError, sculpt_mesh_from_rgb
from vibestorm.assets.sl_mesh import (
    SLMeshDecodeError,
    decode_sl_mesh_asset,
    smooth_vertex_normals,
)
from vibestorm.viewer3d.atmosphere import (
    CLOUD_ALTITUDE_METRES,
    CLOUD_EDGE_HIGH,
    CLOUD_EDGE_LOW,
    CLOUD_NOISE_CELLS_PER_TILE,
    DEFAULT_CELESTIAL_AXES,
    DEFAULT_MOON_DISC,
    DEFAULT_MOON_FACE_AXES,
    DEFAULT_SKY_HORIZON_COLOR,
    DEFAULT_SKY_ZENITH_COLOR,
    DEFAULT_SUN_DISC,
    DEFAULT_UNDERWATER_REACH,
    DEFAULT_WATER_FOG,
    DEFAULT_WATER_FRESNEL,
    DEFAULT_WATER_RIPPLE,
    DEFAULT_WATER_RIPPLE_BELOW,
    DEFAULT_WATER_TINT,
    DEFAULT_WATER_WAVES,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import moderngl
    import pygame

    from vibestorm.viewer3d.camera import Camera3D, GroundHeight
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

# Aliases from PrimShape values to the underlying mesh used. Tube and ring
# now have purpose-built swept meshes, so only ``mesh`` still stands in for
# something else — a placeholder until authored mesh asset fetch/decode
# lands, deliberately routed through the same instanced mesh path rather
# than the cube fallback.
_SHAPE_ALIASES: dict[str, str] = {
    "mesh": "sphere",
    "avatar": "avatar",
}

#: Avatars do not go through ``_shape_meshes``. They are drawn from one buffer
#: per bone so a limb can move on its own, which a single merged mesh cannot
#: do: an instanced draw carries one matrix, and one matrix is one pose.
_AVATAR_SHAPE_KEY: str = "avatar"


def _interleave_vertex_attributes(
    vertices: tuple[float, ...] | list[float],
    normals: tuple[float, ...] | list[float] | None = None,
    uvs: tuple[float, ...] | list[float] | None = None,
) -> list[float]:
    """Pack ``x, y, z, nx, ny, nz, u, v`` per vertex for the shape program.

    ``normals`` falls back to the normalized vertex position — the
    approximation the vertex shader used to compute inline, so primitive shapes
    light exactly as they did before. ``uvs`` falls back to zeros, which the
    fragment shader ignores unless ``u_use_mesh_uv`` says the mesh authored
    them. Wrong-length inputs fall back rather than raising: a partially
    decoded asset should render approximately, not not at all.
    """
    count = len(vertices) // 3
    use_normals = normals is not None and len(normals) == len(vertices)
    use_uvs = uvs is not None and len(uvs) == count * 2
    packed: list[float] = []
    for i in range(count):
        x, y, z = vertices[i * 3], vertices[i * 3 + 1], vertices[i * 3 + 2]
        if use_normals:
            nx, ny, nz = normals[i * 3], normals[i * 3 + 1], normals[i * 3 + 2]
        else:
            nx, ny, nz = x, y, z
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length <= 1e-8:
            nx, ny, nz = 0.0, 0.0, 1.0
        else:
            nx, ny, nz = nx / length, ny / length, nz / length
        u, v = (uvs[i * 2], uvs[i * 2 + 1]) if use_uvs else (0.0, 0.0)
        packed.extend((x, y, z, nx, ny, nz, u, v))
    return packed


def _has_usable_uvs(decoded: object) -> bool:
    """True when a mesh authored its own TexCoord0 and it matches the vertices.

    ``DecodedSLMesh.uvs`` is zero-filled for submeshes that omit TexCoord0, so
    the length check alone would accept a mesh with no real UVs and sample the
    whole thing at one texel. ``has_authored_uvs`` is what distinguishes them.
    """
    if not getattr(decoded, "has_authored_uvs", False):
        return False
    uvs = getattr(decoded, "uvs", None)
    vertices = getattr(decoded, "vertices", None)
    if not uvs or not vertices:
        return False
    return len(uvs) == (len(vertices) // 3) * 2


def _mesh_asset_shape_key(mesh_id: UUID) -> str:
    return f"mesh:{mesh_id}"


def _sculpt_asset_shape_key(sculpt_id: UUID, sculpt_type: int | None) -> str:
    return f"sculpt:{sculpt_id}:{sculpt_type or 0}"


def _texture_memory_bytes(size: tuple[int, int]) -> int:
    """What one uploaded texture costs, mipmaps included.

    An estimate, and deliberately one the renderer can compute without asking
    the driver: four bytes a texel for the RGBA upload, and a third again for
    the mipmap chain.
    """
    width, height = size
    return int(width * height * 4 * MIPMAP_MEMORY_FACTOR)


def _within_texture_budget(surface):
    """Shrink a texture that is larger than `MAX_OBJECT_TEXTURE_EDGE`.

    Done once, on upload, and only downward: scaling a small texture up would
    spend memory to add nothing. The aspect ratio is kept, so a 2048x512 sign
    becomes 512x128 rather than square.

    `smoothscale` rather than `scale`: the nearest-neighbour version of a
    quarter-size reduction throws away three texels in four and aliases badly,
    which is the same mistake as sampling without mipmaps and would undo that
    work at the source.
    """
    import pygame

    width, height = surface.get_size()
    longest = max(width, height)
    if longest <= MAX_OBJECT_TEXTURE_EDGE or longest <= 0:
        return surface
    scale = MAX_OBJECT_TEXTURE_EDGE / longest
    return pygame.transform.smoothscale(
        surface, (max(1, int(width * scale)), max(1, int(height * scale)))
    )


def _minify_through_mipmaps(ctx: moderngl.Context, texture) -> None:
    """Give a world texture mipmaps, and a filter that actually uses them.

    Without them a texture is point-sampled however small it is on screen, so
    anything at a distance -- a brick wall down the street, a tiled ground
    texture towards the horizon -- samples a near-random texel per pixel and
    crawls as the camera moves. It is the most visible artefact this renderer
    had, and it gets worse the further you can see.

    The filter is ``(minification, magnification)``. Setting the first to
    ``LINEAR`` is what was here, and it is what makes mipmaps do nothing: the
    terrain textures were already built with them and never sampled one.
    Magnification stays ``LINEAR`` -- there is no smaller level to blend when a
    texture is drawn larger than it is.

    Mipmaps alone then over-correct, and the ground is where it shows: a
    surface seen at a grazing angle is squashed in one direction and not the
    other, and a mipmap level small enough to stop the crawl along the squashed
    axis throws away everything across it, so the ground goes to mush a few
    metres out. Anisotropic filtering is the answer to exactly that -- it takes
    several samples along the squashed direction instead of dropping to a
    coarser level -- and the GPU decides how many, up to what is asked for
    here, so it costs nothing on a surface facing the camera.
    """
    texture.build_mipmaps()
    texture.filter = (ctx.LINEAR_MIPMAP_LINEAR, ctx.LINEAR)
    texture.anisotropy = min(MAX_ANISOTROPY, ctx.max_anisotropy)


def _has_face_textures(entity: SceneEntity) -> bool:
    """Whether this prim's faces can differ from each other.

    A cube is drawn as six meshes so a ``TextureEntry`` can put a different
    texture on each side. A prim that names no per-face texture wears its
    default all over, and splitting it draws the same pixels six times, at six
    times the cost -- which is most of the prims in a region.

    ``face_texture_ids`` empty is exactly "every face resolves to the default",
    by ``TextureEntry.texture_for_face``. The entry's default is what
    ``SceneEntity.default_texture_id`` already holds, so the whole-mesh path
    picks the same texture the per-face path would have.
    """
    entry = entity.texture_entry
    return entry is not None and bool(entry.face_texture_ids)


def _single_face_index(shape_key: str) -> int | None:
    """SL face index to texture a whole-mesh draw with, if the prim has one face.

    Spheres, tori and sculpts are single-face prims in SL, so a ``TextureEntry``
    override on face 0 is the prim's texture. Reading the entry's default
    instead silently ignores that override. ``texture_for_face`` falls back to
    the default when face 0 carries none, so this only ever adds information.

    Returns ``None`` for the avatar placeholder and for shapes drawn per face
    elsewhere; those callers keep the default-texture behaviour.
    """
    if shape_key in ("sphere", "torus", "tube", "ring") or shape_key.startswith("sculpt:"):
        return 0
    return None

#: How far to push anisotropic filtering on world textures. 16 is what
#: current hardware offers and what this asks for; the driver clamps to its
#: own maximum, and the GPU only spends it on surfaces steep enough to need
#: it. Ground textures are the reason: seen along the surface they are
#: squashed hard in one direction, and isotropic mipmapping answers that by
#: blurring both.
MAX_ANISOTROPY: float = 16.0
#: How much GPU memory the uploaded prim textures may hold, in bytes.
#:
#: Nothing bounded this before. `_prune_object_textures` released what the
#: region no longer references, which is right and is not a bound: a region
#: can reference as much as it likes, and the local test region's handful of
#: textures is what made the absence invisible. A mainland region with a few
#: thousand distinct 512x512 textures is several gigabytes, and the uploaded
#: set grows with every texture the camera has *ever* passed over, not with
#: what is on screen.
OBJECT_TEXTURE_BUDGET_BYTES: int = 384 * 1024 * 1024

#: The largest edge a prim texture is uploaded at, in texels.
#:
#: The other half of the bound, and the half that cannot thrash. OpenSim's
#: `SimulatorFeatures` advertises `MaxTextureResolution: 2048`, and one 2048
#: square texture is 22 MB once mipmapped -- seventeen of them would spend the
#: whole budget above. Downscaling is done once, on upload.
#:
#: Both numbers are guesses and should be read as such. The local test region
#: holds a handful of textures, so nothing here has ever been near the
#: ceiling; what would say whether 384 MB and a 512 edge are the right trade
#: between sharpness and headroom is a mainland region. The diagnostics panel
#: reports both, which is the point of publishing them at all.
MAX_OBJECT_TEXTURE_EDGE: int = 512

#: A mipmapped texture costs about a third more than its base level: each
#: level is a quarter of the one above, and the series sums to 4/3.
MIPMAP_MEMORY_FACTOR: float = 4.0 / 3.0

DEFAULT_SUN_DIRECTION: tuple[float, float, float] = (0.35, -0.55, 0.76)
AMBIENT_LIGHT: float = 0.78
DIFFUSE_LIGHT: float = 0.34


#: The one copy of the fog a submerged viewer sees the world through.
#:
#: Four passes want it -- prims and avatars, the map-tile ground, the textured
#: terrain and the flat terrain fill -- and a second copy of it is a second
#: thing to get wrong. Substituted rather than shared through a GLSL include,
#: which core OpenGL does not have.
_WATER_FOG_GLSL = """// --- the world seen through water ---------------------------------------
//
// `u_water_fog` is the sea's own colour and `u_water_depth` is
// (how far a viewer under the surface can see, the surface's height, the
// eye's height). A reach of zero means the viewer is in air and nothing here
// does anything, which is the usual case and costs one comparison.
uniform vec3 u_water_fog;
uniform vec3 u_water_depth;

vec3 waterlogged(vec3 rgb, float distance_to, float world_z) {
    if (u_water_depth.x <= 0.0) {
        return rgb;
    }
    // Only the part of the line of sight actually *in* the water fogs. A
    // building on the shore is seen through the water in front of it and
    // through clear air beyond, and fogging the whole distance would grey it
    // out as though the sea reached the horizon at eye level.
    float rise = world_z - u_water_depth.z;
    float submerged = rise > 0.0
        ? clamp((u_water_depth.y - u_water_depth.z) / rise, 0.0, 1.0)
        : 1.0;
    return mix(u_water_fog, rgb, exp(-distance_to * submerged / u_water_depth.x));
}
"""

_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec3 u_sun_dir;
uniform vec3 u_ambient_light;
uniform vec3 u_diffuse_light;
in vec3 in_pos;
in vec3 in_normal;
in vec2 in_mesh_uv;
in mat4 in_model;
in vec3 in_tint;

out vec3 v_tint;
out vec3 v_light;
out vec3 v_local_pos;
out vec3 v_local_normal;
out vec2 v_mesh_uv;
out float v_eye_distance;
out float v_world_z;

void main() {
    // in_normal is authored per mesh. Primitive shapes bake the old
    // position-derived approximation into their buffer, so switching to a
    // real attribute changed nothing for them while letting decoded mesh
    // assets supply true normals.
    vec3 local_normal = normalize(in_normal);
    // A normal does not transform like a position under a non-uniform scale.
    // Multiplying by the model matrix leans every normal on a stretched prim
    // -- and SL prims are stretched constantly, a 4 x 0.5 x 0.1 slab being
    // entirely ordinary -- towards the long axis, so a flat wall shades as a
    // curve. The inverse transpose is the correct transform; the guard is for
    // a degenerate scale, where inverse() would hand back infinities and
    // normalize() would turn them into a NaN colour.
    mat3 model3 = mat3(in_model);
    float det = determinant(model3);
    mat3 normal_matrix = abs(det) > 1e-12 ? transpose(inverse(model3)) : model3;
    vec3 world_normal = normalize(normal_matrix * local_normal);
    float diffuse = max(dot(world_normal, normalize(u_sun_dir)), 0.0);
    v_light = clamp(u_ambient_light + diffuse * u_diffuse_light, 0.0, 1.15);
    v_tint = in_tint;
    v_local_pos = in_pos;
    v_local_normal = local_normal;
    v_mesh_uv = in_mesh_uv;
    vec4 world = in_model * vec4(in_pos, 1.0);
    // The eye is the origin of view space, so this is the true distance to
    // the fragment rather than its depth along the view axis. Fog measured
    // along the axis thins towards the edges of the screen, which reads as
    // the water clearing when the camera turns.
    vec4 eye_space = u_view * world;
    v_eye_distance = length(eye_space.xyz);
    v_world_z = world.z;
    gl_Position = u_proj * eye_space;
}
"""

_FRAGMENT_SHADER = """
#version 330

uniform bool u_use_texture;
uniform bool u_use_mesh_uv;
uniform sampler2D u_texture;

in vec3 v_tint;
in vec3 v_light;
in vec3 v_local_pos;
in vec3 v_local_normal;
in vec2 v_mesh_uv;
in float v_eye_distance;
in float v_world_z;
out vec4 frag_color;
__WATER_FOG_GLSL__

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
        // Authored TexCoord0 when the asset carried one; otherwise the
        // position/normal-derived approximation used for primitives.
        vec2 uv = u_use_mesh_uv
            ? v_mesh_uv
            : generated_uv(v_local_pos, v_local_normal);
        base_color = texture(u_texture, uv).rgb;
    }
    frag_color = vec4(
        waterlogged(base_color * v_light, v_eye_distance, v_world_z), 1.0
    );
}
""".replace("__WATER_FOG_GLSL__", _WATER_FOG_GLSL)


_GROUND_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;
in vec2 in_uv;

out vec2 v_uv;
out float v_eye_distance;
out float v_world_z;

void main() {
    v_uv = in_uv;
    vec4 eye_space = u_view * vec4(in_pos, 1.0);
    v_eye_distance = length(eye_space.xyz);
    v_world_z = in_pos.z;
    gl_Position = u_proj * eye_space;
}
"""

_GROUND_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_texture;

in vec2 v_uv;
in float v_eye_distance;
in float v_world_z;

out vec4 frag_color;
__WATER_FOG_GLSL__

void main() {
    frag_color = vec4(
        waterlogged(texture(u_texture, v_uv).rgb, v_eye_distance, v_world_z), 1.0
    );
}
""".replace("__WATER_FOG_GLSL__", _WATER_FOG_GLSL)

#: How many times a ground texture repeats across the region.
#:
#: Nothing in ``RegionHandshake`` carries this -- it names the textures and the
#: elevation bands, and nothing else -- so this is a visual choice, not a value
#: read off the wire. Sixteen puts one tile every 16 m, which keeps the ground
#: detailed at walking distance without turning into noise from the air.
TERRAIN_TEXTURE_REPEATS: float = 16.0

_TERRAIN_TEXTURE_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;
in vec2 in_uv;

out vec2 v_region_uv;
out vec3 v_world_pos;
out float v_eye_distance;

void main() {
    v_region_uv = in_uv;
    v_world_pos = in_pos;
    vec4 eye_space = u_view * vec4(in_pos, 1.0);
    v_eye_distance = length(eye_space.xyz);
    gl_Position = u_proj * eye_space;
}
"""

_TERRAIN_TEXTURE_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_tex0;
uniform sampler2D u_tex1;
uniform sampler2D u_tex2;
uniform sampler2D u_tex3;
// Per region corner, in the message template's 00, 01, 10, 11 order.
uniform vec4 u_start_height;
uniform vec4 u_height_range;
uniform float u_repeats;
uniform vec3 u_sun_dir;
uniform vec3 u_ambient_light;
uniform vec3 u_diffuse_light;

in vec2 v_region_uv;
in vec3 v_world_pos;
in float v_eye_distance;

out vec4 frag_color;
__WATER_FOG_GLSL__

void main() {
    // The band a fragment falls in is set by its height against a start and
    // range that themselves vary across the region: the four values are its
    // corners, bilinearly interpolated.
    float sx = clamp(v_region_uv.x, 0.0, 1.0);
    float sy = clamp(v_region_uv.y, 0.0, 1.0);
    float start = mix(
        mix(u_start_height.x, u_start_height.z, sx),
        mix(u_start_height.y, u_start_height.w, sx),
        sy
    );
    float range = mix(
        mix(u_height_range.x, u_height_range.z, sx),
        mix(u_height_range.y, u_height_range.w, sx),
        sy
    );
    float band = clamp((v_world_pos.z - start) / max(abs(range), 0.01), 0.0, 1.0) * 3.0;

    vec2 uv = v_region_uv * u_repeats;
    vec3 rgb = texture(u_tex0, uv).rgb;
    rgb = mix(rgb, texture(u_tex1, uv).rgb, clamp(band, 0.0, 1.0));
    rgb = mix(rgb, texture(u_tex2, uv).rgb, clamp(band - 1.0, 0.0, 1.0));
    rgb = mix(rgb, texture(u_tex3, uv).rgb, clamp(band - 2.0, 0.0, 1.0));

    // Same trick the flat fill uses: the mesh carries no normals, so take them
    // from the derivatives of the interpolated world position.
    vec3 dx = dFdx(v_world_pos);
    vec3 dy = dFdy(v_world_pos);
    vec3 normal = normalize(cross(dx, dy));
    if (normal.z < 0.0) {
        normal = -normal;
    }
    float diffuse = max(dot(normal, normalize(u_sun_dir)), 0.0);
    vec3 light = clamp(u_ambient_light + diffuse * u_diffuse_light, 0.0, 1.15);
    frag_color = vec4(waterlogged(rgb * light, v_eye_distance, v_world_pos.z), 1.0);
}
""".replace("__WATER_FOG_GLSL__", _WATER_FOG_GLSL)

_TERRAIN_LINE_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;

out float v_height;
out vec3 v_world_pos;
out float v_eye_distance;

void main() {
    v_height = in_pos.z;
    v_world_pos = in_pos;
    vec4 eye_space = u_view * vec4(in_pos, 1.0);
    v_eye_distance = length(eye_space.xyz);
    gl_Position = u_proj * eye_space;
}
"""

_TERRAIN_FILL_FRAGMENT_SHADER = """
#version 330

uniform vec4 u_color;
uniform float u_height_min;
uniform float u_height_max;
uniform vec3 u_sun_dir;
uniform vec3 u_ambient_light;
uniform vec3 u_diffuse_light;

in float v_height;
in vec3 v_world_pos;
in float v_eye_distance;

out vec4 frag_color;
__WATER_FOG_GLSL__

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
    vec3 light = clamp(u_ambient_light + diffuse * u_diffuse_light, 0.0, 1.15);
    rgb *= light;
    frag_color = vec4(waterlogged(rgb, v_eye_distance, v_world_pos.z), u_color.a);
}
""".replace("__WATER_FOG_GLSL__", _WATER_FOG_GLSL)

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
WATER_TINT_RGB: tuple[float, float, float] = DEFAULT_WATER_TINT
TERRAIN_FILL_RGBA: tuple[float, float, float, float] = (0.28, 0.58, 0.22, 1.0)
TERRAIN_LINE_RGBA: tuple[float, float, float, float] = (0.05, 1.0, 0.20, 0.85)
PARCEL_BORDER_RGBA: tuple[float, float, float, float] = (0.45, 0.85, 0.55, 0.9)
# Lift borders slightly off the terrain so they are not z-fought by the ground.
PARCEL_BORDER_HEIGHT_OFFSET_M: float = 0.35
GROUND_FALLBACK_RGBA: bytes = bytes((80, 120, 70, 255))

# Hover text ("floating text") drawn above a prim. SL keeps it at a constant
# apparent size, so the billboard grows with distance rather than staying a
# fixed number of metres tall; HOVER_TEXT_SCREEN_HEIGHT is that apparent
# height as a fraction of the eye-space distance, tuned by eye.
HOVER_TEXT_SCREEN_HEIGHT: float = 0.045
HOVER_TEXT_OFFSET_M: float = 0.25
HOVER_TEXT_FONT_SIZE: int = 28
HOVER_TEXT_MAX_LINES: int = 8
# Avatar name tags share the hover-text billboard but carry no colour of
# their own; SL draws them in plain white.
AVATAR_NAME_COLOR: tuple[int, int, int, int] = (255, 255, 255, 235)

_LABEL_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;
uniform vec3 u_world_pos;
uniform vec2 u_half_size;

in vec2 in_corner;
in vec2 in_uv;

out vec2 v_uv;

void main() {
    // Camera right/up in world space are the first two rows of the view
    // matrix's rotation block, so the quad always faces the eye.
    vec3 right = vec3(u_view[0][0], u_view[1][0], u_view[2][0]);
    vec3 up    = vec3(u_view[0][1], u_view[1][1], u_view[2][1]);
    vec4 eye_center = u_view * vec4(u_world_pos, 1.0);
    float dist = max(length(eye_center.xyz), 0.001);
    vec2 size = u_half_size * dist;
    vec3 world = u_world_pos
        + right * (in_corner.x * size.x)
        + up * (in_corner.y * size.y);
    v_uv = in_uv;
    gl_Position = u_proj * u_view * vec4(world, 1.0);
}
"""

_LABEL_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_texture;
uniform vec4 u_color;

in vec2 v_uv;
out vec4 frag_color;

void main() {
    vec4 texel = texture(u_texture, v_uv);
    if (texel.a < 0.02) {
        discard;
    }
    frag_color = vec4(texel.rgb * u_color.rgb, texel.a * u_color.a);
}
"""

# Corner offsets in [-0.5, 0.5] paired with UVs. V is flipped because pygame
# surfaces are top-down and GL textures are bottom-up.
_LABEL_QUAD: tuple[float, ...] = (
    -0.5, -0.5, 0.0, 1.0,
     0.5, -0.5, 1.0, 1.0,
     0.5,  0.5, 1.0, 0.0,
    -0.5,  0.5, 0.0, 0.0,
)
_LABEL_INDICES: tuple[int, ...] = (0, 1, 2, 0, 2, 3)


#: Sky colours: near the horizon, and at the zenith.
#:
#: A flat fill was what the compositor cleared to, and it read as a blue wall
#: rather than as air. Nothing in the protocol carries a sky palette -- the
#: windlight settings asset is LLSD *notation*, which this tree does not parse
#: and OpenSim itself only regex-scrapes -- so these are chosen to sit either
#: side of the old flat value rather than derived from anything on the wire.
SKY_HORIZON_COLOR: tuple[float, float, float] = DEFAULT_SKY_HORIZON_COLOR
SKY_ZENITH_COLOR: tuple[float, float, float] = DEFAULT_SKY_ZENITH_COLOR

_SKY_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec2 in_ndc;

out vec3 v_ray;

void main() {
    // The view ray for this corner, recovered by unprojecting the near and far
    // planes. Done here rather than per fragment: four inversions a frame
    // instead of two million, and the direction interpolates correctly across
    // the quad.
    mat4 inv = inverse(u_proj * u_view);
    vec4 near = inv * vec4(in_ndc, -1.0, 1.0);
    vec4 far = inv * vec4(in_ndc, 1.0, 1.0);
    v_ray = (far.xyz / far.w) - (near.xyz / near.w);
    // Just inside the far plane, so anything in the world draws over it.
    gl_Position = vec4(in_ndc, 0.999999, 1.0);
}
"""

#: The sun as the sky draws it, as GLSL, shared with the water shader.
#:
#: One string in two programs rather than two copies, and for the same reason
#: `sky_at_height` is the same expression in both: the sea is showing that sky
#: back, so any disagreement between the two draws as a second sun in the
#: water sitting beside the reflection of the real one.
#:
#: The disc is at the size the region asked for, through `sun_scale`. The haze
#: around it does not scale with it -- that is `glow`, which is read off the
#: document and then drawn nowhere, and identical in all eight of the default
#: cycle's keyframes, so there is no evidence in this document for what its
#: components mean, and a guess dressed as a reading is worse than an honest
#: constant.
_SUN_IN_SKY_GLSL = """
vec3 sun_in_sky(vec3 dir, vec3 sun_dir, vec2 disc) {
    // Pure falloff on the angle to the light direction the day cycle gives
    // us -- no disc geometry, so it costs one dot product.
    float alignment = max(dot(dir, sun_dir), 0.0);
    return vec3(1.0, 0.95, 0.80) * smoothstep(disc.x, disc.y, alignment)
        + vec3(1.0, 0.90, 0.72) * pow(alignment, 18.0) * 0.28;
}
"""

#: The region's cloud layer, as GLSL, shared with the water shader.
#:
#: The same argument as `_SUN_IN_SKY_GLSL` above and `sky_at_height` below:
#: the sea is showing this sky back, and two copies of a cloud layer are two
#: cloud layers the first time either of them moves. It carries its own
#: uniforms, so a pass that includes it does not have to know what it reads --
#: only to hand `_bind_cloud_layer` its program.
_CLOUD_IN_SKY_GLSL = """
uniform vec3 u_cloud_color;
// x: coarse coverage, y: fine coverage, z: variance.
uniform vec3 u_cloud_cover;
// The region's own cloud field, and whether one has arrived. `cloud_id` names
// a 512x512 greyscale texture that tiles seamlessly -- which is what the noise
// below was standing in for. `u_cloud_offsets` is the first two components of
// `cloud_pos_density1` and of `cloud_pos_density2`: where in that texture each
// of the two layers starts.
uniform sampler2D u_cloud_tex;
uniform float u_cloud_textured;
uniform vec4 u_cloud_offsets;
// x: metres across one cell, y and z: how far the layer has drifted.
uniform vec3 u_cloud_scale_drift;
uniform float u_cloud_altitude;

// Value noise on a plane, and the sum of three octaves of it. This is what
// stands in for `cloud_id` while the texture the document names is still on
// its way over the network: a few octaves of value noise is the shape such a
// texture holds.
// Its own hash rather than the sky pass's `cell_hash`: this one runs four
// times per octave per pixel across half the screen, and the two-component
// version is measurably cheaper than packing a vec2 into a vec3 to reuse the
// other -- and this string is compiled into a pass that has no `cell_hash`.
float plane_hash(vec2 p) {
    p = fract(p * vec2(0.1031, 0.1030));
    p += dot(p, p.yx + 33.33);
    return fract((p.x + p.y) * p.x);
}

float value_noise(vec2 p) {
    vec2 cell = floor(p);
    vec2 f = fract(p);
    // Smoothstep the interpolant, or the cells show as diamonds.
    f = f * f * (3.0 - 2.0 * f);
    float a = plane_hash(cell);
    float b = plane_hash(cell + vec2(1.0, 0.0));
    float c = plane_hash(cell + vec2(0.0, 1.0));
    float d = plane_hash(cell + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

// Three octaves, not four. A fourth costs another quarter of the cloud pass
// and adds detail at a scale the layer's own perspective has already
// compressed below a pixel over most of the sky. This runs on llvmpipe on the
// machine that matters, where the whole frame is a budget of tens of
// milliseconds.
float cloud_noise(vec2 p) {
    float total = value_noise(p) * 0.5;
    total += value_noise(p * 2.03) * 0.25;
    total += value_noise(p * 4.11) * 0.125;
    return total / 0.875;
}

// How much cloud there is at a point on the layer.
//
// The region's own texture where one has arrived and the noise above where it
// has not -- the asset is fetched over the network mid-session, and a sky with
// no cloud in it for those seconds is worse than a sky with invented cloud.
// The branch is on a uniform, so the mip level this needs is still well
// defined across the quad.
float cloud_field(vec2 p) {
    // A tile of the texture holds several clouds where one cell of the noise
    // holds about one, so the stand-in is scaled to the thing it stands in
    // for. Drawn a cell to a tile it would be one cloud across the whole sky.
    return u_cloud_textured > 0.0
        ? texture(u_cloud_tex, p).r
        : cloud_noise(p * __CLOUD_NOISE_CELLS__);
}

// The cloud layer, over whatever is already being shown in `dir`.
//
// A flat layer at a fixed height, hit by the view ray: the further from
// straight up the ray points, the further across the layer it lands, which
// is the perspective that makes a flat sheet read as sky rather than as
// wallpaper. Near the horizon that distance runs away, so the layer fades
// out before it can alias -- which is also where real cloud disappears
// into the haze.
vec3 cloud_over(vec3 base, vec3 dir) {
    if (u_cloud_cover.x <= 0.0 || dir.z <= 0.001) {
        return base;
    }
    vec2 ground = (dir.xy / dir.z) * u_cloud_altitude;
    vec2 uv = ground / max(u_cloud_scale_drift.x, 1.0) + u_cloud_scale_drift.yz;
    // Two layers, each starting where its own `cloud_pos_density` says.
    // In every keyframe of the default cycle the two offsets are equal,
    // so this draws one field at the sum of the two densities -- which is
    // what the document asks for, oddly, and not a reason to invent a
    // second scale for it to be interesting at.
    float amount = cloud_field(uv + u_cloud_offsets.xy) * u_cloud_cover.x;
    // The second layer is a whole extra sample and the default cycle asks
    // for the third not at all -- `cloud_variance` is 0 in every keyframe
    // of it. Paying for a field multiplied by zero is a third of the
    // cloud pass for nothing.
    if (u_cloud_cover.y > 0.002) {
        amount += cloud_field(uv + u_cloud_offsets.zw) * u_cloud_cover.y;
    }
    if (u_cloud_cover.z > 0.002) {
        amount += (cloud_field(uv * 0.31 - 7.0) - 0.5) * u_cloud_cover.z;
    }
    // The texture is what has the holes in it, so with one in hand the
    // density is the coverage and nothing has to decide where an edge
    // falls. The edge band is for the noise, which is a field of smooth
    // hills with no gaps at all -- without a threshold it draws an even
    // grey haze rather than clouds with sky between them.
    float cover = u_cloud_textured > 0.0
        ? clamp(amount, 0.0, 1.0)
        : smoothstep(CLOUD_EDGE_LOW, CLOUD_EDGE_HIGH, amount);
    cover *= smoothstep(0.02, 0.22, dir.z);
    return mix(base, u_cloud_color, cover);
}
"""

# The edge band and the cell count are `atmosphere.py`'s numbers, and GLSL
# cannot import a Python constant. Substituted once, into the one copy of the
# layer, so both passes are drawing the same cloud.
_CLOUD_IN_SKY_GLSL = (
    _CLOUD_IN_SKY_GLSL.replace("CLOUD_EDGE_LOW", f"{CLOUD_EDGE_LOW:.6f}")
    .replace("CLOUD_EDGE_HIGH", f"{CLOUD_EDGE_HIGH:.6f}")
    .replace("__CLOUD_NOISE_CELLS__", f"{CLOUD_NOISE_CELLS_PER_TILE:f}")
)

#: The moon as the sky draws it, as GLSL, shared with the water shader.
#:
#: The third of these, after `_SUN_IN_SKY_GLSL` and `_CLOUD_IN_SKY_GLSL`, and
#: for the same reason: the sea is showing this sky back, and a moon drawn from
#: two copies is two moons the first time either one moves.
#:
#: The stars are deliberately not in here, and that is the one thing in the sky
#: the sea does not show back. `star_field` is a field of points a twentieth of
#: a degree across, sampled once per pixel with no filter of any kind. In the
#: sky that is fine, because the ray varies smoothly from pixel to pixel; off a
#: rippled sea it does not -- the surface turns the reflected ray by degrees
#: between neighbours, so the field would be sampled at random and come back as
#: white speckle rather than as stars. The moon has no such problem: it is a
#: disc, and a disc smeared along a rippled surface is a moonpath, which is
#: what a moonlit sea looks like.
_MOON_IN_SKY_GLSL = """
uniform vec3 u_moon_dir;
uniform float u_moon_level;
// The cosines of the moon's disc's outer and inner edge.
uniform vec2 u_moon_disc;
// The two world axes across the moon's face, which is what makes the face have
// a way up at all. See `moon_face_axes`.
uniform vec3 u_moon_across;
uniform vec3 u_moon_up;
// The moon's own face, and whether one has arrived. `moon_id` is an ordinary
// texture behind the ordinary GetTexture capability, so the moon is a
// photograph rather than a disc of one colour -- but it is fetched over the
// network mid-session, and until it lands there has to be something up there.
uniform sampler2D u_moon_tex;
uniform float u_moon_textured;

// The moon: a disc with a soft edge, about the angular size of the sun
// blob below it. Both are drawn a little large -- the real pair are half a
// degree across, which at this field of view is a few pixels and reads as
// a speck.
vec3 moon_in_sky(vec3 dir) {
    if (u_moon_level <= 0.0) {
        return vec3(0.0);
    }
    vec3 moon = normalize(u_moon_dir);
    float moon_alignment = dot(dir, moon);
    float disc = smoothstep(u_moon_disc.x, u_moon_disc.y, moon_alignment);
    vec3 face = vec3(0.96, 0.95, 0.90);
    if (u_moon_textured > 0.0 && disc > 0.0) {
        // Two axes across the moon's own face, from the same quaternion
        // that says where the moon is: `moon_rotation` takes the moon's
        // own +Y and +Z to these. Not a cross product with world up,
        // which was here before and turns the face a half circle in one
        // frame as the moon crosses the meridian, because that is where
        // the cross product changes sign.
        vec3 across = u_moon_across;
        vec3 upward = u_moon_up;
        // The disc's outer edge as a sine, which is the radius the face
        // has to span: u_moon_disc.x is its cosine.
        float reach = max(sqrt(1.0 - u_moon_disc.x * u_moon_disc.x), 1e-5);
        vec2 uv = clamp(
            0.5 + vec2(dot(dir, across), dot(dir, upward)) / (2.0 * reach),
            0.0,
            1.0
        );
        vec4 sampled = texture(u_moon_tex, uv);
        face = sampled.rgb;
        // The face's own alpha cuts the disc rather than fading toward
        // the fallback colour: where a moon texture is transparent what
        // is behind it is the sky, not a paler moon. The asset that
        // prompted this is a photograph inscribed in its own square, so
        // its corners are transparent and the disc would otherwise put
        // four dark spurs on them.
        disc *= sampled.a;
    }
    return face * disc * u_moon_level;
}
"""


_SKY_FRAGMENT_SHADER = """
#version 330

uniform vec3 u_horizon;
uniform vec3 u_zenith;
uniform vec3 u_sun_dir;
// The cosine of the sun's disc's outer and inner edge -- how large the region
// asks for it to be drawn. The moon's is in `_MOON_IN_SKY_GLSL`.
uniform vec2 u_sun_disc;
uniform float u_star_level;
// The celestial sphere's own three axes, in world space. The stars are fixed
// to the sphere and the day cycle turns it, so a ray is turned into this
// frame before it is hashed -- see `celestial_axes`.
uniform vec3 u_sphere_x;
uniform vec3 u_sphere_y;
uniform vec3 u_sphere_z;
// The sea's own colour, and (reach, surface height, eye height) -- see the
// water shader. Only the reach is read here: a viewer under the surface has
// no sky, and the far wall of the water is what is in its place.
uniform vec3 u_water_fog;
uniform vec3 u_water_depth;

in vec3 v_ray;

out vec4 frag_color;

__SUN_IN_SKY_GLSL__
__MOON_IN_SKY_GLSL__
__CLOUD_IN_SKY_GLSL__

// One float in 0..1 from a cell of the sky. Deterministic and view
// independent, which is the whole point: the stars have to sit still on the
// celestial sphere while the camera turns under them, and a hash of the *cell*
// rather than of the screen does that for free.
float cell_hash(vec3 cell) {
    vec3 p = fract(cell * 0.1031 + vec3(0.1031, 0.1030, 0.0973));
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
}

// A star field, procedural because the document does not name one: unlike
// `moon_id`, `cloud_id` and the water's `normal_map`, there is no `star_id`
// in any keyframe of the live cycle, so there is nothing to fetch. The sky is
// divided into cells, about one in thirty gets a star at a hashed position
// inside it, and each is a small round falloff.
//
// The direction is the sphere's own, not the world's -- the caller turns it in
// -- so the cells are fixed to the sphere and the day cycle carries them
// round with the sun and the moon. See `celestial_axes`.
float star_field(vec3 dir) {
    vec3 p = dir * 220.0;
    vec3 cell = floor(p);
    float pick = cell_hash(cell);
    if (pick < 0.966) {
        return 0.0;
    }
    vec3 centre = vec3(
        cell_hash(cell + 11.0),
        cell_hash(cell + 23.0),
        cell_hash(cell + 37.0)
    );
    float distance_to = length((p - cell) - centre);
    // Brightness varies per star, or a field of identical dots reads as a
    // pattern rather than as a sky.
    float magnitude = 0.35 + 0.65 * fract(pick * 91.7);
    return smoothstep(0.18, 0.0, distance_to) * magnitude;
}

void main() {
    vec3 dir = normalize(v_ray);

    // How much of the sky survives the water between here and the surface.
    //
    // Drawing a clear blue sky over a submerged camera was the whole reason
    // nobody could tell they had gone under -- but the answer is not *no*
    // sky. It is the sky seen through however much water the ray crosses on
    // its way out, which is the depth divided by how steeply the ray climbs.
    // Straight up from six metres down that is six metres of water and the
    // sky comes through; ten degrees above the horizontal it is thirty-five
    // and it does not. That difference *is* the bright circle overhead a
    // swimmer sees, arriving out of the arithmetic rather than being drawn.
    float clarity = 1.0;
    if (u_water_depth.x > 0.0) {
        clarity = dir.z > 0.001
            ? exp(-((u_water_depth.y - u_water_depth.z) / dir.z) / u_water_depth.x)
            : 0.0;
        // Nearly all of the sky over a submerged camera is in this branch,
        // and the sun, the stars and three octaves of cloud noise are what it
        // skips.
        if (clarity < 0.01) {
            frag_color = vec4(u_water_fog, 1.0);
            return;
        }
    }
    // Z is up. Below the horizon keeps the horizon colour: the water plane
    // covers it, and a second gradient there would show through the sea.
    float height = clamp(dir.z, 0.0, 1.0);
    vec3 rgb = mix(u_horizon, u_zenith, sqrt(height));

    // Stars go under the sun and moon and above the gradient, and the
    // region's own night keyframes are what turn them on at all.
    if (u_star_level > 0.0) {
        // `rise` does two jobs, and the second is the one that matters: it
        // fades the field in over the first few degrees so it does not stop
        // at a hard line along the horizon, and because smoothstep clamps, it
        // is also zero for every ray pointing below it. A separate `dir.z > 0`
        // guard beside this would be dead code -- which is exactly how the
        // mutation battery found it.
        float rise = smoothstep(0.0, 0.12, dir.z);
        // Hashed in the sphere's frame rather than the world's, so the field
        // turns with the night instead of hanging off the region's axes.
        // `rise` stays in the world's: the horizon is a fact about the ground
        // the viewer is standing on, not about the sky.
        vec3 fixed_to_the_sky = vec3(
            dot(dir, u_sphere_x), dot(dir, u_sphere_y), dot(dir, u_sphere_z)
        );
        rgb += vec3(0.92, 0.94, 1.0)
            * star_field(fixed_to_the_sky) * u_star_level * rise;
    }

    rgb += moon_in_sky(dir);

    rgb = cloud_over(rgb, dir);

    // The sun and the haze around it, drawn after the clouds so a cloud in
    // front of the sun still glows rather than reading as a hole. The same
    // expression the sea reflects; see `_SUN_IN_SKY_GLSL`. The disc is at the
    // size the region asked for -- the 900th power this replaced was the same
    // size and no size at all, since nothing could change it.
    rgb += sun_in_sky(dir, normalize(u_sun_dir), u_sun_disc);

    frag_color = vec4(clamp(mix(u_water_fog, rgb, clarity), 0.0, 1.0), 1.0);
}
"""

# The three pieces of sky the sea also shows back, compiled in from the one
# copy of each. They carry their own numbers already; see
# `_CLOUD_IN_SKY_GLSL`. What is left in this shader is the gradient, which the
# water has its own `sky_at_height` for, and the stars, which it deliberately
# has not -- see `_MOON_IN_SKY_GLSL`.
_SKY_FRAGMENT_SHADER = (
    _SKY_FRAGMENT_SHADER.replace("__SUN_IN_SKY_GLSL__", _SUN_IN_SKY_GLSL)
    .replace("__MOON_IN_SKY_GLSL__", _MOON_IN_SKY_GLSL)
    .replace("__CLOUD_IN_SKY_GLSL__", _CLOUD_IN_SKY_GLSL)
)

#: A quad in clip space. Two triangles rather than the usual oversized single
#: triangle, because the ray is interpolated across it and a triangle reaching
#: outside the frustum extrapolates the corners.
_SKY_VERTICES: tuple[float, ...] = (
    -1.0, -1.0,
     1.0, -1.0,
     1.0,  1.0,
    -1.0,  1.0,
)
_SKY_INDICES: tuple[int, ...] = (0, 1, 2, 0, 2, 3)

#: Where the sea starts turning into the sky, and where it finishes, in
#: metres from the viewer.
#:
#: A rendering choice, and the numbers are the region's own size rather than
#: anything on the wire: the near edge is about a region across, so nothing
#: inside the region a viewer is standing in is hazed, and the far edge is
#: inside `VOID_WATER_EXTENT_M` so the plane has finished becoming the sky
#: before it runs out.
#:
#: `WaterSettings.fog_density` is *not* this. That one is the fog seen from
#: **under** the surface, which is a different quantity in a different medium,
#: and using it here would be reading the document to mean something it does
#: not say.
#: Which texture units the sky pass binds the moon's face and the cloud field
#: to.
#:
#: Not zero. The terrain pass owns units 0 through 3 for its four ground
#: textures, and although the two passes never run together, a sampler left
#: pointing at unit 0 reads whatever was bound there last -- which on a frame
#: with terrain in it is the ground, stretched across the moon.
_MOON_TEXTURE_UNIT: int = 4
_CLOUD_TEXTURE_UNIT: int = 5
_WATER_NORMAL_UNIT: int = 6


@dataclass(frozen=True)
class _CloudLayer:
    """What the region's cloud layer is drawn from.

    Two passes draw it now -- the sky, and the sea showing that sky back --
    and there is one reading of the scene between them. A second copy of these
    defaults would be a second cloud layer the first time either one moved.
    """

    color: tuple[float, float, float]
    cover: tuple[float, float, float]
    scale_drift: tuple[float, float, float]
    offsets: tuple[float, float, float, float]


def _cloud_layer(scene: Scene) -> _CloudLayer:
    """The scene's cloud layer, or a layer of nothing if it is switched off."""
    return _CloudLayer(
        color=getattr(scene, "cloud_color", (0.41, 0.41, 0.41)),
        # The toggle is spent here rather than in the shader: a zero cover
        # skips the whole noise field, which is the five milliseconds the
        # setting exists to give back -- in both passes now, since the sea
        # asks the same field the same question.
        cover=(
            getattr(scene, "cloud_cover", (0.0, 0.0, 0.0))
            if getattr(scene, "render_clouds", True)
            else (0.0, 0.0, 0.0)
        ),
        scale_drift=getattr(scene, "cloud_scale_drift", (900.0, 0.0, 0.0)),
        offsets=getattr(scene, "cloud_offsets", (0.0, 0.0, 0.0, 0.0)),
    )


@dataclass(frozen=True)
class _MoonInSky:
    """Where the moon is, how big, how bright and which way up.

    Two passes draw it -- the sky, and the sea showing that sky back -- off
    one reading of the scene, for the same reason as `_CloudLayer`.
    """

    direction: tuple[float, float, float]
    level: float
    disc: tuple[float, float]
    face_axes: tuple[tuple[float, float, float], tuple[float, float, float]]


def _moon_in_sky(scene: Scene) -> _MoonInSky:
    """The scene's moon, or a moon of no brightness if it has not got one."""
    return _MoonInSky(
        direction=_a_direction_or(
            getattr(scene, "moon_direction", None), (0.0, 0.0, -1.0)
        ),
        level=float(getattr(scene, "moon_level", 0.0) or 0.0),
        disc=getattr(scene, "moon_disc", DEFAULT_MOON_DISC),
        face_axes=getattr(scene, "moon_face_axes", DEFAULT_MOON_FACE_AXES),
    )


def _bind_moon_in_sky(program, moon: _MoonInSky, texture: object | None) -> None:
    """Hand a program the moon `_MOON_IN_SKY_GLSL` reads."""
    program["u_moon_dir"].value = moon.direction
    program["u_moon_level"].value = moon.level
    program["u_moon_disc"].value = moon.disc
    program["u_moon_across"].value = moon.face_axes[0]
    program["u_moon_up"].value = moon.face_axes[1]
    # Not unit 0: the terrain pass owns 0 through 3, and although it never runs
    # beside the sky, a sampler left pointing at 0 reads whatever was bound
    # there last -- which on a frame with terrain in it is the ground,
    # stretched across the moon.
    program["u_moon_tex"].value = _MOON_TEXTURE_UNIT
    program["u_moon_textured"].value = 1.0 if texture is not None else 0.0
    if texture is not None:
        texture.use(location=_MOON_TEXTURE_UNIT)


def _bind_cloud_layer(
    program, layer: _CloudLayer, texture: object | None
) -> None:
    """Hand a program the cloud layer `_CLOUD_IN_SKY_GLSL` reads."""
    program["u_cloud_color"].value = layer.color
    program["u_cloud_cover"].value = layer.cover
    program["u_cloud_scale_drift"].value = layer.scale_drift
    program["u_cloud_altitude"].value = CLOUD_ALTITUDE_METRES
    program["u_cloud_offsets"].value = layer.offsets
    program["u_cloud_tex"].value = _CLOUD_TEXTURE_UNIT
    program["u_cloud_textured"].value = 1.0 if texture is not None else 0.0
    if texture is not None:
        texture.use(location=_CLOUD_TEXTURE_UNIT)

WATER_HAZE_NEAR_M: float = 260.0
WATER_HAZE_FAR_M: float = 900.0

#: Each further wave drawn for each of the document's two: how many times
#: finer it is than the one before, how far off that one's heading it runs,
#: and how tall it is beside it.
#:
#: Entirely a rendering choice, and a necessary one. Real water has no period
#: and a small sum of sines has one, which draws as a regular grid. Deriving
#: the extra waves from the two the region gave, rather than picking new
#: headings, keeps the sea pointing where the region said even though the
#: shape of it is this viewer's invention.
#:
#: Not an octave: 2.0 would put every second crest of a harmonic on a crest of
#: its parent and the grid would come back at half the spacing.
WAVE_HARMONIC: float = 2.3
WAVE_HARMONIC_TURN_RAD: float = 0.7
WAVE_HARMONIC_HEIGHT: float = 0.45

#: How many times over to do that, counting the region's own pair as the
#: first. Measured against screenshots rather than reasoned about, because the
#: artefact this is here to remove is an interference pattern and a GL test
#: reads one pixel: from a camera at eye height a wave is seen almost edge on
#: and one harmonic is plenty, but from above the surface is seen in plan and
#: four sines still read as woven mesh. Each term turns another
#: `WAVE_HARMONIC_TURN_RAD` off the last, so three of them span two radians of
#: heading rather than one, which is what stops the crests lining up into
#: rows.
#:
#: Three rather than more because the fourth is under four per cent of the
#: slope and costs as much as any other: on llvmpipe the water pass measures
#: 4.0 ms at one octave and 5.2 at three, and 6.5 at four for a difference
#: nobody can see.
WAVE_OCTAVES: int = 3


#: What the octaves add up to, so a sea with more of them in it is not a
#: steeper sea. Summed from the unfaded heights: an octave that has faded out
#: with distance takes its share of the slope with it, which is what makes the
#: far water flatten rather than merely coarsen.
WAVE_SLOPE_TOTAL: float = sum(WAVE_HARMONIC_HEIGHT**n for n in range(WAVE_OCTAVES))


#: How many of the region's ripples one tile of its `normal_map` holds.
#:
#: Measured off the asset rather than chosen: the map the default cycle names
#: is a wind-ripple sheet whose crests run along v and travel along u, and the
#: power spectrum of its red channel -- the slope along u -- peaks at nine
#: cycles across the tile, with a broadband tail out to the pixel. So one tile
#: is nine wavelengths of the wave it is laid on, and the tail underneath that
#: is the fine chop a sum of sines has to invent.
#:
#: This is the same mistake the clouds made, and it is worth naming twice: a
#: tile of a texture is not one cycle of what is drawn on it. Laid a
#: wavelength to a tile the sea came out as a fine mesh at ten centimetres a
#: ripple, which mips to flat sheet over all but the nearest water.
WATER_NORMAL_RIPPLES_PER_TILE: float = 9.0


_WATER_VERTEX_SHADER = """
#version 330

uniform mat4 u_view;
uniform mat4 u_proj;

in vec3 in_pos;
// Which way this face points before any wave leans it. (0, 0, 1) for the sea
// itself; horizontal for a wall closing the step between two sea levels.
in vec3 in_face;

out vec3 v_world;
out vec3 v_face;

void main() {
    v_world = in_pos;
    v_face = in_face;
    gl_Position = u_proj * u_view * vec4(in_pos, 1.0);
}
"""

_WATER_FRAGMENT_SHADER = """
#version 330

// The sea's own colour -- what is seen looking *through* it -- and how much
// of what is under it shows through at all.
uniform vec4 u_color;
// The sky it shows back, as the two colours the sky itself is drawn from.
uniform vec3 u_horizon;
uniform vec3 u_zenith;
uniform vec3 u_eye;
// The two wave directions, as unit vectors: (d1.xy, d2.xy).
uniform vec4 u_wave_dirs;
// x: radians of wave per metre. y: how far the surface leans at its steepest.
uniform vec3 u_ripple;
// How far each of the two waves has travelled, in radians.
uniform vec2 u_wave_phase;
// x: how much sky is reflected looking straight down. y: how much more of it
// there is at a grazing angle.
uniform vec2 u_fresnel;
// The sea's own colour again, and (how far a viewer under the surface can
// see, the surface's height, the eye's height). A reach of zero means the
// viewer is above the water and this is the ordinary sea.
uniform vec3 u_water_fog;
uniform vec3 u_water_depth;
// The region's own surface, and whether one has arrived. `normal_map` names a
// tangent-space normal map that repeats, which is what the sines below are
// standing in for.
uniform sampler2D u_water_normals;
uniform float u_water_mapped;
// Where the sun is and how large the region draws it. The same two the sky
// pass is given, because this is that sky reflected.
uniform vec3 u_sun_dir;
uniform vec2 u_sun_disc;

in vec3 v_world;
in vec3 v_face;
out vec4 frag_color;

// The sky, as a function of how high a ray leaves the surface. The same
// expression the sky shader is drawn with, and it has to be the same one:
// this is the sea showing that sky back, and any disagreement between the two
// draws as a second horizon in the water.
//
// Takes the height alone rather than the direction, because that is all the
// gradient uses -- and it means the reflected ray never has to be built.
vec3 sky_at_height(float height) {
    return mix(u_horizon, u_zenith, sqrt(clamp(height, 0.0, 1.0)));
}

__SUN_IN_SKY_GLSL__
__MOON_IN_SKY_GLSL__
__CLOUD_IN_SKY_GLSL__

// One wave's contribution to the slope of the surface: the derivative of a
// sine along the direction it runs in, faded by how legible it still is.
vec2 crest(vec2 ground, vec2 direction, float number, float phase, float height) {
    return direction * (cos(dot(ground, direction) * number + phase) * height);
}

// One wave's contribution to the surface's lean, taken from the region's
// normal map.
//
// The map is laid in the wave's own frame -- along its heading and across it
// -- so that sliding it by the wave's phase moves it the way the wave runs.
// `number` is radians of wave per metre, and one tile of the map holds
// `WATER_NORMAL_RIPPLES_PER_TILE` wavelengths of it -- the map's own dominant
// frequency, so that a ripple on it comes out the length the document asked
// for.
//
// What comes back is the *normal's* own horizontal part, not a height
// gradient: a normal map stores the normal, so it is added to the vertical
// rather than subtracted from it. The sines below store a height and have to
// be differentiated and negated, which is the sign this one does not carry.
vec2 mapped_lean(vec2 ground, vec2 direction, float number, float phase) {
    vec2 across = vec2(-direction.y, direction.x);
    float tile = 6.2831853 * __WATER_RIPPLES_PER_TILE__;
    vec2 uv = vec2(dot(ground, direction), dot(ground, across)) * number / tile;
    uv.x -= phase / tile;
    // Tangent space to world: x is along the wave, y across it.
    vec2 tangent = texture(u_water_normals, uv).xy * 2.0 - 1.0;
    return direction * tangent.x + across * tangent.y;
}

// A direction turned by a fixed angle, for the harmonics below.
vec2 turned(vec2 direction) {
    return vec2(
        direction.x * __WAVE_TURN_COS__ - direction.y * __WAVE_TURN_SIN__,
        direction.x * __WAVE_TURN_SIN__ + direction.y * __WAVE_TURN_COS__
    );
}

void main() {
    vec2 ground = v_world.xy;
    // A wall closes the step between two regions that disagree about their
    // sea level, and it is vertical. Every wave term below is a function of
    // world x and y alone, which on a vertical face varies along one axis
    // only -- so waving a wall draws it in stripes rather than in ripples.
    // It takes the normal it was built with, and the whole wave block is
    // skipped for it.
    bool wall = abs(v_face.z) < 0.5;
    vec3 normal = normalize(v_face);
    if (!wall && u_ripple.z > 0.0) {
        if (u_water_mapped > 0.0) {
            // The region's own normal map, one sample per wave. Each is laid
            // along its own wave's heading and slid along it by that wave's
            // phase, so the map travels the way the document says the wave
            // does; the tangent-space lean it gives back is turned into world
            // by the same frame it was sampled in.
            //
            // Two samples rather than the six sines below, and no harmonics
            // at all: a normal map is already a whole spectrum of ripple, and
            // the reason those exist is that a pair of sines is not.
            vec2 lean =
                mapped_lean(ground, u_wave_dirs.xy, u_ripple.x, u_wave_phase.x)
                + mapped_lean(ground, u_wave_dirs.zw, u_ripple.y, u_wave_phase.y);
            // No divisor beside the sines' __WAVE_SLOPE_TOTAL__: the map's
            // tangent reaches about half a unit either way, so the two of
            // them together peak at one, which is where the octaves are
            // normalised to as well. Both paths lean by `u_ripple.z` at the
            // steepest.
            //
            // And no minus sign either, for the reason in `mapped_lean`: this
            // is the normal already, where the sines are a height field.
            normal = normalize(vec3(lean * u_ripple.z, 1.0));
        } else {
            // How wide one pixel is in wave phase, for each of the region's
            // two directions. Waves are about nine metres long and the plane
            // runs for two kilometres, so most of it is being asked for a
            // ripple narrower than a pixel; sampled once per pixel that is
            // not a ripple, it is moire, which is the one artefact that reads
            // as a broken renderer rather than as rough water. Measured once
            // on the base term and multiplied for the harmonics, which is a
            // fade rather than a filter. (The map above needs none of this:
            // its mip levels are the same fade, done by the sampler.)
            vec2 width = vec2(
                fwidth(dot(ground, u_wave_dirs.xy) * u_ripple.x),
                fwidth(dot(ground, u_wave_dirs.zw) * u_ripple.y)
            );
            // Two waves per octave, not the document's two in total. Two
            // alone draw a cross-hatch: a regular diamond grid that reads as
            // corrugated iron, because a sea is not periodic and two sines
            // are. Each octave is the pair before it at __WAVE_HARMONIC__
            // times the frequency, turned another __WAVE_TURN_RAD__ radians
            // and at a fraction of the height -- which breaks the pattern
            // without inventing a heading the region never gave.
            vec2 slope = vec2(0.0);
            vec2 first = u_wave_dirs.xy;
            vec2 second = u_wave_dirs.zw;
            float step_up = 1.0;
            float height = 1.0;
            for (int octave = 0; octave < __WAVE_OCTAVES__; octave++) {
                // The two offsets are there so the octaves do not all start
                // their cycle together at the origin, which would put a seam
                // through it.
                slope += crest(
                    ground,
                    first,
                    u_ripple.x * step_up,
                    u_wave_phase.x * step_up + 1.7 * float(octave),
                    height * (1.0 - smoothstep(1.0, 3.0, width.x * step_up))
                );
                slope += crest(
                    ground,
                    second,
                    u_ripple.y * step_up,
                    u_wave_phase.y * step_up + 4.1 * float(octave),
                    height * (1.0 - smoothstep(1.0, 3.0, width.y * step_up))
                );
                first = turned(first);
                second = turned(-second);
                step_up *= __WAVE_HARMONIC__;
                height *= __WAVE_HARMONIC_HEIGHT__;
            }
            normal = normalize(
                vec3(-slope * u_ripple.z / __WAVE_SLOPE_TOTAL__, 1.0)
            );
        }
    }

    // Seen from underneath, the surface is a ceiling: the same plane with its
    // normal the other way up, so that the angle below is measured the same
    // way the angle above is. A wall is vertical and has no up side, so it is
    // left alone here and turned to face the viewer below instead.
    bool below = u_water_depth.x > 0.0;
    if (below && !wall) {
        normal = -normal;
    }

    vec3 view = normalize(u_eye - v_world);
    // A wall is built with one outward normal but nothing culls faces here,
    // so it is drawn from both sides -- and from the far side that normal
    // points away from the eye, which turns the Fresnel term inside out and
    // reflects the ground instead of the sky. Turning it toward the viewer
    // costs a dot product and makes the wall the same water from either side.
    if (wall && dot(normal, view) < 0.0) {
        normal = -normal;
    }
    float facing = clamp(dot(normal, view), 0.0, 1.0);
    // Schlick's shape: reflectance rises as the fifth power of one minus the
    // cosine of the viewing angle. This is what `fresnel_offset` and
    // `fresnel_scale` are being read as, and it is what replaces the single
    // fixed sky-in-the-sea mixture the flat plane had -- a surface is mostly
    // its own colour looking down into it and mostly sky along it.
    // Multiplied out rather than left as pow(): a general power is an
    // exponential and a logarithm, and this runs on every pixel of half the
    // screen on a software rasteriser.
    float grazing = 1.0 - facing;
    float grazing_squared = grazing * grazing;
    float mirror = clamp(
        u_fresnel.x + u_fresnel.y * grazing_squared * grazing_squared * grazing,
        0.0,
        1.0
    );
    // The reflected ray is `view` mirrored in the normal, and only its height
    // is wanted, so only its height is worked out.
    // From above, what is reflected is the sky and what is transmitted is the
    // sea: more sky the flatter the angle. From below the two swap, and the
    // surface stops being a colour at all -- it is the sea reflected back
    // down, over however much of what is above it still gets through.
    //
    // So underneath it draws as the water's own colour at `mirror` opacity
    // and lets the pass behind it supply the rest. That pass is the sky,
    // which has already taken the same water off the same ray, so the two
    // agree without either knowing about the other. Straight up `mirror` is
    // about a half and what is above comes through; by a grazing angle it is
    // nearly one and the ceiling is a mirror, which is what a diver sees
    // outside the cone overhead.
    //
    // Refraction is left out: it would bend the whole sky into that cone
    // rather than letting it span the sky, and the cone is where a viewer
    // looks anyway.
    if (below) {
        frag_color = vec4(clamp(u_water_fog, 0.0, 1.0), mirror);
        return;
    }
    // The reflected ray, in full this time. The gradient only ever wanted its
    // height, and for a long while that was all that was worked out -- but the
    // sun is a direction in the sky and not a height in it, and the sun in the
    // water is the one thing anyone recognises a sea by.
    vec3 reflected = 2.0 * facing * normal - view;
    // The sky along the reflected ray, built the way the sky pass builds it:
    // the gradient, the moon over that, the cloud layer over both, and the sun
    // over everything. Not a reflection model and deliberately not -- it is
    // the sky that is being drawn overhead, seen in a mirror, and a mirror
    // does not need a model of the thing in front of it. The order is the
    // sky's own, too: a cloud in front of the sun still glows there, and a sea
    // that put the sun under the cloud would show back a sky its own sky
    // disagrees with.
    //
    // Everything the sky pass draws is in here except the stars, and that is
    // on purpose -- see `_MOON_IN_SKY_GLSL`.
    //
    // The cloud layer is hit from the eye rather than from this patch of
    // surface. The sky pass anchors it at the eye as well, so this is exactly
    // the cloud drawn overhead and not a second one arrived at another way --
    // and the layer is a kilometre up where the eye is metres above the
    // water, so there is nothing in the difference to see.
    //
    // What breaks the sun in it into a glittering path rather than one round
    // highlight is the surface, which is the region's own normal map; six
    // sines gave a row of repeating blobs instead.
    vec3 mirrored = cloud_over(
        sky_at_height(reflected.z) + moon_in_sky(reflected), reflected
    );
    mirrored += sun_in_sky(reflected, normalize(u_sun_dir), u_sun_disc);
    // Weighted by `mirror`, so the reflection strengthens toward the horizon
    // the way the sky does: `mix(colour, sky, m) + m * sun` is this same
    // expression, which is what it was before the cloud joined the sun in it.
    vec3 rgb = mix(u_color.rgb, mirrored, mirror);

    // Distant sea becomes the sky it meets. Without this the horizon is a
    // hard line -- measured at sixty-seven levels of jump from a camera three
    // metres over the water, because the sea is the sky's horizon colour with
    // a dark tint blended over it and the two simply meet. Real distance puts
    // air in between, and the far edge of the plane is exactly where the sky
    // starts, so that is where the two have to agree.
    //
    // The distance is measured here and not handed down from the vertex
    // shader, which was the first attempt and was wrong: the plane is two
    // triangles more than two kilometres across, so an interpolated distance
    // is the average of three corners a kilometre away and the sea at the
    // viewer's feet comes out as hazed as the sea at the horizon.
    float haze = smoothstep(
        __WATER_HAZE_NEAR__, __WATER_HAZE_FAR__, length(ground - u_eye.xy)
    );
    // What it becomes is the sky *along this pixel's own bearing*, at the
    // horizon: the gradient there is `u_horizon` everywhere, but the sun is
    // not, and mixing to the bare horizon colour puts the far sea a few
    // levels under the sky right above it -- a seam along the whole horizon
    // wherever the sun is low. Which is the same wall this haze exists to
    // remove, arriving by another route.
    rgb = mix(
        rgb,
        u_horizon + sun_in_sky(
            normalize(vec3(-view.xy, 0.0)), normalize(u_sun_dir), u_sun_disc
        ),
        haze
    );
    // The opacity goes with both. Reflected light does not come from under the
    // surface, so a stretch of water showing back a lot of sky hides what is
    // beneath it; and at the horizon there is nothing beneath it but sky, so
    // staying translucent there only lets that sky through at the wrong
    // brightness -- the same wall by another route.
    float alpha = mix(mix(u_color.a, 1.0, mirror), 1.0, haze);
    frag_color = vec4(clamp(rgb, 0.0, 1.0), alpha);
}
""".replace(
    "__WATER_HAZE_NEAR__", f"{WATER_HAZE_NEAR_M:f}"
).replace(
    "__WATER_HAZE_FAR__", f"{WATER_HAZE_FAR_M:f}"
).replace(
    "__WAVE_HARMONIC_HEIGHT__", f"{WAVE_HARMONIC_HEIGHT:f}"
).replace(
    "__WAVE_HARMONIC__", f"{WAVE_HARMONIC:f}"
).replace(
    "__WAVE_TURN_COS__", f"{math.cos(WAVE_HARMONIC_TURN_RAD):f}"
).replace(
    "__WAVE_TURN_SIN__", f"{math.sin(WAVE_HARMONIC_TURN_RAD):f}"
).replace(
    "__WAVE_TURN_RAD__", f"{WAVE_HARMONIC_TURN_RAD:g}"
).replace(
    "__WAVE_OCTAVES__", f"{WAVE_OCTAVES:d}"
).replace(
    "__WAVE_SLOPE_TOTAL__", f"{WAVE_SLOPE_TOTAL:f}"
).replace(
    "__WATER_RIPPLES_PER_TILE__", f"{WATER_NORMAL_RIPPLES_PER_TILE:f}"
).replace(
    "__SUN_IN_SKY_GLSL__", _SUN_IN_SKY_GLSL
).replace(
    "__MOON_IN_SKY_GLSL__", _MOON_IN_SKY_GLSL
).replace(
    "__CLOUD_IN_SKY_GLSL__", _CLOUD_IN_SKY_GLSL
)

#: How far water extends past the region edge, in metres.
#:
#: The water plane used to be exactly the region: 0..256. From any height that
#: showed the sea ending in a hard straight line with sky beyond it, which is
#: the one thing a horizon must never do. A neighbouring region's terrain draws
#: over this anyway, so extending it costs nothing but two triangles.
#:
#: Tied to the camera's far plane, since nothing past that is drawn.
VOID_WATER_EXTENT_M: float = 1024.0


def _flat_water_quads(
    water_height: float,
) -> tuple[tuple[float, float, float, float, float], ...]:
    """The whole sea as one rectangle, which is what almost every frame is."""
    low = -VOID_WATER_EXTENT_M
    high = REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M
    return ((low, low, high, high, water_height),)


def _water_vertices(water_height: float) -> tuple[float, ...]:
    return _water_mesh(_flat_water_quads(water_height))[0]


def _water_quads(scene: Scene) -> tuple[tuple[float, float, float, float, float], ...]:
    """The sea, as (x0, y0, x1, y1, height) rectangles in this region's frame.

    One rectangle, until a region next door announces a sea of its own at a
    different level. Water height is per region, not per grid, and the plane
    is 2304 m across: a neighbour whose sea sits a metre below ours gets our
    water drawn a metre up its beach, which is enough to make an island next
    door look sunk. So the plane is cut along the region edges that disagree
    and each piece is drawn at the height its own region asked for.

    Only regions whose ground is being drawn count -- `neighbour_terrain` is
    already filtered to those. Over the void there is nothing for a step in
    the sea to be a step *against*, and a lone rectangle of slightly lower
    water in open ocean is a worse picture than the seam it would fix.
    """
    base = float(getattr(scene, "water_height", WATER_LEVEL_M))
    low = -VOID_WATER_EXTENT_M
    high = REGION_GROUND_SIZE_M + VOID_WATER_EXTENT_M
    whole = _flat_water_quads(base)
    if not getattr(scene, "render_neighbours", False):
        return whole
    footprints: list[tuple[float, float, float, float, float]] = []
    for terrain in getattr(scene, "neighbour_terrain", ()):
        height = getattr(terrain, "water_height", None)
        if height is None or abs(float(height) - base) < 0.001:
            continue
        offset_x, offset_y = terrain.offset
        x0 = max(low, float(offset_x))
        y0 = max(low, float(offset_y))
        x1 = min(high, float(offset_x) + REGION_GROUND_SIZE_M)
        y1 = min(high, float(offset_y) + REGION_GROUND_SIZE_M)
        if x1 <= x0 or y1 <= y0:
            continue
        footprints.append((x0, y0, x1, y1, float(height)))
    if not footprints:
        return whole
    # Every edge that matters, in both axes, and then one rectangle per cell
    # of the grid they cut. The cells tile the plane exactly and share their
    # corner coordinates, so there is no overlap to fight over the depth
    # buffer and no gap between two pieces at the same height.
    xs = sorted({low, high, *(f[0] for f in footprints), *(f[2] for f in footprints)})
    ys = sorted({low, high, *(f[1] for f in footprints), *(f[3] for f in footprints)})
    quads: list[tuple[float, float, float, float, float]] = []
    for x0, x1 in zip(xs, xs[1:], strict=False):
        for y0, y1 in zip(ys, ys[1:], strict=False):
            middle_x = (x0 + x1) / 2.0
            middle_y = (y0 + y1) / 2.0
            height = base
            for f_x0, f_y0, f_x1, f_y1, f_height in footprints:
                if f_x0 < middle_x < f_x1 and f_y0 < middle_y < f_y1:
                    height = f_height
                    break
            quads.append((x0, y0, x1, y1, height))
    return tuple(quads)


#: Position (3) then the face's own normal (3). See `_water_mesh`.
FLOATS_PER_WATER_VERTEX = 6

#: Corners of one wall closing the step between two rectangles.
_Corners = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]

#: A wall, and which way it faces. The normal is horizontal -- a wall is the
#: side of a step in the sea -- and the shader needs it because every wave
#: term is a function of world x and y, which on a vertical face varies along
#: one axis and draws as stripes.
_Wall = tuple[_Corners, tuple[float, float, float]]


def _water_walls(
    quads: Sequence[tuple[float, float, float, float, float]],
) -> list[_Wall]:
    """The walls that close the steps between rectangles at different levels.

    Two pieces of sea meeting along an edge at different heights leave a slot
    between them, and a slot in the sea shows the *sky* through it -- which
    is a far worse picture than the seam the levels were cut apart to fix.
    So each such edge gets a wall of water, from the lower level up to the
    higher, the way a terrain skirt closes the same kind of gap.

    Nothing culls faces in this renderer, so a wall is visible from either
    side and its winding does not matter.
    """
    walls: list[_Wall] = []
    for lower in quads:
        for upper in quads:
            if upper[4] <= lower[4]:
                continue
            low, high = lower[4], upper[4]
            # Along x: the two share a vertical edge, and the wall runs the
            # length of the y they have in common.
            if lower[0] == upper[2] or lower[2] == upper[0]:
                edge = lower[0] if lower[0] == upper[2] else lower[2]
                y0 = max(lower[1], upper[1])
                y1 = min(lower[3], upper[3])
                if y1 > y0:
                    # Facing the lower piece: the wall is the side of the
                    # higher sea, so it faces away from it. The shader turns
                    # it toward the eye anyway, since nothing culls faces
                    # here and a wall is seen from both sides -- but a
                    # direction that means something is better than a sign
                    # picked at random, and this is the one a cull would want.
                    # `lower` is the low piece: `lower[0] == upper[2]` means
                    # the high one ends where this begins, so it is at the
                    # smaller x and the wall faces +x.
                    away = 1.0 if lower[0] == upper[2] else -1.0
                    walls.append(
                        (
                            (
                                (edge, y0, low),
                                (edge, y1, low),
                                (edge, y1, high),
                                (edge, y0, high),
                            ),
                            (away, 0.0, 0.0),
                        )
                    )
            # And along y, the same the other way round.
            if lower[1] == upper[3] or lower[3] == upper[1]:
                edge = lower[1] if lower[1] == upper[3] else lower[3]
                x0 = max(lower[0], upper[0])
                x1 = min(lower[2], upper[2])
                if x1 > x0:
                    away = 1.0 if lower[1] == upper[3] else -1.0
                    walls.append(
                        (
                            (
                                (x0, edge, low),
                                (x1, edge, low),
                                (x1, edge, high),
                                (x0, edge, high),
                            ),
                            (0.0, away, 0.0),
                        )
                    )
    return walls


def _water_mesh(
    quads: Sequence[tuple[float, float, float, float, float]],
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Those rectangles, and the walls between them, as vertex and index blobs.

    Six floats a vertex: where it is, then which way its face points before
    any wave leans it. The second three exist for the walls -- the sea itself
    is (0, 0, 1) everywhere -- and they are what stop a wall being drawn in
    stripes, since every wave term in the shader is a function of world x and
    y and a vertical face varies along only one of them.

    Corners are not shared between faces even where they coincide: two pieces
    at different heights must not share one, and the saving on the handful
    that could is not worth a second pass to find them.

    The flat rectangles wind counter-clockwise seen from above, which is what
    the single-rectangle plane always did.
    """
    vertices: list[float] = []
    indices: list[int] = []

    def add(corners: _Corners, face: tuple[float, float, float]) -> None:
        first = len(vertices) // FLOATS_PER_WATER_VERTEX
        for corner in corners:
            vertices.extend(corner)
            vertices.extend(face)
        indices.extend((first, first + 1, first + 2, first, first + 2, first + 3))

    for x0, y0, x1, y1, height in quads:
        add(
            (
                (x0, y0, height),
                (x1, y0, height),
                (x1, y1, height),
                (x0, y1, height),
            ),
            (0.0, 0.0, 1.0),
        )
    for corners, face in _water_walls(quads):
        add(corners, face)
    return tuple(vertices), tuple(indices)


def _underwater_uniforms(
    scene: Scene, eye_position: tuple[float, float, float]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The fog every pass is drawn through when the viewer is under the sea.

    Returns the sea's colour and (reach, surface height, eye height). A reach
    of **zero** is the signal that the viewer is in air, which is what every
    shader tests -- so this is the one place that decides, rather than six.

    Tied to the water actually being drawn, not only to the height. Turning
    the sea off is what a person does to look at what is under it, and a
    viewer that hid the seabed in fog after being asked to take the water away
    would be answering a different question.

    Always *this* region's sea level, even where `_water_quads` has drawn a
    neighbour's at its own: the fog is one uniform for the whole frame, and
    the camera is over this region in every case that matters.
    """
    fog = tuple(float(c) for c in getattr(scene, "water_fog", DEFAULT_WATER_FOG))
    surface = float(getattr(scene, "water_height", WATER_LEVEL_M))
    eye_height = float(eye_position[2])
    if eye_height >= surface or not getattr(scene, "render_water", True):
        return fog, (0.0, surface, eye_height)
    reach = float(getattr(scene, "water_reach", DEFAULT_UNDERWATER_REACH))
    # Never zero: zero is the flag for being in air, and a region that asks
    # for no visibility at all would otherwise turn the water off entirely.
    return fog, (max(reach, 0.01), surface, eye_height)


def _light_uniforms(
    scene: Scene, level: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """This frame's two light colours, strength and daylight already in them.

    `AMBIENT_LIGHT` and `DIFFUSE_LIGHT` are how strong each term is and have
    not changed; `level` is how far through the region's day it is; the hues
    come from the day cycle. Multiplied together here so the four passes that
    light something all say the same thing without each doing the arithmetic.
    """
    ambient = getattr(scene, "ambient_light_color", None) or (1.0, 1.0, 1.0)
    diffuse = getattr(scene, "diffuse_light_color", None) or (1.0, 1.0, 1.0)
    # `cloud_shadow` takes only the direct light: cloud over a landscape dims
    # the sun and leaves the sky lighting everything, which is why an overcast
    # day has soft shadows rather than dark ones. Taking it off the ambient as
    # well would make a cloudy noon read as dusk. Already in range when it gets
    # here -- `cloud_shadow_scale` clamps the document's number on the way in,
    # which is the one place that has to.
    shade = float(getattr(scene, "cloud_shadow", 1.0))
    return (
        tuple(AMBIENT_LIGHT * level * component for component in ambient),
        tuple(DIFFUSE_LIGHT * level * shade * component for component in diffuse),
    )


def lighting_direction(scene: Scene) -> tuple[float, float, float]:
    """Return a normalized world-space light direction for the scene.

    Four sources, in order of how much they know:

    1. The simulator's own `SunDirection`. Best if it ever arrives -- but
       OpenSim sends `(0, 0, 0)`, every message, so in practice it never does.
    2. The region's day cycle, whose every sky keyframe carries a
       `sun_rotation`. This is what actually moves the sun.
    3. `SunPhase`, kept for synthetic and debug scenes that have no region.
    4. A fixed direction, which is what every session got before the day cycle
       was read: the sun sat still for the whole run.
    """
    for candidate in (
        getattr(scene, "sun_direction", None),
        getattr(scene, "environment_sun_direction", None),
        _sun_direction_from_phase(getattr(scene, "sun_phase", None)),
    ):
        if _is_a_direction(candidate):
            return _normalize_vec3(candidate, fallback=DEFAULT_SUN_DIRECTION)
    return _normalize_vec3(DEFAULT_SUN_DIRECTION, fallback=DEFAULT_SUN_DIRECTION)


def _a_direction_or(value, fallback: tuple[float, float, float]):
    """A usable direction, or the fallback. Same zero-vector trap as the sun."""
    if _is_a_direction(value):
        return _normalize_vec3(value, fallback=fallback)
    return fallback


def _is_a_direction(value) -> bool:
    """True for a vector that points somewhere.

    The zero vector is the case that matters: it is what the simulator sends,
    and it is not a missing answer that `None` would signal -- it arrives as a
    perfectly well-formed direction of length nothing.
    """
    try:
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError, IndexError):
        return False
    length = math.sqrt((x * x) + (y * y) + (z * z))
    return math.isfinite(length) and length > 0.000001


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


def _load_sculpt_mesh_from_path(
    path: Path, sculpt_type: int | None
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    import pygame

    surface = pygame.image.load(str(path))
    width, height = surface.get_size()
    pixels = pygame.image.tobytes(surface, "RGB")
    mesh = sculpt_mesh_from_rgb(
        pixels,
        width=width,
        height=height,
        sculpt_type=int(sculpt_type or 1),
    )
    return mesh.vertices, mesh.indices


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


def _ray_hits_entity(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    entity: SceneEntity,
) -> float | None:
    """How far along `direction` the ray first meets this prim's box, or None.

    The prim's own box: the ray is taken into the entity's frame and tested
    against a slab per axis, so a rotated prim is met where it actually is
    rather than where its axis-aligned bounds would be.

    A ray starting *inside* the box is not a hit. Both callers want that --
    a click inside a prim you are standing in should reach what is beyond it,
    and a camera already inside something has nothing to be pulled in front
    of -- and it falls out of `tmin` starting at zero.
    """
    if entity.scale is None or entity.position is None:
        return None
    local_origin = (
        origin[0] - entity.position[0],
        origin[1] - entity.position[1],
        origin[2] - entity.position[2],
    )
    rotation = entity.rotation if entity.rotation is not None else (0.0, 0.0, 0.0, 1.0)
    inverse = (-rotation[0], -rotation[1], -rotation[2], rotation[3])
    local_origin = _quat_rotate(inverse, local_origin)
    local_dir = _quat_rotate(inverse, direction)

    tmin = 0.0
    tmax = float("inf")
    for axis in range(3):
        half_extent = entity.scale[axis] / 2.0
        if abs(local_dir[axis]) < 1e-6:
            if abs(local_origin[axis]) > half_extent:
                return None
        else:
            inverse_dir = 1.0 / local_dir[axis]
            near = (-half_extent - local_origin[axis]) * inverse_dir
            far = (half_extent - local_origin[axis]) * inverse_dir
            if near > far:
                near, far = far, near
            tmin = max(tmin, near)
            tmax = min(tmax, far)
            if tmin > tmax:
                return None
    return tmin if tmin > 0.0 else None


def sight_blocked_by(scene: Scene) -> Callable[
    [tuple[float, float, float], tuple[float, float, float]], float | None
]:
    """A `SightBlocked` for this scene, answering once per pair it is asked.

    The camera asks for its eye several times a frame -- the view matrix, the
    water pass, `pick` -- and each answer walks every prim in the region, so
    the closure remembers what it has already worked out. A new one is built
    each frame, which is what clears it: a prim that moved must not be
    answered for out of last frame's memory.

    Only this region's prims, and not its avatars. The neighbours are 256 m
    away and the segment is a camera boom; an avatar is the thing being
    looked at as often as it is the thing in the way, and a camera that
    jumped in every time someone walked past would be worse than one that
    did not.
    """
    remembered: dict[tuple, float | None] = {}

    def blocked(
        target: tuple[float, float, float], eye: tuple[float, float, float]
    ) -> float | None:
        key = (target, eye)
        if key not in remembered:
            remembered[key] = _first_prim_in_the_way(scene, target, eye)
        return remembered[key]

    return blocked


def _first_prim_in_the_way(
    scene: Scene,
    target: tuple[float, float, float],
    eye: tuple[float, float, float],
) -> float | None:
    """The nearest prim standing across target -> eye, as a fraction of it.

    Walks the region once. The cheap reject in front of the slab test is what
    makes that affordable: a camera boom is metres long and a region holds
    thousands of prims, almost none of which are anywhere near it, and the box
    around the segment throws those out in a short-circuiting chain of six
    comparisons. The bound used for a prim's reach is the sum of its
    half-extents rather than the diagonal it would need a square root for --
    larger than the truth, which is the safe direction for a reject.

    Measured at 15,000 prims: 4 to 7 ms a frame depending on what else the
    machine is doing, against a scene refresh that costs 40 ms at that size.
    Nearly all of it is the walk and the reach -- indexing the position and
    unpacking it into locals measure the same, interleaved, best of twelve,
    so do not "optimise" that again. What the reach buys is correctness: a
    fixed margin is about a third faster and wrong for any prim wider than
    twice the margin, which is what a megaprim is. If this ever needs to be
    cheaper the answer is an index built where the entities already are, not
    a guess about how big a prim can be.
    """
    dx = eye[0] - target[0]
    dy = eye[1] - target[1]
    dz = eye[2] - target[2]
    span = math.sqrt(dx * dx + dy * dy + dz * dz)
    if span < 1e-6:
        return None
    direction = (dx / span, dy / span, dz / span)
    low_x, high_x = min(target[0], eye[0]), max(target[0], eye[0])
    low_y, high_y = min(target[1], eye[1]), max(target[1], eye[1])
    low_z, high_z = min(target[2], eye[2]), max(target[2], eye[2])

    nearest = span
    for entity in scene.object_entities.values():
        position = entity.position
        scale = entity.scale
        if position is None or scale is None:
            continue
        x, y, z = position
        reach = 0.5 * (abs(scale[0]) + abs(scale[1]) + abs(scale[2]))
        if (
            x < low_x - reach
            or x > high_x + reach
            or y < low_y - reach
            or y > high_y + reach
            or z < low_z - reach
            or z > high_z + reach
        ):
            continue
        distance = _ray_hits_entity(target, direction, entity)
        if distance is not None and distance < nearest:
            nearest = distance
    if nearest >= span:
        return None
    return nearest / span


@dataclass(slots=True)
class _NeighbourMesh:
    """One neighbouring region's ground on the GPU, and what it was built from.

    Two vertex arrays over one buffer: `vao` for the shaded fill and
    `texture_vao` for the four-texture splat, which is used the moment that
    region's own ground textures have arrived. Which one draws is decided per
    frame, because the textures arrive several seconds after the ground does.
    """

    vbo: object
    ibo: object
    vao: object
    texture_vao: object
    index_count: int
    revision: int
    offset: tuple[float, float]
    #: The lowest and highest sample in this region, for the fill shader's
    #: colour ramp.
    fill_band: tuple[float, float]
    #: This region's own texture blend bands, from its handshake.
    start_height: tuple[float, float, float, float]
    height_range: tuple[float, float, float, float]
    texture_paths: tuple[Path | None, ...]

    def release(self) -> None:
        for resource in (self.vao, self.texture_vao, self.ibo, self.vbo):
            if resource is not None:
                resource.release()


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


def ground_height_for(scene: Scene) -> GroundHeight | None:
    """How high the terrain is under a point, for the scene's own heightmap.

    `None` when the region has sent no terrain yet, which is also when there is
    nothing drawn for a camera to be inside of. Off the region's square the
    sampler answers `None` too: `terrain_mesh_from_heightmap` lays the samples
    over exactly that square and draws nothing beyond it, so a camera out there
    is over the void rather than in the ground, and pulling it in would be
    inventing a hill that is not on the screen.
    """
    heightmap = scene.terrain_heightmap
    if heightmap is None:
        return None
    z_scale = float(getattr(scene, "terrain_z_scale", 1.0))

    def ground(x: float, y: float) -> float | None:
        if not (0.0 <= x <= REGION_GROUND_SIZE_M and 0.0 <= y <= REGION_GROUND_SIZE_M):
            return None
        return heightmap.height_at(x, y, size_m=REGION_GROUND_SIZE_M) * z_scale

    return ground


def terrain_mesh_from_heightmap(
    samples: list[float] | tuple[float, ...],
    *,
    width: int,
    height: int,
    size_m: float = REGION_GROUND_SIZE_M,
    z_scale: float = 1.0,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[tuple[float, ...], tuple[int, ...]]:
    """Build a textured terrain grid from row-major height samples.

    `origin` moves the whole sheet in the world's x/y, which is what a
    neighbouring region needs: its samples are in *its* region coordinates,
    and the region due north sits at (0, 256) in ours. The texture
    coordinates do not move with it -- each region's ground textures tile
    across that region, starting at its own corner.
    """
    if width < 2 or height < 2:
        raise ValueError("terrain mesh needs at least a 2x2 heightmap")
    if len(samples) != width * height:
        raise ValueError(
            f"height sample count {len(samples)} does not match {width}x{height}"
        )

    origin_x, origin_y = origin
    vertices: list[float] = []
    for row in range(height):
        y = origin_y + (float(row) / float(height - 1)) * size_m
        v = 1.0 - (float(row) / float(height - 1))
        for col in range(width):
            x = origin_x + (float(col) / float(width - 1)) * size_m
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


#: How many samples a side a neighbouring region's ground is drawn with. The
#: full grid is 256 a side -- 65k vertices and 130k triangles, the same as the
#: region the avatar is standing in -- and there can be eight neighbours. At
#: 65 the spacing is 4 m, which is a sixteenth of the geometry for ground that
#: is never closer than a region away, and it keeps both edges: a grid that
#: dropped the last row would leave a seam of sky along the shared border.
NEIGHBOUR_TERRAIN_SAMPLES: int = 65


def coarse_terrain_samples(
    heightmap: object,
    *,
    count: int = NEIGHBOUR_TERRAIN_SAMPLES,
    size_m: float = REGION_GROUND_SIZE_M,
) -> tuple[float, ...]:
    """Resample a region's heightmap onto a `count` x `count` grid.

    Bilinear, through `RegionHeightmap.height_at`, and inclusive of both
    edges: sample 0 sits on the region's near corner and sample `count - 1`
    on the far one, so the sheet spans exactly `size_m` and meets its
    neighbours' edges rather than stopping a sample short of them.
    """
    if count < 2:
        raise ValueError("a terrain grid needs at least 2 samples a side")
    step = size_m / float(count - 1)
    return tuple(
        heightmap.height_at(col * step, row * step, size_m=size_m)
        for row in range(count)
        for col in range(count)
    )


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
        # Per-SL-face index buffers for the built-in multi-face prims
        # (cube, cylinder, prism), keyed by shape key then SL face index.
        self._prim_face_meshes: dict[str, dict[int, _ShapeMesh]] = {}
        # Per-material-group index buffers for decoded mesh assets, keyed by
        # shape key then prim face index. They share the parent mesh's VBO.
        self._mesh_face_meshes: dict[str, dict[int, _ShapeMesh]] = {}
        # Shape keys whose buffers carry authored TexCoord0 data. Everything
        # else falls back to position-generated coordinates in the shader.
        self._mesh_uv_shape_keys: set[str] = set()
        # A few-texel strip of skin, hair and clothing colours. The avatar
        # figure has no in-world texture to wear -- an avatar's TextureEntry
        # names body textures laid out for a mesh this tree does not have --
        # so its parts index into this instead of sharing one instance tint.
        self._avatar_palette: moderngl.Texture | None = None
        #: One index buffer per bone of the avatar figure, keyed by bone name.
        self._avatar_bone_meshes: dict[str, _ShapeMesh] = {}
        self._mesh_asset_paths: dict[UUID, Path] = {}
        self._sculpt_asset_paths: dict[tuple[UUID, int | None], Path] = {}
        self._instance_capacity = 0
        # One entity's packed instance record -- model matrix and tint -- kept
        # beside the entity it was packed from. A cube is drawn as six faces
        # and each pass wanted the same nineteen floats, so they were being
        # rebuilt six times a frame for every prim in the region; and a prim
        # that has not moved packs to exactly what it packed to last frame.
        # ``Scene`` hands back the same ``SceneEntity`` object for anything
        # unchanged, so ``is`` is what says whether this is still good.
        # Keyed by ``(region handle, local id)``, not by local id alone:
        # local ids are assigned per region, so a prim next door can carry the
        # same id as one underfoot and the cache would hand it that prim's
        # model matrix.
        self._instance_blobs: dict[tuple[int, int], tuple[SceneEntity, bytes]] = {}
        #: This frame's daylight, 1.0 until a region's day cycle says otherwise.
        self._light_level: float = 1.0
        self._ambient_light: tuple[float, float, float] = (AMBIENT_LIGHT,) * 3
        self._diffuse_light: tuple[float, float, float] = (DIFFUSE_LIGHT,) * 3
        #: The sea's colour, and (reach, surface height, eye height). A reach
        #: of zero means the viewer is in air, which is where a frame starts.
        self._water_fog: tuple[float, float, float] = DEFAULT_WATER_FOG
        self._water_depth: tuple[float, float, float] = (0.0, WATER_LEVEL_M, 0.0)
        # Ground (region floor) — separate program because the cubes are
        # flat-tinted while the ground samples a texture.
        self._ground_program = None  # type: moderngl.Program | None
        self._ground_vbo = None  # type: moderngl.Buffer | None
        self._ground_ibo = None  # type: moderngl.Buffer | None
        self._ground_vao = None  # type: moderngl.VertexArray | None
        self._ground_texture = None  # type: moderngl.Texture | None
        self._ground_texture_path: Path | None = None
        self._object_textures: dict[UUID, object] = {}
        #: The water normal map as it was last handed its own filtering. See
        #: `_water_normal_texture`; kept so the parameter is set once per
        #: upload rather than once per frame.
        self._water_normal_filtered: object | None = None
        self._object_texture_paths: dict[UUID, Path] = {}
        #: Bytes each uploaded texture is estimated to hold, and the frame it
        #: was last asked for. Together these are what the budget spends.
        self._object_texture_bytes: dict[UUID, int] = {}
        self._object_texture_used: dict[UUID, int] = {}
        #: Counts up once per `render_gl`. Only ever compared, never displayed.
        self._frame_index: int = 0
        #: Textures released for want of room since this renderer was made, and
        #: whether the last prune had to give up. Both are for the diagnostics
        #: panel and for tests: a budget nobody can see is a budget nobody
        #: notices thrashing.
        self._object_textures_evicted: int = 0
        self._object_texture_budget_exceeded: bool = False
        self._terrain_vbo = None  # type: moderngl.Buffer | None
        self._terrain_ibo = None  # type: moderngl.Buffer | None
        self._terrain_fill_program = None  # type: moderngl.Program | None
        self._terrain_fill_vao = None  # type: moderngl.VertexArray | None
        self._sky_program = None  # type: moderngl.Program | None
        self._sky_vao = None  # type: moderngl.VertexArray | None
        self._sky_vbo = None  # type: moderngl.Buffer | None
        self._sky_ibo = None  # type: moderngl.Buffer | None
        self._terrain_texture_program = None  # type: moderngl.Program | None
        self._terrain_texture_vao = None  # type: moderngl.VertexArray | None
        self._terrain_textures: list[object] = []
        self._terrain_texture_paths: tuple[Path | None, ...] = ()
        self._terrain_vao = None  # type: moderngl.VertexArray | None
        self._terrain_line_program = None  # type: moderngl.Program | None
        self._parcel_border_vbo = None  # type: moderngl.Buffer | None
        self._parcel_border_vao = None  # type: moderngl.VertexArray | None
        self._parcel_border_vertex_count = 0
        self._parcel_border_key: tuple[int, int] | None = None
        self._terrain_line_ibo = None  # type: moderngl.Buffer | None
        self._terrain_line_vao = None  # type: moderngl.VertexArray | None
        #: One entry per neighbouring region whose ground has arrived, keyed
        #: by region handle: (vbo, ibo, vao, revision, offset). Drawn with the
        #: fill program rather than the four-texture splat -- a neighbour's
        #: own ground textures are named in *its* handshake and are not
        #: fetched, so the alternative to shaded ground is not textured ground
        #: but no ground at all.
        self._neighbour_meshes: dict[int, _NeighbourMesh] = {}
        #: Ground textures for the regions next door, keyed by the four paths
        #: rather than by region: neighbours on one grid usually share a
        #: palette, and eight copies of four images is VRAM for nothing.
        self._neighbour_texture_sets: dict[tuple[Path | None, ...], list[object]] = {}
        self._terrain_line_index_count: int = 0
        self._terrain_revision: int | None = None
        self._terrain_z_scale: float = 1.0
        self._terrain_height_range: tuple[float, float] = (0.0, 1.0)
        # Water plane at SL's default sea level. Solid translucent fill
        # for v1; lighting/sun reflections move with step 8.
        self._label_program = None  # type: moderngl.Program | None
        self._label_vbo = None  # type: moderngl.Buffer | None
        self._label_ibo = None  # type: moderngl.Buffer | None
        self._label_vao = None  # type: moderngl.VertexArray | None
        # Rendered text textures keyed by the exact string. Colour is applied
        # as a shader tint, so two prims with the same words share one upload.
        self._hover_text_textures: dict[str, tuple[object, int, int]] = {}
        self._water_program = None  # type: moderngl.Program | None
        self._water_vbo = None  # type: moderngl.Buffer | None
        self._water_ibo = None  # type: moderngl.Buffer | None
        self._water_vao = None  # type: moderngl.VertexArray | None
        self._water_capacity = 0
        self._water_index_count = 0
        self._water_quads: tuple[tuple[float, float, float, float, float], ...] | None = None
        if ctx is not None:
            self._setup_gl(ctx)

    # -------------------------------------------------------------- pygame

    def update(self, dt: float, scene: Scene) -> None:
        del dt, scene

    def world_background(self) -> tuple[float, float, float, float] | None:
        """The 3D pass draws everything; the backdrop is one flat colour.

        Painting that colour onto a fullscreen surface, converting it and
        uploading it as a texture cost about 23 ms a frame at 1920x1080 -- to
        say what a GL clear says for nothing. The frame loop clears to this
        instead and skips the world surface entirely.
        """
        red, green, blue = SKY_COLOR
        return (red / 255.0, green / 255.0, blue / 255.0, 1.0)

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

    def _apply_water_fog(self) -> None:
        """Hand this frame's water fog to every pass that draws the world.

        The terrain *line* program is left out on purpose: it is a decode
        debugging overlay, and an overlay that disappears in fog is one a
        person cannot use to find out why the ground is wrong. Labels are out
        for the same reason -- a name tag is not in the world.
        """
        for program in (
            self._program,
            self._ground_program,
            self._terrain_texture_program,
            self._terrain_fill_program,
            self._sky_program,
            self._water_program,
        ):
            if program is None or "u_water_fog" not in program:
                continue
            program["u_water_fog"].value = self._water_fog
            program["u_water_depth"].value = self._water_depth

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

        # The ground this frame, handed to the camera before anything asks it
        # where it is: the camera holds itself clear of the terrain, and it
        # cannot do that until someone who has the heightmap tells it where the
        # terrain is. Once a frame, because the heightmap can change under it.
        self.camera.ground_height = ground_height_for(scene)
        # And what else is in the way, for the same reason and on the same
        # schedule. A fresh closure each frame is what stops it answering out
        # of last frame's memory for a prim that has since moved.
        self.camera.sight_blocked = sight_blocked_by(scene)
        view = self.camera.view_matrix()
        proj = self.camera.projection_matrix(aspect)
        # Where the viewer is standing, in the same terms every mode agrees
        # on. Only the water pass wants it, to know how far away the far sea
        # is, but it has to be the same eye `view_matrix` just drew from --
        # including any pull-in off the ground -- or the sea is fogged for a
        # camera that is not where the picture is taken from.
        eye_position = self.camera.eye()
        view_data = struct.pack("16f", *view)
        proj_data = struct.pack("16f", *proj)
        sun_direction = lighting_direction(scene)
        # One number for the whole frame: how far through the region's day it
        # is. Kept on the renderer rather than threaded through four call
        # signatures, since every pass that lights anything wants the same
        # value and it cannot change within a frame.
        self._light_level = max(0.0, min(1.0, float(getattr(scene, "light_level", 1.0))))
        self._ambient_light, self._diffuse_light = _light_uniforms(scene, self._light_level)
        # Whether this frame is being drawn from under the sea, and in what.
        # Set on every program up front rather than beside each draw: six
        # passes want the same two values and none of them can change within
        # a frame.
        self._water_fog, self._water_depth = _underwater_uniforms(scene, eye_position)
        self._apply_water_fog()

        self._frame_index += 1
        self._prune_object_textures(scene)
        self._prune_mesh_assets(scene)
        self._prune_instance_blobs(scene)
        self._upload_ground_texture(ctx, scene)
        self._upload_scene_mesh_assets(ctx, scene)
        self._upload_scene_sculpt_assets(ctx, scene)
        shape_groups = self._group_entities_by_shape(scene)
        avatar_entities = shape_groups.pop(_AVATAR_SHAPE_KEY, [])
        # Multi-face prims bypass the (shape, texture) grouping: their texture
        # is chosen per SL face, so grouping them by a single texture up front
        # would only split each shape into redundant passes.
        face_shape_groups: dict[str, list[SceneEntity]] = {}
        for shape_key in list(shape_groups):
            if shape_key not in self._prim_face_meshes:
                continue
            per_face: list[SceneEntity] = []
            uniform: list[SceneEntity] = []
            for entity in shape_groups.pop(shape_key):
                (per_face if _has_face_textures(entity) else uniform).append(entity)
            if per_face:
                face_shape_groups[shape_key] = per_face
            if uniform:
                shape_groups[shape_key] = uniform
        groups = self._group_entities_for_draw(scene, shape_groups=shape_groups)

        ctx.enable(ctx.DEPTH_TEST)
        try:
            if scene.render_sky:
                self._render_sky(
                    ctx,
                    view_data,
                    proj_data,
                    sun_direction=sun_direction,
                    horizon=getattr(scene, "sky_horizon_color", DEFAULT_SKY_HORIZON_COLOR),
                    zenith=getattr(scene, "sky_zenith_color", DEFAULT_SKY_ZENITH_COLOR),
                    star_level=float(getattr(scene, "star_level", 0.0) or 0.0),
                    celestial_axes=getattr(
                        scene, "celestial_axes", DEFAULT_CELESTIAL_AXES
                    ),
                    sun_disc=getattr(scene, "sun_disc", DEFAULT_SUN_DISC),
                    moon=_moon_in_sky(scene),
                    moon_texture=self._moon_texture(ctx, scene),
                    cloud=_cloud_layer(scene),
                    cloud_texture=self._cloud_texture(ctx, scene),
                )
            if scene.render_terrain:
                self._upload_terrain_mesh(ctx, scene)
            else:
                self._release_terrain_mesh()
            if scene.render_terrain and self._terrain_vao is not None:
                # The region's own ground textures first. The map tile below is
                # a 256x256 overview of the whole region -- one pixel per metre,
                # with the objects already drawn into it -- so stretching it
                # over the terrain magnified its object dots into blurry black
                # patches on the ground. It is the fallback, not the intent.
                if self._upload_terrain_textures(ctx, scene) and (
                    self._terrain_texture_vao is not None
                ):
                    self._render_terrain_textured(
                        view_data, proj_data, scene, sun_direction=sun_direction
                    )
                elif self._ground_texture is not None and self._ground_texture_path is not None:
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

            # The regions next door, after this one: same depth test, and
            # they never overlap it.
            if scene.render_terrain and scene.render_neighbours:
                self._upload_neighbour_terrain(ctx, scene)
                self._render_neighbour_terrain(
                    ctx, view_data, proj_data, sun_direction=sun_direction
                )
            else:
                self._release_neighbour_terrain()

            if scene.render_parcel_borders:
                self._upload_parcel_borders(ctx, scene)
                self._render_parcel_borders(ctx, view_data, proj_data)

            if scene.render_objects and avatar_entities:
                self._render_avatars(ctx, scene, avatar_entities, view_data, proj_data,
                                     sun_direction=sun_direction)

            if scene.render_objects and (groups or face_shape_groups):
                self._program["u_view"].write(view_data)
                self._program["u_proj"].write(proj_data)
                self._program["u_sun_dir"].value = sun_direction
                self._program["u_ambient_light"].value = self._ambient_light
                self._program["u_diffuse_light"].value = self._diffuse_light
                if "u_texture" in self._program:
                    self._program["u_texture"].value = 0
                if face_shape_groups:
                    self._program["u_use_mesh_uv"].value = False
                    for shape_key, entities in face_shape_groups.items():
                        self._render_prim_faces(ctx, scene, shape_key, entities)
                for (shape_key, texture_id), entities in groups.items():
                    self._program["u_use_mesh_uv"].value = (
                        shape_key in self._mesh_uv_shape_keys
                    )
                    if shape_key in self._mesh_face_meshes:
                        self._render_mesh_faces(ctx, scene, shape_key, entities)
                        continue
                    mesh = self._shape_meshes[shape_key]
                    texture = (
                        self._upload_object_texture(ctx, scene, texture_id)
                        if texture_id is not None
                        else None
                    )
                    if shape_key == "avatar":
                        # Unconditionally, not as a fallback: an avatar's
                        # TextureEntry names skin and clothing layers baked for
                        # the SL avatar mesh's UV layout, and this figure has a
                        # different one. Stretching a face texture over a box
                        # torso looks worse than a flat colour, which is what
                        # made the palette worth having.
                        texture = self._avatar_palette
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
                self._upload_water_mesh(ctx, _water_quads(scene))
                self._water_program["u_view"].write(view_data)
                self._water_program["u_proj"].write(proj_data)
                alpha = max(0.0, min(1.0, float(getattr(scene, "water_alpha", 0.72))))
                # The sea's own colour, not `water_tint`: that one is the same
                # colour with a fixed share of sky already mixed in, which is
                # what a plane with no Fresnel term needs and what this shader
                # would then be adding sky to twice.
                fog = getattr(scene, "water_fog", DEFAULT_WATER_FOG)
                self._water_program["u_color"].value = (*fog, alpha)
                # The sky the sea reflects and has to agree with at the
                # horizon, and where the viewer is standing -- which the
                # surface needs in full, not just on the ground plane, because
                # the angle it is looked at is what decides how much of that
                # sky comes back.
                self._water_program["u_horizon"].value = getattr(
                    scene, "sky_horizon_color", DEFAULT_SKY_HORIZON_COLOR
                )
                self._water_program["u_zenith"].value = getattr(
                    scene, "sky_zenith_color", DEFAULT_SKY_ZENITH_COLOR
                )
                self._water_program["u_eye"].value = (
                    float(eye_position[0]),
                    float(eye_position[1]),
                    float(eye_position[2]),
                )
                self._water_program["u_wave_dirs"].value = getattr(
                    scene, "water_waves", DEFAULT_WATER_WAVES
                )
                ripple = getattr(scene, "water_ripple", DEFAULT_WATER_RIPPLE)
                if self._water_depth[0] > 0.0:
                    # `scale_below`, which the document gives separately and
                    # larger: from underneath the surface is a lens, and the
                    # same swell bends the view much further.
                    ripple = (
                        ripple[0],
                        ripple[1],
                        float(
                            getattr(
                                scene, "water_ripple_below", DEFAULT_WATER_RIPPLE_BELOW
                            )
                        ),
                    )
                self._water_program["u_ripple"].value = ripple
                surface = self._water_normal_texture(ctx, scene)
                self._water_program["u_water_normals"].value = _WATER_NORMAL_UNIT
                self._water_program["u_water_mapped"].value = (
                    1.0 if surface is not None else 0.0
                )
                if surface is not None:
                    surface.use(location=_WATER_NORMAL_UNIT)
                self._water_program["u_wave_phase"].value = getattr(
                    scene, "water_phase", (0.0, 0.0)
                )
                self._water_program["u_fresnel"].value = getattr(
                    scene, "water_fresnel", DEFAULT_WATER_FRESNEL
                )
                # The same two the sky pass is handed, off the same scene:
                # what the sea reflects has to be the sky that is drawn.
                self._water_program["u_sun_dir"].value = sun_direction
                self._water_program["u_sun_disc"].value = getattr(
                    scene, "sun_disc", DEFAULT_SUN_DISC
                )
                # And the same moon and the same cloud layer, for the same
                # reason. Bound here rather than left over from the sky pass:
                # that pass may not have run at all, and a sampler pointing at
                # whatever units 4 and 5 held last would put the terrain in
                # the sea.
                _bind_moon_in_sky(
                    self._water_program,
                    _moon_in_sky(scene),
                    self._moon_texture(ctx, scene),
                )
                _bind_cloud_layer(
                    self._water_program,
                    _cloud_layer(scene),
                    self._cloud_texture(ctx, scene),
                )
                ctx.enable(ctx.BLEND)
                ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
                try:
                    self._water_vao.render(vertices=self._water_index_count)
                finally:
                    ctx.disable(ctx.BLEND)

            # Last, so labels blend over finished geometry rather than being
            # blended into by the water pass behind them.
            self._render_labels(ctx, scene, view_data, proj_data)
        finally:
            # After the draw, not before it: the prune runs at the top of the
            # frame, so publishing there would report the state the frame
            # started in and never count anything this frame uploaded. On the
            # first frame of a region that reads as zero megabytes.
            self._publish_texture_vram(scene)
            # Leave the depth state predictable for the HUD overlay
            # quad and the next frame's compositor draws.
            ctx.disable(ctx.DEPTH_TEST)

    def pick(self, x: int, y: int, scene: Scene, *, aspect: float) -> int | None:
        """The local id under the cursor, or None.

        Walks ``scene.object_entities`` and so never returns a prim from the
        region next door, which is deliberate: the id it would return is only
        meaningful on that region's circuit, and every selection this client
        sends goes out on the root one -- it would select whichever prim
        happens to hold that id underfoot.
        """
        if aspect <= 0.0:
            return None

        # Unproject screen to ray
        camera = self.camera
        eye = camera.eye()

        # Reconstruct camera frame
        from vibestorm.viewer3d.camera import (
            DEFAULT_FOV_Y_RADIANS,
            DEFAULT_UP,
            _cross,
            _normalize,
            _sub,
        )
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

        best_id = None
        best_dist = float("inf")
        for entity in scene.object_entities.values():
            distance = _ray_hits_entity(eye, ray_dir, entity)
            if distance is not None and distance < best_dist:
                best_dist = distance
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
        self._mesh_asset_paths.clear()
        self._sculpt_asset_paths.clear()
        for texture, _width, _height in self._hover_text_textures.values():
            texture.release()
        self._hover_text_textures.clear()
        for face_meshes in self._prim_face_meshes.values():
            for mesh in face_meshes.values():
                mesh.vao.release()
                mesh.ibo.release()
                mesh.vbo.release()
        self._prim_face_meshes.clear()
        for shape_key in list(self._mesh_face_meshes):
            self._release_mesh_face_meshes(shape_key)
        self._mesh_uv_shape_keys.clear()
        if self._avatar_palette is not None:
            self._avatar_palette.release()
            self._avatar_palette = None
        for mesh in self._avatar_bone_meshes.values():
            mesh.vao.release()
            mesh.ibo.release()
            mesh.vbo.release()
        self._avatar_bone_meshes.clear()
        for texture in self._object_textures.values():
            texture.release()
        self._object_textures.clear()
        self._object_texture_paths.clear()
        self._object_texture_bytes.clear()
        self._object_texture_used.clear()
        self._water_normal_filtered = None
        self._release_terrain_textures()
        self._release_neighbour_terrain()
        for resource in (self._sky_vao, self._sky_ibo, self._sky_vbo):
            if resource is not None:
                resource.release()
        self._sky_vao = None
        self._sky_ibo = None
        self._sky_vbo = None
        self._release_parcel_borders()
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
            self._label_vao,
            self._label_ibo,
            self._label_vbo,
            self._label_program,
        ):
            if resource is not None:
                resource.release()
        self._instance_vbo = None
        self._program = None
        self._prim_face_meshes = {}
        self._mesh_face_meshes = {}
        self._hover_text_textures = {}
        self._label_program = None
        self._label_vao = None
        self._label_vbo = None
        self._label_ibo = None
        self._mesh_uv_shape_keys = set()
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
        self._water_capacity = 0
        self._water_index_count = 0
        self._water_quads = None

    # -------------------------------------------------------------- helpers

    def _setup_gl(self, ctx: moderngl.Context) -> None:
        import moderngl

        from vibestorm.viewer3d import avatar_mesh, meshes

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

        palette_size, palette_data = avatar_mesh.palette_texture()
        self._avatar_palette = ctx.texture(palette_size, 3, palette_data)
        # Nearest, and never mipmapped: the strip is one texel per body
        # region, so any filtering at all blends a shirt into a hand.
        self._avatar_palette.filter = (moderngl.NEAREST, moderngl.NEAREST)

        for bone_name, bone_mesh in avatar_mesh.avatar_bone_meshes().items():
            packed = _interleave_vertex_attributes(
                bone_mesh.vertices, bone_mesh.normals, bone_mesh.uvs
            )
            vbo = ctx.buffer(struct.pack(f"{len(packed)}f", *packed))
            ibo = ctx.buffer(struct.pack(f"{len(bone_mesh.indices)}I", *bone_mesh.indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._avatar_bone_meshes[bone_name] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(bone_mesh.indices)
            )

        shape_authors = {
            "cube": meshes.cube_mesh,
            "sphere": meshes.sphere_mesh,
            "cylinder": meshes.cylinder_mesh,
            "torus": meshes.torus_mesh,
            "tube": meshes.tube_mesh,
            "ring": meshes.ring_mesh,
            "prism": meshes.prism_mesh,
        }
        for shape_key, author in shape_authors.items():
            verts, indices = author()
            shape_uvs = meshes.shape_uvs(shape_key)
            if shape_uvs is not None:
                self._mesh_uv_shape_keys.add(shape_key)
            packed = _interleave_vertex_attributes(
                verts, meshes.shape_normals(shape_key), shape_uvs
            )
            vbo = ctx.buffer(struct.pack(f"{len(packed)}f", *packed))
            ibo = ctx.buffer(struct.pack(f"{len(indices)}I", *indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(indices)
            )
        # Multi-face prims get one VAO per SL face so a TextureEntry override
        # can be honoured per face. Single-face prims (sphere, torus) and the
        # avatar placeholder stay on the whole-mesh path.
        for shape_key, author in shape_authors.items():
            face_map = meshes.shape_face_indices(shape_key)
            if face_map is None:
                continue
            shape_vertices, _ = author()
            packed_shape = _interleave_vertex_attributes(
                shape_vertices, meshes.shape_normals(shape_key)
            )
            face_meshes: dict[int, _ShapeMesh] = {}
            for face_index, face_indices in face_map.items():
                vbo = ctx.buffer(struct.pack(f"{len(packed_shape)}f", *packed_shape))
                ibo = ctx.buffer(struct.pack(f"{len(face_indices)}I", *face_indices))
                vao = ctx.vertex_array(
                    self._program,
                    [
                        (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                        (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                    ],
                    index_buffer=ibo,
                    index_element_size=4,
                )
                face_meshes[face_index] = _ShapeMesh(
                    vbo=vbo, ibo=ibo, vao=vao, index_count=len(face_indices)
                )
            self._prim_face_meshes[shape_key] = face_meshes

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
        self._terrain_texture_program = ctx.program(
            vertex_shader=_TERRAIN_TEXTURE_VERTEX_SHADER,
            fragment_shader=_TERRAIN_TEXTURE_FRAGMENT_SHADER,
        )
        for index in range(4):
            name = f"u_tex{index}"
            if name in self._terrain_texture_program:
                self._terrain_texture_program[name].value = index

        self._sky_program = ctx.program(
            vertex_shader=_SKY_VERTEX_SHADER,
            fragment_shader=_SKY_FRAGMENT_SHADER,
        )
        self._sky_vbo = ctx.buffer(struct.pack(f"{len(_SKY_VERTICES)}f", *_SKY_VERTICES))
        self._sky_ibo = ctx.buffer(struct.pack(f"{len(_SKY_INDICES)}I", *_SKY_INDICES))
        self._sky_vao = ctx.vertex_array(
            self._sky_program,
            [(self._sky_vbo, "2f", "in_ndc")],
            index_buffer=self._sky_ibo,
            index_element_size=4,
        )

        self._water_program = ctx.program(
            vertex_shader=_WATER_VERTEX_SHADER,
            fragment_shader=_WATER_FRAGMENT_SHADER,
        )
        # Built from the same two functions the frame uses, and recorded as
        # what the buffers hold, so the first frame at the default sea level
        # is not an upload of what is already there. Writing the vertices one
        # way and the record another is how those two drift apart.
        quads = _flat_water_quads(WATER_LEVEL_M)
        vertices, indices = _water_mesh(quads)
        self._water_capacity = len(indices) // 6
        self._water_vbo = ctx.buffer(
            struct.pack(f"{len(vertices)}f", *vertices), dynamic=True
        )
        self._water_ibo = ctx.buffer(
            struct.pack(f"{len(indices)}I", *indices), dynamic=True
        )
        self._water_vao = ctx.vertex_array(
            self._water_program,
            [(self._water_vbo, "3f 3f", "in_pos", "in_face")],
            index_buffer=self._water_ibo,
            index_element_size=4,
        )
        self._water_index_count = len(indices)
        self._water_quads = quads

        self._label_program = ctx.program(
            vertex_shader=_LABEL_VERTEX_SHADER,
            fragment_shader=_LABEL_FRAGMENT_SHADER,
        )
        self._label_vbo = ctx.buffer(struct.pack(f"{len(_LABEL_QUAD)}f", *_LABEL_QUAD))
        self._label_ibo = ctx.buffer(
            struct.pack(f"{len(_LABEL_INDICES)}I", *_LABEL_INDICES)
        )
        self._label_vao = ctx.vertex_array(
            self._label_program,
            [(self._label_vbo, "2f 2f", "in_corner", "in_uv")],
            index_buffer=self._label_ibo,
            index_element_size=4,
        )

    def _prune_object_textures(self, scene: Scene) -> None:
        """Release uploaded textures: first what the region dropped, then, if
        that is not enough, the oldest of what is left.

        The reference pass is the important one and comes first.
        `scene.texture_paths` is exactly the set the draw loop is able to ask
        for, and it is cleared on a region change, so this is what frees the
        previous region.

        It is not a *bound*, though, which is what this used to assume. A
        region may reference as much as it likes, and the uploaded set grows
        with every texture the camera has ever passed over rather than with
        what is on screen -- so a long session in a busy region climbs without
        limit. `OBJECT_TEXTURE_BUDGET_BYTES` is the ceiling.

        The old objection to a cap was right and is answered rather than
        ignored: uploads happen inside the per-frame draw loop, so evicting
        something still on screen means re-decoding and re-uploading it every
        frame, forever, which is far worse than the memory it saves. **Nothing
        drawn in the previous frame is eligible.** A frame's visible set barely
        changes from one frame to the next, so what that protects is, to within
        a frame, exactly what is about to be asked for again.

        If the previous frame's own set is over budget there is nothing safe to
        release and the prune stops rather than thrashing. That case is
        recorded: it means `MAX_OBJECT_TEXTURE_EDGE` is too generous for the
        region, not that the eviction failed.
        """
        live = getattr(scene, "texture_paths", {})
        for texture_id in [tid for tid in self._object_textures if tid not in live]:
            self._release_object_texture(texture_id)

        total = sum(self._object_texture_bytes.values())
        self._object_texture_budget_exceeded = False
        if total <= OBJECT_TEXTURE_BUDGET_BYTES:
            return

        # Oldest first, and never the frame just drawn.
        drawn_recently = self._frame_index - 1
        evictable = sorted(
            (
                (used, texture_id)
                for texture_id, used in self._object_texture_used.items()
                if used < drawn_recently and texture_id in self._object_textures
            ),
        )
        for _used, texture_id in evictable:
            if total <= OBJECT_TEXTURE_BUDGET_BYTES:
                return
            total -= self._object_texture_bytes.get(texture_id, 0)
            self._release_object_texture(texture_id)
            self._object_textures_evicted += 1
        self._object_texture_budget_exceeded = total > OBJECT_TEXTURE_BUDGET_BYTES

    def _publish_texture_vram(self, scene: Scene) -> None:
        """Put the budget on the diagnostics panel.

        The panel is built from the `Scene` and from nothing else, so this is
        the seam. `OVER BUDGET` is the line that matters: it means the visible
        set alone will not fit, which no amount of eviction fixes and which is
        otherwise invisible -- the viewer just gets slower.
        """
        held = sum(self._object_texture_bytes.values())
        summary = (
            f"texture vram: {held / (1024 * 1024):.1f} MB of "
            f"{OBJECT_TEXTURE_BUDGET_BYTES / (1024 * 1024):.0f} MB"
            f" evicted={self._object_textures_evicted}"
        )
        if self._object_texture_budget_exceeded:
            summary += " OVER BUDGET"
        scene.texture_vram_summary = summary

    def _release_object_texture(self, texture_id: UUID) -> None:
        texture = self._object_textures.pop(texture_id, None)
        if texture is not None:
            if texture is self._water_normal_filtered:
                # Forgotten rather than left dangling: this is compared with
                # `is`, and a released object's identity can be handed to the
                # next texture allocated, which would then never get its own
                # filtering.
                self._water_normal_filtered = None
            texture.release()
        self._object_texture_paths.pop(texture_id, None)
        self._object_texture_bytes.pop(texture_id, None)
        self._object_texture_used.pop(texture_id, None)

    def _prune_label_textures(self, active_texts: set[str]) -> None:
        """Release label textures not drawn this frame.

        Keyed by the text itself, so a prim whose hover text changes — a clock,
        a visitor counter, a vendor price — mints a new texture per distinct
        string, and without pruning those accumulate for the whole session.

        The live set is the labels this frame actually drew, so as with the
        textures above nothing currently visible is ever released. A label that
        scrolls out of view and back is re-rasterised, which is cheap.
        """
        for text in [t for t in self._hover_text_textures if t not in active_texts]:
            texture, _width, _height = self._hover_text_textures.pop(text)
            texture.release()

    def _prune_mesh_assets(self, scene: Scene) -> None:
        """Release mesh-asset geometry the current region no longer references.

        Keyed by asset UUID, so this grows per distinct mesh ever seen, and a
        decoded mesh's vertex and index buffers are larger than a texture.
        Only asset-derived shape keys are considered — the built-in prim meshes
        live in the same dict and must never be released.
        """
        live = getattr(scene, "mesh_paths", {})
        for mesh_id in [mid for mid in self._mesh_asset_paths if mid not in live]:
            shape_key = _mesh_asset_shape_key(mesh_id)
            mesh = self._shape_meshes.pop(shape_key, None)
            if mesh is not None:
                mesh.vao.release()
                mesh.ibo.release()
                mesh.vbo.release()
            self._release_mesh_face_meshes(shape_key)
            self._mesh_uv_shape_keys.discard(shape_key)
            self._mesh_asset_paths.pop(mesh_id, None)

    def _hover_text_texture(
        self, ctx: moderngl.Context, text: str
    ) -> tuple[object, int, int] | None:
        """Rasterise ``text`` to a cached white-on-transparent GL texture.

        White so the shader can tint it with the prim's own hover colour;
        rendering the colour into the bitmap would need one upload per
        (text, colour) pair for no gain.
        """
        cached = self._hover_text_textures.get(text)
        if cached is not None:
            return cached

        import pygame

        if not pygame.font.get_init():
            pygame.font.init()
        font = pygame.font.Font(None, HOVER_TEXT_FONT_SIZE)
        lines = text.split("\n")[:HOVER_TEXT_MAX_LINES]
        rendered = [font.render(line, True, (255, 255, 255)) for line in lines]
        width = max((surface.get_width() for surface in rendered), default=0)
        line_height = font.get_linesize()
        height = line_height * len(rendered)
        if width <= 0 or height <= 0:
            return None

        canvas = pygame.Surface((width, height), pygame.SRCALPHA)
        canvas.fill((0, 0, 0, 0))
        for index, surface in enumerate(rendered):
            canvas.blit(surface, ((width - surface.get_width()) // 2, index * line_height))

        texture = ctx.texture(
            (width, height), components=4, data=pygame.image.tobytes(canvas, "RGBA")
        )
        texture.filter = (ctx.LINEAR, ctx.LINEAR)
        # Clamp: the quad samples the full [0,1] range, and repeating would
        # smear the first column of glyphs onto the last at the seam.
        texture.repeat_x = False
        texture.repeat_y = False
        entry = (texture, width, height)
        self._hover_text_textures[text] = entry
        return entry

    @staticmethod
    def _collect_labels(scene: Scene) -> list[tuple[SceneEntity, str, tuple[int, int, int, int]]]:
        """Gather the world-space text this frame should draw.

        Two sources share one billboard pass: prim hover text, which carries
        its own colour, and avatar name tags, which do not and get
        ``AVATAR_NAME_COLOR``. Both are opt-out via a scene flag.

        The regions next door are left out. Their nearest prim is 256 m away
        and their far one over 700 m; text that reads as a label up close is
        a smear of pixels at that range, and every one of them is drawn.
        """
        labels: list[tuple[SceneEntity, str, tuple[int, int, int, int]]] = []
        if getattr(scene, "render_hover_text", True):
            for entity in scene.object_entities.values():
                text = getattr(entity, "hover_text", None)
                if text:
                    color = getattr(entity, "hover_text_color", None)
                    labels.append((entity, text, color or (255, 255, 255, 255)))
        if getattr(scene, "render_avatar_names", True):
            for entity in scene.avatar_entities.values():
                name = getattr(entity, "name", None)
                if name:
                    labels.append((entity, name, AVATAR_NAME_COLOR))
        return labels

    def _render_labels(
        self,
        ctx: moderngl.Context,
        scene: Scene,
        view_data: bytes,
        proj_data: bytes,
    ) -> None:
        """Draw prim hover text and avatar name tags as camera-facing billboards.

        Depth testing stays on, so text behind a wall is hidden rather than
        floating through it; depth *writes* are off so two labels that overlap
        blend instead of punching holes in each other.
        """
        if self._label_program is None or self._label_vao is None:
            return
        labelled = self._collect_labels(scene)
        # Prune before the early return: a frame with no labels at all is
        # precisely when every cached label texture has become garbage.
        self._prune_label_textures({text for _entity, text, _color in labelled})
        if not labelled:
            return

        self._label_program["u_view"].write(view_data)
        self._label_program["u_proj"].write(proj_data)
        if "u_texture" in self._label_program:
            self._label_program["u_texture"].value = 0
        ctx.enable(ctx.BLEND)
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
        # Depth mask lives on the bound framebuffer, not the context, in
        # moderngl 5; ctx.fbo is whatever the caller is drawing into.
        framebuffer = ctx.fbo
        depth_mask = framebuffer.depth_mask if framebuffer is not None else True
        if framebuffer is not None:
            framebuffer.depth_mask = False
        try:
            for entity, text, color in labelled:
                r, g, b, a = color
                if a <= 0:
                    continue
                entry = self._hover_text_texture(ctx, text)
                if entry is None:
                    continue
                texture, width, height = entry
                x, y, z = entity.position
                top = z + abs(entity.scale[2]) * 0.5 + HOVER_TEXT_OFFSET_M
                half_h = HOVER_TEXT_SCREEN_HEIGHT * 0.5
                self._label_program["u_world_pos"].value = (x, y, top)
                self._label_program["u_half_size"].value = (
                    half_h * (width / height),
                    half_h,
                )
                self._label_program["u_color"].value = (
                    r / 255.0,
                    g / 255.0,
                    b / 255.0,
                    a / 255.0,
                )
                texture.use(location=0)
                self._label_vao.render()
        finally:
            if framebuffer is not None:
                framebuffer.depth_mask = depth_mask
            ctx.disable(ctx.BLEND)

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
        for entity in scene.drawable_entities():
            if (
                entity.mesh_source_kind == "mesh"
                and entity.mesh_asset_id is not None
                and _mesh_asset_shape_key(entity.mesh_asset_id) in self._shape_meshes
            ):
                shape_key = _mesh_asset_shape_key(entity.mesh_asset_id)
                groups.setdefault(shape_key, []).append(entity)
                continue
            if (
                entity.mesh_source_kind == "sculpt"
                and entity.mesh_asset_id is not None
                and _sculpt_asset_shape_key(entity.mesh_asset_id, entity.sculpt_type) in self._shape_meshes
            ):
                shape_key = _sculpt_asset_shape_key(entity.mesh_asset_id, entity.sculpt_type)
                groups.setdefault(shape_key, []).append(entity)
                continue
            raw = "avatar" if entity.kind == "avatar" else entity.shape
            shape_key = _SHAPE_ALIASES.get(raw, raw) if raw is not None else _DEFAULT_SHAPE_KEY
            if shape_key not in self._shape_meshes and shape_key != _AVATAR_SHAPE_KEY:
                shape_key = _DEFAULT_SHAPE_KEY
            groups.setdefault(shape_key, []).append(entity)
        return groups

    def _upload_scene_mesh_assets(self, ctx: moderngl.Context, scene: Scene) -> None:
        for mesh_id, path in scene.mesh_paths.items():
            shape_key = _mesh_asset_shape_key(mesh_id)
            if self._mesh_asset_paths.get(mesh_id) == path and shape_key in self._shape_meshes:
                continue
            if shape_key in self._shape_meshes:
                mesh = self._shape_meshes.pop(shape_key)
                mesh.vao.release()
                mesh.ibo.release()
                mesh.vbo.release()
            self._release_mesh_face_meshes(shape_key)
            self._mesh_uv_shape_keys.discard(shape_key)
            try:
                decoded = decode_sl_mesh_asset(path.read_bytes())
            except (OSError, SLMeshDecodeError):
                continue
            if not decoded.vertices or not decoded.indices:
                continue
            assert self._program is not None
            assert self._instance_vbo is not None
            packed = _interleave_vertex_attributes(
                decoded.vertices, decoded.normals, decoded.uvs
            )
            vbo = ctx.buffer(struct.pack(f"{len(packed)}f", *packed))
            ibo = ctx.buffer(struct.pack(f"{len(decoded.indices)}I", *decoded.indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(decoded.indices)
            )
            self._build_mesh_face_meshes(ctx, shape_key, vbo, decoded)
            if _has_usable_uvs(decoded):
                self._mesh_uv_shape_keys.add(shape_key)
            else:
                self._mesh_uv_shape_keys.discard(shape_key)
            self._mesh_asset_paths[mesh_id] = path

    def _upload_scene_sculpt_assets(self, ctx: moderngl.Context, scene: Scene) -> None:
        for entity in scene.drawable_entities():
            if entity.mesh_source_kind != "sculpt" or entity.mesh_asset_id is None:
                continue
            sculpt_id = entity.mesh_asset_id
            path = scene.texture_paths.get(sculpt_id)
            if path is None:
                continue
            cache_key = (sculpt_id, entity.sculpt_type)
            shape_key = _sculpt_asset_shape_key(sculpt_id, entity.sculpt_type)
            if self._sculpt_asset_paths.get(cache_key) == path and shape_key in self._shape_meshes:
                continue
            if shape_key in self._shape_meshes:
                mesh = self._shape_meshes.pop(shape_key)
                mesh.vao.release()
                mesh.ibo.release()
                mesh.vbo.release()
            try:
                vertices, indices = _load_sculpt_mesh_from_path(path, entity.sculpt_type)
            except (OSError, SculptDecodeError):
                continue
            assert self._program is not None
            assert self._instance_vbo is not None
            # A sculpt map carries only positions, so normals have to come
            # from the decoded geometry. The renderer's normalize(position)
            # fallback would only be right for a sculpt that happens to be a
            # sphere centred on the origin — not for the torus, plane and
            # cylinder sculpt types.
            packed = _interleave_vertex_attributes(
                vertices, smooth_vertex_normals(vertices, indices)
            )
            vbo = ctx.buffer(struct.pack(f"{len(packed)}f", *packed))
            ibo = ctx.buffer(struct.pack(f"{len(indices)}I", *indices))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(indices)
            )
            self._sculpt_asset_paths[cache_key] = path

    def _group_entities_for_draw(
        self,
        scene: Scene,
        *,
        shape_groups: dict[str, list[SceneEntity]] | None = None,
    ) -> dict[tuple[str, UUID | None], list[SceneEntity]]:
        groups: dict[tuple[str, UUID | None], list[SceneEntity]] = {}
        source_groups = shape_groups if shape_groups is not None else self._group_entities_by_shape(scene)
        for shape_key, entities in source_groups.items():
            face_index = _single_face_index(shape_key)
            for entity in entities:
                texture_id = self._texture_id_for_entity_face(scene, entity, face_index)
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

    def _build_mesh_face_meshes(
        self,
        ctx: moderngl.Context,
        shape_key: str,
        vbo: moderngl.Buffer,
        decoded: object,
    ) -> None:
        """Split a decoded mesh's index buffer by ``material_groups``.

        SL submeshes map 1:1 to prim faces, so each group's index slice can be
        drawn with that face's ``TextureEntry`` override — the same per-face
        treatment cubes already get. The groups share the parent mesh's VBO;
        only index buffers and VAOs are per face.

        Meshes with a single group gain nothing from the split, so they keep
        the one-draw-call path.
        """
        groups = getattr(decoded, "material_groups", None) or ()
        if len(groups) < 2 or self._program is None or self._instance_vbo is None:
            return
        indices = decoded.indices
        face_meshes: dict[int, _ShapeMesh] = {}
        for group in groups:
            slice_ = indices[group.index_start : group.index_start + group.index_count]
            if not slice_:
                continue
            ibo = ctx.buffer(struct.pack(f"{len(slice_)}I", *slice_))
            vao = ctx.vertex_array(
                self._program,
                [
                    (vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=ibo,
                index_element_size=4,
            )
            face_meshes[group.face_index] = _ShapeMesh(
                vbo=vbo, ibo=ibo, vao=vao, index_count=len(slice_)
            )
        if face_meshes:
            self._mesh_face_meshes[shape_key] = face_meshes

    def _release_mesh_face_meshes(self, shape_key: str) -> None:
        """Release a mesh's per-face VAOs/IBOs. The VBO belongs to the parent."""
        for mesh in self._mesh_face_meshes.pop(shape_key, {}).values():
            mesh.vao.release()
            mesh.ibo.release()

    def _render_mesh_faces(
        self,
        ctx: moderngl.Context,
        scene: Scene,
        shape_key: str,
        entities: list[SceneEntity],
    ) -> None:
        """Draw one mesh per material group, each with its own face texture."""
        for face_index, mesh in self._mesh_face_meshes[shape_key].items():
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

    def _render_prim_faces(
        self,
        ctx: moderngl.Context,
        scene: Scene,
        shape_key: str,
        entities: list[SceneEntity],
    ) -> None:
        """Draw one instanced pass per SL face, each with that face's texture."""
        for face_index, mesh in self._prim_face_meshes[shape_key].items():
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

    def _render_avatars(
        self,
        ctx: moderngl.Context,
        scene: Scene,
        entities: list[SceneEntity],
        view_data: bytes,
        proj_data: bytes,
        *,
        sun_direction: tuple[float, float, float],
    ) -> None:
        """Draw every avatar, one instanced pass per bone.

        Nine draws for the whole region rather than nine per avatar: each pass
        renders one bone across every avatar at once, with that avatar's own
        pose folded into its instance matrix. The cost does not grow with how
        many people are standing about.

        Colour comes from the palette texture unconditionally, never from the
        instance tint and never from the avatar's own ``TextureEntry`` -- see
        ``avatar_mesh`` for why.
        """
        from vibestorm.viewer3d import avatar_mesh

        assert self._program is not None
        if not self._avatar_bone_meshes or self._avatar_palette is None:
            return

        self._program["u_view"].write(view_data)
        self._program["u_proj"].write(proj_data)
        self._program["u_sun_dir"].value = sun_direction
        self._program["u_ambient_light"].value = self._ambient_light
        self._program["u_diffuse_light"].value = self._diffuse_light
        self._program["u_use_mesh_uv"].value = True
        self._program["u_use_texture"].value = True
        if "u_texture" in self._program:
            self._program["u_texture"].value = 0
        self._avatar_palette.use(location=0)

        poses = [avatar_mesh.bone_matrices(self._pose_for(scene, entity)) for entity in entities]
        for bone_name, mesh in self._avatar_bone_meshes.items():
            floats: list[float] = []
            for entity, bones in zip(entities, poses, strict=True):
                bone = bones.get(bone_name)
                if bone is None:
                    continue
                quat = entity.rotation if entity.rotation is not None else (0.0, 0.0, 0.0, 1.0)
                model = model_matrix(entity.position, entity.scale, quat)
                floats.extend(avatar_mesh.multiply_4x4(model, bone))
                floats.extend((1.0, 1.0, 1.0))
            if not floats:
                continue
            count = len(floats) // _FLOATS_PER_INSTANCE
            if count > self._instance_capacity:
                self._grow_instance_buffer(ctx, count)
            data = struct.pack(f"{len(floats)}f", *floats)
            assert self._instance_vbo is not None
            self._instance_vbo.orphan(size=len(data))
            self._instance_vbo.write(data)
            mesh.vao.render(instances=count)

    def _pose_for(self, scene: Scene, entity: SceneEntity) -> dict[str, float]:
        """The bone pitches this avatar should be drawn with, or an empty rest pose.

        Keyed by local id, and local ids are per region -- so an avatar next
        door is given the rest pose rather than whichever pose belongs to the
        prim or avatar holding that id in the region underfoot.
        """
        poses = getattr(scene, "avatar_poses", None)
        if not poses or entity.region_handle:
            return {}
        return poses.get(entity.local_id) or {}

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

        blob = self._instance_blob
        data = b"".join([blob(entity) for entity in entities])
        assert self._instance_vbo is not None
        self._instance_vbo.orphan(size=len(data))
        self._instance_vbo.write(data)

    def _instance_blob(self, entity: SceneEntity) -> bytes:
        """This entity's model matrix and tint, packed and remembered.

        Neither depends on which face is being drawn, so packing them per face
        was six times the work for the same bytes; and neither changes while
        the prim sits still, which is what most of a region does.
        """
        cached = self._instance_blobs.get((entity.region_handle, entity.local_id))
        if cached is not None and cached[0] is entity:
            return cached[1]
        quat = entity.rotation if entity.rotation is not None else (0.0, 0.0, 0.0, 1.0)
        r, g, b = entity.tint
        packed = struct.pack(
            f"{_FLOATS_PER_INSTANCE}f",
            *model_matrix(entity.position, entity.scale, quat),
            r / 255.0,
            g / 255.0,
            b / 255.0,
        )
        self._instance_blobs[(entity.region_handle, entity.local_id)] = (entity, packed)
        return packed

    def _prune_instance_blobs(self, scene: Scene) -> None:
        """Forget packed instances for prims no longer in view.

        Keyed by region handle and local id, so an id reused by a different
        prim is caught by the identity check rather than by this; what this
        stops is a session that walks a grid holding one record per prim it
        has ever seen.
        """
        in_view = scene.drawable_entity_count()
        if len(self._instance_blobs) > 2 * in_view + 64:
            self._instance_blobs.clear()

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
            tex.repeat_x = False
            tex.repeat_y = False
            _minify_through_mipmaps(ctx, tex)
            self._ground_texture = tex
        self._ground_texture_path = path

    def _render_sky(
        self,
        ctx: moderngl.Context,
        view_data: bytes,
        proj_data: bytes,
        *,
        sun_direction,
        horizon: tuple[float, float, float] = DEFAULT_SKY_HORIZON_COLOR,
        zenith: tuple[float, float, float] = DEFAULT_SKY_ZENITH_COLOR,
        star_level: float = 0.0,
        celestial_axes: tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ] = DEFAULT_CELESTIAL_AXES,
        sun_disc: tuple[float, float] = DEFAULT_SUN_DISC,
        moon: _MoonInSky,
        moon_texture: object | None = None,
        cloud: _CloudLayer,
        cloud_texture: object | None = None,
    ) -> None:
        """Paint the sky before anything else in the frame.

        Drawn with the depth test off, which GL also takes as "do not write
        depth", so the quad covers every pixel and occludes nothing after it.
        It runs first, so the frame no longer needs a colour clear either.

        moderngl 5.12 has no `depth_mask` on the context, hence the enable and
        disable rather than the more obvious mask.
        """
        if self._sky_program is None or self._sky_vao is None:
            return
        self._sky_program["u_view"].write(view_data)
        self._sky_program["u_proj"].write(proj_data)
        self._sky_program["u_horizon"].value = horizon
        self._sky_program["u_zenith"].value = zenith
        self._sky_program["u_sun_dir"].value = sun_direction
        self._sky_program["u_sun_disc"].value = sun_disc
        self._sky_program["u_star_level"].value = float(star_level)
        self._sky_program["u_sphere_x"].value = celestial_axes[0]
        self._sky_program["u_sphere_y"].value = celestial_axes[1]
        self._sky_program["u_sphere_z"].value = celestial_axes[2]
        _bind_moon_in_sky(self._sky_program, moon, moon_texture)
        _bind_cloud_layer(self._sky_program, cloud, cloud_texture)
        ctx.disable(ctx.DEPTH_TEST)
        try:
            self._sky_vao.render()
        finally:
            ctx.enable(ctx.DEPTH_TEST)

    def _upload_terrain_textures(self, ctx: moderngl.Context, scene: Scene) -> bool:
        """Upload the region's four ground textures. True when all four are up.

        All four or none: the shader blends between adjacent bands, so a
        missing one would not leave a gap in a corner of the region -- it would
        put a black stripe across every elevation that names it. Falling back
        to the flat fill until the set is complete looks like loading; a black
        stripe looks like a bug.
        """
        paths = tuple(scene.terrain_texture_paths)
        if len(paths) != 4 or any(path is None for path in paths):
            return False
        if paths == self._terrain_texture_paths and len(self._terrain_textures) == 4:
            return True

        import pygame

        uploaded = []
        for path in paths:
            try:
                surface = pygame.image.load(str(path))
            except (pygame.error, FileNotFoundError, OSError):
                for texture in uploaded:
                    texture.release()
                return False
            texture = ctx.texture(
                surface.get_size(),
                components=4,
                data=pygame.image.tobytes(surface, "RGBA"),
            )
            # The textures tile across the region, so wrapping is the point.
            texture.repeat_x = True
            texture.repeat_y = True
            _minify_through_mipmaps(ctx, texture)
            uploaded.append(texture)

        self._release_terrain_textures()
        self._terrain_textures = uploaded
        self._terrain_texture_paths = paths
        return True

    def _release_terrain_textures(self) -> None:
        for texture in self._terrain_textures:
            texture.release()
        self._terrain_textures = []
        self._terrain_texture_paths = ()

    def _render_terrain_textured(
        self, view_data: bytes, proj_data: bytes, scene: Scene, *, sun_direction
    ) -> None:
        program = self._terrain_texture_program
        assert program is not None
        program["u_view"].write(view_data)
        program["u_proj"].write(proj_data)
        program["u_start_height"].value = tuple(scene.terrain_start_height)
        program["u_height_range"].value = tuple(scene.terrain_height_range)
        program["u_repeats"].value = TERRAIN_TEXTURE_REPEATS
        program["u_sun_dir"].value = sun_direction
        program["u_ambient_light"].value = self._ambient_light
        program["u_diffuse_light"].value = self._diffuse_light
        for index, texture in enumerate(self._terrain_textures):
            texture.use(location=index)
        assert self._terrain_texture_vao is not None
        self._terrain_texture_vao.render()

    def _moon_texture(self, ctx: moderngl.Context, scene: Scene) -> object | None:
        """The moon's face, if the day cycle named one and it has arrived.

        Goes through the object texture cache rather than a cache of its own:
        it is one texture from the same capability with the same lifetime, and
        asking for it each frame is also what keeps it off the eviction list.
        """
        texture_id = getattr(scene, "moon_texture_id", None)
        if texture_id is None:
            return None
        return self._upload_object_texture(ctx, scene, texture_id)

    def _water_normal_texture(
        self, ctx: moderngl.Context, scene: Scene
    ) -> object | None:
        """The region's water normal map, if it named one and it arrived.

        The one texture in the renderer drawn *without* anisotropic
        filtering, and it is the one surface that would seem to need it most:
        the sea is a two-kilometre plane seen almost edge on, which is the
        exact case anisotropy exists for. Two things make it the wrong answer
        here.

        It is by far the most expensive place to spend it. Every other
        grazing surface is a few metres of ground or a prim face; this one
        fills half the frame, twice over -- one sample per wave. Measured in
        one run on llvmpipe at 1280x800, the water pass alone costs 28.4 ms at
        sixteen samples against 10.4 at one, with the three octaves of sines
        it replaces at 8.2.

        And nothing is lost. A normal map on a mirror is not read for its
        detail, it is read for which way the surface leans, and a mip level
        that blurs several ripples into one draws a calmer sea rather than a
        wrong one -- which is what the far water is meant to look like
        anyway; the sines this replaces fade themselves out with distance for
        the same reason. Screenshotted at one sample and at sixteen from both
        cameras, the frames are indistinguishable.
        """
        texture_id = getattr(scene, "water_normal_id", None)
        if texture_id is None:
            return None
        texture = self._upload_object_texture(ctx, scene, texture_id)
        if texture is not None and texture is not self._water_normal_filtered:
            texture.anisotropy = 1.0
            self._water_normal_filtered = texture
        return texture

    def _cloud_texture(self, ctx: moderngl.Context, scene: Scene) -> object | None:
        """The region's cloud field, if it named one and it has arrived."""
        texture_id = getattr(scene, "cloud_texture_id", None)
        if texture_id is None or not getattr(scene, "render_clouds", True):
            return None
        return self._upload_object_texture(ctx, scene, texture_id)

    def _upload_object_texture(
        self, ctx: moderngl.Context, scene: Scene, texture_id: UUID
    ) -> object | None:
        path = scene.texture_paths.get(texture_id)
        if path is None:
            return None
        cached = self._object_textures.get(texture_id)
        if cached is not None and self._object_texture_paths.get(texture_id) == path:
            # Asking for it is what keeps it: the budget's eviction order is
            # this number, so a texture drawn every frame is never a candidate.
            self._object_texture_used[texture_id] = self._frame_index
            return cached

        import pygame

        try:
            surface = pygame.image.load(str(path))
        except (pygame.error, FileNotFoundError, OSError):
            return None

        surface = _within_texture_budget(surface)
        pixels = pygame.image.tobytes(surface, "RGBA")
        self._release_object_texture(texture_id)
        texture = ctx.texture(surface.get_size(), components=4, data=pixels)
        texture.repeat_x = True
        texture.repeat_y = True
        _minify_through_mipmaps(ctx, texture)
        self._object_textures[texture_id] = texture
        self._object_texture_paths[texture_id] = path
        self._object_texture_bytes[texture_id] = _texture_memory_bytes(surface.get_size())
        self._object_texture_used[texture_id] = self._frame_index
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
        if self._terrain_texture_program is not None:
            self._terrain_texture_vao = ctx.vertex_array(
                self._terrain_texture_program,
                [(self._terrain_vbo, "3f 2f", "in_pos", "in_uv")],
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

    def _upload_neighbour_terrain(self, ctx: moderngl.Context, scene: Scene) -> None:
        """Build or refresh a ground sheet for each region next door.

        Rebuilt only when a region's heightmap revision moves. Patches keep
        arriving for a second or two after a circuit opens, so the first few
        frames of a new neighbour do rebuild -- and then it is still.
        """
        if self._terrain_fill_program is None:
            return
        wanted = {entry.handle: entry for entry in scene.neighbour_terrain}
        for handle in list(self._neighbour_meshes):
            if handle not in wanted:
                self._neighbour_meshes.pop(handle).release()

        z_scale = float(getattr(scene, "terrain_z_scale", 1.0))
        for handle, entry in wanted.items():
            existing = self._neighbour_meshes.get(handle)
            if (
                existing is not None
                and existing.revision == entry.heightmap.revision
                and existing.offset == entry.offset
            ):
                # The ground is the same ground; only its bands and textures
                # can still be catching up, and those cost nothing to carry
                # over without rebuilding sixty-five hundred vertices.
                existing.texture_paths = tuple(entry.texture_paths)
                existing.start_height = entry.start_height
                existing.height_range = entry.height_range
                continue
            samples = coarse_terrain_samples(entry.heightmap)
            count = NEIGHBOUR_TERRAIN_SAMPLES
            vertices, indices = terrain_mesh_from_heightmap(
                samples,
                width=count,
                height=count,
                z_scale=z_scale,
                origin=entry.offset,
            )
            vbo = ctx.buffer(struct.pack(f"{len(vertices)}f", *vertices))
            ibo = ctx.buffer(struct.pack(f"{len(indices)}I", *indices))
            vao = ctx.vertex_array(
                self._terrain_fill_program,
                [(vbo, "3f 2x4", "in_pos")],
                index_buffer=ibo,
                index_element_size=4,
            )
            texture_vao = None
            if self._terrain_texture_program is not None:
                texture_vao = ctx.vertex_array(
                    self._terrain_texture_program,
                    [(vbo, "3f 2f", "in_pos", "in_uv")],
                    index_buffer=ibo,
                    index_element_size=4,
                )
            if existing is not None:
                existing.release()
            self._neighbour_meshes[handle] = _NeighbourMesh(
                vbo=vbo,
                ibo=ibo,
                vao=vao,
                texture_vao=texture_vao,
                index_count=len(indices),
                revision=entry.heightmap.revision,
                offset=entry.offset,
                fill_band=(min(samples), max(samples)),
                start_height=entry.start_height,
                height_range=entry.height_range,
                texture_paths=tuple(entry.texture_paths),
            )

    def _neighbour_texture_set(
        self, ctx: moderngl.Context, paths: tuple[Path | None, ...]
    ) -> list[object] | None:
        """The four ground textures for one region, uploaded once and shared.

        Keyed by the paths rather than by the region, because neighbouring
        regions on one grid usually share a ground palette and there is no
        reason to hold eight copies of the same four images.
        """
        if len(paths) != 4 or any(path is None for path in paths):
            return None
        cached = self._neighbour_texture_sets.get(paths)
        if cached is not None:
            return cached

        import pygame

        uploaded: list[object] = []
        for path in paths:
            try:
                surface = pygame.image.load(str(path))
            except (pygame.error, FileNotFoundError, OSError):
                for texture in uploaded:
                    texture.release()
                return None
            texture = ctx.texture(
                surface.get_size(),
                components=4,
                data=pygame.image.tobytes(surface, "RGBA"),
            )
            texture.repeat_x = True
            texture.repeat_y = True
            _minify_through_mipmaps(ctx, texture)
            uploaded.append(texture)
        self._neighbour_texture_sets[paths] = uploaded
        return uploaded

    def _render_neighbour_terrain(
        self,
        ctx: moderngl.Context,
        view_data: bytes,
        proj_data: bytes,
        *,
        sun_direction: tuple[float, float, float],
    ) -> None:
        """Draw each neighbour, textured where its ground textures arrived.

        Which shader draws a region is decided per frame and per region: the
        heightmap arrives seconds before the textures do, and on a grid where
        one neighbour's ground is cached and another's is not, the two are
        drawn differently in the same frame.
        """
        fill = self._terrain_fill_program
        textured = self._terrain_texture_program
        if fill is None or not self._neighbour_meshes:
            return
        fill_ready = False
        textured_ready = False
        for mesh in self._neighbour_meshes.values():
            textures = (
                self._neighbour_texture_set(ctx, mesh.texture_paths)
                if textured is not None and mesh.texture_vao is not None
                else None
            )
            if textures is not None:
                if not textured_ready:
                    textured["u_view"].write(view_data)
                    textured["u_proj"].write(proj_data)
                    textured["u_repeats"].value = TERRAIN_TEXTURE_REPEATS
                    textured["u_sun_dir"].value = sun_direction
                    textured["u_ambient_light"].value = self._ambient_light
                    textured["u_diffuse_light"].value = self._diffuse_light
                    textured_ready = True
                # Per region: the bands come out of that region's own
                # handshake, and a neighbour blended against ours puts its
                # sand where its grass should be.
                textured["u_start_height"].value = tuple(mesh.start_height)
                textured["u_height_range"].value = tuple(mesh.height_range)
                for index, texture in enumerate(textures):
                    texture.use(location=index)
                mesh.texture_vao.render()
                continue

            if not fill_ready:
                fill["u_view"].write(view_data)
                fill["u_proj"].write(proj_data)
                fill["u_color"].value = TERRAIN_FILL_RGBA
                fill["u_sun_dir"].value = sun_direction
                fill["u_ambient_light"].value = self._ambient_light
                fill["u_diffuse_light"].value = self._diffuse_light
                fill_ready = True
            # Each region's own height band: the fill shader ramps its colour
            # between these, and lighting one region's hills with another
            # region's range makes a flat neighbour read as a cliff.
            low, high = mesh.fill_band
            fill["u_height_min"].value = low
            fill["u_height_max"].value = high if high > low else low + 1.0
            mesh.vao.render()

    def _release_neighbour_terrain(self) -> None:
        for mesh in self._neighbour_meshes.values():
            mesh.release()
        self._neighbour_meshes.clear()
        for textures in self._neighbour_texture_sets.values():
            for texture in textures:
                texture.release()
        self._neighbour_texture_sets.clear()

    def _release_terrain_mesh(self) -> None:
        for resource in (
            self._terrain_vao,
            self._terrain_ibo,
            self._terrain_fill_vao,
            self._terrain_texture_vao,
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
        self._terrain_fill_program["u_ambient_light"].value = self._ambient_light
        self._terrain_fill_program["u_diffuse_light"].value = self._diffuse_light
        self._terrain_fill_vao.render()

    def _upload_parcel_borders(self, ctx: moderngl.Context, scene: Scene) -> None:
        """Build the parcel property-line VAO from ``scene.parcel_borders``.

        Segments are ``(x0, y0, x1, y1)`` in region meters. Each endpoint is
        lifted onto the terrain (or to the flat ground plane when no heightmap
        has arrived) plus a small offset, so lines follow the land instead of
        cutting through it. Rebuilt only when the segments or the terrain
        revision change.
        """
        if self._terrain_line_program is None:
            return
        segments = scene.parcel_borders
        heightmap = scene.terrain_heightmap
        revision = heightmap.revision if heightmap is not None else -1
        key = (len(segments), revision)
        if key == self._parcel_border_key and self._parcel_border_vao is not None:
            return
        self._release_parcel_borders()
        self._parcel_border_key = key
        if not segments:
            return

        z_scale = scene.terrain_z_scale

        def _height_at(x: float, y: float) -> float:
            if heightmap is None:
                return 0.0
            ix = min(max(int(x), 0), heightmap.width - 1)
            iy = min(max(int(y), 0), heightmap.height - 1)
            return heightmap.samples[iy * heightmap.width + ix] * z_scale

        vertices: list[float] = []
        for x0, y0, x1, y1 in segments:
            vertices.extend(
                (
                    float(x0),
                    float(y0),
                    _height_at(x0, y0) + PARCEL_BORDER_HEIGHT_OFFSET_M,
                    float(x1),
                    float(y1),
                    _height_at(x1, y1) + PARCEL_BORDER_HEIGHT_OFFSET_M,
                )
            )
        self._parcel_border_vbo = ctx.buffer(
            struct.pack(f"{len(vertices)}f", *vertices)
        )
        self._parcel_border_vao = ctx.vertex_array(
            self._terrain_line_program,
            [(self._parcel_border_vbo, "3f", "in_pos")],
        )
        self._parcel_border_vertex_count = len(vertices) // 3

    def _release_parcel_borders(self) -> None:
        for resource in (self._parcel_border_vao, self._parcel_border_vbo):
            if resource is not None:
                resource.release()
        self._parcel_border_vao = None
        self._parcel_border_vbo = None
        self._parcel_border_vertex_count = 0
        self._parcel_border_key = None

    def _render_parcel_borders(
        self, ctx: moderngl.Context, view_data: bytes, proj_data: bytes
    ) -> None:
        if (
            self._terrain_line_program is None
            or self._parcel_border_vao is None
            or self._parcel_border_vertex_count <= 0
        ):
            return
        self._terrain_line_program["u_view"].write(view_data)
        self._terrain_line_program["u_proj"].write(proj_data)
        self._terrain_line_program["u_color"].value = PARCEL_BORDER_RGBA
        ctx.enable(ctx.BLEND)
        ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
        try:
            self._parcel_border_vao.render(mode=ctx.LINES)
        finally:
            ctx.disable(ctx.BLEND)

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

    def _upload_water_mesh(
        self,
        ctx: moderngl.Context,
        quads: tuple[tuple[float, float, float, float, float], ...],
    ) -> None:
        """Put the sea's rectangles in the buffers, if they are not there now.

        Compared as a whole rather than by height: the plane is one rectangle
        in almost every frame, and the cut-up form only appears at a border
        between two regions that disagree about their sea level.
        """
        if self._water_vbo is None or self._water_ibo is None:
            return
        if self._water_quads == quads:
            return
        vertices, indices = _water_mesh(quads)
        # Faces, not rectangles: the walls between two levels are faces too.
        faces = len(indices) // 6
        if faces > self._water_capacity:
            self._grow_water_buffers(ctx, faces)
            assert self._water_vbo is not None and self._water_ibo is not None
        self._water_vbo.write(struct.pack(f"{len(vertices)}f", *vertices))
        self._water_ibo.write(struct.pack(f"{len(indices)}I", *indices))
        self._water_index_count = len(indices)
        self._water_quads = quads

    def _grow_water_buffers(self, ctx: moderngl.Context, faces: int) -> None:
        """Reallocate the sea's buffers, and the array that records them.

        Same reason as `_grow_instance_buffer`: a vertex array remembers the
        buffers it was built against, so replacing one means rebuilding it.
        """
        assert self._water_program is not None
        for buffer in (self._water_vao, self._water_ibo, self._water_vbo):
            if buffer is not None:
                buffer.release()
        self._water_capacity = max(self._water_capacity * 2, faces)
        self._water_vbo = ctx.buffer(
            reserve=self._water_capacity * 4 * FLOATS_PER_WATER_VERTEX * 4, dynamic=True
        )
        self._water_ibo = ctx.buffer(reserve=self._water_capacity * 6 * 4, dynamic=True)
        self._water_vao = ctx.vertex_array(
            self._water_program,
            [(self._water_vbo, "3f 3f", "in_pos", "in_face")],
            index_buffer=self._water_ibo,
            index_element_size=4,
        )

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
                    (mesh.vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                    (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                ],
                index_buffer=mesh.ibo,
                index_element_size=4,
            )
            self._shape_meshes[shape_key] = mesh
        for face_meshes in (
            *self._prim_face_meshes.values(),
            *self._mesh_face_meshes.values(),
        ):
            for face_index, mesh in face_meshes.items():
                mesh.vao.release()
                mesh.vao = ctx.vertex_array(
                    self._program,
                    [
                        (mesh.vbo, "3f 3f 2f", "in_pos", "in_normal", "in_mesh_uv"),
                        (self._instance_vbo, "16f 3f /i", "in_model", "in_tint"),
                    ],
                    index_buffer=mesh.ibo,
                    index_element_size=4,
                )
                face_meshes[face_index] = mesh
        self._instance_capacity = new_capacity

__all__ = [
    "DEFAULT_SUN_DIRECTION",
    "PerspectiveRenderer",
    "generated_texture_uv",
    "ground_height_for",
    "lighting_direction",
    "model_matrix",
    "terrain_line_indices",
    "coarse_terrain_samples",
    "terrain_mesh_from_heightmap",
]
