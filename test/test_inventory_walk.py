"""Tests for the recursive inventory walk.

The traversal is pure — ``plan_next_batch`` and ``absorb_batch`` over a
``WalkState`` — so cycles, depth limits and budget limits are all testable
without a network. The async driver is exercised against a fake client.

The property these care about most is that a truncated walk *says so*. A
partial inventory listing that reads as complete is worse than none: it
answers "does this account have a mesh to test with?" with a confident no.
"""

import unittest
from uuid import UUID

from vibestorm.caps.inventory_walk import (
    WalkState,
    absorb_batch,
    format_walk,
    plan_next_batch,
    start_walk,
    walk_inventory,
)

ROOT = UUID(int=1)
OWNER = UUID(int=99)


class _Category:
    def __init__(self, category_id):
        self.category_id = category_id
        self.parent_id = None
        self.name = f"folder-{category_id}"
        self.type_default = None
        self.version = 1


class _Folder:
    def __init__(self, folder_id, children=(), items=0):
        self.folder_id = folder_id
        self.owner_id = OWNER
        self.agent_id = OWNER
        self.descendents = len(children) + items
        self.version = 1
        self.categories = tuple(_Category(c) for c in children)
        self.items = tuple(range(items))

    @property
    def item_count(self):
        return len(self.items)

    def sample_item_names(self, limit=3):
        return ()

    def as_payload(self):
        """The shape FetchInventoryDescendents2 actually returns."""
        return {
            "folder_id": str(self.folder_id),
            "owner_id": str(OWNER),
            "agent_id": str(OWNER),
            "descendents": self.descendents,
            "version": self.version,
            "categories": [
                {
                    "category_id": str(category.category_id),
                    "parent_id": str(self.folder_id),
                    "name": category.name,
                    "type_default": 0,
                    "version": 1,
                }
                for category in self.categories
            ],
            "items": [
                {
                    "item_id": str(UUID(int=5000 + index)),
                    "asset_id": str(UUID(int=6000 + index)),
                    "parent_id": str(self.folder_id),
                    "name": f"item-{index}",
                    "desc": "",
                    "type": 0,
                    "inv_type": 0,
                    "flags": 0,
                }
                for index in range(len(self.items))
            ],
        }


class PlanningTests(unittest.TestCase):
    def test_start_queues_the_root(self) -> None:
        state = start_walk(ROOT)

        self.assertEqual(state.pending, [(ROOT, 0)])
        self.assertIn(ROOT, state.seen)

    def test_batch_is_bounded_and_consumes_the_queue(self) -> None:
        state = WalkState()
        for index in range(25):
            state.pending.append((UUID(int=index + 10), 1))

        requests, depths = plan_next_batch(state, OWNER, batch_size=10)

        self.assertEqual(len(requests), 10)
        self.assertEqual(depths, [1] * 10)
        self.assertEqual(len(state.pending), 15)

    def test_requests_ask_for_both_folders_and_items(self) -> None:
        # Asking only for items would make the walk terminate after one level
        # while looking like it had finished.
        requests, _ = plan_next_batch(start_walk(ROOT), OWNER)

        self.assertTrue(requests[0].fetch_folders)
        self.assertTrue(requests[0].fetch_items)
        self.assertEqual(requests[0].owner_id, OWNER)


class AbsorbTests(unittest.TestCase):
    def test_children_are_queued_one_level_deeper(self) -> None:
        state = start_walk(ROOT)
        _requests, depths = plan_next_batch(state, OWNER)

        absorb_batch(state, (_Folder(ROOT, children=[UUID(int=2), UUID(int=3)]),), depths)

        self.assertEqual(state.pending, [(UUID(int=2), 1), (UUID(int=3), 1)])
        self.assertEqual(state.folder_count, 1)

    def test_a_repeated_child_is_not_fetched_twice(self) -> None:
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(ROOT, children=[UUID(int=2)]),), depths)
        _r, depths = plan_next_batch(state, OWNER)

        # Folder 2 names folder 2 as its own child.
        absorb_batch(state, (_Folder(UUID(int=2), children=[UUID(int=2)]),), depths)

        self.assertEqual(state.pending, [])
        self.assertTrue(state.complete)

    def test_a_cycle_terminates(self) -> None:
        # 1 -> 2 -> 1. Without `seen` this walks forever.
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(ROOT, children=[UUID(int=2)]),), depths)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(UUID(int=2), children=[ROOT]),), depths)

        self.assertEqual(state.pending, [])

    def test_depth_limit_records_what_it_skipped(self) -> None:
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)

        absorb_batch(state, (_Folder(ROOT, children=[UUID(int=2)]),), depths, max_depth=0)

        self.assertEqual(state.pending, [])
        self.assertEqual(state.skipped_depth, [UUID(int=2)])
        self.assertFalse(state.complete)
        self.assertIn("TRUNCATED", state.describe())

    def test_budget_limit_records_what_it_skipped(self) -> None:
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)

        absorb_batch(
            state,
            (_Folder(ROOT, children=[UUID(int=2), UUID(int=3), UUID(int=4)]),),
            depths,
            max_folders=2,
        )

        self.assertEqual(len(state.skipped_budget), 2)
        self.assertFalse(state.complete)

    def test_a_full_walk_reports_complete(self) -> None:
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(ROOT, children=[]),), depths)

        self.assertTrue(state.complete)
        self.assertIn("complete", state.describe())

    def test_max_depth_reached_is_tracked(self) -> None:
        state = start_walk(ROOT)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(ROOT, children=[UUID(int=2)]),), depths)
        _r, depths = plan_next_batch(state, OWNER)
        absorb_batch(state, (_Folder(UUID(int=2)),), depths)

        self.assertEqual(state.max_depth_reached, 1)


class _FakeClient:
    """Returns a canned tree as a real capability payload.

    Returns the LLSD-shaped dict the capability actually sends, so the driver
    runs the real ``parse_inventory_descendents_payload``. An earlier version
    of this file monkeypatched that parser to an identity function and so
    never noticed it returns an ``InventoryFetchSnapshot`` rather than a bare
    sequence of folders — the walk raised TypeError on its first live run
    while every test passed.
    """

    def __init__(self, tree: dict) -> None:
        self.tree = tree
        self.calls = 0
        self.requested: list[list[UUID]] = []

    async def fetch_inventory_descendents(self, url, requests, **kwargs):
        del url, kwargs
        self.calls += 1
        self.requested.append([r.folder_id for r in requests])
        return {
            "folders": [
                self.tree[r.folder_id].as_payload()
                for r in requests
                if r.folder_id in self.tree
            ]
        }


class WalkDriverTests(unittest.TestCase):
    def _run(self, tree, **kwargs):
        import asyncio

        client = _FakeClient(tree)
        snapshot, state = asyncio.run(
            walk_inventory(
                client, "http://example.invalid/fetch",
                root_folder_id=ROOT, owner_id=OWNER, **kwargs,
            )
        )
        return client, snapshot, state

    def test_walks_a_whole_tree(self) -> None:
        tree = {
            ROOT: _Folder(ROOT, children=[UUID(int=2), UUID(int=3)], items=1),
            UUID(int=2): _Folder(UUID(int=2), children=[UUID(int=4)], items=2),
            UUID(int=3): _Folder(UUID(int=3), items=3),
            UUID(int=4): _Folder(UUID(int=4), items=4),
        }

        _client, snapshot, state = self._run(tree)

        self.assertEqual(snapshot.folder_count, 4)
        self.assertEqual(snapshot.total_item_count, 10)
        self.assertTrue(state.complete)

    def test_each_level_costs_one_round_trip(self) -> None:
        # The capability takes a list of folders, so a two-child level must not
        # cost two requests.
        tree = {
            ROOT: _Folder(ROOT, children=[UUID(int=2), UUID(int=3)]),
            UUID(int=2): _Folder(UUID(int=2)),
            UUID(int=3): _Folder(UUID(int=3)),
        }

        client, _snapshot, _state = self._run(tree)

        self.assertEqual(client.calls, 2)
        self.assertEqual(client.requested[1], [UUID(int=2), UUID(int=3)])

    def test_an_empty_root_is_one_request(self) -> None:
        client, snapshot, state = self._run({ROOT: _Folder(ROOT)})

        self.assertEqual(client.calls, 1)
        self.assertEqual(snapshot.folder_count, 1)
        self.assertTrue(state.complete)

    def test_a_truncated_walk_is_reported_in_the_output(self) -> None:
        tree = {
            ROOT: _Folder(ROOT, children=[UUID(int=2)]),
            UUID(int=2): _Folder(UUID(int=2), children=[UUID(int=3)]),
            UUID(int=3): _Folder(UUID(int=3)),
        }

        _client, snapshot, state = self._run(tree, max_depth=1)

        lines = format_walk(snapshot, state)
        self.assertTrue(any("TRUNCATED" in line for line in lines))
        self.assertFalse(state.complete)

    def test_a_missing_folder_does_not_stall_the_walk(self) -> None:
        # The sim may decline a folder; the reply then has fewer entries than
        # the request. The walk must finish rather than loop on the gap.
        tree = {ROOT: _Folder(ROOT, children=[UUID(int=2), UUID(int=3)])}

        _client, snapshot, state = self._run(tree)

        self.assertEqual(snapshot.folder_count, 1)
        self.assertEqual(state.pending, [])


if __name__ == "__main__":
    unittest.main()
