"""XML-RPC login/bootstrap client."""

from __future__ import annotations

import asyncio
import hashlib
import xmlrpc.client
from dataclasses import dataclass
from uuid import UUID

from vibestorm.login.models import LoginBootstrap, LoginRequest


class LoginError(RuntimeError):
    """Raised when login/bootstrap fails."""


@dataclass(slots=True)
class LoginClient:
    """Perform the initial XML-RPC login/bootstrap request."""

    async def login(self, request: LoginRequest) -> LoginBootstrap:
        return await asyncio.to_thread(self._login_sync, request)

    def _login_sync(self, request: LoginRequest) -> LoginBootstrap:
        server = xmlrpc.client.ServerProxy(request.login_uri, allow_none=True)
        response = server.login_to_simulator(self._request_payload(request))
        if not isinstance(response, dict):
            raise LoginError("login response is not a struct")

        if str(response.get("login", "")).lower() != "true":
            message = str(response.get("message", "login failed"))
            raise LoginError(message)

        try:
            return LoginBootstrap(
                agent_id=UUID(str(response["agent_id"])),
                session_id=UUID(str(response["session_id"])),
                secure_session_id=UUID(str(response["secure_session_id"])),
                circuit_code=int(response["circuit_code"]),
                sim_ip=str(response["sim_ip"]),
                sim_port=int(response["sim_port"]),
                seed_capability=str(response["seed_capability"]),
                region_x=int(response["region_x"]),
                region_y=int(response["region_y"]),
                message=str(response.get("message", "")),
            )
        except KeyError as exc:
            raise LoginError(f"login response missing field: {exc.args[0]}") from exc

    def _request_payload(self, request: LoginRequest) -> dict[str, object]:
        return {
            "first": request.credentials.first,
            "last": request.credentials.last,
            "passwd": sl_password_hash(request.credentials.password),
            "start": request.start,
            "channel": request.channel,
            "version": request.version,
            "platform": request.platform,
            "platform_version": request.platform_version,
            "mac": request.mac,
            "id0": request.id0,
            "viewer_digest": request.viewer_digest,
            "agree_to_tos": request.agree_to_tos,
            "read_critical": request.read_critical,
        }


def sl_password_hash(password: str) -> str:
    return "$1$" + hashlib.md5(password.encode("utf-8")).hexdigest()
