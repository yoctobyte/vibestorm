"""The three ways a simulator says a session is over.

None of them was handled. Over the recorded sessions in
`local/unknowns.sqlite3`, ten `DisableSimulator`, ten `KickUser` and 292
`LogoutReply` arrived, were decoded to a message name and dropped. The first
two matter: a client that ignores them goes on sending `AgentUpdate` at a
simulator that has dropped it, which from the outside is a hang rather than a
disconnection.

`CloseCircuit` was already handled and sets `close_reason`, which is what the
run loop watches. These join it.
"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.udp.messages import (
    MessageDecodeError,
    parse_kick_user,
    parse_logout_reply,
)
from vibestorm.udp.template import (
    MessageDispatch,
    decode_message_number,
    load_template_summaries,
    template_path,
)

SUMMARIES = load_template_summaries(template_path(Path.cwd()))
AGENT = UUID("11111111-2222-3333-4444-555555555555")
SESSION = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def _dispatch(name: str, body: bytes) -> MessageDispatch:
    summary = SUMMARIES[name]
    header = b"\xFF\xFF" + summary.message_number.to_bytes(2, "big")
    return MessageDispatch(
        summary=summary, message_number=decode_message_number(header), body=body
    )


def _kick_body(reason: str) -> bytes:
    # TargetIP and TargetPort first -- the simulator routing the message to
    # itself -- then the agent, the session, and a Variable 2 reason.
    payload = reason.encode("utf-8") + b"\x00"
    return (
        struct.pack("<IH", 0x7F000001, 9000)
        + AGENT.bytes
        + SESSION.bytes
        + struct.pack("<H", len(payload))
        + payload
    )


class KickUserTests(unittest.TestCase):
    def test_it_reads_the_reason(self) -> None:
        parsed = parse_kick_user(_dispatch("KickUser", _kick_body("logged in from another place")))

        self.assertEqual(parsed.reason, "logged in from another place")

    def test_it_skips_the_routing_address_to_find_the_agent(self) -> None:
        # Six bytes of it. Reading from the wrong offset gives a UUID made of
        # an IP address and the first ten bytes of the real one, which is a
        # perfectly well-formed wrong answer.
        parsed = parse_kick_user(_dispatch("KickUser", _kick_body("x")))

        self.assertEqual(parsed.agent_id, AGENT)
        self.assertEqual(parsed.session_id, SESSION)

    def test_an_empty_reason_is_an_empty_string(self) -> None:
        parsed = parse_kick_user(_dispatch("KickUser", _kick_body("")))

        self.assertEqual(parsed.reason, "")

    def test_a_truncated_body_is_an_error(self) -> None:
        with self.assertRaises(MessageDecodeError):
            parse_kick_user(_dispatch("KickUser", b"\x00" * 20))


class LogoutReplyTests(unittest.TestCase):
    def test_it_reads_the_item_list(self) -> None:
        body = AGENT.bytes + SESSION.bytes + bytes([2]) + UUID(int=1).bytes + UUID(int=2).bytes

        parsed = parse_logout_reply(_dispatch("LogoutReply", body))

        self.assertEqual(parsed.item_ids, (UUID(int=1), UUID(int=2)))

    def test_the_single_null_entry_a_real_reply_carries(self) -> None:
        # The block cannot have none, so an empty list is one null UUID.
        body = AGENT.bytes + SESSION.bytes + bytes([1]) + UUID(int=0).bytes

        parsed = parse_logout_reply(_dispatch("LogoutReply", body))

        self.assertEqual(parsed.item_ids, (UUID(int=0),))

    def test_a_truncated_item_list_is_an_error(self) -> None:
        body = AGENT.bytes + SESSION.bytes + bytes([3]) + UUID(int=1).bytes

        with self.assertRaises(MessageDecodeError):
            parse_logout_reply(_dispatch("LogoutReply", body))


class SessionEndTests(unittest.TestCase):
    """What the session does with each, which is the point of parsing them."""

    def test_disable_simulator_ends_the_session(self) -> None:
        session = _live_session()

        session.handle_incoming(_packet("DisableSimulator", b""), now=1.0)

        self.assertEqual(session.close_reason, "simulator disabled the circuit")

    def test_being_kicked_ends_the_session_and_says_why(self) -> None:
        session = _live_session()

        session.handle_incoming(_packet("KickUser", _kick_body("duplicate login")), now=1.0)

        self.assertEqual(session.close_reason, "kicked by the simulator: duplicate login")

    def test_being_kicked_raises_an_alert_a_person_can_see(self) -> None:
        # Ending the session silently leaves the viewer looking broken rather
        # than looking ejected.
        session = _live_session()

        session.handle_incoming(_packet("KickUser", _kick_body("duplicate login")), now=1.0)

        alerts = [event for event in session.events if event.kind == "chat.alert"]
        self.assertTrue(alerts, "a kick should reach the alert path")
        self.assertIn("duplicate login", alerts[-1].detail)

    def test_a_kick_with_an_unreadable_body_still_ends_the_session(self) -> None:
        # Not being able to say why is no reason to stay connected.
        session = _live_session()

        session.handle_incoming(_packet("KickUser", b"\x00" * 8), now=1.0)

        self.assertEqual(session.close_reason, "kicked by the simulator")

    def test_a_logout_reply_does_not_end_the_session_by_itself(self) -> None:
        # It answers a LogoutRequest this client sent; the shutdown drain reads
        # the flag to stop waiting, and treating it as a close would be one.
        session = _live_session()
        body = AGENT.bytes + SESSION.bytes + bytes([1]) + UUID(int=0).bytes

        session.handle_incoming(_packet("LogoutReply", body), now=1.0)

        self.assertIsNone(session.close_reason)
        self.assertTrue(session.logout_acknowledged)


def _live_session():
    from vibestorm.login.models import LoginBootstrap
    from vibestorm.udp.dispatch import MessageDispatcher
    from vibestorm.udp.session import LiveCircuitSession, SessionConfig

    bootstrap = LoginBootstrap(
        agent_id=AGENT,
        session_id=SESSION,
        secure_session_id=UUID(int=99),
        circuit_code=0x12345678,
        sim_ip="127.0.0.1",
        sim_port=9000,
        seed_capability="http://127.0.0.1:9000/caps/seed",
        region_x=256,
        region_y=512,
        message="ok",
    )
    # No unknowns database: these are unit tests and should not write to the
    # shared sqlite file every run.
    return LiveCircuitSession(
        bootstrap,
        MessageDispatcher.from_repo_root(Path.cwd()),
        config=SessionConfig(duration_seconds=1.0, unknowns_db_path=None),
    )


def _packet(name: str, body: bytes) -> bytes:
    """A whole UDP packet carrying one unreliable, unacked message."""
    summary = SUMMARIES[name]
    if summary.frequency == "Low":
        number = b"\xFF\xFF" + summary.message_number.to_bytes(2, "big")
    elif summary.frequency == "Medium":
        number = b"\xFF" + bytes([summary.message_number])
    else:
        number = bytes([summary.message_number])
    # flags, four sequence bytes, extra-header length, then the message.
    return b"\x00" + struct.pack(">I", 1) + b"\x00" + number + body


if __name__ == "__main__":
    unittest.main()
