"""Live check: is the region next door resending packets this client never acked?

A `RegionHandshake` from a neighbour arrives more than once -- four times over
ninety seconds, in the run recorded in the handoff -- and nothing in the
protocol asks for that. There are two stories that fit, and only one bit on
the wire separates them:

* **A retransmit.** OpenSim sends `RegionHandshake` with `MSG_RELIABLE`
  (LLClientView.cs), resends anything unacked past the RTO -- 1000 ms by
  default, capped at 3000 -- and sets `MSG_RESENT` when it does
  (LLUDPServer.ResendUnacked). A resend reuses the original sequence number.
* **A fresh send.** Then it is a new sequence number with the resent bit
  clear, and the cause is one of OpenSim's own senders rather than this
  client's acking.

Reading the source has already taken the second story most of the way. There
are exactly three `.SendRegionHandshake()` call sites, pinned in
`test/test_opensim_source_pins.py`, and a *child* circuit can reach two of
them: the circuit being created, and `SendInitialData` behind the terrain-PBR
flag. Neither is periodic, so two of the four are explained and two are not.

Which makes this a yes-or-no rather than a fishing trip: if the unexplained
ones reuse a sequence number and carry `MSG_RESENT`, they are retransmits and
the acking is at fault; if they are fresh, there is a sender the reading
missed. Record every packet the neighbour circuit receives with its sequence,
its resent bit and its name, and how many acks sit undelivered in
`queued_acks` at each moment.

**The answer, on 2026-09-07, was the first story**, and it was not close: 22
of 53 reliable packets came back RESENT, one `LayerData` six times in a second
and a half, and the handshake was a retransmit of itself. The cause was
`_pump_neighbours` running only in the receive-timeout branch. Reasoning had
said that could not matter -- `ACK_BATCH` is 10, `receive_timeout_seconds` is
0.25 -- and missed that OpenSim clamps its RTO below at `m_minRTO`, 250 ms, so
on a local sim the resend timer and the flush timer are the same length. The
fix moved one call into the loop body; a second run of this probe showed zero
RESENT, zero repeats, and the ack high-water mark down from nine to one.

Keep it. It is the only thing here that can tell a retransmit from a fresh
send, so it is what any future change to the acking has to be checked against,
and it is three minutes.

    set -a; . local/vibestorm-login.env; set +a
    .venv/bin/python tools/probe_neighbour_acks.py [seconds]
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vibestorm.login.client import LoginClient  # noqa: E402
from vibestorm.login.models import LoginCredentials, LoginRequest  # noqa: E402
from vibestorm.udp.dispatch import MessageDispatcher  # noqa: E402
from vibestorm.udp.neighbour import NeighbourCircuit  # noqa: E402
from vibestorm.udp.packet import parse_packet_header  # noqa: E402
from vibestorm.udp.session import SessionConfig, run_live_session  # noqa: E402
from vibestorm.udp.world_client import WorldClient  # noqa: E402

DEFAULT_SECONDS = 120.0


def _header(payload: bytes):
    """The header of a packet as it arrived.

    Not zero-decoded first, and it must not be: zerocoding compresses the
    message body only, so the flags byte and the sequence number are plain in
    every packet whether the zerocoded flag is set or not.
    """
    try:
        return parse_packet_header(payload)
    except Exception:  # noqa: BLE001 - a probe reports, it does not raise
        return None


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS

    #: (elapsed, handle, sequence, resent, reliable, acks queued after, name)
    seen: list[tuple[float, int, int, bool, bool, int, str]] = []
    started = time.monotonic()
    original = NeighbourCircuit.handle_incoming

    def watched(self, payload: bytes):
        header = _header(payload)
        # `received` is a Counter the circuit keeps by message name, so the
        # one key that moved across the call names this packet -- without
        # decoding it a second time, and with the circuit's own names for the
        # things it could not decode. `Counter` subtraction drops zeros and
        # negatives, so what survives is what this packet added: one key
        # usually, none for a packet dropped before anything was counted, and
        # two for one that decoded and then failed -- which the circuit
        # records as both `X` and `X:undecodable`. Prefer the plain name.
        before = self.received.copy()
        replies = original(self, payload)
        moved = sorted(self.received - before, key=lambda key: (":" in key, key))
        name = moved[0] if moved else "?"
        if header is not None:
            seen.append(
                (
                    time.monotonic() - started,
                    self.handle,
                    header.sequence,
                    header.is_resent,
                    header.is_reliable,
                    len(self.queued_acks),
                    name,
                )
            )
        return replies

    NeighbourCircuit.handle_incoming = watched  # type: ignore[method-assign]
    try:
        request = LoginRequest(
            login_uri=os.environ["VIBESTORM_LOGIN_URI"],
            credentials=LoginCredentials(
                first=os.environ["VIBESTORM_FIRST_NAME"],
                last=os.environ["VIBESTORM_LAST_NAME"],
                password=os.environ["VIBESTORM_PASSWORD"],
            ),
            start=os.environ.get(
                "VIBESTORM_START_LOCATION", "uri:Vibestorm Test&128&128&25"
            ),
            platform=platform.system(),
        )
        bootstrap = await LoginClient().login(request)
        client = WorldClient()
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_live_session(
                bootstrap,
                MessageDispatcher.from_repo_root(Path(__file__).resolve().parents[1]),
                config=SessionConfig(duration_seconds=seconds + 30.0),
                world_client=client,
                stop_event=stop,
            )
        )
        try:
            await asyncio.sleep(seconds)
        finally:
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
    finally:
        NeighbourCircuit.handle_incoming = original  # type: ignore[method-assign]

    return report(seen, seconds, client)


def report(seen, seconds: float, client) -> int:
    if not seen:
        print("no neighbour packets at all -- is a second region running?")
        return 1

    print(f"--- {len(seen)} neighbour packets over {seconds:.0f} s ---\n")

    resent = [row for row in seen if row[3]]
    reliable = [row for row in seen if row[4]]
    print(f"reliable          {len(reliable)}")
    print(f"marked RESENT     {len(resent)}")

    # The decisive count: a sequence number seen more than once is the
    # simulator sending the same packet again, whatever the flag says.
    by_sequence: Counter[tuple[int, int]] = Counter(
        (row[1], row[2]) for row in seen
    )
    repeats = {key: n for key, n in by_sequence.items() if n > 1}
    print(f"sequences seen    {len(by_sequence)}")
    print(f"seen more than once {len(repeats)}")
    print()

    for (handle, sequence), n in sorted(repeats.items(), key=lambda kv: -kv[1])[:8]:
        copies = [row for row in seen if row[1] == handle and row[2] == sequence]
        times = [f"{row[0]:.1f}" for row in copies]
        flags = {row[3] for row in copies}
        names = {row[6] for row in copies}
        print(
            f"  handle={handle:#018x} seq={sequence} x{n} {'/'.join(sorted(names))} "
            f"resent_bits={sorted(flags)} at {', '.join(times[:12])}"
        )

    # The question this probe exists for, asked of the one message that
    # prompted it. Every handshake, with its sequence and its resent bit: if
    # the sequences differ and no bit is set, these are distinct sends and the
    # cause is a sender, not this client's acking.
    handshakes = [row for row in seen if row[6] == "RegionHandshake"]
    print(f"\n--- RegionHandshake: {len(handshakes)} copies ---")
    for elapsed, handle, sequence, is_resent, is_reliable, queued, _ in handshakes:
        print(
            f"  t={elapsed:7.1f}s handle={handle:#018x} seq={sequence:<6} "
            f"{'RESENT' if is_resent else 'fresh '} "
            f"{'reliable' if is_reliable else 'unreliable'} acks_queued_after={queued}"
        )
    if len(handshakes) > 1:
        distinct = len({(row[1], row[2]) for row in handshakes})
        if distinct == len(handshakes) and not any(row[3] for row in handshakes):
            print(
                "  -> all distinct sequences, none marked RESENT: "
                "these are separate sends, not retransmits."
            )
        elif any(row[3] for row in handshakes):
            print("  -> at least one is a retransmit; the acking is at fault.")

    print()
    high = max(row[5] for row in seen)
    ending = [row[5] for row in seen[-5:]]
    print(f"queued acks, high water mark  {high}")
    print(f"queued acks, last five packets {ending}")

    session = client.current
    if session is not None:
        for handle, circuit in session.neighbours.items():
            print(
                f"\n{circuit.region_name or '?'} {handle:#018x}: "
                f"handshakes={circuit.handshakes_seen} "
                f"terrain={circuit.terrain_packets} "
                f"acks still queued={len(circuit.queued_acks)}"
            )
            for name, count in circuit.received.most_common(8):
                print(f"    {name:32s} {count}")

    # Not the neighbour's problem, but the same run answers it for free: this
    # client records the reliable packets it sent and never resends them, so
    # anything still here at the end was lost and nothing tried again.
    if session is not None and session.pending_reliable:
        print(f"\n--- {len(session.pending_reliable)} of our own reliable packets never acked ---")
        for sequence, label in sorted(session.pending_reliable.items()):
            print(f"  seq={sequence:<6} {label}")

    print()
    if repeats:
        print(
            "VERDICT: the simulator is sending packets this client already "
            "received again, which is what an unacked reliable packet does."
        )
    else:
        print("VERDICT: no sequence arrived twice; the repeats are fresh sends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
