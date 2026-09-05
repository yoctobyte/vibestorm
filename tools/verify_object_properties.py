"""Live check: what does the simulator say when you select an object?

`ObjectProperties` is the long form of `ObjectPropertiesFamily` -- everything
the family reply carries plus the creator, the creation date, the inventory
serial, and where a rezzed object came from in inventory. This client had no
parser for it, and 52 of them arrived over the recorded sessions and were
decoded to a message name and thrown away.

The layout comes from `message_template.msg` and the unit tests build bodies
to that spec, which proves the parser agrees with the template and nothing
more. This is the other half: a real simulator's bytes, and answers that can
be checked against things already known.

Four of them:

1. **The object id is the one selected**, so the fields belong to the prim
   being asked about and not to whatever came before it in the packet.
2. **The creator is this agent**, for a prim this tool rezzes itself. That
   field is the reason to want the message and it is in nothing else, so a
   parser reading the wrong sixteen bytes would show up here.
3. **The inventory serial is a plausible small number**, not the 65535 that
   reading a signed field unsigned would give.
4. **The name matches** what `ObjectPropertiesFamily` says about the same
   prim, which is the one field the two messages share and the check that the
   four strings at the end are being counted correctly.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/verify_object_properties.py

It takes its prim away again afterwards -- see `tools/probe_support.py`.
"""

import asyncio
import math
import os
import platform
from pathlib import Path

from probe_support import take_prim_away, wait_until_quiet

from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.world_client import WorldClient

#: An inventory serial above this is not a count of edits; it is a signed
#: field read unsigned, or the wrong two bytes entirely.
PLAUSIBLE_SERIAL = 10_000


async def wait_for(predicate, *, seconds: float, step: float = 1.0) -> bool:
    waited = 0.0
    while waited < seconds:
        if predicate():
            return True
        await asyncio.sleep(step)
        waited += step
    return predicate()


async def main() -> int:
    request = LoginRequest(
        login_uri=os.environ["VIBESTORM_LOGIN_URI"],
        credentials=LoginCredentials(
            first=os.environ["VIBESTORM_FIRST_NAME"],
            last=os.environ["VIBESTORM_LAST_NAME"],
            password=os.environ["VIBESTORM_PASSWORD"],
        ),
        start=os.environ.get("VIBESTORM_START_LOCATION", "uri:Vibestorm Test&128&128&25"),
        platform=platform.system(),
    )
    bootstrap = await LoginClient().login(request)
    print(f"login ok agent={bootstrap.agent_id}")

    client = WorldClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_live_session(
            bootstrap,
            MessageDispatcher.from_repo_root(Path.cwd()),
            config=SessionConfig(duration_seconds=420.0),
            world_client=client,
            stop_event=stop,
        )
    )

    failures: list[str] = []
    prim_id = None
    try:
        await wait_for(
            lambda: client.current is not None and client.current.movement_completed,
            seconds=30.0,
            step=0.5,
        )
        session = client.current
        if session is None or not session.movement_completed:
            print("FAIL: never finished arriving")
            return 1
        handle = client.current_handle or 0

        me = session.world_view.objects.get(bootstrap.agent_id)
        if me is None or me.position is None:
            print("FAIL: cannot see my own avatar")
            return 1

        print("--- 1. rez a prim whose creator we know ---")
        settled = await wait_until_quiet(client)
        print(f"    region settled at {settled} objects")
        session = client.current
        known = {obj.local_id for obj in session.world_view.objects.values()}
        spot = (me.position[0] + 1.5, me.position[1], me.position[2] + 1.0)
        client.queue_outbound_packet(handle, session.build_object_add_packet(position=spot))
        await asyncio.sleep(6.0)

        session = client.current
        fresh = [obj for obj in session.world_view.objects.values() if obj.local_id not in known]
        if not fresh:
            print("FAIL: nothing rezzed")
            return 1
        prim = min(fresh, key=lambda obj: math.dist(obj.position, spot))
        prim_id = prim.full_id
        print(f"    rezzed {prim_id} local={prim.local_id}")

        print("--- 2. select it ---")
        client.queue_outbound_packet(
            handle, session.build_object_select_packet([prim.local_id])
        )

        def answered() -> bool:
            current = client.current
            return current is not None and prim_id in current.world_view.object_properties

        if not await wait_for(answered, seconds=25.0):
            failures.append("no ObjectProperties arrived within 25 s of selecting")
            print("    the simulator sent no ObjectProperties for the selected prim")
            return _report(failures)

        entry = client.current.world_view.object_properties[prim_id]
        print("--- 3. what it says ---")
        print(f"    object_id        {entry.object_id}")
        print(f"    creator_id       {entry.creator_id}")
        print(f"    owner_id         {entry.owner_id}")
        print(f"    creation_date    {entry.creation_date}")
        print(f"    inventory_serial {entry.inventory_serial}")
        print(f"    name             {entry.name!r}")
        print(f"    description      {entry.description!r}")
        print(f"    touch / sit      {entry.touch_name!r} / {entry.sit_name!r}")
        print(f"    masks base/owner/next  {entry.base_mask:#x} {entry.owner_mask:#x} "
              f"{entry.next_owner_mask:#x}")
        print(f"    textures         {len(entry.texture_ids)}")

        if entry.object_id != prim_id:
            failures.append(f"properties are for {entry.object_id}, not the selected {prim_id}")
        if entry.creator_id != bootstrap.agent_id:
            failures.append(
                f"creator is {entry.creator_id}, and this agent {bootstrap.agent_id} rezzed it"
            )
        if not 0 <= entry.inventory_serial <= PLAUSIBLE_SERIAL:
            failures.append(
                f"inventory serial {entry.inventory_serial} is not a count of edits"
            )
        family = client.current.world_view.objects.get(prim_id)
        family_name = getattr(getattr(family, "properties_family", None), "name", None)
        if family_name is not None and family_name != entry.name:
            failures.append(
                f"name {entry.name!r} disagrees with the family reply's {family_name!r}"
            )
        elif family_name is not None:
            print(f"    the family reply agrees on the name: {family_name!r}")
    finally:
        if prim_id is not None and client.current is not None:
            if await take_prim_away(client, client.current_handle or 0, prim_id):
                print("    prim removed")
            else:
                print(f"    left behind -- {prim_id}")
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()

    return _report(failures)


def _report(failures: list[str]) -> int:
    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: selecting an object answers with its full properties, and the "
          "creator, serial and name are what they should be")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
