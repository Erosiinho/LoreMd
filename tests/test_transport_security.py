"""Host and Origin checks of the MCP endpoint (DNS rebinding protection).

The MCP SDK refuses any Host it was not told about. The public host comes from
`server.public_url`, so a deployment behind a proxy works without turning the
protection off.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from loremd.api.mcp_server import build_mcp, transport_security
from loremd.config import Settings

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


def _post(settings: Settings, host: str, origin: str | None = None) -> int:
    mcp = build_mcp(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    headers = {**HEADERS, "host": host}
    if origin:
        headers["origin"] = origin
    with TestClient(mcp.streamable_http_app()) as client:
        return client.post("/mcp", json=INITIALIZE, headers=headers).status_code


def _settings(public_url: str, allowed_hosts: list[str] | None = None) -> Settings:
    return Settings(server={"public_url": public_url, "allowed_hosts": allowed_hosts or []})


def test_the_public_host_is_accepted() -> None:
    assert _post(_settings("https://memory.acme.com"), "memory.acme.com") == 200


def test_another_host_is_refused() -> None:
    assert _post(_settings("https://memory.acme.com"), "evil.example") == 421


def test_localhost_still_works_for_development() -> None:
    assert _post(_settings("https://memory.acme.com"), "127.0.0.1:8000") == 200


def test_a_foreign_origin_is_refused() -> None:
    status = _post(_settings("https://memory.acme.com"), "memory.acme.com", "https://evil.example")

    assert status == 403


def test_the_public_origin_is_accepted() -> None:
    status = _post(
        _settings("https://memory.acme.com"), "memory.acme.com", "https://memory.acme.com"
    )

    assert status == 200


def test_extra_hosts_come_from_the_configuration() -> None:
    settings = _settings("https://memory.acme.com", ["memory.internal:8443"])

    assert _post(settings, "memory.internal:8443") == 200


@pytest.mark.parametrize(
    ("public_url", "host", "origin"),
    [
        ("https://memory.acme.com", "memory.acme.com", "https://memory.acme.com"),
        ("https://memory.acme.com/", "memory.acme.com", "https://memory.acme.com"),
        ("http://10.0.0.5:8010", "10.0.0.5:8010", "http://10.0.0.5:8010"),
    ],
)
def test_the_public_url_gives_one_host_and_one_origin(
    public_url: str, host: str, origin: str
) -> None:
    security = transport_security(_settings(public_url))

    assert security.enable_dns_rebinding_protection
    assert host in security.allowed_hosts
    assert origin in security.allowed_origins
