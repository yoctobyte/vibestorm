"""Tests for the GetObjectPhysicsData capability client.

The payload shape and the one-id-per-request limit are both pinned to
OpenSim's handler, because both are easy to "fix" into something broken: the
limit looks like a missed batching optimisation, and the five field names look
like they could be lowercased to match the dataclass.
"""

import asyncio
import re
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.caps.object_physics_client import (
    MAX_OBJECT_IDS_PER_REQUEST,
    ObjectPhysicsClient,
    ObjectPhysicsError,
    parse_object_physics_payload,
)
from vibestorm.world.physics_shape import PHYS_SHAPE_NONE, PHYS_SHAPE_PRIM

_ROOT = Path(__file__).resolve().parents[1]
_BUNCH_OF_CAPS = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "ClientStack" / "Linden"
    / "Caps" / "BunchOfCaps" / "BunchOfCaps.cs"
)

_OBJECT_ID = UUID("625b95ad-f649-435d-acf6-90c4e69f6f07")

#: The exact response observed live on 2026-08-14 for one prim.
_LIVE_PAYLOAD = {
    str(_OBJECT_ID): {
        "PhysicsShapeType": 0,
        "Density": 1000.0,
        "Friction": 0.6,
        "Restitution": 0.5,
        "GravityMultiplier": 1.0,
    }
}


def _handler_body() -> str:
    text = _BUNCH_OF_CAPS.read_text(encoding="utf-8", errors="replace")
    after = text.split("public void GetObjectPhysicsData", 1)[1]
    return after.split("public void GetObjectCost", 1)[0]


class SourcePinTests(unittest.TestCase):
    def test_the_five_field_names_are_the_handler_s(self) -> None:
        if not _BUNCH_OF_CAPS.exists():
            self.skipTest("opensim-source not present")
        emitted = set(re.findall(r'AddElem\("(\w+)"', _handler_body()))

        self.assertEqual(
            emitted,
            {
                "PhysicsShapeType",
                "Density",
                "Friction",
                "Restitution",
                "GravityMultiplier",
            },
        )

    def test_the_outer_map_is_still_closed_inside_the_loop(self) -> None:
        """The reason for the one-id limit, pinned so a fix upstream is noticed.

        If OpenSim ever moves that AddEndMap out of the for loop, batching
        becomes possible and this test is the prompt to revisit it.
        """
        if not _BUNCH_OF_CAPS.exists():
            self.skipTest("opensim-source not present")
        body = _handler_body()

        loop_body = body.split("for (int i = 0", 1)[1]
        # Everything up to the closing brace of the for loop, found by the
        # dedent back to the enclosing indentation level.
        loop_body = loop_body.split("\n                }", 1)[0]

        self.assertGreater(len(loop_body), 200, "failed to isolate the loop body")

        # The bug stated as the asymmetry it is: the loop opens one map per
        # iteration and closes two. The second close belongs to the outer map,
        # which was opened once before the loop.
        opens = len(re.findall(r"AddMap\(", loop_body))
        closes = len(re.findall(r"AddEndMap", loop_body))

        self.assertEqual(opens, 1)
        self.assertEqual(closes, 2)

    def test_the_client_sends_one_id(self) -> None:
        self.assertEqual(MAX_OBJECT_IDS_PER_REQUEST, 1)


class ParseTests(unittest.TestCase):
    def test_the_live_payload_decodes(self) -> None:
        parsed = parse_object_physics_payload(_LIVE_PAYLOAD)

        self.assertEqual(list(parsed), [_OBJECT_ID])
        properties = parsed[_OBJECT_ID]
        self.assertEqual(properties.shape_type, PHYS_SHAPE_PRIM)
        self.assertEqual(properties.density, 1000.0)
        self.assertEqual(properties.friction, 0.6)
        self.assertEqual(properties.restitution, 0.5)
        self.assertEqual(properties.gravity_multiplier, 1.0)
        # Every value is OpenSim's default, so nothing should read as set.
        self.assertEqual(properties.non_default_fields(), ())

    def test_fields_are_not_transposed(self) -> None:
        # Four adjacent floats in a fixed order. The live-payload test happens
        # to catch a friction/restitution swap because OpenSim's defaults for
        # those two differ (0.6 vs 0.5) — but density and gravity are the
        # values a prim is most likely to have changed, and the defaults there
        # are 1000 and 1. Spread all four far apart so no pairing survives.
        parsed = parse_object_physics_payload(
            {
                str(_OBJECT_ID): {
                    "PhysicsShapeType": PHYS_SHAPE_NONE,
                    "Density": 11.0,
                    "Friction": 22.0,
                    "Restitution": 33.0,
                    "GravityMultiplier": 44.0,
                }
            }
        )

        properties = parsed[_OBJECT_ID]
        self.assertEqual(properties.density, 11.0)
        self.assertEqual(properties.friction, 22.0)
        self.assertEqual(properties.restitution, 33.0)
        self.assertEqual(properties.gravity_multiplier, 44.0)
        self.assertFalse(properties.is_collidable)

    def test_an_empty_map_is_a_prim_the_sim_does_not_have(self) -> None:
        # The live shape for an id that is not a SceneObjectPart -- an avatar
        # id, for instance. Not an error.
        self.assertEqual(parse_object_physics_payload({}), {})

    def test_a_partial_entry_is_skipped_rather_than_defaulted(self) -> None:
        # OpenSim writes all five or omits the prim, so a partial entry means
        # something changed upstream; defaulting would hide it.
        parsed = parse_object_physics_payload(
            {str(_OBJECT_ID): {"PhysicsShapeType": 0, "Density": 1000.0}}
        )

        self.assertEqual(parsed, {})

    def test_a_non_map_payload_raises(self) -> None:
        with self.assertRaises(ObjectPhysicsError):
            parse_object_physics_payload([1, 2, 3])


class FetchTests(unittest.TestCase):
    def _client_with(self, payload, record: list):
        client = ObjectPhysicsClient(timeout_seconds=1.0)
        from vibestorm.caps import object_physics_client as module

        def fake_post(self, url, value, port, agent):  # type: ignore[no-untyped-def]
            record.append(value)
            if isinstance(payload, Exception):
                raise payload
            return payload

        module.CapabilityClient._post_capability_value_sync = fake_post  # type: ignore[assignment]
        return client

    def setUp(self) -> None:
        from vibestorm.caps.client import CapabilityClient

        self._original = CapabilityClient._post_capability_value_sync

    def tearDown(self) -> None:
        from vibestorm.caps.client import CapabilityClient

        CapabilityClient._post_capability_value_sync = self._original  # type: ignore[assignment]

    def test_fetch_sends_exactly_one_id(self) -> None:
        sent: list = []
        client = self._client_with(_LIVE_PAYLOAD, sent)

        result = asyncio.run(client.fetch("http://sim/caps/phys", _OBJECT_ID))

        self.assertIsNotNone(result)
        self.assertEqual(sent, [{"object_ids": [_OBJECT_ID]}])

    def test_a_prim_the_sim_omits_is_none_not_an_error(self) -> None:
        client = self._client_with({}, [])

        self.assertIsNone(asyncio.run(client.fetch("http://sim/caps/phys", _OBJECT_ID)))

    def test_fetch_many_never_batches(self) -> None:
        # The whole point. One request per id, however many are asked for.
        sent: list = []
        client = self._client_with(_LIVE_PAYLOAD, sent)
        ids = [UUID(int=n) for n in (1, 2, 3)]

        asyncio.run(client.fetch_many("http://sim/caps/phys", ids))

        self.assertEqual(len(sent), 3)
        for request in sent:
            self.assertEqual(len(request["object_ids"]), 1)

    def test_fetch_many_returns_only_what_the_sim_answered(self) -> None:
        sent: list = []
        client = self._client_with(_LIVE_PAYLOAD, sent)

        # Each request returns the live payload, keyed by _OBJECT_ID, so the
        # two ids that are not it must not appear in the result.
        found = asyncio.run(
            client.fetch_many("http://sim/caps/phys", [UUID(int=1), _OBJECT_ID])
        )

        self.assertEqual(list(found), [_OBJECT_ID])

    def test_a_parse_error_is_wrapped_with_the_object_id(self) -> None:
        # This is the shape a batched request produces against a real sim, so
        # the message needs to say which prim was being asked about.
        from xml.etree.ElementTree import ParseError

        client = self._client_with(ParseError("mismatched tag"), [])

        with self.assertRaises(ObjectPhysicsError) as caught:
            asyncio.run(client.fetch("http://sim/caps/phys", _OBJECT_ID))

        self.assertIn(str(_OBJECT_ID), str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class CensusReportTests(unittest.TestCase):
    """format_object_physics must distinguish three different silences."""

    def _session(self, physics: dict, attempted: set):
        class _Session:
            object_physics = physics
            object_physics_attempted = attempted

        return _Session()

    def test_not_fetching_is_not_the_same_as_finding_nothing(self) -> None:
        from vibestorm.world.census import format_object_physics

        self.assertEqual(
            format_object_physics(self._session({}, set())), ["physics=not fetched"]
        )

    def test_asked_and_got_nothing_is_reported_as_such(self) -> None:
        from vibestorm.world.census import format_object_physics

        lines = format_object_physics(self._session({}, {UUID(int=1), UUID(int=2)}))

        self.assertEqual(lines[0], "physics prims=0 no_data=2")

    def test_all_defaults_is_stated_rather_than_left_blank(self) -> None:
        # The live result for this region. A blank line here would read like a
        # decoder that produced nothing.
        from vibestorm.world.census import format_object_physics

        properties = parse_object_physics_payload(_LIVE_PAYLOAD)
        lines = format_object_physics(
            self._session(properties, {_OBJECT_ID})
        )

        self.assertIn("physics prims=1 no_data=0", lines)
        self.assertIn("physics shape[prim]=1", lines)
        self.assertIn("physics material=all prims at OpenSim defaults", lines)

    def test_a_non_default_prim_is_named_with_its_differences(self) -> None:
        from vibestorm.world.census import format_object_physics

        properties = parse_object_physics_payload(
            {
                str(_OBJECT_ID): {
                    "PhysicsShapeType": PHYS_SHAPE_NONE,
                    "Density": 2500.0,
                    "Friction": 0.6,
                    "Restitution": 0.5,
                    "GravityMultiplier": 1.0,
                }
            }
        )
        lines = format_object_physics(self._session(properties, {_OBJECT_ID}))

        self.assertIn("physics shape[none]=1", lines)
        self.assertTrue(
            any("density=2500" in line for line in lines), lines
        )
        self.assertFalse(any("friction" in line for line in lines), lines)


class PendingSelectorTests(unittest.TestCase):
    def test_an_attempted_prim_is_not_asked_about_again(self) -> None:
        # Without this the avatar id -- which answers as an empty map -- would
        # be re-requested on every tick of the session loop, forever.
        from vibestorm.udp.session import _next_pending_physics_object_id

        class _View:
            objects = {UUID(int=1): object(), UUID(int=2): object()}

        class _Session:
            world_view = _View()
            object_physics: dict = {}
            object_physics_attempted = {UUID(int=1)}

        self.assertEqual(_next_pending_physics_object_id(_Session()), UUID(int=2))

    def test_nothing_left_to_ask_about_returns_none(self) -> None:
        from vibestorm.udp.session import _next_pending_physics_object_id

        class _View:
            objects = {UUID(int=1): object()}

        class _Session:
            world_view = _View()
            object_physics: dict = {}
            object_physics_attempted = {UUID(int=1)}

        self.assertIsNone(_next_pending_physics_object_id(_Session()))


class LoopBlockingTests(unittest.TestCase):
    """The diagnostic fetches run inside the receive loop, so they must be brief.

    An awaited fetch there is time the session spends not reading UDP and not
    sending AgentUpdate. For an asset the client needs, waiting is correct; for
    a diagnostic the user asked for only as a report, a hung capability must
    not be able to stall the circuit per prim.
    """

    def test_the_diagnostic_timeout_is_shorter_than_the_asset_timeout(self) -> None:
        from vibestorm.udp.session import DIAGNOSTIC_CAP_TIMEOUT_SECONDS

        self.assertLess(DIAGNOSTIC_CAP_TIMEOUT_SECONDS, 10.0)
        self.assertGreater(DIAGNOSTIC_CAP_TIMEOUT_SECONDS, 0.5)

    def test_both_diagnostic_fetches_use_it(self) -> None:
        # Two call sites, and a hard-coded 10.0 at either would reintroduce
        # exactly the stall this constant exists to bound.
        session_py = (
            Path(__file__).resolve().parents[1]
            / "src" / "vibestorm" / "udp" / "session.py"
        ).read_text(encoding="utf-8")

        self.assertEqual(
            session_py.count("timeout_seconds=DIAGNOSTIC_CAP_TIMEOUT_SECONDS"), 2
        )
