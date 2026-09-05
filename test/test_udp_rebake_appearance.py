"""What the client does when the simulator says a bake went missing.

`RebakeAvatarTextures` arrived 45 times over the recorded sessions in
`local/unknowns.sqlite3` and was decoded to a name and dropped. It is the
simulator saying: you named a baked texture in `AgentSetAppearance` and I do
not have that asset. Until something answers it, everyone else in the region
sees the avatar as a cloud -- a goal-A visualization failure that happens to
other people's screens rather than this one's.

There is no rasterizer in this client to bake a fresh texture with. What it
can do is assert the appearance it already holds again, which is the fix in
the case where the asset does exist and only the simulator's cache entry went
missing. That re-send has to carry a *higher* serial: the simulator keeps the
last serial it saw and a repeat of it is the stale appearance it already
rejected.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from struct import unpack_from
from uuid import UUID

from vibestorm.login.models import LoginBootstrap
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    MessageDecodeError,
    parse_rebake_avatar_textures,
)
from vibestorm.udp.packet import build_packet, split_packet
from vibestorm.udp.session import LiveCircuitSession, SessionConfig
from vibestorm.udp.template import (
    MessageDispatch,
    decode_message_number,
    load_template_summaries,
    template_path,
)
from vibestorm.udp.zerocode import decode_zerocode

SUMMARIES = load_template_summaries(template_path(Path.cwd()))
AGENT = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SESSION = UUID("11111111-2222-3333-4444-555555555555")
BAKE = UUID("00000000-0000-0000-0000-0000000000aa")


def _dispatch(body: bytes) -> MessageDispatch:
    summary = SUMMARIES["RebakeAvatarTextures"]
    header = b"\xFF\xFF" + summary.message_number.to_bytes(2, "big")
    return MessageDispatch(
        summary=summary, message_number=decode_message_number(header), body=body
    )


class ParseTests(unittest.TestCase):
    def test_it_reads_the_texture_that_went_missing(self) -> None:
        parsed = parse_rebake_avatar_textures(_dispatch(BAKE.bytes))

        self.assertEqual(parsed.texture_id, BAKE)

    def test_a_short_body_is_an_error(self) -> None:
        with self.assertRaises(MessageDecodeError):
            parse_rebake_avatar_textures(_dispatch(b"\x00" * 15))

    def test_it_refuses_a_different_message(self) -> None:
        summary = SUMMARIES["LogoutReply"]
        header = b"\xFF\xFF" + summary.message_number.to_bytes(2, "big")
        wrong = MessageDispatch(
            summary=summary,
            message_number=decode_message_number(header),
            body=BAKE.bytes,
        )

        with self.assertRaises(MessageDecodeError):
            parse_rebake_avatar_textures(wrong)


class SessionResponseTests(unittest.TestCase):
    """The session's answer, which is the reason for parsing it at all."""

    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        self.bootstrap = LoginBootstrap(
            agent_id=AGENT,
            session_id=SESSION,
            secure_session_id=UUID("99999999-8888-7777-6666-555555555555"),
            circuit_code=0x12345678,
            sim_ip="127.0.0.1",
            sim_port=9000,
            seed_capability="http://127.0.0.1:9000/caps/seed",
            region_x=256,
            region_y=512,
            message="ok",
        )

    def _dressed_session(self) -> LiveCircuitSession:
        """A session that has got as far as sending its appearance once."""
        session = LiveCircuitSession(
            self.bootstrap,
            self.dispatcher,
            config=SessionConfig(duration_seconds=1.0, unknowns_db_path=None),
        )
        session.start(10.0)
        session.handshake_reply_sent = True

        movement_body = (
            AGENT.bytes
            + SESSION.bytes
            + bytes.fromhex("0000803f0000004000004040")
            + bytes.fromhex("000080bf000000000000803f")
            + (123456789).to_bytes(8, "little")
            + (42).to_bytes(4, "little")
            + (3).to_bytes(2, "little")
            + b"sim"
        )
        session.handle_incoming(
            build_packet(bytes([0xFF, 0xFF, 0x00, 0xFA]) + movement_body, sequence=41), 10.2
        )

        wearables_body = (
            AGENT.bytes
            + SESSION.bytes
            + (7).to_bytes(4, "little")
            + bytes([1])
            + UUID(int=0x10).bytes
            + UUID(int=0x20).bytes
            + bytes([5])
        )
        session.handle_incoming(
            build_packet(bytes([0xFF, 0xFF, 0x01, 0x7E]) + wearables_body, sequence=42), 10.4
        )
        session.drain_due_packets(11.5)
        assert session.appearance_sent
        return session

    @staticmethod
    def _appearance_serials(packets: list[bytes], dispatcher: MessageDispatcher) -> list[int]:
        serials = []
        for packet in packets:
            dispatched = dispatcher.dispatch(split_packet(decode_zerocode(packet)).message)
            if dispatched.summary.name == "AgentSetAppearance":
                serials.append(unpack_from("<I", dispatched.body, 32)[0])
        return serials

    def _rebake(self, session: LiveCircuitSession, now: float) -> list[bytes]:
        """Deliver one request, and hand back what the client answered with.

        The answer comes straight out of ``handle_incoming`` rather than a
        later drain: a cloud is visible to other people now, so the re-send
        should not wait for the next agent-update tick.
        """
        summary = SUMMARIES["RebakeAvatarTextures"]
        header = bytes([0xFF, 0xFF]) + summary.message_number.to_bytes(2, "big")
        return session.handle_incoming(
            build_packet(header + BAKE.bytes, sequence=int(now * 10)), now
        )

    def test_a_rebake_request_makes_the_client_send_its_appearance_again(self) -> None:
        session = self._dressed_session()

        answer = self._rebake(session, 12.0)

        names = [
            self.dispatcher.dispatch(split_packet(decode_zerocode(p)).message).summary.name
            for p in answer
        ]
        self.assertIn("AgentSetAppearance", names)

    def test_the_re_sent_appearance_carries_a_higher_serial(self) -> None:
        # The simulator keeps the last serial it saw. Re-sending the same one
        # is re-sending the appearance it has already decided is stale, so the
        # avatar stays a cloud while the client believes it has answered. The
        # wearables update above carried serial 7.
        session = self._dressed_session()

        serials = self._appearance_serials(self._rebake(session, 12.0), self.dispatcher)

        self.assertEqual(serials, [8])

    def test_each_further_request_raises_the_serial_again(self) -> None:
        session = self._dressed_session()

        first = self._appearance_serials(self._rebake(session, 12.0), self.dispatcher)
        second = self._appearance_serials(self._rebake(session, 14.0), self.dispatcher)

        self.assertEqual(first, [8])
        self.assertEqual(second, [9])

    def test_an_uploaded_bake_is_re_asserted_under_a_higher_serial_too(self) -> None:
        # The other branch of the appearance drain: when this client has
        # uploaded its own bakes, the serial comes from the upload rather than
        # from the wearables update, and needs the same lift.
        from vibestorm.udp.messages import WearableCacheEntry
        from vibestorm.udp.session import BakedAppearanceOverride

        session = self._dressed_session()
        session.baked_appearance_override = BakedAppearanceOverride(
            texture_entry=b"\x00" * 16,
            wearable_cache_items=(WearableCacheEntry(cache_id=BAKE, texture_index=8),),
            visual_params=b"\x01\x02",
            serial_num=40,
            size=(0.45, 0.6, 1.9),
        )

        serials = self._appearance_serials(self._rebake(session, 12.0), self.dispatcher)

        self.assertEqual(serials, [41])

    def test_it_records_which_texture_the_simulator_wants(self) -> None:
        session = self._dressed_session()

        self._rebake(session, 12.0)

        events = [e for e in session.events if e.kind == "appearance.rebake_requested"]
        self.assertEqual(len(events), 1)
        self.assertIn(str(BAKE), events[0].detail)

    def test_a_rebake_does_not_end_the_session(self) -> None:
        # It is a request, not a disconnection.
        session = self._dressed_session()

        self._rebake(session, 12.0)

        self.assertIsNone(session.close_reason)

    def test_an_unreadable_rebake_is_recorded_and_ignored(self) -> None:
        session = self._dressed_session()

        summary = SUMMARIES["RebakeAvatarTextures"]
        header = bytes([0xFF, 0xFF]) + summary.message_number.to_bytes(2, "big")
        session.handle_incoming(build_packet(header + b"\x00" * 4, sequence=200), 12.0)

        self.assertTrue(session.appearance_sent, "a broken request should not re-arm the send")
        self.assertTrue(
            any(e.kind == "appearance.rebake.decode_error" for e in session.events)
        )


if __name__ == "__main__":
    unittest.main()
