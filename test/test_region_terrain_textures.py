"""The region's ground textures, from the wire to the shader.

Terrain used to be textured with the region *map tile* -- the 256x256 overview
the grid serves for the world map, one pixel per metre, with the objects
already drawn into it. Stretched across a 256 m region, its few dark object
pixels became large blurry black patches on the ground, and there was no detail
at walking distance at all.

`RegionHandshake` names four real ground textures and the elevation band each
covers, and the parser was already skipping exactly those bytes to reach
`RegionID` beyond them. These tests pin reading them, carrying them, and the
one rule that keeps a partial load from looking like a bug.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from struct import pack
from uuid import UUID

from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import parse_region_handshake

BASE = tuple(UUID(int=0xB0 + index) for index in range(4))
DETAIL = tuple(UUID(int=0xD0 + index) for index in range(4))
CACHE_ID = UUID("11111111-2222-3333-4444-555555555555")
REGION_ID = UUID("99999999-8888-7777-6666-555555555555")
OWNER = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

#: The template's 00, 01, 10, 11 corner order. Four different values so a
#: parser that read them in the wrong order cannot pass.
START = (10.0, 11.0, 12.0, 13.0)
RANGE = (60.0, 61.0, 62.0, 63.0)


def _handshake_body(sim_name: bytes = b"TestSim") -> bytes:
    """A RegionHandshake body laid out from the message template, in order."""
    body = bytearray()
    body += (9).to_bytes(4, "little")            # RegionFlags
    body += bytes([13])                          # SimAccess
    body += bytes([len(sim_name)]) + sim_name    # SimName
    body += OWNER.bytes                          # SimOwner
    body += bytes([1])                           # IsEstateManager
    body += pack("<f", 20.0)                     # WaterHeight
    body += pack("<f", 1.0)                      # BillableFactor
    body += CACHE_ID.bytes                       # CacheID
    for texture_id in BASE:                      # TerrainBase0..3
        body += texture_id.bytes
    for texture_id in DETAIL:                    # TerrainDetail0..3
        body += texture_id.bytes
    for value in START:                          # TerrainStartHeight00..11
        body += pack("<f", value)
    for value in RANGE:                          # TerrainHeightRange00..11
        body += pack("<f", value)
    body += REGION_ID.bytes                      # RegionInfo2.RegionID
    return bytes(body)


class RegionHandshakeTerrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def _parsed(self):
        packet = bytes([0xFF, 0xFF, 0x00, 0x94]) + _handshake_body()
        return parse_region_handshake(self.dispatcher.dispatch(packet))

    def test_it_reads_the_four_detail_textures(self) -> None:
        self.assertEqual(self._parsed().terrain_detail, DETAIL)

    def test_it_does_not_confuse_base_with_detail(self) -> None:
        # Eight consecutive UUIDs, and the second four are the ones the viewer
        # draws. Reading the first four instead produces a plausible-looking
        # set of ids that fetch fine and texture the ground wrongly.
        self.assertEqual(self._parsed().terrain_base, BASE)

    def test_it_reads_the_corner_bands_in_template_order(self) -> None:
        parsed = self._parsed()
        self.assertEqual(parsed.terrain_start_height, START)
        self.assertEqual(parsed.terrain_height_range, RANGE)

    def test_region_id_still_lands_after_the_terrain_fields(self) -> None:
        # The parser used to skip the terrain block to reach this. If reading
        # it advanced the offset by even one byte too few, RegionID would be
        # garbage -- and a UUID built from the wrong bytes is still a UUID.
        self.assertEqual(self._parsed().region_id, REGION_ID)


class RegionSummaryTests(unittest.TestCase):
    """What the parser reads has to survive the trip to the renderer."""

    def _world_view(self):
        from vibestorm.world.models import WorldView
        from vibestorm.world.updater import WorldUpdater

        dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        packet = bytes([0xFF, 0xFF, 0x00, 0x94]) + _handshake_body()
        handshake = parse_region_handshake(dispatcher.dispatch(packet))
        view = WorldView()
        WorldUpdater(view).apply_region_handshake(handshake, region_x=256, region_y=512)
        return view

    def test_the_handshake_reaches_the_region_summary(self) -> None:
        region = self._world_view().region
        self.assertIsNotNone(region)
        self.assertEqual(region.terrain_detail, DETAIL)
        self.assertEqual(region.terrain_start_height, START)
        self.assertEqual(region.terrain_height_range, RANGE)

    def test_a_region_that_names_no_textures_reads_as_zeroes(self) -> None:
        # OpenSim regions left on defaults send zero UUIDs. That is an answer,
        # not a missing field, and the renderer has to be able to tell.
        from vibestorm.world.models import WorldView

        view = WorldView()
        view.set_region(name="Nowhere", grid_x=0, grid_y=0)
        self.assertEqual(view.region.terrain_detail, (UUID(int=0),) * 4)


class TerrainTextureFetchOrderTests(unittest.TestCase):
    """The ground covers every square metre the camera sees."""

    def test_terrain_textures_are_fetched_before_object_textures(self) -> None:
        from types import SimpleNamespace

        from vibestorm.udp.session import _next_pending_object_texture_id
        from vibestorm.world.models import WorldView

        view = WorldView()
        view.set_region(
            name="TestSim",
            grid_x=1,
            grid_y=2,
            terrain_detail=DETAIL,
        )
        prim_texture = UUID(int=0xEEEE)
        view.objects[UUID(int=1)] = SimpleNamespace(
            default_texture_id=prim_texture,
            texture_entry=None,
            extra_params_entries=(),
        )
        session = SimpleNamespace(
            world_view=view,
            texture_paths={},
            texture_fetch_attempted=set(),
            region_map_image_id=None,
        )

        first = _next_pending_object_texture_id(session)
        self.assertEqual(first, DETAIL[0], "the ground should not queue behind prims")

        # And once the ground is done it moves on rather than looping.
        for texture_id in DETAIL:
            session.texture_fetch_attempted.add(texture_id)
        self.assertEqual(_next_pending_object_texture_id(session), prim_texture)

    def test_a_region_naming_no_textures_does_not_stall_the_queue(self) -> None:
        from types import SimpleNamespace

        from vibestorm.udp.session import _next_pending_object_texture_id
        from vibestorm.world.models import WorldView

        view = WorldView()
        view.set_region(name="TestSim", grid_x=1, grid_y=2)
        prim_texture = UUID(int=0xEEEE)
        view.objects[UUID(int=1)] = SimpleNamespace(
            default_texture_id=prim_texture,
            texture_entry=None,
            extra_params_entries=(),
        )
        session = SimpleNamespace(
            world_view=view,
            texture_paths={},
            texture_fetch_attempted=set(),
            region_map_image_id=None,
        )

        self.assertEqual(_next_pending_object_texture_id(session), prim_texture)


if __name__ == "__main__":
    unittest.main()
