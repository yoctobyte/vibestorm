"""Renderer abstraction for the viewer3d fork.

The app loop holds one ``ViewerRenderer`` and calls ``update`` then
``render`` once per frame. Today only ``TopDownRenderer`` exists — it
wraps the existing 2D draw functions in ``viewer3d.render``. A future
``PerspectiveRenderer`` will plug into the same interface.

The protocol stays small on purpose. ``Camera3D``, ``attach``/``detach``
hooks for GL context lifecycle, and event handling will land in later
steps when they have concrete consumers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from vibestorm.viewer3d.render import clear_tile_cache, render_scene

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.camera import Camera
    from vibestorm.viewer3d.scene import Scene


class ViewerRenderer(Protocol):
    """One frame in, one frame out. Implementations own their target."""

    def update(self, dt: float, scene: Scene) -> None: ...
    def render(self, surface: pygame.Surface, scene: Scene) -> None: ...
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

    def clear_caches(self) -> None:
        clear_tile_cache()


__all__ = ["TopDownRenderer", "ViewerRenderer"]
