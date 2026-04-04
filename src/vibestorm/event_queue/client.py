"""EventQueueGet client."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.llsd import format_xml_map, parse_xml_value


@dataclass(slots=True, frozen=True)
class EventQueuePollResult:
    status: str
    payload: object | None


class EventQueueError(RuntimeError):
    """Raised when EventQueueGet polling fails."""


@dataclass(slots=True)
class EventQueueClient:
    """Boundary for EventQueueGet long-poll behavior."""

    timeout_seconds: float = 35.0

    async def poll_once(
        self,
        url: str,
        ack: int = 0,
        done: bool = False,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> EventQueuePollResult:
        return await asyncio.to_thread(self._poll_once_sync, url, ack, done, udp_listen_port, user_agent)

    def _poll_once_sync(
        self,
        url: str,
        ack: int,
        done: bool,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> EventQueuePollResult:
        body = format_xml_map({"ack": ack, "done": done})
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/llsd+xml",
                "Accept-Encoding": "deflate, gzip",
                "Connection": "keep-alive",
                "Keep-Alive": "300",
                "Content-Type": "application/llsd+xml",
                "User-Agent": user_agent,
                **(
                    {"X-SecondLife-UDP-Listen-Port": str(udp_listen_port)}
                    if udp_listen_port is not None
                    else {}
                ),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 502:
                return EventQueuePollResult(status="empty", payload=None)
            raise
        except TimeoutError as exc:
            raise EventQueueError(f"event queue poll timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise EventQueueError(f"event queue poll timed out after {self.timeout_seconds:.1f}s") from exc
        except urllib.error.URLError as exc:
            raise EventQueueError(f"event queue poll failed: {exc.reason}") from exc

        return EventQueuePollResult(status="ok", payload=parse_xml_value(payload))
