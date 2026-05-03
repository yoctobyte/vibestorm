"""WorldClient: top-level owner of one or more LiveCircuitSession circuits.

Today the session machinery assumes a single sim. Cross-sim support
(Phase 5) needs N circuits — one for the region the avatar is in
("current") and zero or more child circuits for visible neighbors.

This skeleton introduces the ownership shape without changing any wire
behavior: a WorldClient holds circuits keyed by region_handle, knows which
one is current, and can route outbound traffic to a specific circuit.

Future phases will move the run-loop's "session" reference to
``world_client.current``, hook the command/event bus through here, and
add the cross-sim transitions (EnableSimulator → child circuit,
CrossedRegion → promote child to current).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from vibestorm.udp.session import LiveCircuitSession
from vibestorm.world.models import WorldView


def region_handle_from_meters(region_x_meters: int, region_y_meters: int) -> int:
    """Pack a (region_x, region_y) meter offset pair into the standard SL U64 region handle."""
    if not 0 <= region_x_meters <= 0xFFFFFFFF:
        raise ValueError("region_x_meters must fit in U32")
    if not 0 <= region_y_meters <= 0xFFFFFFFF:
        raise ValueError("region_y_meters must fit in U32")
    return (region_x_meters << 32) | region_y_meters


def region_handle_for_session(session: LiveCircuitSession) -> int:
    """Compute the region handle for a session from its bootstrap coordinates."""
    return region_handle_from_meters(session.bootstrap.region_x, session.bootstrap.region_y)


class WorldClientError(RuntimeError):
    """Raised on programming errors against the WorldClient API."""


@dataclass(slots=True)
class WorldClient:
    """Multi-circuit session owner.

    Holds N LiveCircuitSession instances keyed by region_handle. Exactly
    one is "current" (the region the agent is in); others are child sims
    (neighbors connected via EstablishAgentCommunication).

    v1 contract:
    - Pure container. Routing helpers are advisory; the run loop still
      owns its socket and packet pump for now.
    - ``world_view()`` returns the current circuit's view. Multi-region
      merging arrives in Phase 5.
    """

    circuits: dict[int, LiveCircuitSession] = field(default_factory=dict)
    current_handle: int | None = None

    # ------------------------------------------------------------------ ops

    def add_circuit(self, session: LiveCircuitSession, *, make_current: bool = False) -> int:
        """Register a session under its region handle. Returns the handle.

        Raises if a circuit is already registered for the same handle —
        callers should ``remove_circuit`` first if they intend to replace.
        """
        handle = region_handle_for_session(session)
        if handle in self.circuits:
            raise WorldClientError(f"circuit already registered for region_handle={handle:#018x}")
        self.circuits[handle] = session
        if make_current or self.current_handle is None:
            self.current_handle = handle
        return handle

    def remove_circuit(self, handle: int) -> LiveCircuitSession | None:
        """Drop a circuit. If it was the current circuit, leave current_handle unset."""
        session = self.circuits.pop(handle, None)
        if self.current_handle == handle:
            self.current_handle = None
        return session

    def set_current(self, handle: int) -> None:
        """Promote an existing child circuit to current."""
        if handle not in self.circuits:
            raise WorldClientError(f"no circuit registered for region_handle={handle:#018x}")
        self.current_handle = handle

    # ------------------------------------------------------------------ views

    @property
    def current(self) -> LiveCircuitSession | None:
        if self.current_handle is None:
            return None
        return self.circuits.get(self.current_handle)

    @property
    def child_handles(self) -> tuple[int, ...]:
        return tuple(h for h in self.circuits if h != self.current_handle)

    def get(self, handle: int) -> LiveCircuitSession | None:
        return self.circuits.get(handle)

    def all_circuits(self) -> Iterable[LiveCircuitSession]:
        return self.circuits.values()

    def world_view(self) -> WorldView | None:
        """Return the current circuit's WorldView (v1: single-region only)."""
        current = self.current
        if current is None:
            return None
        return current.world_view


__all__ = [
    "WorldClient",
    "WorldClientError",
    "region_handle_for_session",
    "region_handle_from_meters",
]
