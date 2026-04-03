from pathlib import Path

from vibestorm.app.cli import build_parser
from vibestorm.app.main import get_status
from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH


def test_status_phase() -> None:
    assert get_status().phase == "phase-2-protocol-runtime"


def test_session_parser_uses_hardcoded_unknowns_db() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "session-run",
            "--login-uri",
            "http://127.0.0.1:9000/",
            "--first",
            "Test",
            "--last",
            "User",
            "--password",
            "secret",
        ],
    )

    assert DEFAULT_UNKNOWNS_DB_PATH == Path("local/unknowns.sqlite3")


def test_unknowns_report_parser_accepts_limit_argument() -> None:
    parser = build_parser()
    args = parser.parse_args(["unknowns-report", "--limit", "5"])

    assert args.limit == 5
