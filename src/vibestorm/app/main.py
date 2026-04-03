"""Top-level application assembly."""

from dataclasses import dataclass


@dataclass(slots=True)
class AppStatus:
    phase: str
    message: str


def get_status() -> AppStatus:
    """Return the current high-level application status."""
    return AppStatus(
        phase="phase-1-scaffold",
        message="Protocol implementation is pending; package structure is in place.",
    )
