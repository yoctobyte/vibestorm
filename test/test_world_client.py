import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import LiveCircuitSession
from vibestorm.udp.world_client import (
    WorldClient,
    WorldClientError,
    region_handle_for_session,
    region_handle_from_meters,
)


def _make_bootstrap(*, region_x: int, region_y: int, sim_port: int) -> LoginBootstrap:
    return LoginBootstrap(
        agent_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        session_id=UUID("11111111-2222-3333-4444-555555555555"),
        secure_session_id=UUID("99999999-8888-7777-6666-555555555555"),
        circuit_code=0x12345678,
        sim_ip="127.0.0.1",
        sim_port=sim_port,
        seed_capability=f"http://127.0.0.1:{sim_port}/caps/seed",
        region_x=region_x,
        region_y=region_y,
        message="ok",
    )


class WorldClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def test_region_handle_packing(self) -> None:
        # SL convention: high 32 bits = region_x_meters, low 32 bits = region_y_meters.
        self.assertEqual(region_handle_from_meters(256, 512), (256 << 32) | 512)
        self.assertEqual(region_handle_from_meters(0, 0), 0)
        self.assertEqual(region_handle_from_meters(0xFFFFFFFF, 0xFFFFFFFF), (1 << 64) - 1)

    def test_region_handle_packing_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            region_handle_from_meters(-1, 0)
        with self.assertRaises(ValueError):
            region_handle_from_meters(0x1_0000_0000, 0)

    def test_region_handle_for_session_uses_bootstrap_coords(self) -> None:
        session = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        self.assertEqual(region_handle_for_session(session), (256 << 32) | 512)

    def test_add_circuit_makes_first_current_by_default(self) -> None:
        client = WorldClient()
        session = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)

        handle = client.add_circuit(session)

        self.assertEqual(handle, (256 << 32) | 512)
        self.assertIs(client.current, session)
        self.assertEqual(client.current_handle, handle)
        self.assertEqual(client.child_handles, ())

    def test_second_circuit_does_not_displace_current(self) -> None:
        client = WorldClient()
        a = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        b = LiveCircuitSession(_make_bootstrap(region_x=512, region_y=512, sim_port=9001), self.dispatcher)

        handle_a = client.add_circuit(a)
        handle_b = client.add_circuit(b)

        self.assertIs(client.current, a)
        self.assertEqual(client.child_handles, (handle_b,))
        self.assertIs(client.get(handle_b), b)

    def test_make_current_promotes_child(self) -> None:
        client = WorldClient()
        a = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        b = LiveCircuitSession(_make_bootstrap(region_x=512, region_y=512, sim_port=9001), self.dispatcher)
        client.add_circuit(a)
        handle_b = client.add_circuit(b, make_current=True)

        self.assertIs(client.current, b)
        self.assertEqual(client.current_handle, handle_b)

    def test_set_current_requires_known_handle(self) -> None:
        client = WorldClient()
        with self.assertRaises(WorldClientError):
            client.set_current(0xDEAD_BEEF)

    def test_add_circuit_rejects_duplicate_handle(self) -> None:
        client = WorldClient()
        a = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        b = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9001), self.dispatcher)
        client.add_circuit(a)
        with self.assertRaises(WorldClientError):
            client.add_circuit(b)

    def test_remove_current_clears_current_handle(self) -> None:
        client = WorldClient()
        a = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        handle = client.add_circuit(a)

        removed = client.remove_circuit(handle)

        self.assertIs(removed, a)
        self.assertIsNone(client.current)
        self.assertIsNone(client.current_handle)
        self.assertEqual(tuple(client.all_circuits()), ())

    def test_world_view_returns_current_view(self) -> None:
        client = WorldClient()
        self.assertIsNone(client.world_view())
        a = LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)
        client.add_circuit(a)
        self.assertIs(client.world_view(), a.world_view)


class RunLiveSessionWorldClientWireupTests(unittest.TestCase):
    """Confirms run_live_session registers its session in a passed-in WorldClient.

    The full live-session loop opens a UDP socket and talks to a sim, so we
    don't drive it end-to-end here. Instead we verify the wire-up at the
    function entry by stubbing socket I/O and bailing out fast.
    """

    def test_run_live_session_registers_session_in_world_client(self) -> None:
        import asyncio
        from unittest.mock import patch

        from vibestorm.udp.session import SessionConfig, run_live_session

        dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        bootstrap = _make_bootstrap(region_x=256, region_y=512, sim_port=9000)
        client = WorldClient()
        config = SessionConfig(duration_seconds=0.0, caps_prelude=False)

        async def runner() -> None:
            with patch("vibestorm.udp.session.socket.socket"):
                # Loop deadline expires immediately (duration=0); no socket I/O happens
                # because the while-loop returns false on the first iteration. The
                # WorldClient registration is set up *before* the loop starts.
                try:
                    await run_live_session(
                        bootstrap,
                        dispatcher,
                        config=config,
                        world_client=client,
                    )
                except Exception:  # noqa: BLE001 - socket stub may break shutdown path
                    pass

        asyncio.run(runner())

        self.assertIsNotNone(client.current)
        self.assertEqual(client.current_handle, (256 << 32) | 512)


if __name__ == "__main__":
    unittest.main()
