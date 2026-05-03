"""In-process command/event bus for the WorldClient.

Designed so a future IPC server (Phase 6, for separate-process tk tools)
can sit in front of the same publish/dispatch surface without touching
consumers.

Usage:

    bus = Bus()
    bus.subscribe(ChatLocal, lambda evt: print(evt.message))
    bus.register_handler(SendChat, lambda cmd: world.build_chat_packet(...))
    bus.publish(ChatLocal(...))
    packets = bus.dispatch(SendChat("hi"))

Subscribers are called synchronously, in registration order. Exceptions in
one subscriber do not prevent others from running; they are collected on
the bus and re-raised as ``BusDeliveryError`` after all subscribers have
fired (so a buggy subscriber is loud but not silent).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

E = TypeVar("E")
C = TypeVar("C")


class BusError(RuntimeError):
    """Base class for bus errors."""


class HandlerAlreadyRegisteredError(BusError):
    """Raised when register_handler is called twice for the same command type."""


class NoHandlerError(BusError):
    """Raised when dispatch is called with a command that has no registered handler."""


@dataclass(frozen=True)
class BusDeliveryError(BusError):
    """Raised after publish when one or more subscribers raised."""
    event: object
    failures: tuple[BaseException, ...]

    def __str__(self) -> str:
        return f"{len(self.failures)} subscriber(s) failed for {type(self.event).__name__}"


@dataclass(slots=True)
class Subscription:
    event_type: type
    handler: Callable[..., Any]
    _bus: "Bus"
    _alive: bool = True

    def cancel(self) -> None:
        if not self._alive:
            return
        self._alive = False
        self._bus._unsubscribe(self)


@dataclass(slots=True)
class Bus:
    _subscribers: dict[type, list[Subscription]] = field(default_factory=lambda: defaultdict(list))
    _handlers: dict[type, Callable[..., Any]] = field(default_factory=dict)

    # ---- events ----------------------------------------------------------

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> Subscription:
        sub = Subscription(event_type=event_type, handler=handler, _bus=self)
        self._subscribers[event_type].append(sub)
        return sub

    def publish(self, event: object) -> None:
        subs = list(self._subscribers.get(type(event), ()))
        failures: list[BaseException] = []
        for sub in subs:
            if not sub._alive:
                continue
            try:
                sub.handler(event)
            except BaseException as exc:  # noqa: BLE001 - collect and rethrow
                failures.append(exc)
        if failures:
            raise BusDeliveryError(event=event, failures=tuple(failures))

    def _unsubscribe(self, sub: Subscription) -> None:
        bucket = self._subscribers.get(sub.event_type)
        if bucket is None:
            return
        try:
            bucket.remove(sub)
        except ValueError:
            pass

    # ---- commands --------------------------------------------------------

    def register_handler(self, command_type: type[C], handler: Callable[[C], Any]) -> None:
        if command_type in self._handlers:
            raise HandlerAlreadyRegisteredError(
                f"command handler already registered for {command_type.__name__}"
            )
        self._handlers[command_type] = handler

    def dispatch(self, command: object) -> Any:
        handler = self._handlers.get(type(command))
        if handler is None:
            raise NoHandlerError(f"no handler registered for {type(command).__name__}")
        return handler(command)

    def has_handler(self, command_type: type) -> bool:
        return command_type in self._handlers


__all__ = [
    "Bus",
    "BusDeliveryError",
    "BusError",
    "HandlerAlreadyRegisteredError",
    "NoHandlerError",
    "Subscription",
]
