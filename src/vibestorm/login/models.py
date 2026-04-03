"""Models for login/bootstrap state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True, frozen=True)
class LoginCredentials:
    first: str
    last: str
    password: str


@dataclass(slots=True, frozen=True)
class LoginRequest:
    login_uri: str
    credentials: LoginCredentials
    start: str = "last"
    channel: str = "Vibestorm"
    version: str = "0.1.0"
    platform: str = "Linux"
    platform_version: str = "Unknown"
    mac: str = ""
    id0: str = ""
    viewer_digest: str = ""
    agree_to_tos: bool = True
    read_critical: bool = True


@dataclass(slots=True, frozen=True)
class LoginBootstrap:
    agent_id: UUID
    session_id: UUID
    secure_session_id: UUID
    circuit_code: int
    sim_ip: str
    sim_port: int
    seed_capability: str
    region_x: int
    region_y: int
    message: str
