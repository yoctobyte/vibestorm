"""Claims that rest on OpenSim's source, pinned to the source they rest on.

A live measurement says *what* a simulator did. It does not say why, and a
claim about why -- the kind that goes into the documentation project -- is
only as good as the code it was read from. This file is the difference
between "we observed this once against 0.9.3.1" and "we read the guard": each
test finds the lines the claim quotes in a committed copy of the OpenSim
source under `referencedocs/`, and fails if a later copy drops them.

It cannot tell whether the *reasoning* is still right, only whether the lines
are still there. That is the whole ambition. A claim whose source has moved
underneath it must be re-read by a person, and a green test that no longer
matches anything is the one outcome worth ruling out.

`referencedocs/` is committed, so these never depend on a checkout of the
full OpenSim tree; if a file is missing the test skips rather than failing,
because a missing pin is a gap in evidence and not a broken client.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "referencedocs"


def _source(*parts: str) -> str | None:
    path = REFERENCE_ROOT.joinpath(*parts)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


class SeedCapabilityGatesInitialDataTests(unittest.TestCase):
    """Why a child circuit that does everything right still gets no terrain.

    Measured first: a circuit to the region next door that sends
    `UseCircuitCode`, answers every `RegionHandshake`, acks everything and
    names a camera inside the neighbour received 0 terrain patches in forty
    seconds, and 256 once an HTTP POST had been made to the seed capability
    that arrived beside `EnableSimulator`. Nothing on the wire explains that
    gap. These are the three lines that do.
    """

    def setUp(self) -> None:
        self.presence = _source("Scenes", "ScenePresence.cs")
        self.caps = _source("Caps", "BunchOfCaps", "BunchOfCaps.cs")
        self.client = _source("UDP", "LLClientView.cs")

    def test_initial_data_waits_for_the_handshake_reply(self) -> None:
        if self.presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertRegex(
            self.presence,
            r"private void SendInitialData\(\)\s*\{[^}]*?NeedInitialData < 2\)\s*\n\s*return;",
            "SendInitialData no longer returns early on NeedInitialData < 2",
        )

    def test_only_the_handshake_reply_opens_that_gate(self) -> None:
        # NeedInitialData = 2 is what the guard above waits for, and this is
        # the one place it is set. So a circuit that never answers the
        # handshake never gets past the first return -- which is why the
        # reply goes out every time rather than once.
        if self.presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertRegex(
            self.presence,
            r"public void RegionHandShakeReply\s*\(IClientAPI client\)[\s\S]*?"
            r"m_gotRegionHandShake = true;\s*\n\s*NeedInitialData = 2;",
            "RegionHandShakeReply no longer sets NeedInitialData = 2",
        )

    def test_and_then_it_waits_for_the_seed_capability(self) -> None:
        # The half that is not on the wire at all.
        if self.presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertIn(
            "if ((flags & (uint)ViewerFlags.SentSeeds) == 0)",
            self.presence,
            "SendInitialData no longer waits for ViewerFlags.SentSeeds",
        )

    def test_the_flag_it_waits_for_is_the_caps_flag(self) -> None:
        # Two different enums with the same member name; the claim is only
        # true because this line joins them.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertRegex(
            self.client,
            r"if\(\(cap\.Flags & Caps\.CapsFlags\.SentSeeds\) != 0\)\s*\n"
            r"\s*ret \|= \(uint\)ViewerFlags\.SentSeeds;",
            "GetViewerCaps no longer maps CapsFlags.SentSeeds to ViewerFlags.SentSeeds",
        )

    def test_and_the_caps_flag_is_set_by_the_seed_request_handler(self) -> None:
        # The end of the chain: the flag is set by the handler for an HTTP
        # POST, so no amount of UDP can set it.
        if self.caps is None:
            self.skipTest("referencedocs/Caps/BunchOfCaps/BunchOfCaps.cs not present")
        self.assertIn(
            "m_HostCapsObj.Flags |= Caps.CapsFlags.SentSeeds;",
            self.caps,
            "BunchOfCaps no longer sets CapsFlags.SentSeeds",
        )

    def test_that_flag_is_set_where_the_seed_request_is_served(self) -> None:
        if self.caps is None:
            self.skipTest("referencedocs/Caps/BunchOfCaps/BunchOfCaps.cs not present")
        seed_handler = re.search(
            r"public void SeedCapRequest\([\s\S]*?\n        \}",
            self.caps,
        )
        self.assertIsNotNone(seed_handler, "SeedCapRequest is no longer in BunchOfCaps")
        self.assertIn(
            "m_HostCapsObj.Flags |= Caps.CapsFlags.SentSeeds;",
            seed_handler.group(0),
            "SentSeeds is no longer set inside SeedCapRequest",
        )


class InitialDataIsAlsoDelayedTests(unittest.TestCase):
    """And then it waits a few heartbeats more, which is not a bug either.

    Worth pinning separately: a client that has done everything right still
    sees a pause before the terrain starts, and the temptation is to go
    looking for what it did wrong.
    """

    def test_it_gives_the_viewer_extra_heartbeats(self) -> None:
        presence = _source("Scenes", "ScenePresence.cs")
        if presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertRegex(
            presence,
            r"// give some extra time to make sure viewers did process seeds\s*\n"
            r"\s*if \(\+\+NeedInitialData < 6\)",
            "SendInitialData no longer delays after the seeds are sent",
        )


class ResendsAreSecondsApartAndNeverStopTests(unittest.TestCase):
    """What sizes the bounded memory of reliable sequence numbers.

    `vibestorm.udp.recent` remembers a window of sequence numbers so a resend
    can be told from a new packet, and the size of that window is an argument
    rather than a measurement: it has to outlast the gap between two copies of
    the same packet, and it does not have to outlast the whole chain, because
    a copy that arrives refreshes the entry. Both halves of that rest on how
    OpenSim retransmits, and neither is visible on the wire in a healthy
    session -- a viewer that acks everything never sees a resend at all. So
    the lines are pinned instead.
    """

    def setUp(self) -> None:
        self.client = _source("UDP", "LLUDPClient.cs")
        self.server = _source("UDP", "LLUDPServer.cs")

    def test_the_retransmission_timeout_is_a_second_and_capped_at_three(self) -> None:
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLUDPClient.cs not present")
        self.assertRegex(
            self.client,
            r"private readonly int m_defaultRTO = 1000;[\s\S]{0,200}?"
            r"private readonly int m_maxRTO = 3000;\s*\n"
            r"\s*private readonly int m_minRTO = 250;",
            "the RTO defaults and bounds have moved",
        )

    def test_and_it_is_a_ceiling_rather_than_a_starting_point(self) -> None:
        # The one thing that would break the sizing: an RTO that doubles on
        # every failed attempt, so copies of the same packet drift minutes
        # apart. `UpdateRoundTrip` is a *clamp* -- five times the measured
        # ping, held inside [minRTO, maxRTO] -- and it is called on a ping,
        # not on a resend.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLUDPClient.cs not present")
        self.assertRegex(
            self.client,
            r"public void UpdateRoundTrip\(int p\)\s*\{\s*\n"
            r"\s*p \*= 5;\s*\n"
            r"\s*if\(\s*p> m_maxRTO\)\s*\n"
            r"\s*p = m_maxRTO;\s*\n"
            r"\s*else if\(p < m_minRTO\)\s*\n"
            r"\s*p = m_minRTO;\s*\n\s*\n"
            r"\s*m_RTO = p;",
            "UpdateRoundTrip no longer clamps the RTO between the two bounds",
        )

    def test_nothing_else_moves_the_timeout_at_all(self) -> None:
        # And the clamp is the whole story only if it is the only writer.
        # Three mentions: the field, the constructor's default, the clamp.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLUDPClient.cs not present")
        assignments = re.findall(r"m_RTO\s*=", self.client)
        self.assertEqual(
            len(assignments),
            2,
            "something other than the constructor and UpdateRoundTrip now writes m_RTO",
        )

    def test_a_resend_carries_the_same_sequence_number(self) -> None:
        # Which is why remembering sequence numbers recognises a resend at
        # all: the resend path ors a flag into the first byte of the buffer
        # that was already built and sends that same buffer again, so every
        # copy carries the number the original did.
        if self.server is None:
            self.skipTest("referencedocs/UDP/LLUDPServer.cs not present")
        self.assertRegex(
            self.server,
            r"public void ResendUnacked\(OutgoingPacket outgoingPacket\)[\s\S]*?"
            r"outgoingPacket\.Buffer\.Data\[0\] = \(byte\)\("
            r"outgoingPacket\.Buffer\.Data\[0\] \| Helpers\.MSG_RESENT\);",
            "ResendUnacked no longer resends the original buffer with MSG_RESENT",
        )

    def test_and_the_simulator_never_gives_up_on_one(self) -> None:
        # The other half of the sizing, and the reason an arriving copy has
        # to refresh the entry rather than let it age out: there is no
        # attempt limit anywhere in the expiry path. Everything older than
        # one RTO is resent, every pass, for as long as the client is there.
        # `ResendCount` is incremented and then read by nothing that stops.
        if self.server is None:
            self.skipTest("referencedocs/UDP/LLUDPServer.cs not present")
        self.assertRegex(
            self.server,
            r"List<OutgoingPacket> expiredPackets = udpClient\.NeedAcks\.GetExpiredPackets\("
            r"udpClient\.m_RTO\);\s*\n\s*\n"
            r"\s*if \(expiredPackets != null\)\s*\n"
            r"\s*\{\s*\n"
            r"\s*for \(int i = 0; i < expiredPackets\.Count; \+\+i\)\s*\n"
            r"\s*expiredPackets\[i\]\.UnackedMethod\(expiredPackets\[i\]\);",
            "the expiry loop has changed -- check whether it now gives up on a packet",
        )

    def test_the_only_timeout_is_the_client_falling_silent(self) -> None:
        # The 60 seconds that does exist is easy to mistake for a give-up on
        # the packet. It is not: it is measured from the last packet the
        # simulator *received*, so it fires when the viewer goes quiet, and a
        # viewer that is talking keeps its unacked packets alive indefinitely.
        if self.server is None:
            self.skipTest("referencedocs/UDP/LLUDPServer.cs not present")
        self.assertRegex(
            self.server,
            r"m_ackTimeout = 1000 \* config\.GetInt\(\"AckTimeout\", 60\);",
            "the ack timeout default is no longer 60 seconds",
        )
        self.assertRegex(
            self.server,
            r"\(Environment\.TickCount & Int32\.MaxValue\) - udpClient\.TickLastPacketReceived "
            r"> timeoutTicks",
            "the ack timeout is no longer measured from the last packet received",
        )


class ThreeThingsSendAHandshakeTests(unittest.TestCase):
    """Where a `RegionHandshake` can come from, which explains a measurement.

    Measured: four `RegionHandshake` packets in ninety seconds on a root
    circuit that answered every one, which looked like a resend storm and is
    not. A resend chain runs at one a second -- the default RTO -- and an
    earlier measurement on a child circuit that did *not* answer saw exactly
    that, twenty-nine copies in thirty seconds.

    Four in ninety is three different code paths each sending one, plus room
    for a single resend. The paths are pinned below because the count is the
    whole argument: a fourth sender, or one of these becoming periodic, turns
    "this is normal" into "this is a bug" without a line of our own changing.

    Finding them takes a dotted grep. `SendRegionHandshake()` on its own also
    matches the method's own definition and a call inside a comment block,
    which is how the first version of this test got the number wrong.
    """

    FILES = (
        ("Scenes", "ScenePresence.cs"),
        ("UDP", "LLClientView.cs"),
        ("UDP", "LLUDPServer.cs"),
    )

    def setUp(self) -> None:
        self.presence = _source("Scenes", "ScenePresence.cs")
        self.client = _source("UDP", "LLClientView.cs")
        self.server = _source("UDP", "LLUDPServer.cs")

    def test_exactly_three_places_call_it(self) -> None:
        sources = [_source(*parts) for parts in self.FILES]
        if any(source is None for source in sources):
            self.skipTest("referencedocs/ not fully present")
        calls = sum(source.count(".SendRegionHandshake()") for source in sources)
        self.assertEqual(calls, 3, "the set of handshake senders has changed")

    def test_the_first_is_the_circuit_being_created(self) -> None:
        # The one that answers "why does a handshake arrive before anything
        # else": accepting a `UseCircuitCode` sends one, for a login rather
        # than a teleport.
        if self.server is None:
            self.skipTest("referencedocs/UDP/LLUDPServer.cs not present")
        self.assertRegex(
            self.server,
            r"if\(aCircuit\.teleportFlags <= 0\)\s*\n\s*client\.SendRegionHandshake\(\);",
            "the circuit-creation handshake has moved or changed its guard",
        )

    def test_and_a_repeated_use_circuit_code_does_not_get_another(self) -> None:
        # Which is what stops that first one being periodic, and it is worth
        # pinning because this client could plausibly send `UseCircuitCode`
        # twice. Once a client exists for the endpoint the packet never
        # reaches the handler; while one is still being made the resend is
        # acked and dropped by name.
        if self.server is None:
            self.skipTest("referencedocs/UDP/LLUDPServer.cs not present")
        self.assertRegex(
            self.server,
            r"if \(packet\.Type == PacketType\.UseCircuitCode\) // ignore viewer resends"
            r"[\s\S]{0,200}?SendAckImmediate\(endPoint, packet\.Header\.Sequence\);",
            "a UseCircuitCode arriving mid-creation is no longer ignored",
        )
        self.assertRegex(
            self.server,
            r"if \(!Scene\.TryGetClient\(endPoint, out IClientAPI client\)\)\s*\n"
            r"\s*\{\s*\n"
            r"\s*// UseCircuitCode handling\s*\n"
            r"\s*if \(packet\.Type == PacketType\.UseCircuitCode\)",
            "the UseCircuitCode path is no longer gated on there being no client yet",
        )

    def test_the_second_is_completing_a_movement_and_fires_once_per_entry(self) -> None:
        if self.presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertRegex(
            self.presence,
            r"if \(!m_gotCrossUpdate\)\s*\n"
            r"\s*\{\s*\n"
            r"\s*m_gotRegionHandShake = false; // allow it if not a crossing\s*\n"
            r"\s*ControllingClient\.SendRegionHandshake\(\);",
            "CompleteMovement no longer sends the handshake the same way",
        )

    def test_the_third_is_the_initial_data_and_is_gated_and_once(self) -> None:
        # `NeedInitialData = -1` before the work means the body runs once per
        # handshake reply, and the flag test means it does not run at all for
        # a viewer that never asked for the PBR terrain capability. So this is
        # at most one extra handshake, not a periodic one.
        if self.presence is None:
            self.skipTest("referencedocs/Scenes/ScenePresence.cs not present")
        self.assertRegex(
            self.presence,
            r"NeedInitialData = -1;[\s\S]*?"
            r"if \(\(flags & \(uint\)\(ViewerFlags\.TPBR \| ViewerFlags\.SentTPBR\)\) "
            r"== \(uint\)ViewerFlags\.TPBR\)\s*\n"
            r"\s*ControllingClient\.SendRegionHandshake\(\);",
            "SendInitialData's handshake is no longer once and behind the TPBR flag",
        )

    def test_and_the_one_in_the_packet_handler_is_commented_out(self) -> None:
        # The fourth mention, and not a sender. `HandleUseCircuitCode` in
        # LLClientView is an empty handler wrapped around a comment, because
        # the server-side one above does the work.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertRegex(
            self.client,
            r"private static void HandleUseCircuitCode\(LLClientView c, Packet Pack\)\s*\n"
            r"\s*\{\s*\n"
            r"\s*/\*[\s\S]*?SendRegionHandshake\(\); // possible someone returning\s*\n"
            r"\s*\*/\s*\n"
            r"\s*\}",
            "HandleUseCircuitCode is no longer an empty handler around a comment",
        )


class CoarseLocationHeightTests(unittest.TestCase):
    """One byte for a region's whole vertical range, and what it costs.

    `CoarseLocationUpdate` spends three bytes on an avatar's whereabouts. X
    and Y are whole metres, which a 256 m region fits exactly; Z has to cover
    the height a region allows, and does not fit at all. The claim the client
    reads the byte by -- four metres a step, and a zero that means two
    different things -- is one line of the encoder, so it is pinned to that
    line.

    The bug it was found by: the viewer read the byte as metres and put an
    avatar standing at 25.9 m at 6.0, then reported it underwater in a region
    whose sea is at 20 m while drawing it correctly on a hilltop.
    """

    def setUp(self) -> None:
        self.client = _source("UDP", "LLClientView.cs")

    def test_the_height_byte_is_a_quarter_of_the_metres(self) -> None:
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertRegex(
            self.client,
            r"data\[pos\+\+\] = CoarseLocations\[i\]\.Z > 1024 \? \(byte\)0 : "
            r"\(byte\)\(CoarseLocations\[i\]\.Z \* 0\.25f\);",
            "SendCoarseLocationUpdate no longer scales Z by 0.25",
        )

    def test_and_x_and_y_are_not_scaled_at_all(self) -> None:
        # The asymmetry is the part that is easy to get wrong: two of the
        # three bytes are plain metres and the third is not.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertRegex(
            self.client,
            r"data\[pos\+\+\] = \(byte\)CoarseLocations\[i\]\.X;\s*\n"
            r"\s*data\[pos\+\+\] = \(byte\)CoarseLocations\[i\]\.Y;",
            "SendCoarseLocationUpdate no longer sends X and Y as plain metres",
        )


class TimeDilationTests(unittest.TestCase):
    """Why `dilation=64512` is a region running at 0.98 of real time.

    The field is a U16 in every object-update header. Nothing on the wire says
    it is a fraction; these are the lines that do.

    What is *not* pinned is the conversion itself: `Utils` belongs to
    libopenmetaverse and is not in this copy, so the exact denominator is a
    convention rather than something read. The client only uses the ratio to
    print, where one part in 65,535 cannot matter.
    """

    def setUp(self) -> None:
        self.client = _source("UDP", "LLClientView.cs")

    def test_the_header_field_is_a_zero_to_one_float_in_a_ushort(self) -> None:
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertIn(
            "Utils.FloatZeroOneToushort(m_scene.TimeDilation)",
            self.client,
            "the object-update header no longer packs TimeDilation as a zero-to-one ushort",
        )

    def test_and_the_other_sender_says_the_range_out_loud(self) -> None:
        # The same value, packed by a function that names its bounds. Two
        # senders agreeing is what makes the range a fact rather than a guess
        # about what a helper called `FloatZeroOneToushort` does.
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertIn(
            "Utils.FloatToUInt16(m_scene.TimeDilation, 0.0f, 1.0f)",
            self.client,
            "no sender states TimeDilation's 0..1 range explicitly any more",
        )


class ChatLengthTests(unittest.TestCase):
    """Why a long chat line is a hitch nobody will see, and not a bomb.

    Chat arrives from other avatars and from scripts, so its length is not
    this client's to choose, and `ChatFromSimulator` carries the message in a
    Variable 2 field -- the wire allows 65,535 bytes. The ticker wraps a whole
    entry before deciding how much of it fits, so the work is linear in the
    message and the display is not: measured with the viewer's own font at
    600 px wide, 65,535 characters wrap to 729 rows in 207 ms, of which only
    the last boxful is ever drawn -- `visible_rows` gathers from the newest
    backwards and stops, but it stops *after* `wrap_entry` has built every row
    of the entry it is reading.

    That would be worth fixing if it could happen. It cannot, from OpenSim:
    the message goes through `Util.StringToBytes1024` before the two-byte
    length is written, so a kilobyte is the whole field. At that size the same
    measurement is **4.4 ms** -- comfortably inside one frame at 30 fps, and
    two orders of magnitude away from mattering.

    So this pin is the reason the ticker was left alone, and it is here rather
    than in a comment because it is the kind of reason that stops being true
    without anybody editing the file it is written in.
    """

    def setUp(self) -> None:
        self.client = _source("UDP", "LLClientView.cs")

    def test_chat_goes_out_through_the_kilobyte_limited_writer(self) -> None:
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertIn(
            "byte[] msg = Util.StringToBytes1024(message);",
            self.client,
            "chat is no longer bounded by StringToBytes1024 before it is sent",
        )

    def test_and_the_length_it_writes_is_the_bounded_one(self) -> None:
        """The bound only holds if the length written is that array's own.

        A writer that clamped the bytes but then wrote the *original* string's
        length would be a different bug and a worse one, so the two lines are
        pinned together rather than the first one alone.
        """
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        start = self.client.index("byte[] msg = Util.StringToBytes1024(message);")
        window = self.client[start : start + 400]
        self.assertIn("len = msg.Length;", window)
        self.assertIn("data[pos++] = (byte)len;", window)
        self.assertIn("data[pos++] = (byte)(len >> 8);", window)


class FloatingTextLengthTests(unittest.TestCase):
    """Why 254 characters is the whole of a prim's floating text.

    The client truncates labels there before rasterising them, and the reason
    is not a rendering budget picked out of the air: it is the length prefix
    the simulator writes. One byte.
    """

    def setUp(self) -> None:
        self.client = _source("UDP", "LLClientView.cs")
        self.encoder = _source("UDP", "LLUDPZeroEncoder.cs")

    def test_floating_text_goes_out_through_the_short_limited_writer(self) -> None:
        if self.client is None:
            self.skipTest("referencedocs/UDP/LLClientView.cs not present")
        self.assertIn(
            "zc.AddShortLimitedUTF8(osUTF8PartText);",
            self.client,
            "a prim's floating text is no longer written by AddShortLimitedUTF8",
        )

    def test_and_that_writer_prefixes_it_with_a_single_byte(self) -> None:
        if self.encoder is None:
            self.skipTest("referencedocs/UDP/LLUDPZeroEncoder.cs not present")
        self.assertRegex(
            self.encoder,
            r"public unsafe void AddShortLimitedUTF8\(osUTF8 str\)[\s\S]*?"
            r"AddByte\(\(byte\)\(len \+ 1\)\); // add null",
            "AddShortLimitedUTF8 no longer writes a one-byte length prefix",
        )


class TextureUploadFallsThroughToTypeZeroTests(unittest.TestCase):
    """Why a texture is the one thing this capability types correctly by accident.

    `NewFileAgentInventory` is documented in this client as mistyping
    anything outside a short list of `inventory_type` strings, because
    `UploadCompleteHandler` branches on those strings and leaves both types
    at their initial value otherwise. That is right, and for a texture the
    initial value is the answer: `AssetType.Texture` and
    `InventoryType.Texture` are both 0.

    So uploading a texture through this capability works, and works for a
    reason nobody chose. The claim rests entirely on two initialisers and on
    the absence of a branch, and "the absence of a branch" is exactly the
    kind of claim that rots quietly -- add `else if (inventoryType ==
    "texture")` upstream and this client's silence becomes wrong without
    anything failing. Hence the pin.
    """

    def setUp(self) -> None:
        self.source = _source("Caps", "BunchOfCaps", "BunchOfCaps.cs")
        if self.source is None:
            self.skipTest("referencedocs/Caps/BunchOfCaps/BunchOfCaps.cs is not committed")

    def test_both_types_start_at_zero(self) -> None:
        self.assertIn("sbyte assType = 0;", self.source)
        self.assertIn("sbyte inType = 0;", self.source)

    def test_the_branch_list_is_the_one_this_client_records(self) -> None:
        """Every string the handler actually switches on, read from the source
        rather than from this client's own constant."""
        branches = set(re.findall(r'inventoryType\s*==\s*"([a-z]+)"', self.source))
        self.assertEqual(
            branches,
            {"sound", "snapshot", "animation", "animset", "wearable", "object"},
        )

    def test_nothing_branches_on_texture(self) -> None:
        self.assertNotIn("texture", re.findall(r'inventoryType\s*==\s*"([a-z]+)"', self.source))

    def test_the_texture_asset_is_stored_without_being_looked_at(self) -> None:
        """And the other half of the same claim: the bytes are not inspected,
        so nothing server-side enforces the dimensions `encode_j2k` rounds
        to. That cap is a convention among readers, not a rule."""
        self.assertIn("textureAsset.Data = texture_list[i].AsBinary();", self.source)

