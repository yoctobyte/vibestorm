"""Seed capability resolution client."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass

from vibestorm.caps.llsd import format_xml_string_array, parse_xml_string_map


class CapabilityError(RuntimeError):
    """Raised when seed capability resolution fails."""


@dataclass(slots=True)
class CapabilityClient:
    """Resolve capability names against a seed capability URL."""

    timeout_seconds: float = 10.0

    async def resolve_seed_caps(self, seed_url: str, names: list[str]) -> dict[str, str]:
        return await asyncio.to_thread(self._resolve_seed_caps_sync, seed_url, names)

    def _resolve_seed_caps_sync(self, seed_url: str, names: list[str]) -> dict[str, str]:
        body = format_xml_string_array(names)
        request = urllib.request.Request(
            seed_url,
            data=body,
            headers={"Content-Type": "application/llsd+xml"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return parse_xml_string_map(response.read())
        except TimeoutError as exc:
            raise CapabilityError(f"seed capability resolution timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise CapabilityError(f"seed capability resolution timed out after {self.timeout_seconds:.1f}s") from exc
        except urllib.error.URLError as exc:
            raise CapabilityError(f"seed capability resolution failed: {exc.reason}") from exc
