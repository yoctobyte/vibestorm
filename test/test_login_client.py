import http.client
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import xmlrpc.client
from pathlib import Path
from uuid import UUID
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

from vibestorm.login.client import (
    MAX_LOGIN_RESPONSE_BYTES,
    LoginClient,
    LoginError,
    TimeoutSafeTransport,
    TimeoutTransport,
    sl_password_hash,
    transport_for,
)
from vibestorm.login.models import DEFAULT_LOGIN_OPTIONS, LoginCredentials, LoginRequest


class LoginHelpersTests(unittest.TestCase):
    def test_sl_password_hash(self) -> None:
        self.assertEqual(sl_password_hash("changeme123"), "$1$c9cdcb06301f9c79e2d20c2fdeda0a02")


class LoginClientSyncTests(unittest.TestCase):
    def test_request_payload_hashes_password(self) -> None:
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )
        payload = LoginClient()._request_payload(request)
        self.assertEqual(payload["passwd"], "$1$c9cdcb06301f9c79e2d20c2fdeda0a02")
        self.assertEqual(payload["options"], list(DEFAULT_LOGIN_OPTIONS))

    def test_login_sync_maps_response(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )
        expected_payload = client._request_payload(request)

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                assert payload == expected_payload
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "inventory-root": [
                        {"folder_id": "49cb1ed7-e8b2-4de5-84d7-4222f540634c"},
                    ],
                    "inventory-skeleton": [
                        {
                            "name": "Current Outfit",
                            "folder_id": "d427dc3a-047a-4b9f-9aaf-15ccce179bf2",
                        },
                        {
                            "name": "My Outfits",
                            "folder_id": "256d4a5d-cb0d-7e27-ca95-ac42b50ec733",
                        },
                    ],
                    "initial-outfit": [
                        {
                            "folder_name": "Nightclub Female",
                            "gender": "female",
                        },
                    ],
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertEqual(result.agent_id, UUID("11111111-2222-3333-4444-555555555555"))
        self.assertEqual(result.session_id, UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        self.assertEqual(result.circuit_code, 7)
        self.assertEqual(result.sim_ip, "127.0.0.1")
        self.assertEqual(result.region_x, 256000)
        self.assertEqual(result.inventory_root_folder_id, UUID("49cb1ed7-e8b2-4de5-84d7-4222f540634c"))
        self.assertEqual(result.current_outfit_folder_id, UUID("d427dc3a-047a-4b9f-9aaf-15ccce179bf2"))
        self.assertEqual(result.my_outfits_folder_id, UUID("256d4a5d-cb0d-7e27-ca95-ac42b50ec733"))
        self.assertEqual(result.initial_outfit_name, "Nightclub Female")
        self.assertEqual(result.initial_outfit_gender, "female")
        self.assertEqual(result.initial_baked_cache_entries, ())

    def test_login_sync_extracts_initial_baked_cache_entries(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "packed_appearance": {
                        "bakedcache": [
                            {
                                "textureindex": 8,
                                "cacheid": "12345678-1111-2222-3333-444444444444",
                                "textureid": "87654321-1111-2222-3333-444444444444",
                            }
                        ],
                        "bc8": [
                            {
                                "textureindex": 40,
                                "cacheid": "12345678-1111-2222-3333-555555555555",
                                "textureid": "87654321-1111-2222-3333-555555555555",
                            }
                        ],
                    },
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertEqual(len(result.initial_baked_cache_entries), 2)
        self.assertEqual(result.initial_baked_cache_entries[0].texture_index, 8)
        self.assertEqual(result.initial_baked_cache_entries[0].cache_id, UUID("12345678-1111-2222-3333-444444444444"))
        self.assertEqual(result.initial_baked_cache_entries[1].texture_index, 40)
        self.assertEqual(result.initial_baked_cache_entries[1].cache_id, UUID("12345678-1111-2222-3333-555555555555"))
        self.assertIsNone(result.initial_packed_appearance)

    def test_login_sync_extracts_initial_packed_appearance(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {
                    "login": "true",
                    "message": "Welcome, Avatar!",
                    "agent_id": "11111111-2222-3333-4444-555555555555",
                    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "secure_session_id": "99999999-8888-7777-6666-555555555555",
                    "circuit_code": 7,
                    "sim_ip": "127.0.0.1",
                    "sim_port": 9000,
                    "seed_capability": "http://127.0.0.1:9000/CAPS/example/",
                    "region_x": 256000,
                    "region_y": 256000,
                    "packed_appearance": {
                        "serial": 12,
                        "height": 1.93,
                        "te8": xmlrpc.client.Binary(b"\x01\x02\x03\x04"),
                        "visualparams": xmlrpc.client.Binary(b"\x05\x06\x07"),
                    },
                }

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            result = client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

        self.assertIsNotNone(result.initial_packed_appearance)
        assert result.initial_packed_appearance is not None
        self.assertEqual(result.initial_packed_appearance.serial_num, 12)
        self.assertAlmostEqual(result.initial_packed_appearance.avatar_height, 1.93)
        self.assertEqual(result.initial_packed_appearance.texture_entry, b"\x01\x02\x03\x04")
        self.assertEqual(result.initial_packed_appearance.visual_params, b"\x05\x06\x07")

    def test_login_sync_raises_on_failure(self) -> None:
        client = LoginClient()
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                return {"login": "false", "message": "bad password"}

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            with self.assertRaises(LoginError):
                client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

    def test_login_sync_wraps_timeout(self) -> None:
        client = LoginClient(timeout_seconds=2.5)
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Admin", password="changeme123"),
        )

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                raise socket.timeout("timed out")

        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *args, **kwargs: DummyServer()  # type: ignore[assignment]
        try:
            with self.assertRaisesRegex(LoginError, "timed out after 2.5s"):
                client._login_sync(request)
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]


class LingeringSessionRetryTests(unittest.TestCase):
    """OpenSim refuses a login while a previous session is still attached.

    The refusal is not a plain no: that same attempt disconnects whatever was
    lingering, so an immediate retry succeeds. Its message asks for "a minute
    or two", which is misleading — waiting is not what fixes it. This mirrors
    the SL grid, and a real viewer retries rather than sleeping.
    """

    LINGERING = {
        "login": "false",
        "message": (
            "You appear to be already logged in. Please wait a a minute or two "
            "and retry. If this takes longer than a few minutes please contact "
            "the grid owner."
        ),
    }

    SUCCESS = {
        "login": "true",
        "message": "Welcome, Avatar!",
        "agent_id": "11111111-2222-3333-4444-555555555555",
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "secure_session_id": "99999999-8888-7777-6666-555555555555",
        "circuit_code": 123456,
        "sim_ip": "127.0.0.1",
        "sim_port": 9000,
        "seed_capability": "http://127.0.0.1:9000/CAPS/seed",
        "region_x": 256000,
        "region_y": 256000,
    }

    def _run(self, responses: list[dict], **client_kwargs):
        calls: list[dict] = []

        class DummyServer:
            def login_to_simulator(self, payload: dict[str, object]) -> dict[str, object]:
                calls.append(payload)
                return responses[min(len(calls) - 1, len(responses) - 1)]

        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="V", last="A", password="p"),
        )
        original = xmlrpc.client.ServerProxy
        xmlrpc.client.ServerProxy = lambda *a, **k: DummyServer()  # type: ignore[assignment]
        try:
            client = LoginClient(**client_kwargs)
            try:
                return client._login_with_retry(request), calls, None
            except LoginError as exc:
                return None, calls, exc
        finally:
            xmlrpc.client.ServerProxy = original  # type: ignore[assignment]

    def test_the_second_attempt_succeeds(self) -> None:
        bootstrap, calls, error = self._run([self.LINGERING, self.SUCCESS])

        self.assertIsNone(error)
        self.assertEqual(len(calls), 2)
        self.assertEqual(bootstrap.circuit_code, 123456)

    def test_a_first_attempt_that_works_is_not_retried(self) -> None:
        # The retry moves an account between machines. It must not happen on
        # the ordinary path.
        bootstrap, calls, error = self._run([self.SUCCESS])

        self.assertIsNone(error)
        self.assertEqual(len(calls), 1)

    def test_it_retries_exactly_once(self) -> None:
        # A second refusal means something other than a lingering session, and
        # looping on it would hammer the grid.
        bootstrap, calls, error = self._run([self.LINGERING])

        self.assertIsNotNone(error)
        self.assertEqual(len(calls), 2)
        self.assertIn("already logged in", str(error))

    def test_other_failures_are_not_retried(self) -> None:
        # Retrying a bad password is pointless and looks like a brute force.
        bootstrap, calls, error = self._run(
            [{"login": "false", "message": "Could not authenticate your avatar."}]
        )

        self.assertIsNotNone(error)
        self.assertEqual(len(calls), 1)

    def test_the_retry_can_be_declined(self) -> None:
        bootstrap, calls, error = self._run(
            [self.LINGERING, self.SUCCESS], retry_lingering_session=False
        )

        self.assertIsNotNone(error)
        self.assertEqual(len(calls), 1)


class _Response:
    """A login server's HTTP response, as `Transport.parse_response` sees it.

    `asks` is what the test is really about. A ceiling that is measured after
    the read is not a ceiling, so the assertions are on what was requested.
    """

    def __init__(self, body: bytes, *, encoding: str = "", chunk: int | None = None):
        self._body = body
        self._encoding = encoding
        self._served = 0
        self._chunk = chunk
        self.asks: list[int] = []

    def getheader(self, name: str, default: str = "") -> str:
        return self._encoding if name == "Content-Encoding" else default

    def read(self, amt: int = -1) -> bytes:
        self.asks.append(amt)
        end = len(self._body) if amt < 0 else min(len(self._body), self._served + amt)
        if self._chunk is not None:
            end = min(end, self._served + self._chunk)
        chunk = self._body[self._served : end]
        self._served = end
        return chunk


class _EndlessResponse:
    """A grid that opens a valid document and never closes it."""

    def __init__(self) -> None:
        #: Bytes handed over, not bytes asked for. Asking is what a reader
        #: with an unclamped chunk size does too much of, and the difference
        #: between the two is exactly the overshoot being measured.
        self.given = 0
        self._head = b"<methodResponse><params><param><value><string>"

    def getheader(self, name: str, default: str = "") -> str:
        return default

    def read(self, amt: int = -1) -> bytes:
        if amt < 0:
            raise AssertionError("unbounded read against an endless response")
        if self._head:
            out, self._head = self._head[:amt], self._head[amt:]
        else:
            out = b"x" * amt
        self.given += len(out)
        return out


def _method_response(text: str) -> bytes:
    return (
        f"<methodResponse><params><param><value><string>{text}"
        "</string></value></param></params></methodResponse>"
    ).encode()


class TransportParseTests(unittest.TestCase):
    """`TimeoutTransport.parse_response`, which replaces the base loop.

    The base one reads in 1 kB pieces and feeds every one of them to the
    parser until the server stops sending. The reads are bounded and the
    accumulation is not, so how much of this process's memory a login answer
    occupies is decided by whoever is on the far end of the login URI --
    before the user has been told anything at all.
    """

    def _parse(self, response: object) -> object:
        return TimeoutTransport(timeout_seconds=5.0).parse_response(response)

    def test_an_ordinary_response_still_parses(self) -> None:
        """The floor. A ceiling that broke parsing would pass every test below."""
        self.assertEqual(self._parse(_Response(_method_response("hello"))), ("hello",))

    def test_a_response_arriving_in_small_pieces_still_parses(self) -> None:
        """A short read is not the end of a body, and the parser is fed
        incrementally precisely so that it need not be."""
        response = _Response(_method_response("hello"), chunk=7)
        self.assertEqual(self._parse(response), ("hello",))
        self.assertGreater(len(response.asks), 1)

    def test_a_gzipped_response_still_parses(self) -> None:
        """`Transport` asks for gzip, so a grid may well send it.

        This unwrapping is reimplemented here rather than inherited, and if
        it were wrong every grid that compresses would refuse to log in --
        with a parse error blamed on the grid.
        """
        import gzip

        body = gzip.compress(_method_response("hello"))
        self.assertEqual(self._parse(_Response(body, encoding="gzip")), ("hello",))

    def test_the_count_is_of_decompressed_bytes(self) -> None:
        """Which is the count that matters: deflate reaches about 1,029:1, so
        bounding what came off the socket would bound the transfer and leave
        the memory unbounded by three orders of magnitude."""
        import gzip

        oversized = _method_response("x" * (MAX_LOGIN_RESPONSE_BYTES + 1024))
        self.assertLess(len(gzip.compress(oversized)), MAX_LOGIN_RESPONSE_BYTES)
        with self.assertRaisesRegex(LoginError, "exceeds the"):
            self._parse(_Response(gzip.compress(oversized), encoding="gzip"))

    def test_a_response_that_never_ends_is_refused(self) -> None:
        response = _EndlessResponse()
        with self.assertRaisesRegex(LoginError, "exceeds the"):
            self._parse(response)
        # The limit plus the one byte that detects the overrun, and not a
        # byte more. A reader that asks for a full chunk each time overshoots
        # by up to a kilobyte, which is harmless here and is the same
        # arithmetic that is not harmless at 64 MB.
        self.assertEqual(response.given, MAX_LOGIN_RESPONSE_BYTES + 1)

    def test_a_body_of_exactly_the_limit_is_accepted(self) -> None:
        """The limit is inclusive, and a boundary nobody pins drifts.

        The module constant is lowered for this rather than an eight megabyte
        document being built, because the arithmetic under test is the
        comparison and not the size.
        """
        import vibestorm.login.client as module

        skeleton = len(_method_response(""))
        original = module.MAX_LOGIN_RESPONSE_BYTES
        module.MAX_LOGIN_RESPONSE_BYTES = skeleton + 100
        try:
            body = _method_response("y" * 100)
            self.assertEqual(len(body), module.MAX_LOGIN_RESPONSE_BYTES)
            self.assertEqual(self._parse(_Response(body)), ("y" * 100,))
            with self.assertRaisesRegex(LoginError, "exceeds the"):
                self._parse(_Response(_method_response("y" * 101)))
        finally:
            module.MAX_LOGIN_RESPONSE_BYTES = original

    def test_the_limit_is_generous_but_finite(self) -> None:
        """A login struct is kilobytes. The floor keeps a real one working;
        the ceiling is the entire point, and a bound with only a floor passes
        every test here while being 2**60."""
        self.assertGreaterEqual(MAX_LOGIN_RESPONSE_BYTES, 1024 * 1024)
        self.assertLessEqual(MAX_LOGIN_RESPONSE_BYTES, 64 * 1024 * 1024)


class MalformedResponseTests(unittest.TestCase):
    """A grid that answers with something that is not XML.

    `ExpatError` comes from the parser underneath `xmlrpc`, subclasses
    `Exception` directly, and is neither an `xmlrpc.client.Error` nor an
    `OSError` -- so every handler in `_login_sync` missed it and a truncated
    login response reached the user as a traceback instead of a failed login.
    The same shape as `DecompressionBombError` escaping `decode_j2k`, on the
    one code path that runs before the user has been told anything.
    """

    def _login_against(self, payload: bytes) -> None:
        import vibestorm.login.client as module

        class Transport(xmlrpc.client.Transport):
            def request(self, host, handler, request_body, verbose=False):  # type: ignore[no-untyped-def]
                parser, unmarshaller = self.getparser()
                parser.feed(payload)
                parser.close()
                return unmarshaller.close()

        original = module.transport_for
        module.transport_for = lambda *args, **kwargs: Transport()  # type: ignore[assignment]
        try:
            LoginClient()._login_sync(
                LoginRequest(
                    login_uri="http://127.0.0.1:9/",
                    credentials=LoginCredentials(first="a", last="b", password="c"),
                )
            )
        finally:
            module.transport_for = original

    def test_a_truncated_response_is_a_login_error(self) -> None:
        with self.assertRaises(LoginError):
            self._login_against(b"<methodResponse><params><param><value><string>unclosed")

    def test_a_response_that_is_not_xml_at_all_is_a_login_error(self) -> None:
        """A proxy's HTML error page is the usual way this happens."""
        with self.assertRaises(LoginError):
            self._login_against(b"<html><body>502 Bad Gateway</body></html>")

    def test_the_message_says_what_went_wrong(self) -> None:
        with self.assertRaisesRegex(LoginError, "not valid XML"):
            self._login_against(b"not xml")



class TransportSchemeTests(unittest.TestCase):
    """Which connection an `https` login URI opens.

    `ServerProxy` chooses `SafeTransport` for `https` only when it is not
    handed a transport of its own. This client always hands it one -- for the
    timeout and the response bound -- and for a long time handed it a plain
    `Transport` whatever the scheme, so an `https` login URI opened an
    **unencrypted** connection to **port 80** of that host and sent the
    password hash in clear. The port goes missing along with the scheme:
    `HTTPConnection(host)` defaults it to 80, and the 443 the URI implies was
    never anywhere in the call.

    That is priority B's grid -- `https://login.agni.lindenlab.com/...` -- and
    the reason it went unnoticed is that the sim this client is developed
    against is `http://`, so the one scheme in daily use was the working one.
    """

    def test_an_https_uri_gets_a_secure_transport(self) -> None:
        transport = transport_for("https://login.agni.lindenlab.com/cgi-bin/login.cgi", 5.0)
        self.assertIsInstance(transport, xmlrpc.client.SafeTransport)

    def test_an_https_uri_connects_over_tls_on_443(self) -> None:
        """The class alone is not the claim: what matters is the connection."""
        connection = transport_for("https://example.invalid/x", 5.0).make_connection("example.invalid")
        self.assertIsInstance(connection, http.client.HTTPSConnection)
        self.assertEqual(connection.port, 443)

    def test_an_http_uri_still_gets_a_plain_transport(self) -> None:
        connection = transport_for("http://127.0.0.1:9000/", 5.0).make_connection("127.0.0.1:9000")
        self.assertNotIsInstance(connection, http.client.HTTPSConnection)
        self.assertEqual(connection.port, 9000)

    def test_a_mixed_case_scheme_is_still_https(self) -> None:
        """This one pins `urlsplit`, not the branch: it lowercases the scheme
        on the way out, so the comparison in `transport_for` needs no `.lower()`
        of its own. Written down because a comparison whose correctness lives
        in another library's normalisation is one nobody rereads -- and because
        the alternative is a redundant call that looks load-bearing."""
        from urllib.parse import urlsplit

        self.assertEqual(urlsplit("HTTPS://X/y").scheme, "https")
        self.assertIsInstance(transport_for("HTTPS://X/y", 5.0), xmlrpc.client.SafeTransport)

    def test_both_transports_carry_the_timeout(self) -> None:
        """It is set in the mixin, so a transport that skipped it would wait on
        the default -- which is no timeout at all, on the one call the user is
        sitting in front of."""
        for uri in ("https://example.invalid/x", "http://example.invalid/x"):
            with self.subTest(uri):
                connection = transport_for(uri, 3.5).make_connection("example.invalid")
                self.assertEqual(connection.timeout, 3.5)

    def test_both_transports_bound_the_response(self) -> None:
        for transport in (TimeoutTransport(5.0), TimeoutSafeTransport(5.0)):
            with self.subTest(type(transport).__name__):
                self.assertIs(
                    type(transport).parse_response,
                    type(TimeoutTransport(5.0)).parse_response,
                )

    def test_an_unknown_scheme_does_not_quietly_become_secure(self) -> None:
        self.assertNotIsInstance(transport_for("ftp://x/y", 5.0), xmlrpc.client.SafeTransport)


class _AnyPathHandler(SimpleXMLRPCRequestHandler):
    """Serve the call wherever it is posted.

    The default handler answers only `/` and `/RPC2`, and a login URI is
    `/cgi-bin/login.cgi` on every grid this client will ever talk to.
    """

    rpc_paths = ()


class _TLSXMLRPCServer(SimpleXMLRPCServer):
    def __init__(self, context: ssl.SSLContext) -> None:
        super().__init__(("127.0.0.1", 0), requestHandler=_AnyPathHandler, logRequests=False)
        self.socket = context.wrap_socket(self.socket, server_side=True)


class LiveTLSLoginTests(unittest.TestCase):
    """A real TLS handshake, because everything short of one has lied before.

    Every other test in this file replaces `ServerProxy` wholesale, which is
    why the plain-transport bug above survived: no test of `_login_sync` had
    ever opened a socket. This one stands up an XML-RPC server on the loopback
    with its own certificate and logs in to it over `https`, so the claim is
    the whole chain -- scheme to transport to handshake to bootstrap -- rather
    than the type of an object.
    """

    RESPONSE = {
        "login": "true",
        "agent_id": "11111111-1111-4111-8111-111111111111",
        "session_id": "22222222-2222-4222-8222-222222222222",
        "secure_session_id": "33333333-3333-4333-8333-333333333333",
        "circuit_code": 123456,
        "sim_ip": "127.0.0.1",
        "sim_port": 9000,
        "seed_capability": "https://127.0.0.1:9000/cap/seed",
        "region_x": 256000,
        "region_y": 256000,
        "message": "Welcome",
    }

    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("openssl") is None:  # pragma: no cover - environment
            raise unittest.SkipTest("openssl is needed to make a test certificate")
        cls.directory = Path(tempfile.mkdtemp(prefix="vibestorm-tls-test-"))
        cls.certificate = cls.directory / "cert.pem"
        cls.key = cls.directory / "key.pem"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", str(cls.key), "-out", str(cls.certificate),
                "-days", "1", "-nodes", "-subj", "/CN=localhost",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, ignore_errors=True)

    def setUp(self) -> None:
        self.calls: list[dict] = []
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(str(self.certificate), str(self.key))
        self.server = _TLSXMLRPCServer(server_context)
        self.server.register_function(self._login_to_simulator, "login_to_simulator")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5.0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.uri = f"https://localhost:{self.server.server_address[1]}/cgi-bin/login.cgi"
        self.client_context = ssl.create_default_context(cafile=str(self.certificate))

    def _login_to_simulator(self, payload: dict) -> dict:
        self.calls.append(payload)
        return dict(self.RESPONSE)

    def request(self, **overrides) -> LoginRequest:
        fields = {
            "login_uri": self.uri,
            "credentials": LoginCredentials(first="Vibestorm", last="Tester", password="secret"),
            "start": "home",
        }
        fields.update(overrides)
        return LoginRequest(**fields)

    def test_a_login_over_https_reaches_the_grid_and_comes_back(self) -> None:
        bootstrap = LoginClient(ssl_context=self.client_context)._login_sync(self.request())
        self.assertEqual(bootstrap.circuit_code, 123456)
        self.assertEqual(str(bootstrap.agent_id), self.RESPONSE["agent_id"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["start"], "home")
        self.assertEqual(self.calls[0]["passwd"], sl_password_hash("secret"))

    def test_the_password_never_goes_out_in_the_clear(self) -> None:
        """The bug this replaces sent it to port 80 unencrypted. A plain HTTP
        request to the TLS port has to fail rather than be served."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5.0)
        with self.assertRaises((http.client.HTTPException, OSError)):
            connection.request("POST", "/cgi-bin/login.cgi", body=b"<methodCall/>")
            connection.getresponse()
        self.assertEqual(self.calls, [])

    def test_an_untrusted_certificate_is_a_failed_login_not_a_traceback(self) -> None:
        """The default context does not trust this certificate. A grid behind a
        private CA is a real case and the answer is a context, not a flag that
        turns verification off -- so this has to arrive as a `LoginError`."""
        with self.assertRaises(LoginError):
            LoginClient()._login_sync(self.request())
        self.assertEqual(self.calls, [])


class RefusalTextTests(unittest.TestCase):
    """What a refused login says, which is all the user gets."""

    def _refusal(self, **fields) -> str:
        from vibestorm.login.client import _refusal_text

        return _refusal_text({"login": "false", **fields})

    def test_the_grids_message_is_passed_through(self) -> None:
        self.assertEqual(self._refusal(message="Wrong password."), "Wrong password.")

    def test_the_reason_is_appended_when_the_message_does_not_carry_it(self) -> None:
        """A bad password and an unread critical notice are both "login
        failed" in the message alone; the reason is the part that says which."""
        self.assertEqual(
            self._refusal(message="Login failed.", reason="critical"),
            "Login failed. (reason: critical)",
        )

    def test_a_reason_already_in_the_message_is_not_repeated(self) -> None:
        self.assertEqual(
            self._refusal(message="Agent presence problem", reason="presence"),
            "Agent presence problem",
        )

    def test_a_refusal_with_nothing_in_it_still_says_something(self) -> None:
        self.assertEqual(self._refusal(), "login failed")

    def test_a_blank_message_does_not_swallow_the_reason(self) -> None:
        self.assertEqual(self._refusal(message="  ", reason="key"), "login failed (reason: key)")

    def test_the_lingering_session_retry_still_sees_its_message(self) -> None:
        """The retry matches on the sentence OpenSim writes. Appending the
        reason must not push it out of the string it matches against."""
        from vibestorm.login.client import LINGERING_SESSION_MESSAGE

        text = self._refusal(
            message="You appear to be already logged in. Please wait a a minute or two and retry",
            reason="presence",
        )
        self.assertIn(LINGERING_SESSION_MESSAGE, text.lower())
