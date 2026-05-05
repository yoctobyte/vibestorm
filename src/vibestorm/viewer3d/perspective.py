"""Placeholder 3D renderer for the viewer3d fork.

This is the renderer that takes over when the user picks "Render: 3D"
in the View menu. It draws into an ordinary ``pygame.Surface``; the
``GLCompositor`` in ``app.py`` uploads that surface as a texture and
draws it as a fullscreen quad on the GL framebuffer (step 5b-ii).
That fulfills "draw a single textured quad — the map tile" without
yet having any 3D geometry.

What this class does today:

- If the region's cached map tile is available, blits it scaled to
  the surface size so the user sees the region under the camera.
  This is the textured-quad payload that step 5b-ii promises.
- Otherwise fills with a recognisable dark-blue fog colour so the
  user can see the mode swap took effect even before a tile arrives.
- Draws a centred crosshair and small camera/scene labels so the
  placeholder reads as a placeholder, not a broken render.

Step 6 replaces this body with native GL geometry (one cube per
``SceneEntity``) targeting the same default framebuffer. The class
keeps the ``ViewerRenderer`` shape (``update`` / ``render`` /
``clear_caches``) so the renderer-swap plumbing in ``app.py``
doesn't change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.camera import Camera3D
    from vibestorm.viewer3d.scene import Scene


PLACEHOLDER_BG: tuple[int, int, int] = (12, 16, 28)
PLACEHOLDER_FG: tuple[int, int, int] = (140, 160, 200)
PLACEHOLDER_ACCENT: tuple[int, int, int] = (255, 200, 80)


class PerspectiveRenderer:
    """Placeholder 3D renderer. See module docstring."""

    def __init__(self, camera: Camera3D) -> None:
        self.camera = camera
        self._font = None  # type: object | None
        # Cached tile image keyed by source path. The tile is reusable
        # across frames and across surface-size changes (we smoothscale
        # on the fly), so the cache key is just the path.
        self._tile_cache: dict[Path, pygame.Surface] = {}

    def update(self, dt: float, scene: Scene) -> None:
        del dt, scene

    def render(self, surface: pygame.Surface, scene: Scene) -> None:
        import pygame

        sw, sh = surface.get_size()
        if not self._draw_map_tile_background(pygame, surface, scene, (sw, sh)):
            surface.fill(PLACEHOLDER_BG)

        cx, cy = sw // 2, sh // 2

        # Crosshair so the user sees the placeholder is alive even when
        # the map tile background is present.
        pygame.draw.line(surface, PLACEHOLDER_FG, (cx - 30, cy), (cx + 30, cy), 2)
        pygame.draw.line(surface, PLACEHOLDER_FG, (cx, cy - 30), (cx, cy + 30), 2)
        pygame.draw.circle(surface, PLACEHOLDER_ACCENT, (cx, cy), 6, width=2)

        font = self._get_font(pygame)
        if font is None:
            return

        label = font.render(
            "Vibestorm 3D placeholder — geometry rendering not yet wired",
            True,
            PLACEHOLDER_FG,
        )
        surface.blit(label, label.get_rect(center=(cx, cy + 60)))

        cam_text = (
            f"camera.mode={self.camera.mode}  "
            f"world_center=({self.camera.world_center[0]:.1f}, "
            f"{self.camera.world_center[1]:.1f})  "
            f"zoom={self.camera.zoom:.2f}"
        )
        cam_label = font.render(cam_text, True, PLACEHOLDER_FG)
        surface.blit(cam_label, cam_label.get_rect(center=(cx, cy + 90)))

        ent_count = len(scene.object_entities) + len(scene.avatar_entities)
        ent_text = f"scene entities={ent_count}  sun_phase={scene.sun_phase}"
        ent_label = font.render(ent_text, True, PLACEHOLDER_FG)
        surface.blit(ent_label, ent_label.get_rect(center=(cx, cy + 116)))

    def clear_caches(self) -> None:
        self._tile_cache.clear()

    def _draw_map_tile_background(
        self,
        pygame_module,
        surface: pygame.Surface,
        scene: Scene,
        size: tuple[int, int],
    ) -> bool:
        """Blit the cached region map tile scaled to ``size``.

        Returns True when something was drawn. The compositor uploads
        the result and shows it as the textured quad of step 5b. When
        no tile is cached yet (region just changed, fetch in flight)
        the caller falls back to a flat background.
        """
        path = scene.map_tile_path
        if path is None:
            return False
        tile = self._load_tile(pygame_module, path)
        if tile is None:
            return False
        scaled = pygame_module.transform.smoothscale(tile, size)
        surface.blit(scaled, (0, 0))
        return True

    def _load_tile(self, pygame_module, path: Path) -> pygame.Surface | None:
        cached = self._tile_cache.get(path)
        if cached is not None:
            return cached
        try:
            tile = pygame_module.image.load(str(path)).convert_alpha()
        except (pygame_module.error, FileNotFoundError):
            return None
        self._tile_cache[path] = tile
        return tile

    def _get_font(self, pygame_module) -> object | None:
        if self._font is not None:
            return self._font
        try:
            self._font = pygame_module.font.SysFont(None, 20)
        except (pygame_module.error, RuntimeError):
            return None
        return self._font


__all__ = ["PerspectiveRenderer"]
