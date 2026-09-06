"""Opening the region next door, from the session's side.

The circuit itself is `test_udp_neighbour.py`'s business. This is the half
that decides *when* to open one, and it is fiddly for a reason that is not
obvious from either event on its own: `EnableSimulator` says where the
region is, `EstablishAgentCommunication` carries the seed capability that
unlocks its terrain, they arrive in either order, and they do not share a
key. One names a handle and an ip and a port; the other names a string,
"ip:port", and no handle at all. Matching them up wrongly means dialling
one region with another's front door key.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.event_queue.events import (
    EnableSimulatorEvent,
    EstablishAgentCommunicationEvent,
    EventQueueBatch,
)
from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import LiveCircuitSession

REPO_ROOT = Path(__file__).resolve().parents[1]

AGENT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SESSION = UUID("11111111-2222-3333-4444-555555555555")

#: Metres. The root's x and y differ deliberately -- with a square handle,
#: a region handle assembled from the wrong word round is indistinguishable
#: from the right one.
ROOT_X, ROOT_Y = 256000, 256256
NORTH_HANDLE = (256000 << 32) | 256512
EAST_HANDLE = (256256 << 32) | 256256

NORTH = EnableSimulatorEvent(
    handle=NORTH_HANDLE,
    ip="127.0.0.1",
    port=9001,
    region_size_x=256,
    region_size_y=256,
)
EAST = EnableSimulatorEvent(
    handle=EAST_HANDLE,
    ip="127.0.0.1",
    port=9002,
    region_size_x=256,
    region_size_y=256,
)
NORTH_SEED = EstablishAgentCommunicationEvent(
    agent_id=str(AGENT),
    sim_ip_and_port="127.0.0.1:9001",
    seed_capability="http://127.0.0.1:9000/CAPS/north0000/",
)
EAST_SEED = EstablishAgentCommunicationEvent(
    agent_id=str(AGENT),
    sim_ip_and_port="127.0.0.1:9002",
    seed_capability="http://127.0.0.1:9000/CAPS/east0000/",
)


class NeighbourTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dispatcher = MessageDispatcher.from_repo_root(REPO_ROOT)

    def session(self) -> LiveCircuitSession:
        bootstrap = LoginBootstrap(
            agent_id=AGENT,
            session_id=SESSION,
            secure_session_id=UUID("99999999-8888-7777-6666-555555555555"),
            circuit_code=0x12345678,
            sim_ip="127.0.0.1",
            sim_port=9000,
            seed_capability="http://127.0.0.1:9000/caps/seed",
            region_x=ROOT_X,
            region_y=ROOT_Y,
            message="ok",
        )
        return LiveCircuitSession(bootstrap, self.dispatcher)

    def fold(self, session: LiveCircuitSession, *events: object) -> None:
        session.handle_event_queue_batch(
            EventQueueBatch(ack_id=1, events=list(events)), now=1.0
        )


class WaitingForBothHalvesTests(NeighbourTestCase):
    def test_an_announcement_alone_is_not_enough(self) -> None:
        # This is the whole finding: a circuit opened on EnableSimulator alone
        # connects, is answered, and receives no ground at all.
        session = self.session()
        self.fold(session, NORTH)
        self.assertEqual(session.neighbours_ready_to_open(), [])

    def test_a_seed_capability_alone_is_not_enough(self) -> None:
        # Nothing to dial: the seed event carries no region handle.
        session = self.session()
        self.fold(session, NORTH_SEED)
        self.assertEqual(session.neighbours_ready_to_open(), [])

    def test_both_halves_open_the_region(self) -> None:
        session = self.session()
        self.fold(session, NORTH, NORTH_SEED)
        self.assertEqual(
            session.neighbours_ready_to_open(), [(NORTH, NORTH_SEED.seed_capability)]
        )

    def test_the_seed_capability_may_arrive_first(self) -> None:
        # Both orders are seen live; nothing in the event queue guarantees one.
        session = self.session()
        self.fold(session, NORTH_SEED, NORTH)
        self.assertEqual(
            session.neighbours_ready_to_open(), [(NORTH, NORTH_SEED.seed_capability)]
        )

    def test_each_region_needs_its_own_key(self) -> None:
        # Two neighbours, one seed capability. Matching on anything looser
        # than the address would dial the east region with the north one's
        # URL -- which resolves, and unlocks the wrong region.
        session = self.session()
        self.fold(session, NORTH, EAST, NORTH_SEED)
        ready = session.neighbours_ready_to_open()
        self.assertEqual([announcement for announcement, _ in ready], [NORTH])

    def test_the_right_key_goes_with_the_right_region(self) -> None:
        session = self.session()
        self.fold(session, NORTH, EAST, NORTH_SEED, EAST_SEED)
        self.assertEqual(
            dict(session.neighbours_ready_to_open()),
            {NORTH: NORTH_SEED.seed_capability, EAST: EAST_SEED.seed_capability},
        )

    def test_a_region_is_not_offered_twice(self) -> None:
        session = self.session()
        self.fold(session, NORTH, NORTH_SEED)
        session.open_neighbour(NORTH)
        self.assertEqual(session.neighbours_ready_to_open(), [])

    def test_a_region_that_refused_is_not_retried(self) -> None:
        # A dead neighbour that stayed on the list would cost an HTTP timeout
        # every pass round the run loop, forever.
        session = self.session()
        self.fold(session, NORTH, NORTH_SEED)
        session.neighbour_failures[NORTH_HANDLE] = "connection refused"
        self.assertEqual(session.neighbours_ready_to_open(), [])

    def test_a_repeated_announcement_does_not_pile_up(self) -> None:
        session = self.session()
        self.fold(session, NORTH, NORTH, NORTH_SEED)
        self.assertEqual(len(session.neighbour_announcements), 1)
        self.assertEqual(len(session.neighbours_ready_to_open()), 1)


class OpeningTheCircuitTests(NeighbourTestCase):
    def test_the_circuit_is_dialled_at_the_announced_address(self) -> None:
        session = self.session()
        circuit = session.open_neighbour(NORTH)
        self.assertEqual(circuit.address, ("127.0.0.1", 9001))
        self.assertEqual(circuit.handle, NORTH_HANDLE)

    def test_the_circuit_carries_this_agent_and_this_session(self) -> None:
        # A child circuit is the same agent seen from another region. Fresh
        # ids here would be a second, unauthorised login.
        session = self.session()
        circuit = session.open_neighbour(NORTH)
        self.assertEqual(circuit.agent_id, AGENT)
        self.assertEqual(circuit.session_id, SESSION)
        self.assertEqual(circuit.circuit_code, 0x12345678)

    def test_the_neighbour_knows_where_it_sits(self) -> None:
        session = self.session()
        circuit = session.open_neighbour(NORTH)
        self.assertEqual(circuit.offset_from(session.region_handle), (0.0, 256.0))

    def test_the_region_handle_is_this_region(self) -> None:
        self.assertEqual(self.session().region_handle, (ROOT_X << 32) | ROOT_Y)


class RoutingTests(NeighbourTestCase):
    """One socket, several simulators; the source address is the only clue."""

    def test_a_packet_from_a_neighbour_finds_its_circuit(self) -> None:
        session = self.session()
        north = session.open_neighbour(NORTH)
        east = session.open_neighbour(EAST)
        self.assertIs(session.neighbour_at(("127.0.0.1", 9001)), north)
        self.assertIs(session.neighbour_at(("127.0.0.1", 9002)), east)

    def test_a_packet_from_this_region_belongs_to_no_neighbour(self) -> None:
        # Answering the root sim's traffic on a child circuit would decode it
        # against the wrong sequence space and the wrong world.
        session = self.session()
        session.open_neighbour(NORTH)
        self.assertIsNone(session.neighbour_at(("127.0.0.1", 9000)))

    def test_a_packet_from_a_stranger_belongs_to_no_neighbour(self) -> None:
        session = self.session()
        session.open_neighbour(NORTH)
        self.assertIsNone(session.neighbour_at(("10.0.0.9", 9001)))

    def test_the_port_is_part_of_the_address(self) -> None:
        # Two regions on one host is the ordinary case, including the local
        # grid this was measured on.
        session = self.session()
        session.open_neighbour(NORTH)
        self.assertIsNone(session.neighbour_at(("127.0.0.1", 9002)))


class NeighbourGroundTextureTests(NeighbourTestCase):
    """A neighbour's ground textures go through this region's capability."""

    def test_a_neighbour_s_ground_textures_join_the_fetch_queue(self) -> None:
        # They are asset ids like any other, and the asset service does not
        # care which region asked. Fetching them through the neighbour's own
        # capabilities would be a second texture pipeline for no reason.
        from vibestorm.udp.session import _next_pending_object_texture_id

        session = self.session()
        circuit = session.open_neighbour(NORTH)
        wanted = UUID("cccccccc-dddd-eeee-ffff-000000000001")
        circuit.terrain_detail = (wanted, wanted, wanted, wanted)
        self.assertEqual(_next_pending_object_texture_id(session), wanted)

    def test_a_texture_already_fetched_is_not_asked_for_again(self) -> None:
        from pathlib import Path

        from vibestorm.udp.session import _next_pending_object_texture_id

        session = self.session()
        circuit = session.open_neighbour(NORTH)
        wanted = UUID("cccccccc-dddd-eeee-ffff-000000000001")
        circuit.terrain_detail = (wanted,) * 4
        session.texture_paths[wanted] = Path("/tmp/already-here.png")
        self.assertIsNone(_next_pending_object_texture_id(session))

    def test_a_region_that_never_answered_asks_for_nothing(self) -> None:
        from vibestorm.udp.session import _next_pending_object_texture_id

        session = self.session()
        session.open_neighbour(NORTH)
        self.assertIsNone(_next_pending_object_texture_id(session))


class NeighbourObjectAssetTests(NeighbourTestCase):
    """A neighbour's *prims* go through this region's capabilities too.

    Not for tidiness: a prim drawn without its texture is a grey box and a
    mesh prim drawn without its asset is a cube, and next door that is a
    skyline of cubes where the buildings are. Asset ids are grid-wide, so the
    only thing standing between them and the same pipeline is somebody
    walking the neighbour's world view.
    """

    TEXTURE = UUID("cccccccc-dddd-eeee-ffff-000000000011")
    MESH = UUID("cccccccc-dddd-eeee-ffff-000000000022")

    def _prim(self, *, texture_id=None, mesh_id=None, face_texture_id=None):
        from vibestorm.world.models import ExtraParamEntry, WorldObject
        from vibestorm.world.texture_entry import TextureEntry

        entry = None
        if face_texture_id is not None:
            entry = TextureEntry(
                default_texture_id=texture_id,
                face_texture_ids=((3, face_texture_id),),
            )
        entries = ()
        if mesh_id is not None:
            entries = (
                ExtraParamEntry(
                    param_type=0x30,
                    param_in_use=True,
                    param_data=mesh_id.bytes + bytes([5]),
                ),
            )
        return WorldObject(
            full_id=UUID(int=7),
            local_id=7,
            parent_id=0,
            pcode=9,
            material=0,
            click_action=0,
            scale=(1.0, 1.0, 1.0),
            state=0,
            crc=0,
            update_flags=0,
            region_handle=0,
            time_dilation=0,
            object_data_size=0,
            position=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0, 1.0),
            variant="prim_basic",
            name_values={},
            texture_entry_size=0,
            texture_anim_size=0,
            data_size=0,
            text_size=0,
            media_url_size=0,
            ps_block_size=0,
            extra_params_size=0,
            extra_params_entries=entries,
            default_texture_id=texture_id,
            texture_entry=entry,
        )

    def _with_prim(self, **kwargs):
        session = self.session()
        circuit = session.open_neighbour(NORTH)
        prim = self._prim(**kwargs)
        circuit.world_view.objects[prim.full_id] = prim
        return session

    def test_a_prim_next_door_gets_its_texture_fetched(self) -> None:
        from vibestorm.udp.session import _next_pending_object_texture_id

        session = self._with_prim(texture_id=self.TEXTURE)
        self.assertEqual(_next_pending_object_texture_id(session), self.TEXTURE)

    def test_the_ground_next_door_is_still_asked_for_first(self) -> None:
        # It covers every square metre of the region; a prim covers a few.
        from vibestorm.udp.session import _next_pending_object_texture_id

        ground = UUID("cccccccc-dddd-eeee-ffff-000000000001")
        session = self._with_prim(texture_id=self.TEXTURE)
        session.neighbours[NORTH_HANDLE].terrain_detail = (ground,) * 4
        self.assertEqual(_next_pending_object_texture_id(session), ground)

    def test_our_own_prims_are_asked_for_before_the_neighbour_s(self) -> None:
        from vibestorm.udp.session import _next_pending_object_texture_id

        here = UUID("cccccccc-dddd-eeee-ffff-000000000033")
        session = self._with_prim(texture_id=self.TEXTURE)
        ours = self._prim(texture_id=here)
        session.world_view.objects[UUID(int=8)] = ours
        self.assertEqual(_next_pending_object_texture_id(session), here)

    def test_a_texture_already_fetched_is_not_asked_for_again(self) -> None:
        from vibestorm.udp.session import _next_pending_object_texture_id

        session = self._with_prim(texture_id=self.TEXTURE)
        session.texture_fetch_attempted.add(self.TEXTURE)
        self.assertIsNone(_next_pending_object_texture_id(session))

    def test_a_prim_s_other_faces_are_asked_for_too(self) -> None:
        # A prim's `TextureEntry` names a texture per face, and the default
        # is only the one the rest fall back to. Reading the default alone
        # draws a six-sided prim in one texture and nothing says so.
        from vibestorm.udp.session import _next_pending_object_texture_id

        face = UUID("cccccccc-dddd-eeee-ffff-000000000055")
        session = self._with_prim(texture_id=self.TEXTURE, face_texture_id=face)
        session.texture_fetch_attempted.add(self.TEXTURE)
        self.assertEqual(_next_pending_object_texture_id(session), face)

    def test_a_mesh_prim_next_door_gets_its_asset_fetched(self) -> None:
        from vibestorm.udp.session import _next_pending_mesh_asset_id

        session = self._with_prim(mesh_id=self.MESH)
        self.assertEqual(_next_pending_mesh_asset_id(session), self.MESH)

    def test_our_own_meshes_are_asked_for_before_the_neighbour_s(self) -> None:
        from vibestorm.udp.session import _next_pending_mesh_asset_id

        here = UUID("cccccccc-dddd-eeee-ffff-000000000044")
        session = self._with_prim(mesh_id=self.MESH)
        session.world_view.objects[UUID(int=8)] = self._prim(mesh_id=here)
        self.assertEqual(_next_pending_mesh_asset_id(session), here)

    def test_a_mesh_already_fetched_is_not_asked_for_again(self) -> None:
        from vibestorm.udp.session import _next_pending_mesh_asset_id

        session = self._with_prim(mesh_id=self.MESH)
        session.mesh_fetch_attempted.add(self.MESH)
        self.assertIsNone(_next_pending_mesh_asset_id(session))

    def test_a_region_with_nothing_in_it_asks_for_nothing(self) -> None:
        from vibestorm.udp.session import (
            _next_pending_mesh_asset_id,
            _next_pending_object_texture_id,
        )

        session = self.session()
        session.open_neighbour(NORTH)
        self.assertIsNone(_next_pending_object_texture_id(session))
        self.assertIsNone(_next_pending_mesh_asset_id(session))


class DiallingDoesNotStopThePumpTests(unittest.IsolatedAsyncioTestCase):
    """Opening a neighbour must not hold up the region we are standing in.

    The seed capability is an HTTP POST, and it runs on the loop that pumps
    this session's packets. An eight-way corner on a busy grid is eight of
    them; awaiting each in turn would stop acking the region the avatar is
    actually in for as long as the slowest one takes, which the simulator
    reads as a viewer that has gone away.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.dispatcher = MessageDispatcher.from_repo_root(REPO_ROOT)

    def setUp(self) -> None:
        import socket

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind(("127.0.0.1", 0))
        self.addCleanup(self.sock.close)
        self.session = NeighbourTestCase.session(self)
        self.pending: dict = {}

    def _stub_capability_client(self, behaviour):
        """Replace the seed-cap client with one whose answer we control."""
        import vibestorm.udp.session as session_module

        class _Client:
            def __init__(self, **_kwargs) -> None:
                pass

            async def resolve_seed_caps(self, seed_url, names, **_kwargs):
                return await behaviour(seed_url, names)

        original = session_module.CapabilityClient
        session_module.CapabilityClient = _Client
        self.addCleanup(setattr, session_module, "CapabilityClient", original)

    async def _dial(self, *events):
        from vibestorm.udp.session import (
            _open_finished_neighbours,
            _start_neighbour_seed_requests,
        )

        loop = asyncio.get_running_loop()
        self.session.handle_event_queue_batch(
            EventQueueBatch(ack_id=1, events=list(events)), now=1.0
        )
        _start_neighbour_seed_requests(self.session, self.sock, loop, self.pending)
        await _open_finished_neighbours(self.session, self.sock, loop, self.pending)

    async def _settle(self):
        from vibestorm.udp.session import _open_finished_neighbours

        # One turn of the loop is enough for a request that is already
        # resolved; the point is that the caller never awaited it.
        await asyncio.sleep(0)
        await _open_finished_neighbours(
            self.session, self.sock, asyncio.get_running_loop(), self.pending
        )

    async def test_a_slow_region_does_not_stall_the_pass(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(_seed_url, _names):
            started.set()
            await release.wait()
            return {}

        self._stub_capability_client(slow)
        await self._dial(NORTH, NORTH_SEED)

        # Back already, and the request has not even begun -- it is scheduled
        # on the loop, which is the strongest form of "nobody waited".
        self.assertFalse(started.is_set())
        self.assertEqual(self.session.neighbours, {})
        self.assertIn(NORTH_HANDLE, self.pending)

        # It does begin, and still nothing is waiting for it to finish.
        await asyncio.sleep(0)
        self.assertTrue(started.is_set())
        self.assertEqual(self.session.neighbours, {})

        release.set()
        await self._settle()
        self.assertIn(NORTH_HANDLE, self.session.neighbours)
        self.assertEqual(self.pending, {})

    async def test_two_regions_are_dialled_at_once(self) -> None:
        release = asyncio.Event()

        async def slow(_seed_url, _names):
            await release.wait()
            return {}

        self._stub_capability_client(slow)
        await self._dial(NORTH, EAST, NORTH_SEED, EAST_SEED)
        self.assertEqual(sorted(self.pending), sorted((NORTH_HANDLE, EAST_HANDLE)))

        release.set()
        await self._settle()
        self.assertEqual(sorted(self.session.neighbours), sorted((NORTH_HANDLE, EAST_HANDLE)))

    async def test_a_request_in_flight_is_not_sent_twice(self) -> None:
        calls = []
        release = asyncio.Event()

        async def slow(seed_url, _names):
            calls.append(seed_url)
            await release.wait()
            return {}

        self._stub_capability_client(slow)
        await self._dial(NORTH, NORTH_SEED)
        await asyncio.sleep(0)
        await self._dial()
        await self._dial()
        await asyncio.sleep(0)
        self.assertEqual(len(calls), 1)
        release.set()
        await self._settle()

    async def test_the_circuit_opens_only_once_the_seed_cap_answered(self) -> None:
        async def quick(_seed_url, _names):
            return {"EventQueueGet": "http://example.invalid/eq"}

        self._stub_capability_client(quick)
        await self._dial(NORTH, NORTH_SEED)
        await self._settle()

        circuit = self.session.neighbours[NORTH_HANDLE]
        self.assertEqual(circuit.address, ("127.0.0.1", 9001))
        # And it said hello: `start` is idempotent, so an empty list here
        # means the opening packets have already gone out.
        self.assertEqual(circuit.start(), [])

    async def test_a_region_that_refuses_is_recorded_and_left_alone(self) -> None:
        from vibestorm.caps.client import CapabilityError

        calls = []

        async def refuse(seed_url, _names):
            calls.append(seed_url)
            raise CapabilityError("connection refused")

        self._stub_capability_client(refuse)
        await self._dial(NORTH, NORTH_SEED)
        await self._settle()

        self.assertIn(NORTH_HANDLE, self.session.neighbour_failures)
        self.assertEqual(self.session.neighbours, {})

        await self._dial()
        await self._settle()
        self.assertEqual(len(calls), 1, "a dead region was dialled again")

    async def test_a_region_that_answers_rubbish_does_not_end_the_session(self) -> None:
        # The seed capability answers LLSD. A grid that answers an HTML error
        # page instead is a region that stays undrawn, not a viewer that goes
        # down mid-flight.
        async def rubbish(_seed_url, _names):
            raise ValueError("not LLSD")

        self._stub_capability_client(rubbish)
        await self._dial(NORTH, NORTH_SEED)
        await self._settle()
        self.assertIn(NORTH_HANDLE, self.session.neighbour_failures)


class SessionEventTests(NeighbourTestCase):
    def test_both_halves_are_recorded_as_events(self) -> None:
        # The session log is where a neighbour that never opened is diagnosed
        # from, and "which half was missing" is the first question.
        session = self.session()
        self.fold(session, NORTH, NORTH_SEED)
        details = " ".join(event.detail for event in session.events)
        self.assertIn("neighbour.announced", [e.kind for e in session.events])
        self.assertIn("neighbour.seed", [e.kind for e in session.events])
        self.assertIn("127.0.0.1:9001", details)


if __name__ == "__main__":
    unittest.main()
