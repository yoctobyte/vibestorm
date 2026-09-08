"""A stable identifier for this installation, which is not this machine.

The login payload carries two fields, `mac` and `id0`, that grids use to tell
one installation from another -- rate limiting, ban evasion, fraud scoring.
This client sent the empty string for both, which is a plausible reason for a
Second Life login to be refused and is in any case a distinctive thing to say.

The obvious way to fill them in is the way the name suggests: read the
machine's MAC address and its disk serial. This does not do that, and the
choice is deliberate. Those numbers identify the owner's *hardware* to a third
party, they follow them across every account and every reinstall, and they are
not ours to hand over. What is generated here instead is sixteen random bytes,
written once, kept in `local/` and never sent anywhere else. It answers the
question the grid is actually asking -- "is this the same installation as
last time?" -- and answers nothing it is not.

Two consequences worth knowing before relying on it:

- **It is per checkout, not per machine.** A second clone is a second
  installation as far as any grid is concerned. Copy the file if that matters.
- **The shape is an educated guess.** Thirty-two hex characters is what a
  hashed identifier of this kind looks like, and OpenSim's login service takes
  the field as an opaque string and stores it (`LLLoginService.cs`). What
  Second Life requires of it, nobody here has seen, because nobody here has
  completed a login against it yet. If it turns out to want something else,
  this is the one place to change.

Setting `VIBESTORM_LOGIN_MAC` or `VIBESTORM_LOGIN_ID0` overrides the stored
value, and setting either to the empty string restores the old behaviour of
sending nothing.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

#: Where the identity lives. Under `local/`, which is gitignored, beside the
#: credentials -- it is not a secret, but it is not something to publish.
DEFAULT_INSTALL_ID_PATH = Path("local/vibestorm-install-id")

#: The two fields, in the order `install_identity` returns them.
FIELDS = ("mac", "id0")

#: Sixteen random bytes as hex. See the module docstring on why this shape.
IDENTIFIER_BYTES = 16


def _fresh() -> dict[str, str]:
    return {field: secrets.token_bytes(IDENTIFIER_BYTES).hex() for field in FIELDS}


def _read(path: Path) -> dict[str, str]:
    """The stored identity, or an empty dict if there is not a complete one.

    A partial or damaged file is treated as absent rather than repaired: the
    only thing that matters about these values is that they stay the same, and
    half of a remembered identity is not that.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if name in FIELDS and value:
            found[name] = value
    return found if len(found) == len(FIELDS) else {}


def _write(path: Path, identity: dict[str, str]) -> None:
    """Store it, and do not mind if the store is not writable.

    A read-only checkout should still be able to log in; it just gets a new
    identity each time, which is the situation before this file existed.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{field}={identity[field]}\n" for field in FIELDS)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
    except OSError:
        pass


def stored_identity(path: Path | None = None) -> dict[str, str]:
    """The installation's `mac` and `id0`, generating and storing them once."""
    path = path or DEFAULT_INSTALL_ID_PATH
    identity = _read(path)
    if identity:
        return identity
    identity = _fresh()
    _write(path, identity)
    return identity


@lru_cache(maxsize=1)
def _cached_identity() -> dict[str, str]:
    return stored_identity()


def install_identity() -> dict[str, str]:
    """What to put in the login payload, environment overrides applied.

    Cached, because a `LoginRequest` is built for every command and a file
    read per construction would be a surprising cost in a value object. The
    environment is read every call, so a test that sets the variables does not
    have to know about the cache.
    """
    identity = dict(_cached_identity())
    for field in FIELDS:
        override = os.environ.get(f"VIBESTORM_LOGIN_{field.upper()}")
        if override is not None:
            identity[field] = override.strip()
    return identity


def install_mac() -> str:
    return install_identity()["mac"]


def install_id0() -> str:
    return install_identity()["id0"]


__all__ = [
    "DEFAULT_INSTALL_ID_PATH",
    "FIELDS",
    "IDENTIFIER_BYTES",
    "install_id0",
    "install_identity",
    "install_mac",
    "stored_identity",
]
