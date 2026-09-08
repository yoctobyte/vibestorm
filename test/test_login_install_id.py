"""The identifier the login payload carries, and what it must not be.

Grids use `mac` and `id0` to tell one installation from another. This client
sent the empty string for both, which is a plausible reason for a Second Life
login to be refused -- priority B -- and is in any case a distinctive thing to
say.

The obvious way to fill them in is to read the machine's MAC address and disk
serial. The tests here are mostly about *not* doing that: what goes out is
sixteen random bytes generated once and kept in `local/`, which answers the
question the grid is asking -- "is this the same installation as last time?"
-- and answers nothing it is not.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from vibestorm.login.install_id import (
    DEFAULT_INSTALL_ID_PATH,
    FIELDS,
    IDENTIFIER_BYTES,
    install_identity,
    stored_identity,
)
from vibestorm.login.models import LoginCredentials, LoginRequest

HEX = re.compile(r"\A[0-9a-f]+\Z")


class StoredIdentityTests(unittest.TestCase):
    """Generated once, then remembered."""

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "install-id"

    def test_the_same_identity_comes_back_on_the_next_run(self) -> None:
        first = stored_identity(self.path)
        self.assertEqual(stored_identity(self.path), first)

    def test_two_installations_get_different_identities(self) -> None:
        """Otherwise it is not an identifier, it is a constant."""
        other = self.path.with_name("other")
        self.assertNotEqual(stored_identity(self.path), stored_identity(other))

    def test_both_fields_are_hex_of_the_declared_length(self) -> None:
        identity = stored_identity(self.path)
        self.assertEqual(sorted(identity), sorted(FIELDS))
        for field, value in identity.items():
            with self.subTest(field):
                self.assertEqual(len(value), IDENTIFIER_BYTES * 2)
                self.assertRegex(value, HEX)

    def test_the_two_fields_are_not_the_same_number(self) -> None:
        identity = stored_identity(self.path)
        self.assertNotEqual(identity["mac"], identity["id0"])

    def test_a_half_written_file_is_replaced_rather_than_repaired(self) -> None:
        """The only thing that matters about these values is that they stay
        the same, and half of a remembered identity is not that."""
        self.path.write_text("mac=abc\n", encoding="utf-8")
        identity = stored_identity(self.path)
        self.assertEqual(len(identity), len(FIELDS))
        self.assertNotEqual(identity["mac"], "abc")
        self.assertEqual(stored_identity(self.path), identity)

    def test_the_stored_file_is_owner_only(self) -> None:
        import stat

        stored_identity(self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_store_that_cannot_be_written_still_yields_an_identity(self) -> None:
        """A read-only checkout should still be able to log in. It gets a new
        identity each run, which is where this started."""
        unwritable = Path("/proc/definitely-not-writable/install-id")
        identity = stored_identity(unwritable)
        self.assertEqual(sorted(identity), sorted(FIELDS))

    def test_it_is_kept_where_git_will_not_see_it(self) -> None:
        self.assertEqual(DEFAULT_INSTALL_ID_PATH.parts[0], "local")


class NotTheHardwareTests(unittest.TestCase):
    """The point of the whole module.

    A grid asking "is this the same installation" gets an answer. It does not
    get the owner's MAC address, their disk serial, their hostname or their
    username, and none of those is what would change if the machine changed.
    """

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "install-id"

    def test_nothing_about_the_machine_is_in_it(self) -> None:
        import platform
        import socket
        import uuid

        identity = stored_identity(self.path)
        blob = "".join(identity.values())
        leaks = {
            "mac address": f"{uuid.getnode():012x}",
            "hostname": socket.gethostname(),
            "node": platform.node(),
            "user": os.environ.get("USER", "\0no user\0"),
        }
        for label, secret in leaks.items():
            with self.subTest(label):
                self.assertNotIn(secret.lower(), blob.lower())

    def test_the_module_never_asks_for_the_machine_s_identifiers(self) -> None:
        """A guard on the shape rather than on the output: the next person to
        edit this file will reach for `uuid.getnode()`, and the docstring
        alone will not stop them."""
        import inspect

        from vibestorm.login import install_id

        source = inspect.getsource(install_id)
        body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        _, _, body = body.partition('"""')
        _, _, body = body.partition('"""')
        for forbidden in ("getnode", "gethostname", "platform.node", "getpass", "ifconfig"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, body)


class EnvironmentOverrideTests(unittest.TestCase):
    """The way out, for anyone who wants to send something else or nothing."""

    def test_an_override_replaces_the_stored_value(self) -> None:
        with mock.patch.dict(os.environ, {"VIBESTORM_LOGIN_MAC": "  deadbeef  "}):
            self.assertEqual(install_identity()["mac"], "deadbeef")

    def test_an_empty_override_sends_nothing_again(self) -> None:
        with mock.patch.dict(os.environ, {"VIBESTORM_LOGIN_ID0": ""}):
            self.assertEqual(install_identity()["id0"], "")

    def test_an_override_of_one_leaves_the_other_alone(self) -> None:
        with mock.patch.dict(os.environ, {"VIBESTORM_LOGIN_MAC": "abcd"}):
            overridden = install_identity()
        self.assertEqual(overridden["id0"], install_identity()["id0"])


class LoginRequestTests(unittest.TestCase):
    """What actually reaches the payload."""

    def request(self) -> LoginRequest:
        return LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="Vibestorm", last="Tester", password="x"),
        )

    def test_a_request_built_with_no_arguments_carries_the_identity(self) -> None:
        request = self.request()
        self.assertEqual(request.mac, install_identity()["mac"])
        self.assertEqual(request.id0, install_identity()["id0"])

    def test_two_requests_in_one_process_agree(self) -> None:
        self.assertEqual(self.request().mac, self.request().mac)

    def test_a_caller_can_still_say_exactly_what_to_send(self) -> None:
        request = LoginRequest(
            login_uri="http://127.0.0.1:9000/",
            credentials=LoginCredentials(first="a", last="b", password="c"),
            mac="",
            id0="",
        )
        self.assertEqual((request.mac, request.id0), ("", ""))

    def test_the_payload_carries_them(self) -> None:
        """Through `_request_payload`, which is what goes on the wire."""
        from vibestorm.login.client import LoginClient

        payload = LoginClient()._request_payload(self.request())
        self.assertEqual(payload["mac"], install_identity()["mac"])
        self.assertEqual(payload["id0"], install_identity()["id0"])


if __name__ == "__main__":
    unittest.main()
