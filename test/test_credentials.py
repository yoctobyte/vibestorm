import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from vibestorm.util.credentials import get_profile_path, load_profile, save_profile


class TestCredentials(unittest.TestCase):
    def setUp(self):
        self._orig_profile = os.environ.get("VIBESTORM_LOGIN_PROFILE")
        self._orig_name = os.environ.get("VIBESTORM_LOGIN_PROFILE_NAME")
        if "VIBESTORM_LOGIN_PROFILE" in os.environ:
            del os.environ["VIBESTORM_LOGIN_PROFILE"]
        if "VIBESTORM_LOGIN_PROFILE_NAME" in os.environ:
            del os.environ["VIBESTORM_LOGIN_PROFILE_NAME"]

    def tearDown(self):
        if self._orig_profile is not None:
            os.environ["VIBESTORM_LOGIN_PROFILE"] = self._orig_profile
        elif "VIBESTORM_LOGIN_PROFILE" in os.environ:
            del os.environ["VIBESTORM_LOGIN_PROFILE"]

        if self._orig_name is not None:
            os.environ["VIBESTORM_LOGIN_PROFILE_NAME"] = self._orig_name
        elif "VIBESTORM_LOGIN_PROFILE_NAME" in os.environ:
            del os.environ["VIBESTORM_LOGIN_PROFILE_NAME"]

    def test_get_profile_path_defaults(self):
        # Default name and no profile path env var
        path = get_profile_path()
        self.assertEqual(path, Path("local/vibestorm-login.env"))

        # Explicit name
        os.environ["VIBESTORM_LOGIN_PROFILE_NAME"] = "tester"
        path = get_profile_path()
        self.assertEqual(path, Path("local/vibestorm-login-tester.env"))

        # Explicit path overrides name
        os.environ["VIBESTORM_LOGIN_PROFILE"] = "/tmp/custom.env"
        path = get_profile_path()
        self.assertEqual(path, Path("/tmp/custom.env"))

    def test_load_profile_tester_fallback(self):
        # If no profile file exists and name is 'tester', returns fallback preset
        os.environ["VIBESTORM_LOGIN_PROFILE_NAME"] = "tester"
        with TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "does-not-exist.env"
            data = load_profile(non_existent)
            self.assertEqual(data["VIBESTORM_FIRST_NAME"], "Vibestorm")
            self.assertEqual(data["VIBESTORM_LAST_NAME"], "Tester")
            self.assertEqual(data["VIBESTORM_LOGIN_URI"], "http://127.0.0.1:9000/")

    def test_load_and_save_profile(self):
        with TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "login.env"
            credentials = {
                "VIBESTORM_LOGIN_URI": "http://localhost:9000",
                "VIBESTORM_FIRST_NAME": "John",
                "VIBESTORM_LAST_NAME": "O'Connor",  # contains quote
                "VIBESTORM_PASSWORD": "secret password with spaces & symbols!",
                "VIBESTORM_START_LOCATION": "last",
            }
            save_profile(profile_file, credentials)

            # Check file is generated and exists
            self.assertTrue(profile_file.is_file())

            # Load it back
            loaded = load_profile(profile_file)
            self.assertEqual(loaded["VIBESTORM_LOGIN_URI"], "http://localhost:9000")
            self.assertEqual(loaded["VIBESTORM_FIRST_NAME"], "John")
            self.assertEqual(loaded["VIBESTORM_LAST_NAME"], "O'Connor")
            self.assertEqual(loaded["VIBESTORM_PASSWORD"], "secret password with spaces & symbols!")
            self.assertEqual(loaded["VIBESTORM_START_LOCATION"], "last")


class SavedProfilePermissionTests(unittest.TestCase):
    """A saved profile holds a password. It must never be readable by anyone else."""

    CREDENTIALS = {
        "VIBESTORM_LOGIN_URI": "https://login.agni.lindenlab.com/cgi-bin/login.cgi",
        "VIBESTORM_PASSWORD": "not the real one",
    }

    def test_a_saved_profile_is_owner_only(self):
        with TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "login.env"
            save_profile(profile_file, self.CREDENTIALS)
            self.assertEqual(stat.S_IMODE(profile_file.stat().st_mode), 0o600)

    def test_a_profile_that_was_already_wide_is_narrowed(self):
        """Overwriting someone's world-readable file has to fix the mode too.

        ``O_CREAT`` applies its mode only to a file it actually creates, so
        this is the case the trailing ``chmod`` exists for.
        """
        with TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "login.env"
            profile_file.write_text("VIBESTORM_PASSWORD=old\n", encoding="utf-8")
            profile_file.chmod(0o644)
            save_profile(profile_file, self.CREDENTIALS)
            self.assertEqual(stat.S_IMODE(profile_file.stat().st_mode), 0o600)
            self.assertEqual(load_profile(profile_file)["VIBESTORM_PASSWORD"], "not the real one")

    def test_the_file_is_created_narrow_rather_than_narrowed_afterwards(self):
        """The mode has to be right at creation, not a moment later.

        ``save_profile`` tolerates a failing ``chmod`` by design, so making the
        ``chmod`` fail leaves on disk exactly the mode the file was *created*
        with. Under a permissive umask a write-then-chmod implementation would
        leave a world-readable password file behind here, which is the window
        an attacker with a read on the directory would have to hit.
        """
        with TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "login.env"
            previous_umask = os.umask(0o000)
            try:
                with mock.patch.object(Path, "chmod", side_effect=PermissionError):
                    save_profile(profile_file, self.CREDENTIALS)
            finally:
                os.umask(previous_umask)
            self.assertTrue(profile_file.is_file())
            self.assertEqual(stat.S_IMODE(profile_file.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
