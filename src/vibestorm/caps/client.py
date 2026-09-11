"""Seed capability resolution client."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.llsd import (
    LlsdError,
    format_xml_map,
    format_xml_string_array,
    parse_xml_string_map,
    parse_xml_value,
)
from vibestorm.util.http_body import MAX_LLSD_BODY_BYTES, read_bounded
from vibestorm.util.remote_url import open_remote, require_remote_http_url


class CapabilityError(RuntimeError):
    """Raised when seed capability resolution fails."""


@dataclass(slots=True)
class CapabilityClient:
    """Resolve capability names against a seed capability URL."""

    timeout_seconds: float = 10.0

    async def resolve_seed_caps(
        self,
        seed_url: str,
        names: list[str],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> dict[str, str]:
        return await asyncio.to_thread(
            self._resolve_seed_caps_sync,
            seed_url,
            names,
            udp_listen_port,
            user_agent,
        )

    async def fetch_capability_value(
        self,
        url: str,
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        return await asyncio.to_thread(self._fetch_capability_value_sync, url, udp_listen_port, user_agent)

    async def post_capability_value(
        self,
        url: str,
        payload: dict[str, object],
        *,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        return await asyncio.to_thread(
            self._post_capability_value_sync,
            url,
            payload,
            udp_listen_port,
            user_agent,
        )

    def _resolve_seed_caps_sync(
        self,
        seed_url: str,
        names: list[str],
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> dict[str, str]:
        body = format_xml_string_array(names)
        # Before the `Request`, which raises a bare `ValueError` on a URL with
        # no scheme at all -- and that is not CapabilityError.
        require_remote_http_url(seed_url, what="the seed capability", error=CapabilityError)
        request = urllib.request.Request(
            seed_url,
            data=body,
            headers=self._request_headers(
                user_agent=user_agent,
                udp_listen_port=udp_listen_port,
                content_type="application/llsd+xml",
            ),
            method="POST",
        )
        try:
            with open_remote(
                request,
                timeout=self.timeout_seconds,
                what="the seed capability",
                error=CapabilityError,
            ) as response:
                return parse_xml_string_map(
                    read_bounded(
                        response,
                        max_bytes=MAX_LLSD_BODY_BYTES,
                        what="seed capability response",
                        error=CapabilityError,
                    )
                )
        except TimeoutError as exc:
            raise CapabilityError(f"seed capability resolution timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise CapabilityError(f"seed capability resolution timed out after {self.timeout_seconds:.1f}s") from exc
        except urllib.error.URLError as exc:
            raise CapabilityError(f"seed capability resolution failed: {exc.reason}") from exc
        except LlsdError as exc:
            # The body arrived and was not LLSD -- a proxy's error page, a
            # truncated response. Converted here rather than left to the
            # caller: `LlsdError` is caught nowhere in this client, so
            # letting it through would only rename the exception that
            # escapes.
            raise CapabilityError(f"seed capability resolution was not valid LLSD: {exc}") from exc

    def _fetch_capability_value_sync(
        self,
        url: str,
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        # Before the `Request`, which raises a bare `ValueError` on a URL with
        # no scheme at all -- and that is not CapabilityError.
        require_remote_http_url(url, what="a capability", error=CapabilityError)
        request = urllib.request.Request(
            url,
            headers=self._request_headers(
                user_agent=user_agent,
                udp_listen_port=udp_listen_port,
            ),
            method="GET",
        )
        try:
            with open_remote(
                request,
                timeout=self.timeout_seconds,
                what="a capability",
                error=CapabilityError,
            ) as response:
                return parse_xml_value(
                    read_bounded(
                        response,
                        max_bytes=MAX_LLSD_BODY_BYTES,
                        what="capability response",
                        error=CapabilityError,
                    )
                )
        except TimeoutError as exc:
            raise CapabilityError(f"capability fetch timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise CapabilityError(f"capability fetch timed out after {self.timeout_seconds:.1f}s") from exc
        except urllib.error.URLError as exc:
            raise CapabilityError(f"capability fetch failed: {exc.reason}") from exc
        except LlsdError as exc:
            # The body arrived and was not LLSD -- a proxy's error page, a
            # truncated response. Converted here rather than left to the
            # caller: `LlsdError` is caught nowhere in this client, so
            # letting it through would only rename the exception that
            # escapes.
            raise CapabilityError(f"capability fetch was not valid LLSD: {exc}") from exc

    def _post_capability_value_sync(
        self,
        url: str,
        payload: dict[str, object],
        udp_listen_port: int | None = None,
        user_agent: str = "Vibestorm",
    ) -> object:
        body = format_xml_map(payload)
        # Before the `Request`, which raises a bare `ValueError` on a URL with
        # no scheme at all -- and that is not CapabilityError.
        require_remote_http_url(url, what="a capability", error=CapabilityError)
        request = urllib.request.Request(
            url,
            data=body,
            headers=self._request_headers(
                user_agent=user_agent,
                udp_listen_port=udp_listen_port,
                content_type="application/llsd+xml",
            ),
            method="POST",
        )
        try:
            with open_remote(
                request,
                timeout=self.timeout_seconds,
                what="a capability",
                error=CapabilityError,
            ) as response:
                return parse_xml_value(
                    read_bounded(
                        response,
                        max_bytes=MAX_LLSD_BODY_BYTES,
                        what="capability response",
                        error=CapabilityError,
                    )
                )
        except TimeoutError as exc:
            raise CapabilityError(f"capability post timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise CapabilityError(f"capability post timed out after {self.timeout_seconds:.1f}s") from exc
        except urllib.error.URLError as exc:
            raise CapabilityError(f"capability post failed: {exc.reason}") from exc
        except LlsdError as exc:
            # The body arrived and was not LLSD -- a proxy's error page, a
            # truncated response. Converted here rather than left to the
            # caller: `LlsdError` is caught nowhere in this client, so
            # letting it through would only rename the exception that
            # escapes.
            raise CapabilityError(f"capability post was not valid LLSD: {exc}") from exc

    @staticmethod
    def _request_headers(
        *,
        user_agent: str,
        udp_listen_port: int | None,
        content_type: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/llsd+xml",
            "Accept-Encoding": "deflate, gzip",
            "Connection": "keep-alive",
            "Keep-Alive": "300",
            "User-Agent": user_agent,
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        if udp_listen_port is not None:
            headers["X-SecondLife-UDP-Listen-Port"] = str(udp_listen_port)
        return headers
