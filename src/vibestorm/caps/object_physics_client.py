"""Client for the GetObjectPhysicsData capability.

The same five values `ObjectPhysicsProperties` carries — physics shape type,
density, friction, restitution and gravity multiplier — but *pulled* rather
than waited for. That difference matters more than it sounds: over UDP OpenSim
sends `ObjectPhysicsProperties` only as an echo of an edit the viewer itself
made, so reading a prim's physics meant editing someone's region first. This
capability answers for any prim, changes nothing, and needs no consent.

**One object id per request.** OpenSim's handler closes the outer LLSD map
inside its loop:

    for (int i = 0 ; i < object_ids.Count ; i++)
    {
        ...
        if (obj != null) { AddMap(uuid); ...; AddEndMap(); }
    AddEndMap(lsl);            // <-- inside the for, once per id
    }

so a request for N ids emits N closing tags for a map that was opened once.
Confirmed live on 2026-08-14: one id returns well-formed LLSD, two ids fail to
parse at all ("mismatched tag"). Batching is therefore not a tuning decision
this client gets to make, and :func:`fetch_many` loops rather than batching.

A prim the sim cannot find is omitted from the response rather than reported —
including, unhelpfully, an avatar id, which is a UUID in the same object
collection but is not a `SceneObjectPart`. An id that comes back missing is
absent, not an error.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.world.physics_shape import PhysicsProperties


class ObjectPhysicsError(RuntimeError):
    """Raised when a GetObjectPhysicsData request fails."""


#: The handler emits one closing tag per id for a singly-opened map, so any
#: request carrying more than this returns XML that will not parse.
MAX_OBJECT_IDS_PER_REQUEST = 1


def parse_object_physics_payload(
    payload: object,
) -> dict[UUID, PhysicsProperties]:
    """Decode a GetObjectPhysicsData response into typed properties.

    Entries that are not a well-formed map of the five fields are skipped
    rather than defaulted: OpenSim writes all five together or omits the prim
    entirely, so a partial entry means something changed on the sim side and
    inventing a default would hide it.
    """
    if not isinstance(payload, dict):
        raise ObjectPhysicsError(
            f"GetObjectPhysicsData did not return an LLSD map, got {type(payload).__name__}"
        )
    result: dict[UUID, PhysicsProperties] = {}
    for raw_id, raw in payload.items():
        if not isinstance(raw, dict):
            continue
        try:
            object_id = UUID(str(raw_id))
            result[object_id] = PhysicsProperties(
                shape_type=int(raw["PhysicsShapeType"]),
                density=float(raw["Density"]),
                friction=float(raw["Friction"]),
                restitution=float(raw["Restitution"]),
                gravity_multiplier=float(raw["GravityMultiplier"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return result


@dataclass(slots=True)
class ObjectPhysicsClient:
    """Read prim physics properties over HTTP instead of waiting for an echo."""

    timeout_seconds: float = 10.0

    async def fetch(
        self,
        capability_url: str,
        object_id: UUID,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> PhysicsProperties | None:
        """Physics properties for one prim, or None if the sim has no such part."""
        client = CapabilityClient(timeout_seconds=self.timeout_seconds)
        try:
            payload = await asyncio.to_thread(
                client._post_capability_value_sync,
                capability_url,
                {"object_ids": [object_id]},
                udp_listen_port,
                user_agent,
            )
        except CapabilityError as exc:
            raise ObjectPhysicsError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - an XML parse error is not a CapabilityError
            raise ObjectPhysicsError(
                f"GetObjectPhysicsData for {object_id} failed: {type(exc).__name__}: {exc}"
            ) from exc
        return parse_object_physics_payload(payload).get(object_id)

    async def fetch_many(
        self,
        capability_url: str,
        object_ids: list[UUID],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> dict[UUID, PhysicsProperties]:
        """One request per id, because the sim cannot answer more than one.

        Sequential rather than concurrent: this is a diagnostic read over the
        same connection the session needs, and a burst of requests for every
        prim in a populated region would be a self-inflicted load spike.
        """
        found: dict[UUID, PhysicsProperties] = {}
        for object_id in object_ids:
            properties = await self.fetch(
                capability_url,
                object_id,
                udp_listen_port=udp_listen_port,
                user_agent=user_agent,
            )
            if properties is not None:
                found[object_id] = properties
        return found


__all__ = [
    "MAX_OBJECT_IDS_PER_REQUEST",
    "ObjectPhysicsClient",
    "ObjectPhysicsError",
    "parse_object_physics_payload",
]
