"""The grid chooses every URL this client fetches. It does not choose the scheme.

The login response names the seed capability; the seed capability's own
response names every other one. Nothing in that chain is ours, and
`urllib.request.urlopen` builds its opener from every handler installed by
default -- including `FileHandler` and `FTPHandler`. So a grid answering the
seed request with

    <key>GetTexture</key><string>file:///home/you/.ssh/id_rsa</string>

got that file read and handed back to whatever asked for a texture. Measured,
not theorised: a `file://` URL went through
`CapabilityClient._fetch_capability_value_sync` and returned the file's
contents. On the sync path the same response is *written into* the folder
being synced.

Two guards, tested separately because either alone is weaker than it looks.
The scheme check gives a refusal that names the URL, in the calling module's
own error class, before a `Request` is built -- `urllib.request.Request("")`
raises a bare `ValueError`, and that is not a `CapabilityError`. The opener
carries no `FileHandler` at all, so a call site that forgets the check still
cannot open a file.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
import urllib.request
from pathlib import Path

from vibestorm.caps.client import CapabilityClient, CapabilityError
from vibestorm.util.remote_url import (
    ALLOWED_SCHEMES,
    open_remote,
    require_remote_http_url,
)

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vibestorm"


class _Refused(RuntimeError):
    pass


class SchemeTests(unittest.TestCase):
    REFUSED = (
        "file:///etc/passwd",
        "ftp://grid.example/asset",
        "data:text/plain;base64,aGk=",
        "gopher://grid.example/1",
        "jar:file:///tmp/x!/y",
        "//grid.example/no-scheme",
        "/caps/relative",
        "",
    )

    def test_only_http_and_https_are_fetched(self) -> None:
        self.assertEqual(ALLOWED_SCHEMES, frozenset({"http", "https"}))

    def test_every_other_scheme_is_refused(self) -> None:
        for url in self.REFUSED:
            with self.subTest(url):
                with self.assertRaises(_Refused):
                    require_remote_http_url(url, what="a capability", error=_Refused)

    def test_the_refusal_names_the_url_and_what_wanted_it(self) -> None:
        """So a bad capability says *which* capability, not only that one was."""
        with self.assertRaises(_Refused) as caught:
            require_remote_http_url(
                "file:///etc/passwd", what="the GetTexture capability", error=_Refused
            )
        message = str(caught.exception)
        self.assertIn("GetTexture", message)
        self.assertIn("/etc/passwd", message)

    def test_http_and_https_pass_through_unchanged(self) -> None:
        for url in ("http://grid.example/caps/x", "https://grid.example/caps/x?y=1"):
            with self.subTest(url):
                self.assertEqual(
                    require_remote_http_url(url, what="a capability", error=_Refused), url
                )

    def test_the_scheme_is_matched_case_insensitively(self) -> None:
        """`urlsplit` lowercases the scheme itself, so `HTTPS:` is `https`.
        Pinned because a `.lower()` added here would look like a fix and a
        removal of it would look like a cleanup."""
        self.assertEqual(
            require_remote_http_url("HTTPS://grid.example/x", what="a cap", error=_Refused),
            "HTTPS://grid.example/x",
        )

    def test_a_url_that_is_not_a_string_is_refused(self) -> None:
        for value in (None, 3, b"http://grid.example/"):
            with self.subTest(repr(value)):
                with self.assertRaises(_Refused):
                    require_remote_http_url(value, what="a capability", error=_Refused)


class OpenerTests(unittest.TestCase):
    """The second guard: the transport itself has no way to read a file."""

    def test_the_opener_has_no_handler_for_files_or_ftp(self) -> None:
        from vibestorm.util.remote_url import _OPENER

        names = {type(handler).__name__ for handler in _OPENER.handlers}
        self.assertNotIn("FileHandler", names)
        self.assertNotIn("FTPHandler", names)
        self.assertNotIn("DataHandler", names)
        self.assertIn("HTTPHandler", names)
        self.assertIn("HTTPSHandler", names)

    def test_a_file_url_is_refused_even_with_the_check_bypassed(self) -> None:
        """Belt and braces, and the braces are what survives a new call site
        whose author did not read this file."""
        from vibestorm.util.remote_url import open_http

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("SECRET CONTENTS")
            path = handle.name
        self.addCleanup(Path(path).unlink, True)
        with self.assertRaises(urllib.error.URLError):
            open_http(f"file://{path}", 5.0)


class ThroughTheRealClientTests(unittest.TestCase):
    """The path that actually leaked, end to end."""

    def test_a_file_capability_does_not_read_the_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as handle:
            handle.write("<llsd><string>SECRET</string></llsd>")
            path = handle.name
        self.addCleanup(Path(path).unlink, True)
        with self.assertRaises(CapabilityError) as caught:
            CapabilityClient()._fetch_capability_value_sync(f"file://{path}")
        self.assertNotIn("SECRET", str(caught.exception))

    def test_an_empty_capability_is_the_client_s_own_error(self) -> None:
        """Not the `ValueError` that `urllib.request.Request("")` raises,
        which is caught nowhere."""
        with self.assertRaises(CapabilityError):
            CapabilityClient()._fetch_capability_value_sync("")


class NoDirectUrlopenTests(unittest.TestCase):
    """The rule that outlives the eleven call sites this started with.

    A check added at eleven places is a check missing from the twelfth. What
    keeps this true is that `urllib.request.urlopen` does not appear in the
    client at all: every request goes through `open_remote`, which validates
    before it opens and opens through an opener that cannot read a file.
    """

    def offenders(self) -> list[str]:
        found = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr != "urlopen":
                    continue
                found.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}")
        return found

    def test_nothing_in_the_client_calls_urlopen_directly(self) -> None:
        self.assertEqual(
            self.offenders(),
            [],
            "use open_remote() from vibestorm.util.remote_url instead:\n"
            + "\n".join(self.offenders()),
        )

    def test_every_remote_client_reaches_the_shared_opener(self) -> None:
        """Anti-vacuity: the rule above also passes if nothing fetches at all."""
        users = [
            path.relative_to(SOURCE_ROOT).as_posix()
            for path in sorted(SOURCE_ROOT.rglob("*.py"))
            if "open_remote(" in path.read_text(encoding="utf-8")
        ]
        self.assertGreaterEqual(len(users), 8, f"only {users} reach the shared opener")
        for expected in ("caps/client.py", "caps/get_texture_client.py", "event_queue/client.py"):
            with self.subTest(expected):
                self.assertIn(expected, users)


class OpenRemoteTests(unittest.TestCase):
    def test_it_refuses_before_it_opens(self) -> None:
        """A `file:` URL must not reach the transport at all -- the refusal is
        the calling module's error, not a `URLError` from underneath."""
        with self.assertRaises(_Refused):
            open_remote("file:///etc/passwd", timeout=5.0, what="a cap", error=_Refused)

    def test_a_request_object_is_checked_by_its_own_url(self) -> None:
        request = urllib.request.Request("file:///etc/passwd", method="GET")
        with self.assertRaises(_Refused):
            open_remote(request, timeout=5.0, what="a cap", error=_Refused)


if __name__ == "__main__":
    unittest.main()
