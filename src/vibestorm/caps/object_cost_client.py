"""Client for the GetObjectCost capability — a prim's land impact.

Returns, per prim, the resource cost of the prim and of the linkset it belongs
to, the same numbers a viewer shows as "land impact". Read-only; no consent
needed.

Two things differ from its neighbour `GetObjectPhysicsData`, and both are
counter-intuitive enough to be worth stating.

**Batching works here.** The physics handler closes its outer LLSD map inside
the per-id loop and so can only answer one id at a time; this handler closes it
after the loop, correctly. Confirmed live on 2026-08-14: four ids returned four
entries. Two capabilities in the same source file, with the same request shape,
that differ on whether batching is possible — so the limit belongs to the
handler, not to the family.

**A request that resolves nothing is not answered with an empty map.** When no
id names a prim the handler writes a filler entry keyed by the *zero UUID* with
every cost at 0 and `resource_limiting_type` still `"legacy"`, which is
indistinguishable in shape from a real prim that costs nothing. Confirmed live:
one bogus id came back as a single zero-UUID entry.
:func:`parse_object_cost_payload` drops it, because a caller that kept it would
report a cost for a prim that does not exist.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient, CapabilityError


class ObjectCostError(RuntimeError):
    """Raised when a GetObjectCost request fails."""


#: The key OpenSim uses for its "nothing resolved" filler entry.
FILLER_OBJECT_ID = UUID(int=0)


@dataclass(slots=True, frozen=True)
class ObjectCost:
    """One prim's land impact, and its linkset's."""

    resource_cost: float
    linked_set_resource_cost: float
    physics_cost: float
    linked_set_physics_cost: float
    #: OpenSim always sends ``"legacy"``. Kept rather than dropped: it is the
    #: sim telling us which costing model produced these numbers, and a future
    #: value would change what they mean.
    resource_limiting_type: str

    @property
    def is_linkset_root_or_single(self) -> bool:
        """True when this prim's cost accounts for its whole linkset.

        Equal costs mean either a single-prim object or the only prim that was
        asked about in its linkset — not that the linkset has one prim.
        """
        return abs(self.resource_cost - self.linked_set_resource_cost) < 1e-6

    def describe(self) -> str:
        return (
            f"cost={self.resource_cost:g} linkset={self.linked_set_resource_cost:g} "
            f"physics={self.physics_cost:g} "
            f"linkset_physics={self.linked_set_physics_cost:g}"
        )


def parse_object_cost_payload(payload: object) -> dict[UUID, ObjectCost]:
    """Decode a GetObjectCost response, dropping OpenSim's filler entry.

    Entries missing any of the four costs are skipped rather than defaulted:
    the handler writes all of them together, so a partial entry means something
    changed on the sim side and a zero would read as a free prim.
    """
    if not isinstance(payload, dict):
        raise ObjectCostError(
            f"GetObjectCost did not return an LLSD map, got {type(payload).__name__}"
        )
    result: dict[UUID, ObjectCost] = {}
    for raw_id, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        try:
            object_id = UUID(str(raw_id))
        except (TypeError, ValueError):
            continue
        if object_id == FILLER_OBJECT_ID:
            # "Nothing you asked about exists", wearing the shape of a real
            # answer. Never a prim.
            continue
        try:
            result[object_id] = ObjectCost(
                resource_cost=float(raw["resource_cost"]),
                linked_set_resource_cost=float(raw["linked_set_resource_cost"]),
                physics_cost=float(raw["physics_cost"]),
                linked_set_physics_cost=float(raw["linked_set_physics_cost"]),
                resource_limiting_type=str(raw.get("resource_limiting_type", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


@dataclass(slots=True)
class ObjectCostClient:
    """Fetch land impact for one or many prims in a single request."""

    timeout_seconds: float = 10.0

    async def fetch(
        self,
        capability_url: str,
        object_ids: list[UUID],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> dict[UUID, ObjectCost]:
        """Costs for every id the sim recognises; ids it does not are absent."""
        if not object_ids:
            # The handler answers an empty list with an empty map, so this is
            # a round trip that cannot tell us anything.
            return {}
        client = CapabilityClient(timeout_seconds=self.timeout_seconds)
        try:
            payload = await asyncio.to_thread(
                client._post_capability_value_sync,
                capability_url,
                {"object_ids": list(object_ids)},
                udp_listen_port,
                user_agent,
            )
        except CapabilityError as exc:
            raise ObjectCostError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - a parse error is not a CapabilityError
            raise ObjectCostError(
                f"GetObjectCost for {len(object_ids)} id(s) failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return parse_object_cost_payload(payload)


__all__ = [
    "FILLER_OBJECT_ID",
    "ObjectCost",
    "ObjectCostClient",
    "ObjectCostError",
    "parse_object_cost_payload",
]
