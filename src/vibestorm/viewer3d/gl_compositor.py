"""GL compositor for the viewer3d hybrid GL+pygame_gui pipeline.

The GL backend cannot blit pygame surfaces to the screen directly —
once the display is opened with ``pygame.OPENGL`` the screen surface is
the default GL framebuffer, not a software canvas. The compositor
bridges that gap:

- Software renderers (``TopDownRenderer`` and the current
  ``PerspectiveRenderer`` placeholder) draw to ordinary
  ``pygame.Surface`` objects.
- ``pygame_gui`` draws the HUD into another ``pygame.Surface`` (with
  per-pixel alpha so empty UI space stays transparent).
- Each frame, the app uploads each surface to a ``moderngl.Texture``
  and draws it as a fullscreen textured quad — the world quad first,
  the HUD quad on top with alpha blending — then ``display.flip()``
  swaps the GL framebuffer.

This step (5b-i) deliberately keeps existing software draw paths
intact. Step 5b-ii wires the compositor into ``app.py`` and switches
the display to ``OPENGL | DOUBLEBUF``. Step 6+ replaces the
``PerspectiveRenderer`` body with native GL geometry that targets the
same default framebuffer the compositor draws to.

The compositor is GL-only: it does not own the window and does not
call ``pygame.display.*``. The app constructs the
``moderngl.Context`` (via ``moderngl.create_context()`` for an
attached window or ``moderngl.create_standalone_context()`` for tests)
and passes it in.

Coordinate-system note: pygame surfaces have row 0 at the top; GL
textures store row 0 at the bottom. The fullscreen quad's UV mapping
flips V so uploaded pygame pixels render right-side up without
needing per-frame memory copies.
"""

from __future__ import annotations

import struct
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import moderngl
    import pygame


_VERTEX_SHADER = """
#version 330

in vec2 in_pos;
in vec2 in_uv;

out vec2 v_uv;

void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_texture;
// Set when the texture was uploaded straight from pygame's own memory, which
// is laid out B, G, R, A. Swizzling here is free; converting on the CPU is not.
uniform bool u_bgra;

in vec2 v_uv;

out vec4 frag_color;

void main() {
    vec4 texel = texture(u_texture, v_uv);
    frag_color = u_bgra ? texel.bgra : texel;
}
"""


def _is_bgra_in_memory(surface: pygame.Surface) -> bool:
    """Whether the surface's bytes can be handed to GL as-is, with a swizzle.

    ``pygame.image.tobytes(surface, "RGBA")`` converts and copies the whole
    surface on the CPU: measured at 11.2 ms for a 1920x1080 frame, and it ran
    twice per frame. The pixels are already in memory in a layout GL can read
    directly, so the conversion buys nothing that a one-instruction shader
    swizzle cannot.

    A 32-bit pygame surface stores channel ``c`` in the byte its mask selects.
    On a little-endian host mask ``0xFF << (8 * i)`` is byte ``i``, so masks of
    (R=0x00FF0000, G=0x0000FF00, B=0x000000FF, A=0xFF000000) mean the bytes run
    B, G, R, A. Anything else -- a big-endian host, a 24-bit surface, a padded
    row -- falls back to the conversion rather than guessing.
    """
    if sys.byteorder != "little" or surface.get_bytesize() != 4:
        return False
    width, _height = surface.get_size()
    if surface.get_pitch() != width * 4:
        return False
    red, green, blue, alpha = surface.get_masks()
    return (red, green, blue, alpha) == (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)


# Fullscreen quad as two triangles. Each row: (clip_x, clip_y, u, v).
# Top of pygame surface (row 0) maps to top of screen (clip_y = +1) by
# pairing positive-y vertices with v = 0. Without this flip the image
# would render upside down, since GL texture row 0 is the bottom row.
_QUAD_VERTICES: tuple[float, ...] = (
    # triangle 1
    -1.0,  1.0, 0.0, 0.0,   # top-left
    -1.0, -1.0, 0.0, 1.0,   # bottom-left
     1.0, -1.0, 1.0, 1.0,   # bottom-right
    # triangle 2
    -1.0,  1.0, 0.0, 0.0,   # top-left
     1.0, -1.0, 1.0, 1.0,   # bottom-right
     1.0,  1.0, 1.0, 0.0,   # top-right
)


class GLCompositor:
    """Uploads pygame surfaces and draws them as fullscreen quads.

    The compositor maintains one cached ``moderngl.Texture`` per name.
    Re-uploading a surface of the same size reuses the texture object
    (cheap path); a size change recreates it. ``release()`` frees all
    GL resources — call it from the app's shutdown path so the GL
    context can be cleanly released.
    """

    def __init__(self, ctx: moderngl.Context) -> None:
        self.ctx = ctx
        self._program = ctx.program(
            vertex_shader=_VERTEX_SHADER,
            fragment_shader=_FRAGMENT_SHADER,
        )
        # Texture unit 0 is fine for a single-quad pipeline.
        if "u_texture" in self._program:
            self._program["u_texture"].value = 0

        # Pack vertex data without numpy — moderngl accepts raw bytes,
        # and numpy isn't a viewer3d hard dep yet.
        vbo_bytes = struct.pack(f"{len(_QUAD_VERTICES)}f", *_QUAD_VERTICES)
        self._vbo = ctx.buffer(vbo_bytes)
        self._vao = ctx.vertex_array(
            self._program,
            [(self._vbo, "2f 2f", "in_pos", "in_uv")],
        )

        self._textures: dict[str, moderngl.Texture] = {}
        self._bgra: dict[str, bool] = {}

    # --------------------------------------------------------------- public

    def clear(self, color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)) -> None:
        """Clear the bound framebuffer to ``color`` (RGBA, 0..1)."""
        r, g, b, a = color
        self.ctx.clear(red=r, green=g, blue=b, alpha=a)

    def upload_surface(self, name: str, surface: pygame.Surface) -> None:
        """Upload (or re-upload) a pygame surface as RGBA8 under ``name``.

        First call for a name allocates a texture sized to the surface.
        Subsequent calls write into the same texture if the size is
        unchanged; a size change releases the old texture and allocates
        a new one. The surface is read as RGBA8 with no flip — the
        fullscreen-quad UVs handle the GL/pygame Y-axis difference.
        """
        import pygame

        size = surface.get_size()
        # Prefer the surface's own memory; fall back to a converting copy when
        # its layout is not one GL can read directly. Either way row 0 is the
        # top of the pygame surface, which the quad's UVs already account for.
        bgra = _is_bgra_in_memory(surface)
        if bgra:
            pixels = surface.get_view("1")
        else:
            pixels = pygame.image.tobytes(surface, "RGBA")
        self._bgra[name] = bgra

        existing = self._textures.get(name)
        if existing is not None and existing.size == size:
            existing.write(pixels)
            return
        if existing is not None:
            existing.release()
        texture = self.ctx.texture(size, components=4, data=pixels)
        # Linear filtering: pygame_gui text and the map tile both look
        # better than nearest, especially when the window isn't an exact
        # multiple of the surface size.
        texture.filter = (self.ctx.LINEAR, self.ctx.LINEAR)
        # Clamp avoids edge bleed when the quad samples the rightmost
        # texel. Border addressing isn't necessary.
        texture.repeat_x = False
        texture.repeat_y = False
        self._textures[name] = texture

    def draw(self, name: str, *, alpha: bool = False) -> None:
        """Draw the named texture as a fullscreen quad.

        ``alpha=True`` enables source-over blending so the HUD's
        per-pixel alpha composites correctly over the world. The world
        quad uses ``alpha=False`` — its surface is fully opaque.
        """
        texture = self._textures.get(name)
        if texture is None:
            raise KeyError(f"GLCompositor has no texture for {name!r}; upload it first")

        if "u_bgra" in self._program:
            self._program["u_bgra"].value = self._bgra.get(name, False)

        if alpha:
            self.ctx.enable(self.ctx.BLEND)
            self.ctx.blend_func = (self.ctx.SRC_ALPHA, self.ctx.ONE_MINUS_SRC_ALPHA)
        else:
            self.ctx.disable(self.ctx.BLEND)

        texture.use(location=0)
        self._vao.render()

        if alpha:
            # Leave the GL state predictable for the next renderer.
            self.ctx.disable(self.ctx.BLEND)

    def has_texture(self, name: str) -> bool:
        return name in self._textures

    def texture_size(self, name: str) -> tuple[int, int] | None:
        texture = self._textures.get(name)
        return texture.size if texture is not None else None

    def release(self) -> None:
        """Release all GL resources owned by the compositor."""
        for texture in self._textures.values():
            texture.release()
        self._textures.clear()
        self._bgra.clear()
        try:
            self._vao.release()
        finally:
            self._vbo.release()
            self._program.release()


__all__ = ["GLCompositor"]
