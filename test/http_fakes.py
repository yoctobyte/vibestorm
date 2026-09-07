"""Serving a body the way an `HTTPResponse` does, for the fakes in this suite.

Every cap and asset client here is tested against a hand-rolled fake response
whose `read()` took no argument and handed back the whole body in one go.
That was a faithful enough double while the clients called `read()` the same
way. They no longer do -- `read_bounded` asks for a bounded number of bytes,
because the size of an unbounded read is a decision made by the far end -- and
a fake that ignores the argument cannot tell a bounded read from an unbounded
one. It would report every ceiling as working, including a missing one.

So the fakes serve from a cursor, at most `amt` bytes a call, which is what
`http.client.HTTPResponse.read` promises and what the real thing does.
"""

from __future__ import annotations


def serve_body(response: object, body: bytes, amt: int = -1) -> bytes:
    """Return at most `amt` bytes of `body`, advancing `response`'s cursor.

    `amt` of -1 or None means "the rest", matching the real signature. The
    cursor lives on the response rather than in here so that each fake
    response is independent, as separate HTTP responses are.
    """
    start: int = getattr(response, "_served", 0)
    end = len(body) if amt is None or amt < 0 else min(len(body), start + amt)
    response._served = end  # type: ignore[attr-defined]
    return body[start:end]


class FakeHeaders:
    """The two things this suite's code asks of `response.headers`.

    `get` is a real mapping lookup, so a fake can declare a `Content-Length`
    and a test can check that a declared size is refused before the transfer.
    Absent by default, because most responses in these tests do not declare
    one and a chunked response never can.
    """

    def __init__(self, content_type: str = "application/octet-stream", **fields: str):
        self._content_type = content_type
        self._fields = {name.replace("_", "-").lower(): value for name, value in fields.items()}

    def get_content_type(self) -> str:
        return self._content_type

    def get(self, name: str, default: object = None) -> object:
        return self._fields.get(name.lower(), default)
