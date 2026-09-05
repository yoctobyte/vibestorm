"""Tests for creating task inventory rows during a whole-folder object sync.

The point of these is the *identification* logic. Sending RezScript is checked
live and by `test_rez_script`; what can silently go wrong here is deciding
which of the rows that came back belongs to which name, and doing that by name
alone would upload a file over a pre-existing row that happens to share it.

These used to drive a copy of the routine that lived in ``viewer3d/app.py``.
That copy is gone -- the viewer's Upload button now goes through the same
engine as the CLI -- so they drive ``vibestorm.sync.task_inventory``, which is
the only implementation left.
"""

from __future__ import annotations

import unittest
from uuid import UUID

from vibestorm.bus import Bus
from vibestorm.bus.events import ObjectInventorySnapshotReady
from vibestorm.udp.messages import DEFAULT_SCRIPT_ASSET_ID
from vibestorm.world.object_inventory import ObjectInventoryItem, ObjectInventorySnapshot

TASK = UUID("d7f47f7e-4328-4d17-a665-19feaec7b1e9")
LOCAL_ID = 176203873
EXISTING = UUID("00000000-0000-4000-8000-00000000000e")
NEW_A = UUID("00000000-0000-4000-8000-00000000000a")
NEW_B = UUID("00000000-0000-4000-8000-00000000000b")


def _item(item_id: UUID, name: str, asset_id: UUID | None = None) -> ObjectInventoryItem:
    return ObjectInventoryItem(
        item_id=item_id,
        asset_id=asset_id if asset_id is not None else DEFAULT_SCRIPT_ASSET_ID,
        parent_id=TASK,
        name=name,
        description="",
        asset_type="lsltext",
        inventory_type="lsl",
        raw_fields={},
    )


def _snapshot(*items: ObjectInventoryItem) -> ObjectInventorySnapshot:
    return ObjectInventorySnapshot(
        local_id=LOCAL_ID,
        task_id=TASK,
        serial=1,
        filename="inventory",
        items=tuple(items),
        raw_text="",
    )


class _FakeSession:
    """Records the RezScript calls instead of building real packets."""

    def __init__(self) -> None:
        self.rez_calls: list[dict[str, object]] = []

    def build_rez_script_packet(self, **kwargs: object) -> bytes:
        self.rez_calls.append(kwargs)
        return b"packet"


class _FakeClient:
    """Answers each inventory request with the next queued snapshot."""

    def __init__(self, snapshots: list[ObjectInventorySnapshot | None]) -> None:
        self.bus = Bus()
        self._snapshots = snapshots
        self.sent: list[bytes] = []
        self.requests = 0

    def queue_outbound_packet(self, handle: int, packet: bytes) -> None:
        self.sent.append(packet)

    def _reply(self) -> None:
        snapshot = self._snapshots.pop(0) if self._snapshots else None
        if snapshot is not None:
            self.bus.publish(ObjectInventorySnapshotReady(region_handle=1, snapshot=snapshot))


class _DispatchingBusClient(_FakeClient):
    def __init__(self, snapshots: list[ObjectInventorySnapshot | None]) -> None:
        super().__init__(snapshots)
        outer = self

        class _BusProxy:
            def __init__(self, bus: Bus) -> None:
                self._bus = bus

            def subscribe(self, *args: object, **kwargs: object) -> object:
                return self._bus.subscribe(*args, **kwargs)  # type: ignore[arg-type]

            def publish(self, event: object) -> None:
                self._bus.publish(event)

            def dispatch(self, _command: object) -> None:
                outer.requests += 1
                outer._reply()

        self.bus = _BusProxy(self.bus)  # type: ignore[assignment]


class CreateTaskScriptRowTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, snapshots, names):
        from vibestorm.sync.task_inventory import create_task_script_rows

        client = _DispatchingBusClient(list(snapshots))
        session = _FakeSession()
        created, skipped = await create_task_script_rows(
            client,
            session,
            handle=1,
            task_id=TASK,
            local_id=LOCAL_ID,
            names=list(names),
            # Real timeout is 15s; the two failure cases below would otherwise
            # spend it, and a slow suite is a suite that stops being run.
            timeout=0.25,
        )
        return created, skipped, session, client

    async def test_new_row_is_matched_to_its_file(self) -> None:
        before = _snapshot(_item(EXISTING, "Old"))
        after = _snapshot(_item(EXISTING, "Old"), _item(NEW_A, "hello"))

        created, skipped, session, client = await self._run([before, after], ["hello"])

        self.assertEqual(skipped, [])
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].name, "hello")
        self.assertEqual(created[0].item_id, NEW_A)
        self.assertEqual(len(session.rez_calls), 1)
        self.assertEqual(session.rez_calls[0]["name"], "hello")
        self.assertEqual(session.rez_calls[0]["part_id"], TASK)

    async def test_a_preexisting_row_of_the_same_name_is_not_claimed(self) -> None:
        """The baseline diff is the whole point.

        If the object already holds a script called `hello` and the sim creates
        nothing, matching on name alone would hand back the old row and the
        sync would overwrite it.
        """
        before = _snapshot(_item(EXISTING, "hello"))
        after = _snapshot(_item(EXISTING, "hello"))

        created, skipped, _session, _client = await self._run([before, after], ["hello"])

        self.assertEqual(created, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("did not create a row", skipped[0][1])

    async def test_several_files_create_several_rows_with_one_reread(self) -> None:
        before = _snapshot()
        after = _snapshot(_item(NEW_A, "one"), _item(NEW_B, "two"))

        created, skipped, session, client = await self._run([before, after], ["one", "two"])

        self.assertEqual(skipped, [])
        self.assertEqual({row.name for row in created}, {"one", "two"})
        self.assertEqual(len(session.rez_calls), 2)
        # Two creates, but only the before and after reads.
        self.assertEqual(client.requests, 2)

    async def test_unreadable_inventory_before_creating_skips_everything(self) -> None:
        created, skipped, session, _client = await self._run([None], ["a"])

        self.assertEqual(created, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("before creating", skipped[0][1])
        self.assertEqual(session.rez_calls, [], "must not create rows it cannot verify")

    async def test_missing_reread_reports_the_files_as_unresolved(self) -> None:
        created, skipped, session, _client = await self._run([_snapshot(), None], ["a"])

        self.assertEqual(created, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("did not come back", skipped[0][1])
        self.assertEqual(len(session.rez_calls), 1, "the create was still sent")


if __name__ == "__main__":
    unittest.main()
