"""Open a URL the grid chose, over a transport the grid does not choose.

Every capability URL in this client comes from the far end. The login
response names the seed capability; the seed capability's own response names
every other one. Nothing in that chain is ours.

`urllib.request.urlopen` builds its opener from every handler installed by
default, and those include `FileHandler` and `FTPHandler`. So a grid that
answers the seed request with

    <llsd><map>
      <key>GetTexture</key><string>file:///home/you/.ssh/id_rsa</string>
    </map></llsd>

gets that file read and handed to whatever asked for a texture -- and on the
sync path, written into the folder being synced. Measured, not theorised: a
`file://` capability went through `CapabilityClient._fetch_capability_value_sync`
and came back with the file's contents.

Two guards, because either alone is weaker than it looks:

- **The scheme is checked before the request is built**, so the refusal names
  the URL and arrives as the calling module's own error class rather than as
  something no handler expects.
- **The opener carries only the HTTP and HTTPS handlers.** A check can be
  forgotten at a new call site; an opener that has no `FileHandler` in it
  cannot open a file however it is called. It also closes the redirect route,
  where the scheme that is checked is not the scheme that is finally fetched:
  `HTTPRedirectHandler` already refuses anything but http, https and ftp, and
  this removes the ftp.

`test_remote_url.py` keeps `urllib.request.urlopen` out of `src/vibestorm`
entirely, which is the part that survives the next call site.
"""

from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit

#: What a grid may name. Everything else -- `file:`, `ftp:`, `data:` -- is a
#: way to make this client fetch something that is not on a grid.
ALLOWED_SCHEMES = frozenset({"http", "https"})


def _build_opener() -> urllib.request.OpenerDirector:
    """An opener with no handler for anything but HTTP and HTTPS.

    Built by naming what is wanted rather than by removing what is not:
    `build_opener` adds the default set and then applies the arguments, so
    subtracting would mean knowing the whole default list and keeping up with
    it. `OpenerDirector` starts empty.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.HTTPRedirectHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        # Without this, `OpenerDirector.open` on a scheme it has no handler
        # for returns **None** rather than raising, and the caller meets an
        # `AttributeError: 'NoneType' object has no attribute 'read'` -- an
        # exception class nothing upstream names, which is the failure this
        # whole module is about. `UnknownHandler` makes it a `URLError`.
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


#: One opener for the process. `urlopen` builds and caches one globally too;
#: this is the same trade, with a handler list this client chose.
_OPENER = _build_opener()


def require_remote_http_url(url: object, *, what: str, error: type[Exception]) -> str:
    """Return `url` if a grid may legitimately have named it, else raise.

    `what` names the thing being fetched, so a refusal says which capability
    was wrong rather than only that one was.
    """
    if not isinstance(url, str) or not url:
        raise error(f"{what} has no URL")
    scheme = urlsplit(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise error(
            f"{what} is {scheme or 'a URL with no'}: {url!r} -- only http and https are fetched"
        )
    return url


def open_http(request: urllib.request.Request | str, timeout: float):
    """The transport, on its own line so it can be replaced in a test.

    Every client in this project used to reach a fake response by assigning
    over `urllib.request.urlopen`, which is someone else's global. This is the
    same seam, owned and named here, and it sits *below* the scheme check --
    so a test that replaces it still cannot make `open_remote` fetch a
    `file:` URL.
    """
    return _OPENER.open(request, timeout=timeout)


def open_remote(
    request: urllib.request.Request | str,
    *,
    timeout: float,
    what: str,
    error: type[Exception],
):
    """`urlopen`, restricted to HTTP and HTTPS, for a URL the grid chose."""
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    require_remote_http_url(url, what=what, error=error)
    return open_http(request, timeout)


__all__ = ["ALLOWED_SCHEMES", "open_http", "open_remote", "require_remote_http_url"]
