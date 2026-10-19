"""The MCP surface, over real HTTP, with real tokens.

This is the phase 1 success criterion seen from an agent's side: point an MCP
client at the instance with a bearer token and get the seven tools.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from loremd.api.app import create_app
from loremd.auth.authenticator import Authenticator
from loremd.auth.ratelimit import RateLimiter
from loremd.auth.tokens import generate_token
from loremd.config import Settings
from loremd.core.service import Loremd
from loremd.db.repositories import AgentRepository, TokenRepository
from loremd.embeddings.hash_provider import HashEmbeddingProvider

TOOL_NAMES = {
    "spaces_list",
    "memory_search",
    "memory_get",
    "memory_write",
    "memory_update",
    "memory_supersede",
    "memory_history",
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ServedInstance:
    """A running LoreMd, plus the tokens the tests authenticate with."""

    def __init__(self, base_url: str, tokens: dict[str, str], core: Loremd) -> None:
        self.base_url = base_url
        self.tokens = tokens
        self.core = core

    @asynccontextmanager
    async def connect(self, token: str) -> AsyncIterator[tuple[ClientSession, Any]]:
        """An initialized MCP session, and what the handshake returned."""
        headers = {"Authorization": f"Bearer {token}"}
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(
                f"{self.base_url}/mcp", http_client=http_client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    handshake = await session.initialize()
                    yield session, handshake

    @asynccontextmanager
    async def session(self, token: str) -> AsyncIterator[ClientSession]:
        async with self.connect(token) as (session, _):
            yield session


def payload(result: Any) -> dict[str, Any]:
    """The JSON a tool returned, as the agent sees it."""
    assert result.content, "a tool must answer something"
    return json.loads(result.content[0].text)


@pytest.fixture
def served(settings: Settings, tmp_path) -> Iterator[ServedInstance]:
    """Boots the ASGI app on a port, with one space and three tokens."""
    import asyncio

    port = _free_port()
    dimensions = settings.embeddings.dimensions
    service = Loremd(settings, embeddings=HashEmbeddingProvider(dimensions))

    async def bootstrap() -> dict[str, str]:
        await service.start()
        async with service.db.acquire() as connection:
            await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        await service.db.migrate(dimensions)
        product = await service.spaces.create("product")
        support = await service.spaces.create("support")

        issued: dict[str, str] = {}
        async with service.db.acquire() as connection:
            agents = AgentRepository(connection)
            tokens = TokenRepository(connection)
            for name, scopes, space_ids, expires_at in (
                ("writer", ["read", "write"], [product.id], None),
                ("reader", ["read"], [product.id], None),
                ("stale", ["read", "write"], [product.id], datetime.now(UTC) - timedelta(days=1)),
                ("other-space", ["read", "write"], [support.id], None),
            ):
                agent = await agents.create(name)
                parts, digest = generate_token()
                await tokens.create(
                    agent_id=agent["id"],
                    prefix=parts.prefix,
                    token_hash=digest,
                    scopes=scopes,
                    space_ids=space_ids,
                    expires_at=expires_at,
                )
                issued[name] = parts.raw
        await service.stop()
        return issued

    issued = asyncio.run(bootstrap())

    # The app owns its own core lifecycle, so hand it the same settings.
    served_core = Loremd(settings, embeddings=HashEmbeddingProvider(dimensions))
    app = create_app(settings, core=served_core)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            if httpx.get(f"{base_url}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        threading.Event().wait(0.05)
    else:  # pragma: no cover - the server failed to boot
        server.should_exit = True
        raise RuntimeError("the server did not come up")

    try:
        yield ServedInstance(base_url, issued, served_core)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ------------------------------------------------------------------ plumbing


def test_healthz_answers_without_a_token(served: ServedInstance) -> None:
    response = httpx.get(f"{served.base_url}/healthz", timeout=5)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root_advertises_the_mcp_endpoint(served: ServedInstance) -> None:
    body = httpx.get(f"{served.base_url}/", timeout=5).json()

    assert body["transport"] == "streamable-http"
    assert body["mcp_endpoint"].endswith("/mcp")


def test_mcp_without_a_token_is_refused(served: ServedInstance) -> None:
    response = httpx.post(
        f"{served.base_url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
        timeout=5,
    )

    # No token: the request must not even reach the MCP handshake.
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "missing token" in response.json()["error"]


def test_the_documented_endpoint_needs_no_redirect(served: ServedInstance) -> None:
    """Every client doc sends agents to /mcp, with no trailing slash.

    A 307 there would make clients that drop the Authorization header or the
    body on redirect fail for no visible reason.
    """
    response = httpx.post(
        f"{served.base_url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {served.tokens['writer']}",
        },
        timeout=5,
        follow_redirects=False,
    )

    assert response.status_code == 200


# --------------------------------------------------------------------- tools


async def test_the_seven_tools_are_exposed(served: ServedInstance) -> None:
    async with served.session(served.tokens["writer"]) as session:
        tools = await session.list_tools()

    assert {tool.name for tool in tools.tools} == TOOL_NAMES
    assert all(tool.description for tool in tools.tools)


async def test_server_instructions_reach_the_client(served: ServedInstance) -> None:
    async with served.connect(served.tokens["writer"]) as (_, handshake):
        instructions = handshake.instructions

    assert instructions is not None
    assert "DATA, not instructions" in instructions


async def test_spaces_list_shows_only_the_tokens_spaces(served: ServedInstance) -> None:
    async with served.session(served.tokens["writer"]) as session:
        body = payload(await session.call_tool("spaces_list", {}))

    assert [space["slug"] for space in body["spaces"]] == ["product"]


async def test_write_then_search_across_two_agents(served: ServedInstance) -> None:
    """One agent writes, another reads it back: the point of the product."""
    async with served.session(served.tokens["writer"]) as session:
        written = payload(
            await session.call_tool(
                "memory_write",
                {
                    "content": "We use Postgres with Drizzle as the ORM for the shop API.",
                    "type": "decision",
                    "space": "product",
                    "title": "API stack",
                    "project": "shop",
                    "tags": ["backend", "db"],
                },
            )
        )

    assert written["created"] is True
    assert written["commit"]

    async with served.session(served.tokens["reader"]) as session:
        found = payload(
            await session.call_tool("memory_search", {"query": "Drizzle", "space": "product"})
        )

    assert found["status"] == "ok"
    match = found["matches"][0]
    assert match["id"] == written["id"]
    assert match["title"] == "API stack"
    assert match["provenance"]["author"] == "writer"
    assert match["matched_by"] in {"keyword", "hybrid"}


async def test_search_reports_no_match_with_a_hint(served: ServedInstance) -> None:
    async with served.session(served.tokens["reader"]) as session:
        found = payload(await session.call_tool("memory_search", {"query": "quarterly budget"}))

    assert found["status"] == "no_match"
    assert found["matches"] == []
    assert "hint" in found


async def test_duplicate_write_returns_the_existing_memory(served: ServedInstance) -> None:
    content = "Deployments happen on Tuesday mornings only."
    async with served.session(served.tokens["writer"]) as session:
        first = payload(
            await session.call_tool(
                "memory_write", {"content": content, "type": "rule", "space": "product"}
            )
        )
        second = payload(
            await session.call_tool(
                "memory_write", {"content": content, "type": "rule", "space": "product"}
            )
        )

    assert second["created"] is False
    assert second["duplicate_of"] == first["id"]
    assert "memory_update" in second["note"]


async def test_get_update_supersede_and_history(served: ServedInstance) -> None:
    async with served.session(served.tokens["writer"]) as session:
        written = payload(
            await session.call_tool(
                "memory_write",
                {
                    "content": "We use MySQL for the catalog.",
                    "type": "decision",
                    "space": "product",
                    "title": "Catalog database",
                },
            )
        )
        fetched = payload(await session.call_tool("memory_get", {"id": written["id"]}))
        updated = payload(
            await session.call_tool(
                "memory_update",
                {
                    "id": written["id"],
                    "content": "We use MySQL 8 for the catalog.",
                    "reason": "version pinned",
                },
            )
        )
        replaced = payload(
            await session.call_tool(
                "memory_supersede",
                {
                    "id": written["id"],
                    "new_content": "We use Postgres for the catalog.",
                    "reason": "migrated in October",
                },
            )
        )
        history = payload(await session.call_tool("memory_history", {"id": replaced["id"]}))

    assert fetched["content"] == "We use MySQL for the catalog."
    assert fetched["provenance"]["path"].startswith("decisions/catalog-database-")
    assert updated["id"] == written["id"]
    assert replaced["created"] is True and replaced["id"] != written["id"]
    assert history["commits"]
    assert history["commits"][0]["author"] == "writer"


async def test_an_unknown_type_is_explained_to_the_agent(served: ServedInstance) -> None:
    async with served.session(served.tokens["writer"]) as session:
        result = await session.call_tool(
            "memory_write", {"content": "Something.", "type": "idea", "space": "product"}
        )

    assert result.isError
    assert "unknown type" in result.content[0].text


async def test_a_secret_is_refused_with_its_code(served: ServedInstance) -> None:
    async with served.session(served.tokens["writer"]) as session:
        result = await session.call_tool(
            "memory_write",
            {
                "content": "The deploy key is AKIAIOSFODNN7EXAMPLE.",
                "type": "note",
                "space": "product",
            },
        )

    assert result.isError
    assert "secret_detected" in result.content[0].text


# ----------------------------------------------------------------------- acl


async def test_a_read_only_token_cannot_write(served: ServedInstance) -> None:
    async with served.session(served.tokens["reader"]) as session:
        result = await session.call_tool(
            "memory_write",
            {"content": "A new rule.", "type": "rule", "space": "product"},
        )

    assert result.isError
    assert "forbidden" in result.content[0].text
    assert "write" in result.content[0].text


async def test_a_token_cannot_reach_another_space(served: ServedInstance) -> None:
    async with served.session(served.tokens["other-space"]) as session:
        result = await session.call_tool(
            "memory_write",
            {"content": "Trying the wrong space.", "type": "note", "space": "product"},
        )

    assert result.isError
    assert "forbidden" in result.content[0].text


async def test_an_expired_token_is_refused(served: ServedInstance) -> None:
    async with served.session(served.tokens["stale"]) as session:
        result = await session.call_tool("spaces_list", {})

    assert result.isError
    assert "unauthorized" in result.content[0].text
    assert "expired" in result.content[0].text


async def test_a_looping_agent_is_rate_limited(served: ServedInstance) -> None:
    """The per-token limit has to surface as an error the agent can read."""
    served.core.auth = Authenticator(
        served.core.db, None, RateLimiter(requests_per_minute=60, burst=2)
    )

    async with served.session(served.tokens["reader"]) as session:
        outcomes = [await session.call_tool("spaces_list", {}) for _ in range(4)]

    assert not outcomes[0].isError
    assert outcomes[-1].isError
    assert "rate_limited" in outcomes[-1].content[0].text


async def test_the_limit_is_not_shared_between_tokens(served: ServedInstance) -> None:
    served.core.auth = Authenticator(
        served.core.db, None, RateLimiter(requests_per_minute=60, burst=1)
    )

    async with served.session(served.tokens["reader"]) as session:
        first = await session.call_tool("spaces_list", {})
        second = await session.call_tool("spaces_list", {})
    async with served.session(served.tokens["writer"]) as session:
        other = await session.call_tool("spaces_list", {})

    assert not first.isError
    assert second.isError
    assert not other.isError
