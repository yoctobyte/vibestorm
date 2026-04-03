"""Top-level application assembly."""

from dataclasses import dataclass


@dataclass(slots=True)
class AppStatus:
    phase: str
    message: str


def get_status() -> AppStatus:
    """Return the current high-level application status."""
    return AppStatus(
        phase="phase-2-protocol-runtime",
        message=(
            "Login bootstrap, capabilities, event queue polling, UDP session handling, "
            "and normalized world-state updates are implemented."
        ),
    )
