"""Models for login/bootstrap state."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


DEFAULT_LOGIN_OPTIONS: tuple[str, ...] = (
    "inventory-root",
    "inventory-skeleton",
    "inventory-lib-root",
    "inventory-lib-owner",
    "inventory-skel-lib",
    "initial-outfit",
    "gestures",
    "display_names",
    "event_categories",
    "event_notifications",
    "classified_categories",
    "adult_compliant",
    "buddy-list",
    "newuser-config",
    "ui-config",
    "advanced-mode",
    "max-agent-groups",
    "map-server-url",
    "voice-config",
    "tutorial_setting",
    "login-flags",
    "global-textures",
    "currency",
    "max_groups",
    "search",
    "destination_guide_url",
    "avatar_picker_url",
)


@dataclass(slots=True, frozen=True)
class LoginCredentials:
    first: str
    last: str
    password: str


@dataclass(slots=True, frozen=True)
class BootstrapBakedCacheEntry:
    texture_index: int
    cache_id: UUID
    texture_id: UUID | None = None


@dataclass(slots=True, frozen=True)
class BootstrapPackedAppearance:
    serial_num: int | None = None
    avatar_height: float | None = None
    texture_entry: bytes | None = None
    visual_params: bytes | None = None


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
    options: tuple[str, ...] = DEFAULT_LOGIN_OPTIONS


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
    inventory_root_folder_id: UUID | None = None
    current_outfit_folder_id: UUID | None = None
    my_outfits_folder_id: UUID | None = None
    initial_outfit_name: str | None = None
    initial_outfit_gender: str | None = None
    initial_baked_cache_entries: tuple[BootstrapBakedCacheEntry, ...] = ()
    initial_packed_appearance: BootstrapPackedAppearance | None = None
