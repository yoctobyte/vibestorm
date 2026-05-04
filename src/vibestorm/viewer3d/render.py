"""Pygame rendering for the bird's-eye viewer."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

from vibestorm.viewer3d.camera import REGION_SIZE_METERS
from vibestorm.viewer3d.scene import Scene, SceneEntity

if TYPE_CHECKING:
    import pygame

    from vibestorm.viewer3d.camera import Camera


BG_COLOR: tuple[int, int, int] = (15, 18, 24)
REGION_BORDER_COLOR: tuple[int, int, int] = (60, 70, 90)
GRID_COLOR: tuple[int, int, int] = (35, 40, 50)
AVATAR_DOT_COLOR: tuple[int, int, int] = (255, 230, 90)


_TILE_CACHE: dict[Path, pygame.Surface] = {}


def _load_tile(path: Path) -> pygame.Surface | None:
    import pygame

    cached = _TILE_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        surface = pygame.image.load(str(path)).convert_alpha()
    except (pygame.error, FileNotFoundError):
        return None
    _TILE_CACHE[path] = surface
    return surface


def render_scene(surface: pygame.Surface, camera: Camera, scene: Scene) -> None:
    """Draw one frame: map tile background → grid → markers → avatars."""
    surface.fill(BG_COLOR)

    _draw_region_background(surface, camera, scene)
    _draw_grid(surface, camera)
    _draw_region_border(surface, camera)

    for entity in scene.object_entities.values():
        _draw_entity(surface, camera, entity)
    for entity in scene.avatar_entities.values():
        _draw_entity(surface, camera, entity, is_avatar=True)


def _draw_region_background(surface: pygame.Surface, camera: Camera, scene: Scene) -> None:
    if scene.map_tile_path is None:
        return
    tile = _load_tile(scene.map_tile_path)
    if tile is None:
        return

    # The tile covers the full 256m region from (0,0) to (256,256). Project
    # those corners to screen and blit a scaled copy. We use bounding-rect
    # blit + smoothscale; rotation is not needed for axis-aligned tiles.
    sx0, sy0 = camera.world_to_screen(0.0, REGION_SIZE_METERS)  # north-west corner
    sx1, sy1 = camera.world_to_screen(REGION_SIZE_METERS, 0.0)  # south-east corner
    width = max(1, int(round(sx1 - sx0)))
    height = max(1, int(round(sy1 - sy0)))
    if width < 4 or height < 4:
        return
    import pygame

    scaled = pygame.transform.smoothscale(tile, (width, height))
    surface.blit(scaled, (int(round(sx0)), int(round(sy0))))


def _draw_grid(surface: pygame.Surface, camera: Camera, spacing_m: float = 16.0) -> None:
    if camera.zoom < 0.5:  # too zoomed out for a useful 16m grid
        return
    import pygame

    # World-aligned grid lines at multiples of spacing.
    sw, sh = camera.screen_size
    wx_left, wy_top = camera.screen_to_world(0, 0)
    wx_right, wy_bottom = camera.screen_to_world(sw, sh)

    x = math.floor(min(wx_left, wx_right) / spacing_m) * spacing_m
    while x <= max(wx_left, wx_right):
        sx, _ = camera.world_to_screen(x, 0.0)
        pygame.draw.line(surface, GRID_COLOR, (int(sx), 0), (int(sx), sh), 1)
        x += spacing_m

    y = math.floor(min(wy_bottom, wy_top) / spacing_m) * spacing_m
    while y <= max(wy_bottom, wy_top):
        _, sy = camera.world_to_screen(0.0, y)
        pygame.draw.line(surface, GRID_COLOR, (0, int(sy)), (sw, int(sy)), 1)
        y += spacing_m


def _draw_region_border(surface: pygame.Surface, camera: Camera) -> None:
    import pygame

    p0 = camera.world_to_screen(0.0, 0.0)
    p1 = camera.world_to_screen(REGION_SIZE_METERS, 0.0)
    p2 = camera.world_to_screen(REGION_SIZE_METERS, REGION_SIZE_METERS)
    p3 = camera.world_to_screen(0.0, REGION_SIZE_METERS)
    points = [(int(p[0]), int(p[1])) for p in (p0, p1, p2, p3)]
    pygame.draw.lines(surface, REGION_BORDER_COLOR, True, points, 2)


def _draw_entity(
    surface: pygame.Surface, camera: Camera, entity: SceneEntity, *, is_avatar: bool = False
) -> None:
    import pygame

    cx, cy = camera.world_to_screen(entity.position[0], entity.position[1])
    half_x = max(2.0, entity.scale[0] * 0.5 * camera.zoom)
    half_y = max(2.0, entity.scale[1] * 0.5 * camera.zoom)

    # Oriented rectangle: rotate four corners around the center.
    cos_y = math.cos(entity.rotation_z_radians)
    sin_y = math.sin(entity.rotation_z_radians)
    corners_local = [(-half_x, -half_y), (half_x, -half_y), (half_x, half_y), (-half_x, half_y)]
    corners_screen = []
    for lx, ly in corners_local:
        rx = lx * cos_y - ly * sin_y
        ry = lx * sin_y + ly * cos_y
        # ry is in screen-space already (we used screen-axis half_x/half_y).
        corners_screen.append((int(round(cx + rx)), int(round(cy - ry))))

    pygame.draw.polygon(surface, entity.tint, corners_screen)

    if is_avatar:
        # Bright dot at the center for legibility at low zoom.
        radius = max(2, int(min(half_x, half_y) * 0.6))
        pygame.draw.circle(surface, AVATAR_DOT_COLOR, (int(cx), int(cy)), radius, width=2)


def clear_tile_cache() -> None:
    """Drop cached tile surfaces. Call on region change or shutdown."""
    _TILE_CACHE.clear()


__all__ = ["clear_tile_cache", "render_scene"]
