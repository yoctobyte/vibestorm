"""Client for the OpenSim/SL GetTexture capability."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from uuid import UUID


class GetTextureError(RuntimeError):
    """Raised when a GetTexture fetch fails."""


@dataclass(slots=True, frozen=True)
class FetchedTexture:
    texture_id: UUID
    content_type: str
    data: bytes


@dataclass(slots=True)
class GetTextureClient:
    """Fetch texture asset bytes from the GetTexture capability."""

    timeout_seconds: float = 10.0

    async def fetch(
        self,
        capability_url: str,
        texture_id: UUID,
        *,
        user_agent: str = "Vibestorm",
    ) -> FetchedTexture:
        return await asyncio.to_thread(
            self._fetch_sync,
            capability_url,
            texture_id,
            user_agent,
        )

    def _fetch_sync(
        self,
        capability_url: str,
        texture_id: UUID,
        user_agent: str,
    ) -> FetchedTexture:
        query = urllib.parse.urlencode({"texture_id": str(texture_id)})
        separator = "&" if urllib.parse.urlparse(capability_url).query else "?"
        url = f"{capability_url}{separator}{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "image/x-j2c, image/jp2, */*",
                "User-Agent": user_agent,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise GetTextureError(
                        f"GetTexture {texture_id} returned HTTP {status}"
                    )
                content_type = response.headers.get_content_type()
                data = response.read()
        except TimeoutError as exc:
            raise GetTextureError(
                f"GetTexture {texture_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise GetTextureError(
                f"GetTexture {texture_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.HTTPError as exc:
            raise GetTextureError(
                f"GetTexture {texture_id} failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise GetTextureError(
                f"GetTexture {texture_id} failed: {exc.reason}"
            ) from exc

        if not data:
            raise GetTextureError(f"GetTexture {texture_id} returned empty body")

        return FetchedTexture(
            texture_id=texture_id,
            content_type=content_type,
            data=data,
        )
