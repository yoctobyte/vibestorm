"""Putting a linkset's children where they actually are.

An `ObjectUpdate` for a prim with a non-zero ``parent_id`` reports a position
and rotation **in its parent's frame**, not the region's. Observed live against
local OpenSim with ``tools/verify_child_prim_frame.py``: two prims rezzed at
(130, 128, 27.1) and (134, 128, 27.1) and then linked, after which the child
reported

    position (4.0, 0.0, 0.0)

-- exactly its offset from the root. The viewer drew every prim at whatever the
update said, so every child of every linkset landed a few metres from the region
corner instead of beside its root.

Attachments are the same shape of thing, and that was checked rather than
assumed: ``tools/verify_attachment_frame.py`` rezzes a prim, wears it, and
watches the simulator reparent it onto the avatar and start reporting its
position in the avatar's frame. A seated avatar is the third of the same shape
-- see ``tools/verify_seated_avatar.py``.

So each child has to be composed back through its parent:

    world_position = parent_position + parent_rotation * child_position
    world_rotation = parent_rotation * child_rotation

Scale is *not* relative and is left alone.

Two cases that matter as much as the arithmetic:

- **A chain.** An attachment's own children are children of a child. Resolving
  has to work outward from the roots rather than assume one level.
- **A parent that has not arrived.** Updates are not ordered, so a child is
  routinely seen before its root. Such a child is reported unresolved rather
  than guessed at -- drawing it at its raw position is the bug this module
  exists to fix, and drawing it at the origin is no better.
"""

from __future__ import annotations

from collections.abc import Container, Mapping

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

#: What a resolved transform is: position, then rotation as (x, y, z, w).
Transform = tuple[Vec3, Quat]

IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)


def quat_multiply(a: Quat, b: Quat) -> Quat:
    """The rotation "``b`` and then ``a``", both as (x, y, z, w)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_rotate(quat: Quat, vector: Vec3) -> Vec3:
    """``vector`` turned by ``quat``.

    The cross-product form rather than building a matrix: one rotation of one
    vector, done a few hundred times a frame.
    """
    qx, qy, qz, qw = quat
    vx, vy, vz = vector
    # t = 2 * (q.xyz x v)
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def compose(parent: Transform, child: Transform) -> Transform:
    """Lift a child's parent-relative transform into the parent's frame."""
    parent_position, parent_rotation = parent
    child_position, child_rotation = child
    offset = quat_rotate(parent_rotation, child_position)
    return (
        (
            parent_position[0] + offset[0],
            parent_position[1] + offset[1],
            parent_position[2] + offset[2],
        ),
        quat_multiply(parent_rotation, child_rotation),
    )


def resolve_world_transforms(
    local_transforms: Mapping[int, tuple[int, Vec3, Quat | None]],
    *,
    unchanged: Container[int] = (),
    previous: Mapping[int, Transform] | None = None,
) -> dict[int, Transform]:
    """World transforms for everything whose parents are all present.

    ``local_transforms`` maps local id to ``(parent_id, position, rotation)``
    exactly as the updates reported them; a ``parent_id`` of 0 means a root.
    The result omits any object whose parent is missing, and any object caught
    in a parent cycle -- a simulator should never send one, but a viewer that
    loops forever on a malformed update is worse than one that leaves a prim
    out.

    ``unchanged`` and ``previous`` make a re-resolve cost only what moved. A
    caller that resolves the same region every frame passes the ids whose own
    transform is exactly what it was last time, together with the answer it got
    then; anything in both is carried straight across, **as the same tuple**,
    unless something above it in its linkset moved. Composing a still region of
    15,000 prims sixty times a second is otherwise most of a frame, spent
    arriving back where it started.

    Passing neither resolves everything from scratch, which is what the two are
    defined against: ``unchanged`` empty means every object is treated as newly
    arrived.
    """
    known: Mapping[int, Transform] = previous if previous is not None else {}
    resolved: dict[int, Transform] = {}
    pending: dict[int, tuple[int, Vec3, Quat]] = {}
    # Whose *world* transform is not what it was -- which is not the same as
    # whose own transform changed: a child that did not move is somewhere else
    # entirely if its root did. Seeded with the roots, then grown outward as
    # the composing goes, so a moved root carries its whole linkset.
    moved: set[int] = set()

    for local_id, (parent_id, position, rotation) in local_transforms.items():
        turn = rotation if rotation is not None else IDENTITY
        if not parent_id:
            was = known.get(local_id) if local_id in unchanged else None
            if was is None:
                moved.add(local_id)
                resolved[local_id] = (position, turn)
            else:
                resolved[local_id] = was
        else:
            pending[local_id] = (parent_id, position, turn)

    # Outward from the roots: each pass resolves the children of everything
    # resolved so far, so a chain of depth n costs n passes. The pass count is
    # bounded by how many objects are pending -- there cannot be more levels
    # than that -- rather than by trusting the loop to make progress, so a
    # malformed parent cycle costs a few wasted passes instead of hanging the
    # viewer. The early exit is only there to stop short of the bound.
    for _ in range(len(pending)):
        progressed = False
        for local_id in list(pending):
            parent_id, position, turn = pending[local_id]
            parent = resolved.get(parent_id)
            if parent is None:
                continue
            was = known.get(local_id) if local_id in unchanged else None
            if was is None or parent_id in moved:
                moved.add(local_id)
                resolved[local_id] = compose(parent, (position, turn))
            else:
                resolved[local_id] = was
            del pending[local_id]
            progressed = True
        if not progressed:
            break

    return resolved


__all__ = [
    "IDENTITY",
    "Quat",
    "Transform",
    "Vec3",
    "compose",
    "quat_multiply",
    "quat_rotate",
    "resolve_world_transforms",
]
