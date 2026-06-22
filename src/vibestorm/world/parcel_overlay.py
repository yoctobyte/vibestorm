"""Decoder for the ``ParcelOverlay`` packed bit-field.

OpenSim/SL sends the region parcel grid as N ``ParcelOverlay`` packets
(N = 4 for a 256 m region). The grid is in 4 m ``LandUnit`` cells, row-major
with ``y`` (south -> north) as the outer axis and ``x`` (west -> east) as the
inner axis. Each cell is one byte:

- low 3 bits: ownership type (see ``OWNERSHIP_*``)
- ``0x10``: avatars hidden on this parcel
- ``0x20``: local sound only
- ``0x40``: property border on the cell's **west** edge
- ``0x80``: property border on the cell's **south** edge

Constants mirror ``LandChannel.cs`` in the bundled OpenSim source.
"""

from __future__ import annotations

from dataclasses import dataclass

LAND_UNIT_METERS = 4
DEFAULT_REGION_SIZE_METERS = 256

OWNERSHIP_MASK = 0x07
OWNERSHIP_PUBLIC = 0
OWNERSHIP_OWNED_BY_OTHER = 1
OWNERSHIP_OWNED_BY_GROUP = 2
OWNERSHIP_OWNED_BY_SELF = 3
OWNERSHIP_FOR_SALE = 4
OWNERSHIP_AUCTION = 5

FLAG_HIDE_AVATARS = 0x10
FLAG_LOCAL_SOUND = 0x20
FLAG_BORDER_WEST = 0x40
FLAG_BORDER_SOUTH = 0x80

_OWNERSHIP_NAMES = {
    OWNERSHIP_PUBLIC: "public",
    OWNERSHIP_OWNED_BY_OTHER: "other",
    OWNERSHIP_OWNED_BY_GROUP: "group",
    OWNERSHIP_OWNED_BY_SELF: "self",
    OWNERSHIP_FOR_SALE: "for_sale",
    OWNERSHIP_AUCTION: "auction",
}


class ParcelOverlayDecodeError(ValueError):
    """Raised when ParcelOverlay packets cannot be reassembled into a grid."""


def ownership_name(value: int) -> str:
    """Return a stable lowercase label for an ownership type code."""
    return _OWNERSHIP_NAMES.get(value & OWNERSHIP_MASK, "unknown")


@dataclass(slots=True, frozen=True)
class ParcelOverlay:
    """Decoded parcel overlay grid for one region."""

    cells: tuple[int, ...]
    cells_per_edge: int
    region_size_meters: int = DEFAULT_REGION_SIZE_METERS

    def cell(self, x_units: int, y_units: int) -> int:
        """Return the raw cell byte at the given LandUnit coordinates."""
        if not (0 <= x_units < self.cells_per_edge and 0 <= y_units < self.cells_per_edge):
            raise IndexError(f"cell ({x_units}, {y_units}) outside grid")
        return self.cells[y_units * self.cells_per_edge + x_units]

    def ownership_at(self, x_units: int, y_units: int) -> int:
        """Return the ownership type code at the given LandUnit coordinates."""
        return self.cell(x_units, y_units) & OWNERSHIP_MASK

    def ownership_at_meters(self, x_meters: float, y_meters: float) -> int:
        """Return the ownership type code at a region-relative world position."""
        return self.ownership_at(
            int(x_meters) // LAND_UNIT_METERS,
            int(y_meters) // LAND_UNIT_METERS,
        )

    def border_segments(self) -> tuple[tuple[float, float, float, float], ...]:
        """Return parcel-edge line segments as ``(x0, y0, x1, y1)`` in meters.

        West borders run south->north along a cell's west edge; south borders
        run west->east along a cell's south edge.
        """
        unit = LAND_UNIT_METERS
        segments: list[tuple[float, float, float, float]] = []
        for y in range(self.cells_per_edge):
            for x in range(self.cells_per_edge):
                byte = self.cells[y * self.cells_per_edge + x]
                wx = x * unit
                wy = y * unit
                if byte & FLAG_BORDER_WEST:
                    segments.append((wx, wy, wx, wy + unit))
                if byte & FLAG_BORDER_SOUTH:
                    segments.append((wx, wy, wx + unit, wy))
        return tuple(segments)


def decode_parcel_overlay(
    packets: list[tuple[int, bytes]],
    *,
    region_size_meters: int = DEFAULT_REGION_SIZE_METERS,
) -> ParcelOverlay:
    """Reassemble ``(sequence_id, data)`` packets into a ``ParcelOverlay``.

    Packets may arrive out of order; they are sorted by sequence id. The
    concatenated cell count must be a perfect square matching the region grid.
    """
    if not packets:
        raise ParcelOverlayDecodeError("no ParcelOverlay packets supplied")

    by_sequence: dict[int, bytes] = {}
    for sequence_id, data in packets:
        if sequence_id in by_sequence and by_sequence[sequence_id] != data:
            raise ParcelOverlayDecodeError(
                f"conflicting data for ParcelOverlay sequence {sequence_id}"
            )
        by_sequence[sequence_id] = bytes(data)

    cells = bytearray()
    for sequence_id in sorted(by_sequence):
        cells.extend(by_sequence[sequence_id])

    expected_per_edge = region_size_meters // LAND_UNIT_METERS
    expected_total = expected_per_edge * expected_per_edge
    if len(cells) != expected_total:
        raise ParcelOverlayDecodeError(
            f"ParcelOverlay has {len(cells)} cells, expected {expected_total} "
            f"for a {region_size_meters} m region"
        )

    return ParcelOverlay(
        cells=tuple(cells),
        cells_per_edge=expected_per_edge,
        region_size_meters=region_size_meters,
    )


__all__ = [
    "DEFAULT_REGION_SIZE_METERS",
    "FLAG_BORDER_SOUTH",
    "FLAG_BORDER_WEST",
    "FLAG_HIDE_AVATARS",
    "FLAG_LOCAL_SOUND",
    "LAND_UNIT_METERS",
    "OWNERSHIP_AUCTION",
    "OWNERSHIP_FOR_SALE",
    "OWNERSHIP_MASK",
    "OWNERSHIP_OWNED_BY_GROUP",
    "OWNERSHIP_OWNED_BY_OTHER",
    "OWNERSHIP_OWNED_BY_SELF",
    "OWNERSHIP_PUBLIC",
    "ParcelOverlay",
    "ParcelOverlayDecodeError",
    "decode_parcel_overlay",
    "ownership_name",
]
