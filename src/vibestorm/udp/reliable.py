"""What a circuit remembers about a reliable packet it has sent.

Reliable means the simulator is expected to acknowledge it, and both sides of
this protocol are expected to send it again until that happens. OpenSim does:
it resends anything unacked every RTO for as long as the client is talking
and gives up never, which is pinned in `test/test_opensim_source_pins.py`.
This client did not, for either its root circuit or its child ones -- it sent
each packet once and forgot it, keeping only a label for the report.

A soak run put six such packets in the root circuit's `pending_reliable` at
the end of two hours: six sent, never acknowledged, nothing tried again. The
gauge read `settled`, which is the report being right about the shape and
silent about the meaning -- a container that stops growing because the losses
stopped looks exactly like one that stops growing because nothing ever leaves.

The one rule a resend must obey is that it keeps its sequence number. A
resend with a fresh number is a *second packet*: the simulator's duplicate
detection cannot see it for what it is, and a client doing that would be
manufacturing the duplicate storm a viewer otherwise spends its effort
surviving. Hence `_marked_resent` rather than rebuilding the message, and
hence keeping the bytes rather than the arguments they were built from.

The two circuits differ only in their clock -- the root one has `now` passed
into nearly everything, a child circuit has none -- so the timings live here
and the sweeps live with their circuits.
"""

from __future__ import annotations

from dataclasses import dataclass

from vibestorm.udp.packet import LL_RESENT_FLAG

#: How long to wait for an ack before sending a reliable packet again.
#:
#: Ours to choose, not the simulator's. OpenSim's own default is 1000 ms and
#: it clamps its measured value into [250, 3000]; a second is the same order
#: and errs towards patience, because the cost of resending too eagerly is
#: paid on every packet and the cost of resending too late is one extra
#: second on a packet that was lost anyway.
RELIABLE_RESEND_AFTER_S = 1.0

#: How many times to send one packet before giving up on it.
#:
#: OpenSim gives up never, which is right for a simulator: it has one client
#: to look after and stops when that client goes quiet. A viewer that never
#: gives up keeps talking to a simulator that has stopped listening, and the
#: session has its own timeout for that case. Five attempts over five seconds
#: is far past any plausible loss on a working link.
RELIABLE_RESEND_ATTEMPTS = 5

#: How many unacked packets to hold at all.
#:
#: A bound, because this holds whole packets rather than short labels: a
#: simulator that stopped acking would otherwise turn a stalled session into
#: a growing one. Reaching it means something is badly wrong already, and
#: dropping the oldest is the least surprising thing to do about that.
PENDING_RELIABLE_LIMIT = 256


@dataclass(slots=True)
class PendingReliable:
    """One reliable packet that has gone out and not been acked."""

    label: str
    #: The bytes exactly as they went out the first time, so a resend is the
    #: same packet rather than a new one wearing the same sequence number.
    packet: bytes
    #: When it went out, or `None` when whoever built it did not know the
    #: time. Inventing a zero here is worse than admitting the gap: it makes
    #: the packet overdue by the whole monotonic clock, so the first sweep
    #: resends it immediately. A sweep that sees `None` starts the clock
    #: instead, which costs one interval and cannot fire early.
    sent_at: float | None
    attempts: int = 1

    def due(self, now: float) -> bool:
        """Is it time to send this one again?

        Starts the clock as a side effect when there was none, and answers
        `False` for that call -- see `sent_at`.
        """
        if self.sent_at is None:
            self.sent_at = now
            return False
        return now - self.sent_at >= RELIABLE_RESEND_AFTER_S

    @property
    def spent(self) -> bool:
        return self.attempts >= RELIABLE_RESEND_ATTEMPTS

    def going_again(self, now: float) -> bytes:
        """Mark another attempt and hand back the packet to send."""
        self.sent_at = now
        self.attempts += 1
        return marked_resent(self.packet)


def marked_resent(packet: bytes) -> bytes:
    """The same packet with `MSG_RESENT` set.

    One bit in the first byte, which is the flags byte and is never zerocoded
    -- zerocoding compresses the body and copies the header through. So this
    works on a packet that was compressed on the way out, and it leaves the
    sequence number where it was, which is the whole point.
    """
    return bytes([packet[0] | LL_RESENT_FLAG]) + packet[1:]


def remember(
    pending: dict[int, PendingReliable],
    sequence: int,
    *,
    label: str,
    packet: bytes,
    now: float | None,
) -> list[tuple[int, PendingReliable]]:
    """Record a sent packet, and say what had to be dropped to fit it in."""
    dropped: list[tuple[int, PendingReliable]] = []
    while len(pending) >= PENDING_RELIABLE_LIMIT:
        oldest = next(iter(pending))
        dropped.append((oldest, pending.pop(oldest)))
    pending[sequence] = PendingReliable(label=label, packet=packet, sent_at=now)
    return dropped


__all__ = [
    "PENDING_RELIABLE_LIMIT",
    "RELIABLE_RESEND_AFTER_S",
    "RELIABLE_RESEND_ATTEMPTS",
    "PendingReliable",
    "marked_resent",
    "remember",
]
