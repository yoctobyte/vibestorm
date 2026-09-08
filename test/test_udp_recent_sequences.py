"""The bounded memory of reliable sequence numbers.

The set this replaces held every reliable packet of the whole session and
nothing ever took anything out of it -- the one container in this client that
grows with every packet rather than with the size of the world. A soak run
against the quiet local region put it at 1,415 entries over two hours, 671
an hour, still climbing at the end: the one container gauge in forty-odd the
report called `growing`.

Two things have to be true of the replacement and they pull against each
other: it must still recognise a resend, and it must not remember for ever.
Every test here is one or the other.
"""

from __future__ import annotations

import unittest

from vibestorm.udp.recent import SEQUENCE_MEMORY, RecentSequences


class RecognisingResendsTests(unittest.TestCase):
    """The half it exists for."""

    def test_a_sequence_just_seen_is_remembered(self) -> None:
        recent = RecentSequences()
        recent.add(7)
        self.assertIn(7, recent)

    def test_one_never_seen_is_not(self) -> None:
        recent = RecentSequences()
        recent.add(7)
        self.assertNotIn(8, recent)

    def test_a_whole_window_is_still_remembered(self) -> None:
        # A resend arrives seconds after its original, so everything inside
        # one window has to be recognised or the replacement is useless.
        recent = RecentSequences(window=64)
        for sequence in range(64):
            recent.add(sequence)
        for sequence in range(64):
            self.assertIn(sequence, recent, sequence)

    def test_the_oldest_survives_the_first_rollover(self) -> None:
        # The reason there are two halves rather than one: a single set
        # emptied when it fills forgets everything at once, so a resend
        # arriving just after the sweep is treated as new.
        recent = RecentSequences(window=8)
        for sequence in range(8):
            recent.add(sequence)
        recent.add(100)
        self.assertIn(0, recent)
        self.assertIn(100, recent)

    def test_update_takes_several_at_once(self) -> None:
        recent = RecentSequences()
        recent.update([1, 2, 3])
        self.assertEqual(len(recent), 3)
        self.assertIn(2, recent)


class NotRememberingForEverTests(unittest.TestCase):
    """The half the soak asked for."""

    def test_it_never_holds_more_than_two_windows(self) -> None:
        recent = RecentSequences(window=8)
        for sequence in range(10_000):
            recent.add(sequence)
            self.assertLessEqual(len(recent), 16)

    def test_something_far_enough_back_is_forgotten(self) -> None:
        # Which is the trade, stated as a test rather than left in a comment:
        # a resend that arrives two windows late is handled twice.
        recent = RecentSequences(window=8)
        for sequence in range(64):
            recent.add(sequence)
        self.assertNotIn(0, recent)

    def test_it_counts_both_halves(self) -> None:
        # `len` is the soak gauge. Counting only the newer half halves the
        # number the report is reading, which turns a container at its
        # ceiling into one that looks comfortable.
        recent = RecentSequences(window=4)
        recent.update([1, 2, 3, 4, 5])
        self.assertEqual(len(recent), 5)

    def test_one_that_spans_both_halves_is_counted_once(self) -> None:
        # And the other way: re-adding something that has rolled into the
        # older half must not put a second copy in the newer one. A set
        # deduplicates within a half and cannot across two.
        recent = RecentSequences(window=4)
        recent.update([1, 2, 3, 4, 5])
        recent.add(1)
        self.assertEqual(len(recent), 5)
        self.assertIn(1, recent)

    def test_adding_the_same_one_twice_does_not_grow_it(self) -> None:
        # `len` is read by the soak probe, so it has to count what is
        # remembered rather than how many times `add` was called.
        recent = RecentSequences(window=8)
        for _ in range(100):
            recent.add(5)
        self.assertEqual(len(recent), 1)

    def test_repeating_one_sequence_never_rolls_the_window(self) -> None:
        recent = RecentSequences(window=4)
        recent.update([1, 2, 3])
        for _ in range(100):
            recent.add(1)
        self.assertIn(1, recent)
        self.assertIn(3, recent)
        self.assertEqual(len(recent), 3)


class ArrivingAgainRefreshesItTests(unittest.TestCase):
    """The half that decides whether the window must outlast a whole chain.

    OpenSim never gives up on an unacked reliable packet -- the 60-second
    timeout is on the client falling silent, not on the packet, pinned in
    `test/test_opensim_source_pins.py`. So when our acks are being lost,
    copies of one sequence keep arriving for as long as the session lasts,
    each an RTO behind the last but the chain as a whole running to minutes.

    `seen` exists for this and not for tidiness. A caller that asks `in` and
    returns early on a hit recognises the resend but never refreshes it, so
    the entry ages out under the traffic behind it and the next copy is
    handled as new. Every test here drives it the way the receive path does:
    ask once, act on the answer.
    """

    def test_one_that_keeps_arriving_is_never_called_new_again(self) -> None:
        # Far more traffic goes by than two windows hold, and the sequence is
        # still recognised, because each arrival buys it another window.
        recent = RecentSequences(window=8)
        self.assertFalse(recent.seen(1))
        for sequence in range(1_000):
            recent.seen(sequence + 100)
            self.assertTrue(recent.seen(1), sequence)

    def test_and_one_that_stops_arriving_is(self) -> None:
        # The negative control the test above needs: it would pass just as
        # well against a window that forgot nothing at all.
        recent = RecentSequences(window=8)
        self.assertFalse(recent.seen(1))
        for sequence in range(1_000):
            recent.seen(sequence + 100)
        self.assertFalse(recent.seen(1))

    def test_the_first_sight_of_one_is_not_a_duplicate(self) -> None:
        recent = RecentSequences()
        self.assertFalse(recent.seen(7))

    def test_and_the_second_is(self) -> None:
        recent = RecentSequences()
        recent.seen(7)
        self.assertTrue(recent.seen(7))

    def test_one_recognised_from_the_older_half_is_a_duplicate_too(self) -> None:
        # The case the refresh changes the storage of: the answer must not
        # change with it. Moving the entry forward still means "seen".
        recent = RecentSequences(window=4)
        recent.seen(1)
        for sequence in range(6):
            recent.seen(sequence + 100)
        self.assertTrue(recent.seen(1))

    def test_refreshing_one_does_not_put_a_second_copy_in(self) -> None:
        # Moving it forward rather than copying it: the two halves stay
        # disjoint, so `len` is still what the soak gauge reads it as.
        recent = RecentSequences(window=4)
        recent.update([1, 2, 3, 4, 5])
        before = len(recent)
        recent.seen(1)
        self.assertEqual(len(recent), before)

    def test_refreshing_still_cannot_grow_it_past_two_windows(self) -> None:
        # And the bound survives the refresh: a stream that alternates
        # between new sequences and old ones is the shape most likely to
        # defeat it.
        recent = RecentSequences(window=8)
        for sequence in range(10_000):
            recent.seen(sequence)
            recent.seen(sequence // 2)
            self.assertLessEqual(len(recent), 16, sequence)

    def test_add_is_the_same_thing_with_the_answer_dropped(self) -> None:
        # `add` and `update` are kept because the tests and the gauge read
        # more naturally through them, so they must not be a second, staler
        # implementation of the same step.
        through_add = RecentSequences(window=4)
        through_seen = RecentSequences(window=4)
        for sequence in [1, 2, 3, 4, 5, 1, 6, 2]:
            through_add.add(sequence)
            through_seen.seen(sequence)
        self.assertEqual(len(through_add), len(through_seen))
        for sequence in range(10):
            self.assertEqual(
                sequence in through_add, sequence in through_seen, sequence
            )


class ShippedWindowTests(unittest.TestCase):
    def test_the_window_is_large_enough_to_outlast_a_resend(self) -> None:
        # OpenSim resends an unacked reliable packet every RTO -- 1000 ms by
        # default, capped at 3000. At a few hundred packets a second, two
        # windows of this size is tens of seconds of traffic, which is an
        # order of magnitude more than a resend needs. The tests above use
        # tiny windows so they say what they mean; this one holds the number
        # that actually ships.
        self.assertGreaterEqual(SEQUENCE_MEMORY, 1024)

    def test_a_window_of_nothing_is_refused(self) -> None:
        # Zero would remember nothing and call every resend a new packet.
        with self.assertRaises(ValueError):
            RecentSequences(window=0)


class StandsInForTheSetTests(unittest.TestCase):
    """It replaced a `set` in a live session, and nothing that reads it changed."""

    def test_a_fresh_one_is_empty(self) -> None:
        self.assertEqual(len(RecentSequences()), 0)

    def test_the_session_starts_with_a_bounded_one(self) -> None:
        from pathlib import Path
        from uuid import UUID

        from vibestorm.login.models import LoginBootstrap
        from vibestorm.udp.dispatch import MessageDispatcher
        from vibestorm.udp.session import LiveCircuitSession

        bootstrap = LoginBootstrap(
            agent_id=UUID(int=1),
            session_id=UUID(int=2),
            secure_session_id=UUID(int=3),
            circuit_code=1,
            sim_ip="127.0.0.1",
            sim_port=9000,
            seed_capability="http://127.0.0.1:9000/caps/seed",
            region_x=256000,
            region_y=256000,
            message="ok",
        )
        session = LiveCircuitSession(
            bootstrap,
            MessageDispatcher.from_repo_root(Path(__file__).resolve().parents[1]),
        )
        self.assertIsInstance(session.seen_reliable_sequences, RecentSequences)
        # And the thing that made it worth replacing: it is not a plain set,
        # so it cannot go on growing for the length of a session.
        for sequence in range(SEQUENCE_MEMORY * 3):
            session.seen_reliable_sequences.add(sequence)
        self.assertLessEqual(len(session.seen_reliable_sequences), SEQUENCE_MEMORY * 2)


class CapacityTests(unittest.TestCase):
    """The bound, published so that a running client can be held to it.

    `SEQUENCE_MEMORY * 2` appeared in a docstring, in a comment beside the
    soak gauge, and in the assertion above -- three copies of a number the
    code did not state anywhere. `capacity` states it once, the soak log
    records it beside the count, and `growth_report` reads a `.limit` row as
    the ceiling for the row it names: the run says whether the bound held.
    """

    def test_the_capacity_is_both_halves_full(self) -> None:
        self.assertEqual(RecentSequences(window=64).capacity, 128)

    def test_it_follows_the_window_it_was_given(self) -> None:
        self.assertEqual(RecentSequences(window=7).capacity, 14)

    def test_the_default_capacity_is_the_documented_one(self) -> None:
        self.assertEqual(RecentSequences().capacity, SEQUENCE_MEMORY * 2)

    def test_nothing_ever_exceeds_it(self) -> None:
        """Checked at every step rather than at the end, because the swap
        happens on an insert and a bound that only holds between inserts is
        not one the soak's ceiling row could be trusted to describe."""
        recent = RecentSequences(window=16)
        for sequence in range(16 * 10):
            recent.add(sequence)
            self.assertLessEqual(len(recent), recent.capacity)

    def test_a_repeated_sequence_does_not_grow_it_either(self) -> None:
        recent = RecentSequences(window=8)
        for _ in range(5):
            for sequence in range(20):
                recent.seen(sequence)
                self.assertLessEqual(len(recent), recent.capacity)
