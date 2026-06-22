import unittest


class ParcelOverlayDecodeTests(unittest.TestCase):
    def _decode(self, packets, region_size_meters):
        from vibestorm.world.parcel_overlay import decode_parcel_overlay

        return decode_parcel_overlay(packets, region_size_meters=region_size_meters)

    def test_decode_small_region_grid(self) -> None:
        from vibestorm.world.parcel_overlay import (
            FLAG_BORDER_SOUTH,
            FLAG_BORDER_WEST,
            OWNERSHIP_OWNED_BY_SELF,
            OWNERSHIP_PUBLIC,
        )

        # 8 m region -> 2x2 cells. Cell (0,0) owned-by-self with W+S borders;
        # the rest public, no borders.
        cells = bytes(
            [
                OWNERSHIP_OWNED_BY_SELF | FLAG_BORDER_WEST | FLAG_BORDER_SOUTH,
                OWNERSHIP_PUBLIC,
                OWNERSHIP_PUBLIC,
                OWNERSHIP_PUBLIC,
            ]
        )
        overlay = self._decode([(0, cells)], region_size_meters=8)

        self.assertEqual(overlay.cells_per_edge, 2)
        self.assertEqual(overlay.ownership_at(0, 0), OWNERSHIP_OWNED_BY_SELF)
        self.assertEqual(overlay.ownership_at(1, 1), OWNERSHIP_PUBLIC)

    def test_ownership_at_meters_maps_to_land_units(self) -> None:
        from vibestorm.world.parcel_overlay import (
            OWNERSHIP_FOR_SALE,
            OWNERSHIP_PUBLIC,
        )

        cells = bytes([OWNERSHIP_PUBLIC, OWNERSHIP_PUBLIC, OWNERSHIP_PUBLIC, OWNERSHIP_FOR_SALE])
        overlay = self._decode([(0, cells)], region_size_meters=8)

        # Cell (1,1) spans x,y in [4,8). 5 m -> unit 1.
        self.assertEqual(overlay.ownership_at_meters(5.0, 5.0), OWNERSHIP_FOR_SALE)
        self.assertEqual(overlay.ownership_at_meters(0.0, 0.0), OWNERSHIP_PUBLIC)

    def test_border_segments_use_meter_coordinates(self) -> None:
        from vibestorm.world.parcel_overlay import FLAG_BORDER_SOUTH, FLAG_BORDER_WEST

        cells = bytes([FLAG_BORDER_WEST | FLAG_BORDER_SOUTH, 0, 0, 0])
        overlay = self._decode([(0, cells)], region_size_meters=8)

        segments = set(overlay.border_segments())
        # West edge of cell (0,0): vertical from (0,0) to (0,4).
        self.assertIn((0, 0, 0, 4), segments)
        # South edge of cell (0,0): horizontal from (0,0) to (4,0).
        self.assertIn((0, 0, 4, 0), segments)
        self.assertEqual(len(segments), 2)

    def test_packets_reassembled_in_sequence_order(self) -> None:
        from vibestorm.world.parcel_overlay import OWNERSHIP_FOR_SALE, OWNERSHIP_OWNED_BY_SELF

        first = bytes([OWNERSHIP_OWNED_BY_SELF, OWNERSHIP_OWNED_BY_SELF])
        second = bytes([OWNERSHIP_FOR_SALE, OWNERSHIP_FOR_SALE])
        # Supplied out of order; sequence id orders them.
        overlay = self._decode([(1, second), (0, first)], region_size_meters=8)

        self.assertEqual(overlay.ownership_at(0, 0), OWNERSHIP_OWNED_BY_SELF)
        self.assertEqual(overlay.ownership_at(0, 1), OWNERSHIP_FOR_SALE)

    def test_decode_rejects_wrong_cell_count(self) -> None:
        from vibestorm.world.parcel_overlay import ParcelOverlayDecodeError

        with self.assertRaises(ParcelOverlayDecodeError):
            self._decode([(0, bytes(3))], region_size_meters=8)

    def test_decode_rejects_empty_packet_list(self) -> None:
        from vibestorm.world.parcel_overlay import ParcelOverlayDecodeError

        with self.assertRaises(ParcelOverlayDecodeError):
            self._decode([], region_size_meters=8)

    def test_decode_rejects_conflicting_sequence_data(self) -> None:
        from vibestorm.world.parcel_overlay import ParcelOverlayDecodeError

        with self.assertRaises(ParcelOverlayDecodeError):
            self._decode([(0, bytes(4)), (0, bytes([1, 2, 3, 4]))], region_size_meters=8)

    def test_full_256m_region_has_4096_cells(self) -> None:
        packets = [(seq, bytes(1024)) for seq in range(4)]
        overlay = self._decode(packets, region_size_meters=256)

        self.assertEqual(overlay.cells_per_edge, 64)
        self.assertEqual(len(overlay.cells), 4096)

    def test_ownership_name_labels(self) -> None:
        from vibestorm.world.parcel_overlay import (
            FLAG_BORDER_WEST,
            OWNERSHIP_OWNED_BY_GROUP,
            ownership_name,
        )

        # Flags above the ownership bits must not change the label.
        self.assertEqual(
            ownership_name(OWNERSHIP_OWNED_BY_GROUP | FLAG_BORDER_WEST), "group"
        )
        self.assertEqual(ownership_name(0), "public")


if __name__ == "__main__":
    unittest.main()
