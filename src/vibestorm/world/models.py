"""World-facing normalized models."""

from dataclasses import dataclass


@dataclass(slots=True)
class RegionInfo:
    name: str
    grid_x: int
    grid_y: int
