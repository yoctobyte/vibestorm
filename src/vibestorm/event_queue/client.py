"""EventQueueGet client."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.llsd import LlsdError, format_xml_map, parse_xml_value
from vibestorm.util.http_body import MAX_LLSD_BODY_BYTES, read_bounded
from vibestorm.util.remote_url import open_remote, require_remote_http_url


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
        # Before the `Request`, which raises a bare `ValueError` on a URL with
        # no scheme at all -- and that is not EventQueueError.
        require_remote_http_url(url, what="the event queue", error=EventQueueError)
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
            with open_remote(
                request,
                timeout=self.timeout_seconds,
                what="the event queue",
                error=EventQueueError,
            ) as response:
                payload = read_bounded(
                    response,
                    max_bytes=MAX_LLSD_BODY_BYTES,
                    what="event queue poll",
                    error=EventQueueError,
                )
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

        try:
            return EventQueuePollResult(status="ok", payload=parse_xml_value(payload))
        except LlsdError as exc:
            # Outside the request block on purpose -- the body arrived, and
            # what failed is reading it. The poll loop upstream catches
            # `EventQueueError` and backs off; it would catch a bare
            # `LlsdError` only through its blanket handler, which exists for
            # the cases nobody predicted rather than for this one.
            raise EventQueueError(f"event queue poll returned invalid LLSD: {exc}") from exc
