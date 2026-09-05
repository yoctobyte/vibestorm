"""What every live probe in this directory needs and none of it is the point.

Two things, both learned the hard way:

**Waiting for the region.** Arriving is not the same as having seen the region.
Objects stream in over the tens of seconds after that, so a probe that
snapshots "what was already here" the moment it lands calls every late arrival
a prim it just rezzed -- which is how one run of
``verify_child_prim_frame.py`` linked two prims from an earlier run and
reported on those instead.

**Taking a prim away again.** Every probe that rezzes something used to leave
it there.

Every live probe that rezzes something used to leave it there. ``ObjectDelete``
is the message for it and the local OpenSim build has no handler for it at all
(see ``tools/delete_prims.py``), so the region filled up with test cubes.

There is another way, found while checking what an attachment is: **wear it,
then take it off.** ``ObjectDetach`` takes an attachment into inventory, and
the prim leaves the region. It is not the same as deleting -- the prim ends up
in the agent's inventory -- and the simulator permission-checks the wearing, so
it can only ever reach prims the agent owns. For a probe cleaning up after
itself, both of those are exactly right.

``ObjectDelete`` is still sent first: it is the message that means what is
wanted, and Second Life implements it.
"""

from __future__ import annotations

import asyncio
from uuid import UUID


async def wait_until_quiet(client, *, quiet_for: float = 6.0, limit: float = 90.0) -> int:
    """Wait until the region stops sending new objects. Returns how many it has.

    "Quiet" is the count holding still for ``quiet_for`` seconds, not a fixed
    sleep: a big region takes longer than a small one, and a fixed wait is
    either too short for the first or wasted on the second.
    """
    waited = 0.0
    last = -1
    steady = 0.0
    while waited < limit:
        current = client.current
        count = len(current.world_view.objects) if current is not None else 0
        steady = steady + 1.0 if count == last else 0.0
        if count and steady >= quiet_for:
            return count
        last = count
        await asyncio.sleep(1.0)
        waited += 1.0
    current = client.current
    return len(current.world_view.objects) if current is not None else 0


async def take_prim_away(client, handle: int, object_id: UUID, *, seconds: float = 20.0) -> bool:
    """Remove ``object_id`` from the region. True if it is gone afterwards.

    Reports what the region says, not what was asked for: the caller finds out
    from the return value, and a probe that leaves something behind should say
    so rather than assume.
    """
    session = client.current
    if session is None:
        return False
    obj = session.world_view.objects.get(object_id)
    if obj is None:
        return True

    # ObjectDelete first: it is the message that means what is wanted, and
    # Second Life implements it. Not long, though -- where it is unhandled
    # (the local OpenSim build) there is nothing to wait for, and the wearing
    # below is what actually works.
    client.queue_outbound_packet(handle, session.build_object_delete_packet([obj.local_id]))
    if await _wait_until_gone(client, object_id, seconds=5.0):
        return True

    # Wear it. The reparent is how we know it went on; detaching something
    # that is not on yet does nothing at all.
    session = client.current
    if session is None:
        return False
    obj = session.world_view.objects.get(object_id)
    if obj is None:
        return True
    client.queue_outbound_packet(handle, session.build_object_attach_packet([obj.local_id]))
    worn = await _wait_for_parent(client, object_id, seconds=seconds)
    if worn is None:
        return False

    session = client.current
    if session is None:
        return False
    client.queue_outbound_packet(handle, session.build_object_detach_packet([worn]))
    return await _wait_until_gone(client, object_id, seconds=seconds)


async def _wait_until_gone(client, object_id: UUID, *, seconds: float) -> bool:
    waited = 0.0
    while waited < seconds:
        current = client.current
        if current is not None and object_id not in current.world_view.objects:
            return True
        await asyncio.sleep(1.0)
        waited += 1.0
    current = client.current
    return current is not None and object_id not in current.world_view.objects


async def _wait_for_parent(client, object_id: UUID, *, seconds: float) -> int | None:
    """The prim's local id once it reports a parent, or None if it never does."""
    waited = 0.0
    while waited < seconds:
        current = client.current
        obj = current.world_view.objects.get(object_id) if current is not None else None
        if obj is not None and obj.parent_id:
            return obj.local_id
        await asyncio.sleep(1.0)
        waited += 1.0
    return None
