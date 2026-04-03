"""Seed capability resolution client."""

from __future__ import annotations

import asyncio
import urllib.request

from vibestorm.caps.llsd import format_xml_string_array, parse_xml_string_map


class CapabilityClient:
    """Resolve capability names against a seed capability URL."""

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
        with urllib.request.urlopen(request, timeout=10) as response:
            return parse_xml_string_map(response.read())
