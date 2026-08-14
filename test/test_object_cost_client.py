"""Tests for the GetObjectCost capability client.

The interesting cases are the two ways this handler differs from its neighbour
`GetObjectPhysicsData`, both pinned to source and both confirmed live:
batching works, and "nothing resolved" arrives dressed as a real answer.
"""

import asyncio
import re
import unittest
from pathlib import Path
from uuid import UUID

from vibestorm.caps.object_cost_client import (
    FILLER_OBJECT_ID,
    ObjectCostClient,
    ObjectCostError,
    parse_object_cost_payload,
)

_ROOT = Path(__file__).resolve().parents[1]
_BUNCH_OF_CAPS = (
    _ROOT / "opensim-source" / "OpenSim" / "Region" / "ClientStack" / "Linden"
    / "Caps" / "BunchOfCaps" / "BunchOfCaps.cs"
)

_OBJECT_ID = UUID("d7f47f7e-4328-4d17-a665-19feaec7b1e9")

#: Observed live 2026-08-14 for one prim.
_LIVE_PAYLOAD = {
    str(_OBJECT_ID): {
        "linked_set_resource_cost": 1.0,
        "resource_cost": 1.0,
        "physics_cost": 1.0,
        "linked_set_physics_cost": 1.0,
        "resource_limiting_type": "legacy",
    }
}

#: Observed live for one id that names no prim. Note it is *not* an empty map.
_LIVE_FILLER_PAYLOAD = {
    str(FILLER_OBJECT_ID): {
        "linked_set_resource_cost": 0,
        "resource_cost": 0,
        "physics_cost": 0,
        "linked_set_physics_cost": 0,
        "resource_limiting_type": "legacy",
    }
}


def _handler_body() -> str:
    text = _BUNCH_OF_CAPS.read_text(encoding="utf-8", errors="replace")
    after = text.split("public void GetObjectCost", 1)[1]
    return after.split("public struct AttachmentScriptInfo", 1)[0]


class SourcePinTests(unittest.TestCase):
    def test_the_field_names_are_the_handler_s(self) -> None:
        if not _BUNCH_OF_CAPS.exists():
            self.skipTest("opensim-source not present")
        emitted = set(re.findall(r'AddElem\("(\w+)"', _handler_body()))

        self.assertEqual(
            emitted,
            {
                "linked_set_resource_cost",
                "resource_cost",
                "physics_cost",
                "linked_set_physics_cost",
                "resource_limiting_type",
            },
        )

    def test_this_handler_closes_its_outer_map_outside_the_loop(self) -> None:
        """Why batching is allowed here and not for GetObjectPhysicsData.

        Same file, same request shape, opposite answer — so the one-id limit
        next door belongs to that handler, not to the family. If this ever
        regresses to the physics handler's shape, batching breaks silently and
        this test is the warning.
        """
        if not _BUNCH_OF_CAPS.exists():
            self.skipTest("opensim-source not present")
        body = _handler_body()

        loop_body = body.split("for (int i = 0", 1)[1].split("\n                }", 1)[0]

        self.assertGreater(len(loop_body), 200, "failed to isolate the loop body")

        # Balanced inside the loop: one map opened per prim, one closed.
        self.assertEqual(len(re.findall(r"AddMap\(", loop_body)), 1)
        self.assertEqual(len(re.findall(r"AddEndMap", loop_body)), 1)

    def test_the_handler_really_does_emit_a_zero_uuid_filler(self) -> None:
        if not _BUNCH_OF_CAPS.exists():
            self.skipTest("opensim-source not present")

        self.assertIn("AddMap(UUID.Zero.ToString(), lsl)", _handler_body())


class ParseTests(unittest.TestCase):
    def test_the_live_payload_decodes(self) -> None:
        parsed = parse_object_cost_payload(_LIVE_PAYLOAD)

        self.assertEqual(list(parsed), [_OBJECT_ID])
        cost = parsed[_OBJECT_ID]
        self.assertEqual(cost.resource_cost, 1.0)
        self.assertEqual(cost.linked_set_resource_cost, 1.0)
        self.assertEqual(cost.resource_limiting_type, "legacy")
        self.assertTrue(cost.is_linkset_root_or_single)

    def test_the_filler_entry_is_dropped(self) -> None:
        # The whole trap. Keeping it would report a real-looking zero cost for
        # a prim that does not exist, which is worse than reporting nothing.
        self.assertEqual(parse_object_cost_payload(_LIVE_FILLER_PAYLOAD), {})

    def test_the_filler_is_dropped_even_beside_real_entries(self) -> None:
        parsed = parse_object_cost_payload({**_LIVE_PAYLOAD, **_LIVE_FILLER_PAYLOAD})

        self.assertEqual(list(parsed), [_OBJECT_ID])

    def test_costs_are_not_transposed(self) -> None:
        # Four adjacent floats, all 1.0 in the live payload, so a swap of any
        # pair round-trips there unnoticed.
        parsed = parse_object_cost_payload(
            {
                str(_OBJECT_ID): {
                    "resource_cost": 11.0,
                    "linked_set_resource_cost": 22.0,
                    "physics_cost": 33.0,
                    "linked_set_physics_cost": 44.0,
                    "resource_limiting_type": "legacy",
                }
            }
        )

        cost = parsed[_OBJECT_ID]
        self.assertEqual(cost.resource_cost, 11.0)
        self.assertEqual(cost.linked_set_resource_cost, 22.0)
        self.assertEqual(cost.physics_cost, 33.0)
        self.assertEqual(cost.linked_set_physics_cost, 44.0)
        self.assertFalse(cost.is_linkset_root_or_single)

    def test_a_partial_entry_is_skipped_rather_than_zeroed(self) -> None:
        parsed = parse_object_cost_payload(
            {str(_OBJECT_ID): {"resource_cost": 1.0, "physics_cost": 1.0}}
        )

        self.assertEqual(parsed, {})

    def test_an_empty_map_is_no_prims(self) -> None:
        self.assertEqual(parse_object_cost_payload({}), {})

    def test_a_non_map_payload_raises(self) -> None:
        with self.assertRaises(ObjectCostError):
            parse_object_cost_payload("nope")


class FetchTests(unittest.TestCase):
    def setUp(self) -> None:
        from vibestorm.caps.client import CapabilityClient

        self._original = CapabilityClient._post_capability_value_sync

    def tearDown(self) -> None:
        from vibestorm.caps.client import CapabilityClient

        CapabilityClient._post_capability_value_sync = self._original  # type: ignore[assignment]

    def _install(self, payload, record: list) -> ObjectCostClient:
        from vibestorm.caps.client import CapabilityClient

        def fake_post(self, url, value, port, agent):  # type: ignore[no-untyped-def]
            record.append(value)
            if isinstance(payload, Exception):
                raise payload
            return payload

        CapabilityClient._post_capability_value_sync = fake_post  # type: ignore[assignment]
        return ObjectCostClient(timeout_seconds=1.0)

    def test_many_ids_go_out_in_one_request(self) -> None:
        # The contrast with GetObjectPhysicsData, which must loop.
        sent: list = []
        client = self._install(_LIVE_PAYLOAD, sent)
        ids = [UUID(int=n) for n in (1, 2, 3, 4)]

        asyncio.run(client.fetch("http://sim/caps/cost", ids))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], {"object_ids": ids})

    def test_an_empty_request_never_reaches_the_network(self) -> None:
        sent: list = []
        client = self._install(_LIVE_PAYLOAD, sent)

        self.assertEqual(asyncio.run(client.fetch("http://sim/caps/cost", [])), {})
        self.assertEqual(sent, [])

    def test_a_filler_only_response_reads_as_nothing_found(self) -> None:
        client = self._install(_LIVE_FILLER_PAYLOAD, [])

        found = asyncio.run(client.fetch("http://sim/caps/cost", [UUID(int=1)]))

        self.assertEqual(found, {})

    def test_a_parse_error_is_wrapped(self) -> None:
        from xml.etree.ElementTree import ParseError

        client = self._install(ParseError("mismatched tag"), [])

        with self.assertRaises(ObjectCostError):
            asyncio.run(client.fetch("http://sim/caps/cost", [UUID(int=1)]))


if __name__ == "__main__":
    unittest.main()
