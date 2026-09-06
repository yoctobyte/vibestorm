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
