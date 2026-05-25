import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

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


if __name__ == "__main__":
    unittest.main()
