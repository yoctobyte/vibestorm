"""Client for the OpenSim/SL GetMesh capability."""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from uuid import UUID


class GetMeshError(RuntimeError):
    """Raised when a GetMesh fetch fails."""


@dataclass(slots=True, frozen=True)
class FetchedMesh:
    mesh_id: UUID
    content_type: str
    data: bytes


@dataclass(slots=True)
class GetMeshClient:
    """Fetch mesh asset bytes from the GetMesh capability."""

    timeout_seconds: float = 10.0

    async def fetch(
        self,
        capability_url: str,
        mesh_id: UUID,
        *,
        user_agent: str = "Vibestorm",
    ) -> FetchedMesh:
        return await asyncio.to_thread(
            self._fetch_sync,
            capability_url,
            mesh_id,
            user_agent,
        )

    def _fetch_sync(
        self,
        capability_url: str,
        mesh_id: UUID,
        user_agent: str,
    ) -> FetchedMesh:
        query = urllib.parse.urlencode({"mesh_id": str(mesh_id)})
        separator = "&" if urllib.parse.urlparse(capability_url).query else "?"
        url = f"{capability_url}{separator}{query}"

        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.ll.mesh, application/octet-stream, */*",
                "User-Agent": user_agent,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise GetMeshError(f"GetMesh {mesh_id} returned HTTP {status}")
                content_type = response.headers.get_content_type()
                data = response.read()
        except TimeoutError as exc:
            raise GetMeshError(
                f"GetMesh {mesh_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise GetMeshError(
                f"GetMesh {mesh_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.HTTPError as exc:
            raise GetMeshError(f"GetMesh {mesh_id} failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise GetMeshError(f"GetMesh {mesh_id} failed: {exc.reason}") from exc

        if not data:
            raise GetMeshError(f"GetMesh {mesh_id} returned empty body")

        return FetchedMesh(mesh_id=mesh_id, content_type=content_type, data=data)
