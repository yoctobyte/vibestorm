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
