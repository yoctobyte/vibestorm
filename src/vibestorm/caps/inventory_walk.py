"""Breadth-first recursive walk of user inventory.

``FetchInventoryDescendents2`` returns one level: a folder's items and the
*names* of its subfolders, not their contents. The client could open folders
one at a time, which is right for a UI responding to a click but useless for
answering "what is actually in here" — a question that comes up constantly
when deciding what content the account has to test against.

The traversal is deliberately split in two:

- ``plan_next_batch`` and ``absorb_batch`` are pure functions over a
  ``WalkState``. All the interesting behaviour — cycle detection, depth and
  budget limits, and recording what was skipped — lives there and is testable
  without a network.
- ``walk_inventory`` is a thin async driver that posts each planned batch.

The capability takes a *list* of folders per request, so each level of the
tree costs one round trip rather than one per folder.

Limits are reported, never silent. A walk that stopped early because it hit
its folder budget looks exactly like a complete walk unless it says so, and a
truncated inventory listing that reads as complete is worse than no listing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from vibestorm.caps.inventory_client import (
    InventoryFetchSnapshot,
    InventoryFolderRequest,
    parse_inventory_descendents_payload,
)
from vibestorm.caps.inventory_types import count_asset_types, missing_gap_closing_types

#: Folders fetched per request. OpenSim answers a batched request happily, but
#: an unbounded list makes one failure lose the whole level.
DEFAULT_BATCH_SIZE = 10

#: Stops a runaway walk on a very large account. Reported when hit.
DEFAULT_MAX_FOLDERS = 500

#: Depth 0 is the root itself. Deep enough for real inventories, bounded
#: because a malformed parent chain could otherwise descend forever.
DEFAULT_MAX_DEPTH = 20


@dataclass(slots=True)
class WalkState:
    """Progress of a recursive walk. Mutated in place by ``absorb_batch``."""

    #: Folder ids discovered but not yet fetched, with their depth.
    pending: list[tuple[UUID, int]] = field(default_factory=list)
    #: Every folder id ever queued, so a cycle or a repeated child cannot
    #: cause a folder to be fetched twice.
    seen: set[UUID] = field(default_factory=set)
    fetched: list[object] = field(default_factory=list)
    #: Folders left unfetched because a limit was reached, and why.
    skipped_depth: list[UUID] = field(default_factory=list)
    skipped_budget: list[UUID] = field(default_factory=list)
    max_depth_reached: int = 0

    @property
    def folder_count(self) -> int:
        return len(self.fetched)

    @property
    def complete(self) -> bool:
        """True when the whole subtree was walked with nothing skipped."""
        return not self.pending and not self.skipped_depth and not self.skipped_budget

    def describe(self) -> str:
        parts = [f"folders={self.folder_count}", f"depth={self.max_depth_reached}"]
        if self.skipped_depth:
            parts.append(f"skipped_depth={len(self.skipped_depth)}")
        if self.skipped_budget:
            parts.append(f"skipped_budget={len(self.skipped_budget)}")
        if self.pending:
            parts.append(f"unvisited={len(self.pending)}")
        parts.append("complete" if self.complete else "TRUNCATED")
        return " ".join(parts)


def start_walk(root_folder_id: UUID) -> WalkState:
    state = WalkState()
    state.pending.append((root_folder_id, 0))
    state.seen.add(root_folder_id)
    return state


def plan_next_batch(
    state: WalkState,
    owner_id: UUID,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[list[InventoryFolderRequest], list[int]]:
    """Take up to ``batch_size`` pending folders and build their requests.

    Returns the requests and the depth of each, positionally aligned, because
    a reply carries no depth of its own.
    """
    taken = state.pending[:batch_size]
    del state.pending[:batch_size]
    requests = [
        InventoryFolderRequest(
            folder_id=folder_id,
            owner_id=owner_id,
            fetch_folders=True,
            fetch_items=True,
            sort_order=0,
        )
        for folder_id, _depth in taken
    ]
    return requests, [depth for _folder_id, depth in taken]


def absorb_batch(
    state: WalkState,
    folders: tuple[object, ...],
    depths: list[int],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_folders: int = DEFAULT_MAX_FOLDERS,
) -> None:
    """Record fetched folders and queue their children.

    ``depths`` is positional against the *request*; a reply may return fewer
    folders than asked for, so the depth of a returned folder is looked up by
    matching its id against the requested set rather than by index.
    """
    for index, folder in enumerate(folders):
        state.fetched.append(folder)
        depth = depths[index] if index < len(depths) else 0
        state.max_depth_reached = max(state.max_depth_reached, depth)

        for category in getattr(folder, "categories", ()) or ():
            child_id = getattr(category, "category_id", None)
            if child_id is None or child_id in state.seen:
                # Already queued or fetched. A folder appearing twice is not
                # an error — inventory trees can name the same child from a
                # stale parent — but fetching it twice would be.
                continue
            state.seen.add(child_id)
            if depth + 1 > max_depth:
                state.skipped_depth.append(child_id)
                continue
            if len(state.seen) > max_folders:
                state.skipped_budget.append(child_id)
                continue
            state.pending.append((child_id, depth + 1))


async def walk_inventory(
    client: object,
    url: str,
    *,
    root_folder_id: UUID,
    owner_id: UUID,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_folders: int = DEFAULT_MAX_FOLDERS,
    udp_listen_port: int | None = None,
) -> tuple[InventoryFetchSnapshot, WalkState]:
    """Walk a folder and everything beneath it, one request per batch."""
    state = start_walk(root_folder_id)
    while state.pending:
        requests, depths = plan_next_batch(state, owner_id, batch_size=batch_size)
        if not requests:
            break
        payload = await client.fetch_inventory_descendents(
            url, requests, udp_listen_port=udp_listen_port
        )
        # The parser returns a whole InventoryFetchSnapshot, not a bare
        # sequence of folders — the walk only wants that snapshot's folders.
        batch = parse_inventory_descendents_payload(payload)
        absorb_batch(
            state, batch.folders, depths, max_depth=max_depth, max_folders=max_folders
        )

    snapshot = InventoryFetchSnapshot(
        folders=tuple(state.fetched),  # type: ignore[arg-type]
        inventory_root_folder_id=root_folder_id,
    )
    return snapshot, state


def format_walk(snapshot: InventoryFetchSnapshot, state: WalkState) -> list[str]:
    """Render a walk as report lines, tree-shaped by parent id."""
    lines = [f"inventory walk={state.describe()}"]
    lines.append(
        f"inventory totals=folders:{snapshot.folder_count} "
        f"items:{snapshot.total_item_count}"
    )

    all_items = [item for folder in snapshot.folders for item in folder.items]
    for name, count in sorted(
        count_asset_types(all_items).items(), key=lambda i: (-i[1], i[0])
    ):
        lines.append(f"inventory type[{name}]={count}")
    # The account-side counterpart of the census `absent=` line: these are the
    # things that, if present, could turn an unverified decoder into a verified
    # one by being rezzed, worn or played.
    absent = missing_gap_closing_types(all_items)
    if absent:
        lines.append(f"inventory absent={', '.join(absent)}")
    if state.skipped_depth:
        lines.append(f"inventory skipped[depth>{DEFAULT_MAX_DEPTH}]={len(state.skipped_depth)}")
    if state.skipped_budget:
        lines.append(f"inventory skipped[budget]={len(state.skipped_budget)}")
    for folder in snapshot.folders:
        name_hint = ", ".join(folder.sample_item_names(3))
        lines.append(
            f"inventory folder[{folder.folder_id}] items={folder.item_count} "
            f"subfolders={len(folder.categories)}"
            + (f" e.g. {name_hint}" if name_hint else "")
        )
    return lines


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_FOLDERS",
    "WalkState",
    "absorb_batch",
    "format_walk",
    "plan_next_batch",
    "start_walk",
    "walk_inventory",
]
