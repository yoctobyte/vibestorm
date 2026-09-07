import socket
import unittest
import xmlrpc.client
from uuid import UUID

from vibestorm.login.client import (
    MAX_LOGIN_RESPONSE_BYTES,
    LoginClient,
    LoginError,
    TimeoutTransport,
    sl_password_hash,
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

        original = module.TimeoutTransport
        module.TimeoutTransport = lambda timeout_seconds: Transport()  # type: ignore[assignment]
        try:
            LoginClient()._login_sync(
                LoginRequest(
                    login_uri="http://127.0.0.1:9/",
                    credentials=LoginCredentials(first="a", last="b", password="c"),
                )
            )
        finally:
            module.TimeoutTransport = original

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

