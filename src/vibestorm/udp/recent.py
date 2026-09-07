"""The reliable sequence numbers seen lately, and no more than lately.

A reliable packet arrives with a sequence number, and the simulator resends
one it has not seen acked -- reusing the same number, so the way to tell a
resend from a new packet is to remember what has already been through. This
client remembered *everything*, in a set that nothing ever took anything out
of: one integer per reliable packet, for as long as the session lasted.

That is the only container in this client that grows with every packet rather
than with the size of the world, which makes it the one whose cost is
measured in hours rather than in prims. A soak run against the quiet local
region -- three prims and one avatar -- put it at 1,415 entries over two
hours, 671 an hour, still climbing at the end and at a rate that had not
fallen: the one container gauge in forty-odd the report called `growing`.

The window below is what a real viewer keeps: enough to recognise a resend,
which arrives within a few round trips, and nothing older. Two sets rather
than a queue, because eviction is then one assignment rather than one
bookkeeping step per packet -- when the recent half fills it becomes the old
half and a fresh one starts, so what is remembered swings between one window
and two and never exceeds two.

`seen` answers and remembers in the same call, so a sequence that arrives
again is carried into the newer half rather than left to age out. What the
window really bounds is therefore how long an *idle* sequence is remembered;
one the simulator is still resending is remembered for as long as it keeps
arriving -- which matters, because OpenSim gives up on an unacked
reliable packet never: the 60-second timeout is on the client falling silent,
not on the packet, so a chain of resends can outlast any fixed window while
each copy sits only an RTO behind the last.

A resend that arrives after a whole idle window has gone by is still handled
twice. That is a bounded cost on messages that are near enough idempotent --
an object update re-applied says the same thing -- and it is the trade every
viewer makes, because the alternative is a set that grows until the session
ends.
"""

from __future__ import annotations

from collections.abc import Iterable

#: How many sequence numbers each half remembers, so twice this at the most.
#:
#: The number that matters is how long the window lasts, not how big it is.
#: OpenSim resends an unacked reliable packet every RTO -- 1000 ms by default,
#: capped at 3000 and never backed off (LLUDPClient.cs, pinned in
#: `test/test_opensim_source_pins.py`) -- so consecutive copies of the same
#: sequence are seconds apart, and each one refreshes the window. What has to
#: fit inside it is therefore the gap between copies, not the whole chain. At
#: a busy region's few hundred packets a second, 8192 remembered is something
#: like half a minute of traffic: an order of magnitude more than one RTO.
#: Both halves full measures 505 kB, which is the point -- it is a *bound*.
SEQUENCE_MEMORY = 4096


class RecentSequences:
    """A bounded "have I seen this?" over reliable sequence numbers.

    Stands in for the `set` this used to be, and answers `in`, `add`, `update`
    and `len` the same way, so nothing that reads it had to change.
    """

    __slots__ = ("_recent", "_older", "_window")

    def __init__(self, window: int = SEQUENCE_MEMORY) -> None:
        if window < 1:
            raise ValueError(f"window must be at least 1, got {window}")
        self._window = window
        self._recent: set[int] = set()
        self._older: set[int] = set()

    def __contains__(self, sequence: int) -> bool:
        return sequence in self._recent or sequence in self._older

    def seen(self, sequence: int) -> bool:
        """Answer whether this has been through before, and remember it now.

        One call rather than a lookup and an insert, because the two have to
        happen together: asking without remembering is what lets a sequence
        the simulator is *still* resending fall out of the window while the
        answer keeps coming back yes. A caller that checks `in` and then
        returns early on a hit never refreshes anything, and the window ends
        up bounding how long a resend chain may last rather than how long a
        quiet sequence is kept -- which is the wrong bound, because OpenSim
        gives up on an unacked packet never.
        """
        if sequence in self._recent:
            return True
        # Either new, or in the half that is next to go. Both end the same
        # way: it goes into the newer half, so an arrival always buys another
        # window. Moving it rather than copying it -- `discard` is a no-op
        # when it was not there -- is what keeps the two halves disjoint, and
        # so keeps `len` a count of what is remembered rather than of set
        # entries. The gauge reads that number.
        already = sequence in self._older
        self._older.discard(sequence)
        if len(self._recent) >= self._window:
            self._older = self._recent
            self._recent = set()
        self._recent.add(sequence)
        return already

    def add(self, sequence: int) -> None:
        self.seen(sequence)

    def update(self, sequences: Iterable[int]) -> None:
        for sequence in sequences:
            self.add(sequence)

    def __len__(self) -> int:
        return len(self._recent) + len(self._older)

    def __repr__(self) -> str:
        return f"RecentSequences(remembered={len(self)}, window={self._window})"


__all__ = ["SEQUENCE_MEMORY", "RecentSequences"]
