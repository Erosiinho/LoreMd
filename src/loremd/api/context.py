"""Carries call information down to the MCP tools.

The MCP SDK does not expose the HTTP request to tools consistently; a
contextvar set by an ASGI middleware remains the most stable path.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from ipaddress import ip_address
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Receive, Scope, Send

from ..errors import AuthError, LoremdError

if TYPE_CHECKING:
    from ..auth.authenticator import Authenticator
    from ..auth.principal import Principal
    from ..auth.ratelimit import RateLimiter


@dataclass(frozen=True)
class CallContext:
    token: str | None
    ip: str | None
    #: Set when the HTTP gate already verified the token for this request.
    principal: Principal | None = None


#: No default instance: a mutable default shared by every context would be a
#: trap. An unset context means "no HTTP request", i.e. no token.
_current: ContextVar[CallContext | None] = ContextVar("loremd_call_context", default=None)

_NO_CALL = CallContext(None, None)


def current_context() -> CallContext:
    return _current.get() or _NO_CALL


def valid_ip(value: str | None) -> str | None:
    """The address if it is one, else None: it ends up in an `inet` column."""
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(scope: Scope) -> str | None:
    headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        # Caddy puts the real client IP first in the list.
        return valid_ip(forwarded.split(",")[0])
    client = scope.get("client")
    return valid_ip(client[0]) if client else None


def _bearer(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode().lower() == "authorization":
            header = value.decode()
            scheme, _, token = header.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


_MISSING_TOKEN = "unauthorized: missing token, use the Authorization: Bearer header"


async def _refuse(send: Send, status: int, message: str, extra: list[tuple[bytes, bytes]]) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _unauthorized(send: Send, message: str = _MISSING_TOKEN) -> None:
    await _refuse(send, 401, message, [(b"www-authenticate", b"Bearer")])


async def _too_many_failures(send: Send) -> None:
    await _refuse(
        send,
        429,
        "rate_limited: too many failed authentications from this address, retry later",
        [(b"retry-after", b"60")],
    )


class CallContextMiddleware:
    """Captures the token and IP of every HTTP request.

    With an authenticator, every request is checked here, before the MCP SDK
    sees it: an unknown, revoked or expired token gets a 401 and never reaches
    the handshake or the tool list. The per-token rate limit stays with the
    tools, which charge the principal verified here.

    Failed attempts are counted per client IP: past the limit, the gate
    answers 429 without a database lookup, so a flood of made-up tokens cannot
    exhaust the connection pool.
    """

    def __init__(
        self,
        app: ASGIApp,
        authenticator: Authenticator | None = None,
        failures: RateLimiter | None = None,
    ) -> None:
        self.app = app
        self.authenticator = authenticator
        self.failures = failures

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _bearer(scope)
        if token is None:
            await _unauthorized(send)
            return
        ip = client_ip(scope)
        principal = None
        if self.authenticator is not None:
            key = ip or "unknown"
            if self.failures is not None and self.failures.blocked(key):
                await _too_many_failures(send)
                return
            try:
                principal = await self.authenticator.authenticate(token, ip=ip, charge=False)
            except AuthError as error:
                if self.failures is not None:
                    try:
                        self.failures.check(key)
                    except LoremdError:
                        pass
                await _unauthorized(send, f"{error.code}: {error}")
                return
        reset = _current.set(CallContext(token=token, ip=ip, principal=principal))
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(reset)
