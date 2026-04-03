"""EventQueueGet client."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.llsd import format_xml_map, parse_xml_value


@dataclass(slots=True, frozen=True)
class EventQueuePollResult:
    status: str
    payload: object | None


class EventQueueClient:
    """Boundary for EventQueueGet long-poll behavior."""

    async def poll_once(self, url: str, ack: int = 0, done: bool = False) -> EventQueuePollResult:
        return await asyncio.to_thread(self._poll_once_sync, url, ack, done)

    def _poll_once_sync(self, url: str, ack: int, done: bool) -> EventQueuePollResult:
        body = format_xml_map({"ack": ack, "done": done})
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/llsd+xml"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 502:
                return EventQueuePollResult(status="empty", payload=None)
            raise

        return EventQueuePollResult(status="ok", payload=parse_xml_value(payload))
