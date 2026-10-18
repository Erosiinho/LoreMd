"""Carries call information down to the MCP tools.

The MCP SDK does not expose the HTTP request to tools consistently; a
contextvar set by an ASGI middleware remains the most stable path.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class CallContext:
    token: str | None
    ip: str | None


_current: ContextVar[CallContext] = ContextVar(
    "loremd_call_context", default=CallContext(None, None)
)


def current_context() -> CallContext:
    return _current.get()


def _client_ip(scope: Scope) -> str | None:
    headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        # Caddy puts the real client IP first in the list.
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None


def _bearer(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode().lower() == "authorization":
            header = value.decode()
            scheme, _, token = header.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


class CallContextMiddleware:
    """Captures the token and IP of every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _bearer(scope)
        reset = _current.set(CallContext(token=token, ip=_client_ip(scope)))
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(reset)
