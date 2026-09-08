"""Utility for loading and saving shell-sourceable .env profile files."""

from __future__ import annotations

import os
import shlex
from pathlib import Path


def get_profile_path() -> Path:
    """Resolve the active .env profile file path from environment variables."""
    if os.environ.get("VIBESTORM_LOGIN_PROFILE"):
        return Path(os.environ["VIBESTORM_LOGIN_PROFILE"])
    name = os.environ.get("VIBESTORM_LOGIN_PROFILE_NAME", "default")
    if name == "default":
        return Path("local/vibestorm-login.env")
    else:
        return Path(f"local/vibestorm-login-{name}.env")


def load_profile(path: Path) -> dict[str, str]:
    """Parse a shell .env file, resolving quoted values using shlex."""
    res: dict[str, str] = {}
    if not path.is_file():
        # Apply tester preset if profile is 'tester' and file doesn't exist
        profile_name = os.environ.get("VIBESTORM_LOGIN_PROFILE_NAME", "default")
        if profile_name == "tester":
            return {
                "VIBESTORM_LOGIN_URI": "http://127.0.0.1:9000/",
                "VIBESTORM_FIRST_NAME": "Vibestorm",
                "VIBESTORM_LAST_NAME": "Tester",
                "VIBESTORM_START_LOCATION": "uri:Vibestorm Test&128&128&25",
            }
        return res

    try:
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                try:
                    parsed = shlex.split(val)
                    if parsed:
                        res[key] = parsed[0]
                    else:
                        res[key] = ""
                except Exception:
                    # Basic fallback unquoting
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                        res[key] = val[1:-1]
                    else:
                        res[key] = val
    except Exception as exc:
        print(f"Error loading profile {path}: {exc}")

    # Fallback default values for fields if tester profile file exists but is incomplete
    profile_name = os.environ.get("VIBESTORM_LOGIN_PROFILE_NAME", "default")
    if profile_name == "tester":
        res.setdefault("VIBESTORM_LOGIN_URI", "http://127.0.0.1:9000/")
        res.setdefault("VIBESTORM_FIRST_NAME", "Vibestorm")
        res.setdefault("VIBESTORM_LAST_NAME", "Tester")
        res.setdefault("VIBESTORM_START_LOCATION", "uri:Vibestorm Test&128&128&25")

    return res


def save_profile(path: Path, values: dict[str, str]) -> None:
    """Save credentials back to the target .env profile file with mode 600."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Vibestorm local login profile. Ignored by git. Keep mode 600."]
        for k, v in sorted(values.items()):
            lines.append(f"{k}={shlex.quote(v)}")

        # Created narrow rather than created wide and narrowed afterwards.
        # `write_text` then `chmod` leaves a window — however short — in which a
        # file holding a password exists at the process umask, typically 0644,
        # and anyone with a read on the directory can win that race. `os.open`
        # with the mode up front never opens that window. The `chmod` stays for
        # the case the file already existed: `O_CREAT` only applies its mode to
        # a file it actually creates.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        try:
            path.chmod(0o600)
        except Exception:
            pass
    except Exception as exc:
        print(f"Error saving profile {path}: {exc}")
