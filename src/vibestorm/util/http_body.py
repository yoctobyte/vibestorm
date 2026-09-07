"""Read an HTTP body with a ceiling on what it may cost.

Every cap and asset fetch in this client called `response.read()` with no
argument, which reads until the server stops sending. The size of that
allocation is a decision made by the far end. A grid that serves a two
gigabyte body -- by malice, by a misconfigured CDN, or by a bug in a region
nobody has looked at in years -- takes the viewer down with it, and the
owner's first priority is "without crashes".

This is the same defect as the J2K header and the mesh block, one layer
further out, and the bounds added there do not help: `MAX_TEXTURE_PIXELS`
is checked once the bytes are already in memory, which is the wrong side of
the allocation. The read itself has to stop.

**The check happens twice, and only one of them counts.** `Content-Length`
is consulted first because a truthful server lets us refuse before
transferring anything -- but it is a claim, and a claim is exactly what the
J2K header was. So the loop below bounds the read whether or not a length
was declared, and a server that declares nothing, declares a lie, or sends
chunked is bounded by the same ceiling.

One byte past the limit is read on purpose. It is how "this body is too
big" is distinguished from "this body is exactly at the limit" without
reading the rest of it.
"""

from __future__ import annotations

from typing import Protocol

#: Asset bodies -- a texture, a mesh, a notecard off a grid's asset service.
#: Set at the mesh decoder's own block ceiling: past this the decode cannot
#: succeed anyway, so the transfer is wasted either way.
MAX_ASSET_BODY_BYTES = 64 * 1024 * 1024

#: LLSD control-plane bodies -- seed caps, an event-queue poll, an upload
#: confirmation. These carry structure rather than content and are small;
#: the largest legitimate one is an inventory listing. Kept well clear of
#: that and still four hundred times under the asset ceiling, because a
#: control-plane response has no business being asset-sized.
MAX_LLSD_BODY_BYTES = 16 * 1024 * 1024

#: Read granularity. Large enough that a 64 MB asset is 64 reads, small
#: enough that the ceiling is not overshot by a meaningful amount.
_CHUNK_BYTES = 1 << 20


class _Readable(Protocol):
    """What this needs of an HTTP response: a bounded read.

    Narrow on purpose -- the tests pass a plain object with a `read` and a
    `headers`, which is the whole contract, so they do not have to build an
    `HTTPResponse` to check a ceiling.
    """

    def read(self, amt: int = ..., /) -> bytes: ...


def read_bounded(
    response: _Readable,
    *,
    max_bytes: int,
    what: str,
    error: type[Exception],
) -> bytes:
    """Read `response`'s body, refusing to hold more than `max_bytes` of it.

    `error` is the caller's own exception type rather than one of ours on
    purpose. Each client here documents the errors it raises and its callers
    catch those; a new class leaking out of a shared helper would escape
    every one of those handlers, which is how `DecompressionBombError` got
    past `decode_j2k`. Passing the type in makes it impossible to forget.
    """
    declared = _declared_length(response)
    if declared is not None and declared > max_bytes:
        raise error(
            f"{what} declares {declared:,} bytes, over the {max_bytes:,} byte limit"
        )

    body = bytearray()
    while len(body) <= max_bytes:
        wanted = min(_CHUNK_BYTES, max_bytes + 1 - len(body))
        chunk = response.read(wanted)
        if not chunk:
            return bytes(body)
        body += chunk
    raise error(f"{what} exceeds the {max_bytes:,} byte limit")


def _declared_length(response: object) -> int | None:
    """`Content-Length` as an int, or None if absent or unusable.

    A missing or malformed header is not an error -- chunked responses have
    no length at all, and the loop bounds those the same way. Only a
    declared length that is *over* the limit is acted on, which is also why
    a negative one needs no special case: it is not over anything. A guard
    for it was written, survived its own mutant because it changed no
    answer, and was removed rather than left to suggest otherwise.
    """
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


__all__ = ["MAX_ASSET_BODY_BYTES", "MAX_LLSD_BODY_BYTES", "read_bounded"]
