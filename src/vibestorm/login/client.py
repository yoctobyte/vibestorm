"""XML-RPC login/bootstrap client."""

from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
import xmlrpc.client
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID
from xml.parsers.expat import ExpatError

from vibestorm.login.models import (
    BootstrapBakedCacheEntry,
    BootstrapPackedAppearance,
    LoginBootstrap,
    LoginRequest,
)


class LoginError(RuntimeError):
    """Raised when login/bootstrap fails."""


#: Ceiling on a login response. A grid answers this call with a struct of a
#: few kilobytes -- the largest field is the initial outfit, and even a
#: heavily dressed avatar's is small. Set far above that and still finite,
#: because `xmlrpc.client` reads in 1 kB pieces and hands every one of them
#: to the parser, so the transfer is chunked and the *accumulation* is not.
MAX_LOGIN_RESPONSE_BYTES = 8 * 1024 * 1024


class _BoundedTransport:
    """The timeout and the response bound, shared by both schemes.

    A mixin rather than a base class because the two transports differ only in
    which connection `xmlrpc.client` opens, and that difference lives entirely
    in the classes below this one in the MRO.
    """

    timeout_seconds: float

    def make_connection(self, host: object) -> xmlrpc.client.HTTPConnection:
        connection = super().make_connection(host)
        connection.timeout = self.timeout_seconds
        return connection

    def parse_response(self, response: object) -> tuple[object, ...]:
        """Feed the parser, and stop feeding it past `MAX_LOGIN_RESPONSE_BYTES`.

        The base implementation loops on `stream.read(1024)` until the server
        stops sending. The reads are bounded; nothing else is. Whoever is on
        the far end of a login URI decides how much of this process's memory
        the answer occupies, and on the login path that is a decision taken
        before the user has been told anything at all.

        The count is of *decompressed* bytes on purpose. `Transport` asks for
        gzip and unwraps it here, so counting what came off the socket would
        bound the transfer and not the memory, and a compressed body is where
        the two differ by three orders of magnitude.
        """
        stream = self._decoded_stream(response)
        parser, unmarshaller = self.getparser()
        total = 0
        try:
            while total <= MAX_LOGIN_RESPONSE_BYTES:
                data = stream.read(min(1024, MAX_LOGIN_RESPONSE_BYTES + 1 - total))
                if not data:
                    parser.close()
                    return unmarshaller.close()
                total += len(data)
                parser.feed(data)
        finally:
            if stream is not response:
                stream.close()
        raise LoginError(
            f"login response exceeds the {MAX_LOGIN_RESPONSE_BYTES:,} byte limit"
        )

    @staticmethod
    def _decoded_stream(response: object):
        """Unwrap a gzipped body the way `Transport.parse_response` does."""
        header = getattr(response, "getheader", None)
        if header is not None and header("Content-Encoding", "") == "gzip":
            return xmlrpc.client.GzipDecodedResponse(response)
        return response


class TimeoutTransport(_BoundedTransport, xmlrpc.client.Transport):
    """Plain HTTP. Local sims and the older OpenSim grids."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self.timeout_seconds = timeout_seconds


class TimeoutSafeTransport(_BoundedTransport, xmlrpc.client.SafeTransport):
    """HTTPS, which is every grid worth reaching.

    `context=None` means `xmlrpc` builds the default one, which verifies the
    chain and the hostname. A caller may pass its own -- an OpenSim grid
    behind a private CA is a real case, and `ssl.create_default_context(
    cafile=...)` is the way to reach it. There is deliberately no flag for
    turning verification off: the payload of this request is a password.
    """

    def __init__(self, timeout_seconds: float, *, context: ssl.SSLContext | None = None) -> None:
        super().__init__(context=context)
        self.timeout_seconds = timeout_seconds


def transport_for(
    login_uri: str,
    timeout_seconds: float,
    *,
    context: ssl.SSLContext | None = None,
) -> xmlrpc.client.Transport:
    """The transport an XML-RPC login to `login_uri` needs.

    `ServerProxy` picks `SafeTransport` for an `https` URI *only when it is
    not given a transport of its own*. This client always gives it one, to set
    the timeout and bound the response, and for a long time gave it a plain
    `Transport` regardless of scheme -- so an `https` login URI opened an
    unencrypted connection to **port 80** of that host (the port the URI
    implies is dropped along with the scheme, since `HTTPConnection(host)`
    defaults it) and sent the password hash in clear.

    Nothing caught it because the local sim this client is developed against
    is `http://`, so the one scheme in daily use was the one that worked, and
    every test of `_login_sync` replaced `ServerProxy` wholesale and never
    reached a transport at all.
    """
    # `urlsplit` lowercases the scheme itself, which is why there is no
    # `.lower()` here. It is pinned in the tests, because a comparison whose
    # correctness lives in another library's normalisation is one nobody
    # rereads.
    if urlsplit(login_uri).scheme == "https":
        return TimeoutSafeTransport(timeout_seconds, context=context)
    return TimeoutTransport(timeout_seconds)


#: The substring OpenSim's refusal carries when a previous session is still
#: attached. Matched on rather than parsed: the surrounding text is a sentence
#: written for a human ("Please wait a a minute or two and retry", typo and
#: all) and is not a stable format.
LINGERING_SESSION_MESSAGE = "already logged in"


@dataclass(slots=True)
class LoginClient:
    """Perform the initial XML-RPC login/bootstrap request."""

    timeout_seconds: float = 10.0
    #: Retry once when the grid reports a lingering session.
    #:
    #: The refusal is not a plain "no": the same attempt *disconnects* whatever
    #: was still attached, so the immediate retry succeeds. The message asking
    #: for a minute or two is misleading — waiting is not what fixes it, the
    #: first attempt already did. This mirrors how the SL grid behaves and what
    #: a real viewer does with it.
    #:
    #: Consequential enough to be a flag rather than unconditional: the retry
    #: takes an account that was logged in elsewhere and moves it here. It is
    #: on by default because the disconnect has already happened by the time
    #: this is decided — declining to retry loses the session without saving
    #: it — but a caller that wants the refusal surfaced can turn it off.
    retry_lingering_session: bool = True
    #: TLS settings for an `https` login URI. `None` is the verifying default.
    #: Supplied for a grid behind a private CA, and by the tests, which stand
    #: up a real TLS server on the loopback rather than trusting that asking
    #: for a secure transport produced one.
    ssl_context: ssl.SSLContext | None = None

    async def login(self, request: LoginRequest) -> LoginBootstrap:
        return await asyncio.to_thread(self._login_with_retry, request)

    def _login_with_retry(self, request: LoginRequest) -> LoginBootstrap:
        try:
            return self._login_sync(request)
        except LoginError as exc:
            if not self.retry_lingering_session:
                raise
            if LINGERING_SESSION_MESSAGE not in str(exc).lower():
                raise
            # Exactly one retry. A second refusal means something other than a
            # lingering session, and looping on it would hammer the grid.
            return self._login_sync(request)

    def _login_sync(self, request: LoginRequest) -> LoginBootstrap:
        transport = transport_for(
            request.login_uri, self.timeout_seconds, context=self.ssl_context
        )
        server = xmlrpc.client.ServerProxy(request.login_uri, allow_none=True, transport=transport)
        try:
            response = server.login_to_simulator(self._request_payload(request))
        except TimeoutError as exc:
            raise LoginError(f"login timed out after {self.timeout_seconds:.1f}s") from exc
        except socket.timeout as exc:
            raise LoginError(f"login timed out after {self.timeout_seconds:.1f}s") from exc
        except OSError as exc:
            raise LoginError(f"login request failed: {exc}") from exc
        except xmlrpc.client.Error as exc:
            raise LoginError(f"login XML-RPC failed: {exc}") from exc
        except ExpatError as exc:
            # Not an `xmlrpc.client.Error` and not an `OSError` -- it comes
            # from the parser underneath and subclasses `Exception` directly,
            # so every handler above this one missed it and a truncated or
            # malformed login response reached the user as a traceback rather
            # than as a failed login. The same shape as
            # `DecompressionBombError` escaping `decode_j2k`.
            raise LoginError(f"login response was not valid XML: {exc}") from exc
        if not isinstance(response, dict):
            raise LoginError("login response is not a struct")

        if str(response.get("login", "")).lower() != "true":
            raise LoginError(_refusal_text(response))

        try:
            return LoginBootstrap(
                agent_id=UUID(str(response["agent_id"])),
                session_id=UUID(str(response["session_id"])),
                secure_session_id=UUID(str(response["secure_session_id"])),
                circuit_code=int(response["circuit_code"]),
                sim_ip=str(response["sim_ip"]),
                sim_port=int(response["sim_port"]),
                seed_capability=str(response["seed_capability"]),
                region_x=int(response["region_x"]),
                region_y=int(response["region_y"]),
                message=str(response.get("message", "")),
                inventory_root_folder_id=_extract_inventory_root_folder_id(response),
                current_outfit_folder_id=_extract_folder_id_by_name(response, "Current Outfit"),
                my_outfits_folder_id=_extract_folder_id_by_name(response, "My Outfits"),
                initial_outfit_name=_extract_initial_outfit_field(response, "folder_name"),
                initial_outfit_gender=_extract_initial_outfit_field(response, "gender"),
                initial_baked_cache_entries=_extract_initial_baked_cache_entries(response),
                initial_packed_appearance=_extract_initial_packed_appearance(response),
            )
        except KeyError as exc:
            raise LoginError(f"login response missing field: {exc.args[0]}") from exc

    def _request_payload(self, request: LoginRequest) -> dict[str, object]:
        return {
            "first": request.credentials.first,
            "last": request.credentials.last,
            "passwd": sl_password_hash(request.credentials.password),
            "start": request.start,
            "channel": request.channel,
            "version": request.version,
            "platform": request.platform,
            "platform_version": request.platform_version,
            "mac": request.mac,
            "id0": request.id0,
            "viewer_digest": request.viewer_digest,
            "agree_to_tos": request.agree_to_tos,
            "read_critical": request.read_critical,
            "options": list(request.options),
        }


def _refusal_text(response: dict[str, object]) -> str:
    """What to tell the user when the grid says no.

    A refusal carries a `message` written for a human and, on the grids that
    send one, a short machine-readable `reason` beside it. The reason is the
    part that says what to *do* -- a wrong password and an account that must
    read a critical notice both arrive as "login failed" otherwise -- so it is
    appended when it is there and adds something.

    No branching on particular reason values: this client has never completed
    a login against the Second Life grid, and a table of its refusal codes
    would be a table nobody here has seen. Passing the grid's own word through
    is the honest amount to claim.
    """
    message = str(response.get("message", "")).strip() or "login failed"
    reason = str(response.get("reason", "")).strip()
    if reason and reason.lower() not in message.lower():
        return f"{message} (reason: {reason})"
    return message


def sl_password_hash(password: str) -> str:
    return "$1$" + hashlib.md5(password.encode("utf-8")).hexdigest()


def _extract_inventory_root_folder_id(response: dict[str, object]) -> UUID | None:
    inventory_root = response.get("inventory-root")
    if not isinstance(inventory_root, list) or not inventory_root:
        return None
    first = inventory_root[0]
    if not isinstance(first, dict):
        return None
    raw_folder_id = first.get("folder_id")
    if raw_folder_id is None:
        return None
    try:
        return UUID(str(raw_folder_id))
    except (TypeError, ValueError, AttributeError):
        return None


def _extract_folder_id_by_name(response: dict[str, object], folder_name: str) -> UUID | None:
    skeleton = response.get("inventory-skeleton")
    if not isinstance(skeleton, list):
        return None
    for entry in skeleton:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("name", "")) != folder_name:
            continue
        raw_folder_id = entry.get("folder_id")
        if raw_folder_id is None:
            return None
        try:
            return UUID(str(raw_folder_id))
        except (TypeError, ValueError, AttributeError):
            return None
    return None


def _extract_initial_outfit_field(response: dict[str, object], field_name: str) -> str | None:
    initial_outfit = response.get("initial-outfit")
    if not isinstance(initial_outfit, list) or not initial_outfit:
        return None
    first = initial_outfit[0]
    if not isinstance(first, dict):
        return None
    value = first.get(field_name)
    if value is None:
        return None
    return str(value)


def _extract_initial_baked_cache_entries(response: dict[str, object]) -> tuple[BootstrapBakedCacheEntry, ...]:
    packed = response.get("packed_appearance")
    if not isinstance(packed, dict):
        return ()

    entries: list[BootstrapBakedCacheEntry] = []
    seen_indices: set[int] = set()
    for key in ("bakedcache", "bc8"):
        raw_entries = packed.get(key)
        if not isinstance(raw_entries, list):
            continue
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            texture_index = _extract_int(raw_entry.get("textureindex"))
            cache_id = _extract_uuid(raw_entry.get("cacheid"))
            if texture_index is None or cache_id is None or texture_index in seen_indices:
                continue
            seen_indices.add(texture_index)
            entries.append(
                BootstrapBakedCacheEntry(
                    texture_index=texture_index,
                    cache_id=cache_id,
                    texture_id=_extract_uuid(raw_entry.get("textureid")),
                )
            )
    entries.sort(key=lambda entry: entry.texture_index)
    return tuple(entries)


def _extract_initial_packed_appearance(response: dict[str, object]) -> BootstrapPackedAppearance | None:
    packed = response.get("packed_appearance")
    if not isinstance(packed, dict):
        return None

    texture_entry = _extract_binary(packed.get("te8"))
    visual_params = _extract_binary(packed.get("visualparams"))
    serial_num = _extract_int(packed.get("serial"))
    avatar_height = _extract_float(packed.get("height"))

    if texture_entry is None and visual_params is None and serial_num is None and avatar_height is None:
        return None

    return BootstrapPackedAppearance(
        serial_num=serial_num,
        avatar_height=avatar_height,
        texture_entry=texture_entry,
        visual_params=visual_params,
    )


def _extract_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _extract_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_binary(value: object) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, xmlrpc.client.Binary):
        return bytes(value.data)
    return None
