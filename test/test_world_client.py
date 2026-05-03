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


class WorldClientBusBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def _make_session(self, *, region_x: int = 256, region_y: int = 512, sim_port: int = 9000) -> LiveCircuitSession:
        return LiveCircuitSession(_make_bootstrap(region_x=region_x, region_y=region_y, sim_port=sim_port), self.dispatcher)

    def test_chat_local_session_event_publishes_typed_chatlocal(self) -> None:
        from vibestorm.bus.events import ChatLocal
        from vibestorm.udp.session import SessionEvent

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)
        received: list[ChatLocal] = []
        client.bus.subscribe(ChatLocal, received.append)

        # Trigger via the session's _record_event so the bridge sees it.
        session._record_event(
            10.0,
            "chat.local",
            "from='Resident Joe' type=1 audible=1 pos=(1.00,2.00,3.00) message='hello world'",
        )

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].from_name, "Resident Joe")
        self.assertEqual(received[0].chat_type, 1)
        self.assertEqual(received[0].audible, 1)
        self.assertEqual(received[0].message, "hello world")
        self.assertEqual(received[0].region_handle, (256 << 32) | 512)

    def test_chat_outbound_session_event_publishes_typed_chatoutbound(self) -> None:
        from vibestorm.bus.events import ChatOutbound

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)
        received: list[ChatOutbound] = []
        client.bus.subscribe(ChatOutbound, received.append)

        session._record_event(11.0, "chat.outbound", "type=1 channel=0 message='hi all'")

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].message, "hi all")

    def test_chat_alert_session_event_publishes_typed_chatalert(self) -> None:
        from vibestorm.bus.events import ChatAlert

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)
        received: list[ChatAlert] = []
        client.bus.subscribe(ChatAlert, received.append)

        session._record_event(12.0, "chat.alert", "message='Region restart in 5 minutes'")
        session._record_event(13.0, "chat.agent_alert", "modal=1 message='You have been muted'")

        self.assertEqual(len(received), 2)
        self.assertFalse(received[0].is_agent_alert)
        self.assertTrue(received[1].is_agent_alert)

    def test_world_kind_session_event_publishes_world_state_changed(self) -> None:
        from vibestorm.bus.events import WorldStateChanged

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)
        received: list[WorldStateChanged] = []
        client.bus.subscribe(WorldStateChanged, received.append)

        session._record_event(14.0, "world.object_added", "local_id=42")

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].reason, "object_added")

    def test_add_circuit_publishes_region_changed(self) -> None:
        from vibestorm.bus.events import RegionChanged

        client = WorldClient()
        received: list[RegionChanged] = []
        client.bus.subscribe(RegionChanged, received.append)

        session = self._make_session()
        client.add_circuit(session)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].region_handle, (256 << 32) | 512)

    def test_existing_on_event_callback_still_fires(self) -> None:
        client = WorldClient()
        captured: list[str] = []
        session = LiveCircuitSession(
            _make_bootstrap(region_x=256, region_y=512, sim_port=9000),
            self.dispatcher,
            on_event=lambda evt: captured.append(evt.kind),
        )
        client.add_circuit(session)

        session._record_event(15.0, "chat.local", "from='X' type=1 audible=1 pos=(0,0,0) message='ping'")

        self.assertIn("chat.local", captured)


class WorldClientCommandHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())

    def _make_session(self) -> LiveCircuitSession:
        return LiveCircuitSession(_make_bootstrap(region_x=256, region_y=512, sim_port=9000), self.dispatcher)

    def test_set_control_flags_command_updates_session(self) -> None:
        from vibestorm.bus.commands import SetControlFlags
        from vibestorm.udp.control_flags import AgentControlFlags

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)

        client.bus.dispatch(SetControlFlags(int(AgentControlFlags.AT_POS | AgentControlFlags.FLY)))

        self.assertEqual(
            session.agent_control_flags,
            int(AgentControlFlags.AT_POS) | int(AgentControlFlags.FLY),
        )

    def test_add_remove_clear_control_flags_commands(self) -> None:
        from vibestorm.bus.commands import (
            AddControlFlags,
            ClearControlFlags,
            RemoveControlFlags,
        )
        from vibestorm.udp.control_flags import AgentControlFlags

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)

        client.bus.dispatch(AddControlFlags(int(AgentControlFlags.AT_POS)))
        client.bus.dispatch(AddControlFlags(int(AgentControlFlags.FLY)))
        self.assertEqual(
            session.agent_control_flags,
            int(AgentControlFlags.AT_POS) | int(AgentControlFlags.FLY),
        )

        client.bus.dispatch(RemoveControlFlags(int(AgentControlFlags.AT_POS)))
        self.assertEqual(session.agent_control_flags, int(AgentControlFlags.FLY))

        client.bus.dispatch(ClearControlFlags())
        self.assertEqual(session.agent_control_flags, 0)

    def test_set_body_and_head_rotation_commands(self) -> None:
        from vibestorm.bus.commands import SetBodyRotation, SetHeadRotation

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)

        client.bus.dispatch(SetBodyRotation((0.1, 0.2, 0.3)))
        client.bus.dispatch(SetHeadRotation((0.4, 0.5, 0.6)))

        self.assertEqual(session.body_rotation, (0.1, 0.2, 0.3))
        self.assertEqual(session.head_rotation, (0.4, 0.5, 0.6))

    def test_set_camera_command(self) -> None:
        from vibestorm.bus.commands import SetCamera

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)

        client.bus.dispatch(
            SetCamera(
                center=(10.0, 20.0, 30.0),
                at_axis=(0.0, 1.0, 0.0),
                left_axis=(-1.0, 0.0, 0.0),
                up_axis=(0.0, 0.0, 1.0),
            )
        )

        self.assertEqual(session.camera_center, (10.0, 20.0, 30.0))
        self.assertEqual(session.camera_at_axis, (0.0, 1.0, 0.0))

    def test_send_chat_command_returns_packet_bytes(self) -> None:
        from vibestorm.bus.commands import SendChat

        client = WorldClient()
        session = self._make_session()
        client.add_circuit(session)

        result = client.bus.dispatch(SendChat("hello"))

        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_command_without_current_circuit_raises(self) -> None:
        from vibestorm.bus.commands import SetControlFlags
        from vibestorm.udp.world_client import WorldClientError

        client = WorldClient()
        with self.assertRaises(WorldClientError):
            client.bus.dispatch(SetControlFlags(0x1))


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
