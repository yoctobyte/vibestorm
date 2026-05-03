import unittest
from dataclasses import dataclass

from vibestorm.bus import (
    Bus,
    BusDeliveryError,
    HandlerAlreadyRegisteredError,
    NoHandlerError,
)


@dataclass(frozen=True)
class TwoEvent:
    value: int


@dataclass(frozen=True)
class OtherEvent:
    text: str


@dataclass(frozen=True)
class DoCommand:
    target: str


@dataclass(frozen=True)
class OtherCommand:
    n: int


class BusEventTests(unittest.TestCase):
    def test_publish_calls_subscribed_handler(self) -> None:
        bus = Bus()
        received = []
        bus.subscribe(TwoEvent, lambda evt: received.append(evt.value))

        bus.publish(TwoEvent(7))
        bus.publish(TwoEvent(11))

        self.assertEqual(received, [7, 11])

    def test_publish_does_not_call_other_subscribers(self) -> None:
        bus = Bus()
        received_two: list[TwoEvent] = []
        received_other: list[OtherEvent] = []
        bus.subscribe(TwoEvent, received_two.append)
        bus.subscribe(OtherEvent, received_other.append)

        bus.publish(TwoEvent(1))
        self.assertEqual(received_two, [TwoEvent(1)])
        self.assertEqual(received_other, [])

    def test_unsubscribe_stops_delivery(self) -> None:
        bus = Bus()
        received: list[TwoEvent] = []
        sub = bus.subscribe(TwoEvent, received.append)

        bus.publish(TwoEvent(1))
        sub.cancel()
        bus.publish(TwoEvent(2))

        self.assertEqual(received, [TwoEvent(1)])

    def test_publish_with_no_subscribers_is_noop(self) -> None:
        bus = Bus()
        bus.publish(TwoEvent(42))  # no exception

    def test_publish_collects_subscriber_failures(self) -> None:
        bus = Bus()
        good_received: list[TwoEvent] = []

        def boom(_evt: TwoEvent) -> None:
            raise RuntimeError("subscriber blew up")

        bus.subscribe(TwoEvent, boom)
        bus.subscribe(TwoEvent, good_received.append)

        with self.assertRaises(BusDeliveryError) as ctx:
            bus.publish(TwoEvent(1))
        self.assertEqual(len(ctx.exception.failures), 1)
        # Good subscriber still ran despite the bad one raising.
        self.assertEqual(good_received, [TwoEvent(1)])


class BusCommandTests(unittest.TestCase):
    def test_register_and_dispatch(self) -> None:
        bus = Bus()
        seen: list[str] = []
        bus.register_handler(DoCommand, lambda cmd: seen.append(cmd.target) or "ack")

        result = bus.dispatch(DoCommand("alice"))

        self.assertEqual(seen, ["alice"])
        self.assertEqual(result, "ack")

    def test_dispatch_without_handler_raises(self) -> None:
        bus = Bus()
        with self.assertRaises(NoHandlerError):
            bus.dispatch(DoCommand("bob"))

    def test_register_twice_raises(self) -> None:
        bus = Bus()
        bus.register_handler(DoCommand, lambda c: None)
        with self.assertRaises(HandlerAlreadyRegisteredError):
            bus.register_handler(DoCommand, lambda c: None)

    def test_has_handler_check(self) -> None:
        bus = Bus()
        self.assertFalse(bus.has_handler(DoCommand))
        bus.register_handler(DoCommand, lambda c: None)
        self.assertTrue(bus.has_handler(DoCommand))
        self.assertFalse(bus.has_handler(OtherCommand))


if __name__ == "__main__":
    unittest.main()
