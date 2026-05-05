"""Renderer abstraction for the viewer3d fork.

The app loop holds one ``ViewerRenderer`` and calls ``update``,
``render``, then ``render_gl`` once per frame. ``render`` paints the
software ``world_surface`` (which the compositor uploads as a textured
quad in the next compositor pass); ``render_gl`` is the optional
native-GL pass for renderers that want to draw geometry directly to
the GL framebuffer (PerspectiveRenderer in step 6+).

Protocol stays small on purpose. Event handling and ``attach`` /
``detach`` GL-lifecycle hooks land if/when they have concrete
consumers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from vibestorm.viewer3d.render import clear_tile_cache, render_scene

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.camera import Camera
    from vibestorm.viewer3d.scene import Scene


class ViewerRenderer(Protocol):
    """One frame in, one frame out. Implementations own their target.

    The frame loop calls ``render`` (software world surface) before
    ``render_gl`` (optional native GL) so a renderer can do either,
    both, or neither. ``render`` runs every frame; ``render_gl`` runs
    after the world surface is uploaded as a textured quad and before
    the HUD overlay.
    """

    def update(self, dt: float, scene: Scene) -> None: ...
    def render(self, surface: pygame.Surface, scene: Scene) -> None: ...
    def render_gl(self, scene: Scene, *, aspect: float) -> None: ...
    def clear_caches(self) -> None: ...


class TopDownRenderer:
    """2D map view. Wraps the existing ``render_scene`` draw."""

    def __init__(self, camera: Camera) -> None:
        self.camera = camera

    def update(self, dt: float, scene: Scene) -> None:
        # Top-down rendering is stateless across frames; nothing to update.
        del dt, scene

    def render(self, surface: pygame.Surface, scene: Scene) -> None:
        render_scene(surface, self.camera, scene)

    def render_gl(self, scene: Scene, *, aspect: float) -> None:
        # Top-down mode is fully software; the GL framebuffer only
        # receives the uploaded world quad. Nothing to draw here.
        del scene, aspect

    def clear_caches(self) -> None:
        clear_tile_cache()


__all__ = ["TopDownRenderer", "ViewerRenderer"]
