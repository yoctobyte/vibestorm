"""Client for the ViewerAsset capability — one HTTP fetch for any asset type.

``ViewerAsset`` is the generic sibling of ``GetTexture`` and ``GetMesh``: the
same ``?<key>=<uuid>`` shape, but the key selects the asset type rather than
being fixed by the capability. It has been resolved every session since the
seed-cap list was written and had no client until now, so notecards, scripts,
animations and sounds could only come down the UDP ``TransferRequest`` channel,
which is slower and — for task-inventory assets — unreliable.

The query keys are OpenSim's, from ``GetAssetsHandler.queryTypes``. There is no
generic ``asset_id``: a request whose key is not in that table is answered 404
before the asset service is consulted, so the caller must know the type. That
is the one thing this capability asks for that ``GetTexture`` does not.

What it does *not* enforce, despite appearances: ``GetAssetsHandler`` compares
``asset.Type`` against the type the key implies and, if they disagree, logs
``asset with wrong type`` and serves the bytes anyway — the ``return`` beneath
that warning is commented out in the source. So a wrong key still yields the
right asset, and a client cannot use a successful fetch as evidence that its
idea of the type was right. :func:`asset_type_query_key` is offered so callers
name the type deliberately rather than reaching for whichever key is nearest.

Confirmed live on 2026-08-14, and it goes further than the source reading
suggested: the library's ``Shirt`` (clothing) was fetched under ``clothing_id``,
``bodypart_id`` *and* ``gesture_id``, and all three returned the same 563 bytes
with ``Content-Type: application/vnd.ll.clothing``. The key therefore only has
to be *recognised*; it neither selects nor constrains what comes back, and the
response's content type reports the asset's real type regardless of what was
asked for. That is what :func:`asset_type_from_content_type` is for — it lets a
caller fetch an asset whose type it cannot determine in advance and then learn
the type from the server rather than assert it.

The concrete case that needs this: ``AgentWearablesUpdate`` identifies each
worn item by libomv's *wearable* type (shirt, hair, ...), not by asset type,
and nothing in ``opensim-source/`` maps one to the other — ``WearableType`` is
used there but defined in libomv. Rather than reconstruct that table from
memory, this client fetches with any valid wearable key and reads the answer
off the response.
"""

from __future__ import annotations

import asyncio
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from uuid import UUID


class ViewerAssetError(RuntimeError):
    """Raised when a ViewerAsset fetch fails."""


#: OpenSim's query key for each asset type number this client can source.
#:
#: The keys come from ``GetAssetsHandler.queryTypes``, which is sourceable. The
#: *numbers* are not: ``AssetType`` is libomv's enum and the DLL is all that
#: ships in ``opensim-source/``. What is sourceable is the subset LSL exposes
#: as ``INVENTORY_*``, because ``llGetInventoryType`` returns ``item.Type`` —
#: the same asset numbering. So this table is exactly the intersection of
#: OpenSim's key list and that pinned subset, which
#: ``caps/inventory_types.ASSET_TYPE_NAMES`` also draws from.
#:
#: Absent for that reason, not because the capability lacks them:
#: ``callcard_id``, ``lslbyte_id``, ``txtr_tga_id``, ``snd_wav_id``,
#: ``img_tga_id``, ``jpeg_id`` and ``mesh_id``. Meshes are no loss — ``GetMesh``
#: is implemented and verified. The rest can be added the moment their numbers
#: have a source; inventing them would produce 404s indistinguishable from a
#: missing asset.
#:
#: LSLText accepts two keys, ``script_id`` and ``lsltext_id``. Both work; this
#: is the one OpenSim lists first.
ASSET_TYPE_QUERY_KEYS: dict[int, str] = {
    0: "texture_id",  # Texture
    1: "sound_id",  # Sound
    3: "landmark_id",  # Landmark
    5: "clothing_id",  # Clothing
    6: "object_id",  # Object
    7: "notecard_id",  # Notecard
    10: "script_id",  # LSLText
    13: "bodypart_id",  # Bodypart
    20: "animatn_id",  # Animation
    21: "gesture_id",  # Gesture
    56: "settings_id",  # Settings
    57: "material_id",  # Material
}


def asset_type_query_key(asset_type: int) -> str:
    """The ViewerAsset query key for an OpenSim asset type number.

    Raises rather than falling back to a plausible key. OpenSim answers an
    unrecognised key with 404 before it looks the asset up, so guessing turns
    "this client does not know that type" into "the sim does not have it".
    """
    try:
        return ASSET_TYPE_QUERY_KEYS[int(asset_type)]
    except (KeyError, TypeError, ValueError):
        raise ViewerAssetError(
            f"no ViewerAsset query key for asset type {asset_type!r}; "
            f"known types: {sorted(ASSET_TYPE_QUERY_KEYS)}"
        ) from None


#: The content type OpenSim serves for each asset type number, from the
#: ``TypeMapping`` table in ``Framework/SLUtil.cs``.
#:
#: Restricted to the same LSL-pinned numbers as `ASSET_TYPE_QUERY_KEYS`, and for
#: the same reason: `SLUtil.cs` writes the types as ``AssetType.Clothing``, and
#: turning those names into numbers needs libomv's enum. Where the table lists
#: several content types for one asset type, this holds the first — the one
#: OpenSim serves — not the legacy ``application/x-metaverse-*`` aliases it also
#: accepts.
#:
#: Texture and Material are absent on purpose rather than missing: `SLUtil.cs`
#: maps ``image/x-j2c`` to Texture from *two* rows and ``application/llsd+xml``
#: to Material, and that last one is shared with other LLSD payloads, so a
#: reverse lookup on it would be a guess.
CONTENT_TYPE_ASSET_TYPES: dict[str, int] = {
    "audio/ogg": 1,  # Sound
    "application/vnd.ll.landmark": 3,  # Landmark
    "application/vnd.ll.clothing": 5,  # Clothing
    "application/vnd.ll.primitive": 6,  # Object
    "application/vnd.ll.notecard": 7,  # Notecard
    "application/vnd.ll.lsltext": 10,  # LSLText
    "application/vnd.ll.bodypart": 13,  # Bodypart
    "application/vnd.ll.animation": 20,  # Animation
    "application/vnd.ll.gesture": 21,  # Gesture
}


def asset_type_from_content_type(content_type: str) -> int | None:
    """The asset type OpenSim's response claims, or None if unrecognised.

    None rather than a raise or a default: this answers "what did the server
    say this is", and "it said something this client does not know" is a real
    answer. A caller that already knows the type from inventory should keep
    its own value; this is for the case where it does not.

    Parameters after a ``;`` are ignored, so ``application/vnd.ll.gesture;
    charset=utf-8`` resolves like the bare type.
    """
    return CONTENT_TYPE_ASSET_TYPES.get(content_type.split(";")[0].strip().lower())


@dataclass(slots=True, frozen=True)
class FetchedAsset:
    asset_id: UUID
    asset_type: int
    query_key: str
    content_type: str
    data: bytes


@dataclass(slots=True)
class ViewerAssetClient:
    """Fetch any asset type through the ViewerAsset capability."""

    timeout_seconds: float = 10.0

    async def fetch(
        self,
        capability_url: str,
        asset_id: UUID,
        asset_type: int,
        *,
        user_agent: str = "Vibestorm",
    ) -> FetchedAsset:
        return await asyncio.to_thread(
            self._fetch_sync, capability_url, asset_id, asset_type, user_agent
        )

    def _fetch_sync(
        self,
        capability_url: str,
        asset_id: UUID,
        asset_type: int,
        user_agent: str,
    ) -> FetchedAsset:
        query_key = asset_type_query_key(asset_type)
        query = urllib.parse.urlencode({query_key: str(asset_id)})
        separator = "&" if urllib.parse.urlparse(capability_url).query else "?"
        url = f"{capability_url}{separator}{query}"

        request = urllib.request.Request(
            url,
            headers={"Accept": "application/octet-stream, */*", "User-Agent": user_agent},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status not in (200, 206):
                    raise ViewerAssetError(
                        f"ViewerAsset {asset_id} returned HTTP {status}"
                    )
                content_type = response.headers.get_content_type()
                data = response.read()
        except TimeoutError as exc:
            raise ViewerAssetError(
                f"ViewerAsset {asset_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except socket.timeout as exc:
            raise ViewerAssetError(
                f"ViewerAsset {asset_id} timed out after {self.timeout_seconds:.1f}s"
            ) from exc
        except urllib.error.HTTPError as exc:
            # 404 is both "no such asset" and "this key is not a type I know",
            # and 400 is a malformed query. The sim does not distinguish them
            # in the response, so neither does this message.
            raise ViewerAssetError(
                f"ViewerAsset {asset_id} ({query_key}) failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ViewerAssetError(
                f"ViewerAsset {asset_id} ({query_key}) failed: {exc.reason}"
            ) from exc

        if not data:
            # OpenSim answers a zero-length asset with 404, so an empty 200 is
            # not something the sim produces; treat it as a broken response
            # rather than as an empty asset.
            raise ViewerAssetError(f"ViewerAsset {asset_id} returned empty body")

        return FetchedAsset(
            asset_id=asset_id,
            asset_type=int(asset_type),
            query_key=query_key,
            content_type=content_type,
            data=data,
        )


__all__ = [
    "ASSET_TYPE_QUERY_KEYS",
    "CONTENT_TYPE_ASSET_TYPES",
    "FetchedAsset",
    "ViewerAssetClient",
    "ViewerAssetError",
    "asset_type_from_content_type",
    "asset_type_query_key",
]
