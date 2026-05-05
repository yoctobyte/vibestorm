"""Placeholder 3D renderer for the viewer3d fork.

This is the stub that proves the renderer-swap mechanism end to end.
When the user picks "Render: 3D" in the View menu, the app swaps the
active ``ViewerRenderer`` from ``TopDownRenderer`` to this class. There
is no GL yet — the actual moderngl bootstrap, fullscreen quad, and
HUD-over-GL compositing land in step 5b/5c.

What this class does today:

- Fills the surface with a recognisable dark-blue fog colour so the
  user can see the mode swap took effect.
- Draws a centred crosshair and a small label so the placeholder is
  obviously a placeholder, not a broken render.
- Reads ``camera.world_center`` and ``camera.zoom`` and prints them in
  the placeholder text — this proves Camera3D is reachable from the
  renderer without further plumbing.

Once moderngl lands (5b), this class swaps from "fill+blit on the
software surface" to "render to GL framebuffer; blit/upload as needed
for HUD compositing" without changing its public interface.
"""

from __future__ import annotations

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

    def update(self, dt: float, scene: Scene) -> None:
        del dt, scene

    def render(self, surface: pygame.Surface, scene: Scene) -> None:
        import pygame

        surface.fill(PLACEHOLDER_BG)

        sw, sh = surface.get_size()
        cx, cy = sw // 2, sh // 2

        # Crosshair so the user sees the placeholder is alive.
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
        # No tile cache, no shader cache, nothing to clear yet.
        pass

    def _get_font(self, pygame_module) -> object | None:
        if self._font is not None:
            return self._font
        try:
            self._font = pygame_module.font.SysFont(None, 20)
        except (pygame_module.error, RuntimeError):
            return None
        return self._font


__all__ = ["PerspectiveRenderer"]
