"""The console's read-only views: overview, spaces, memory browser, sessions, agents."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from loremd.api.app import create_app
from loremd.auth import totp
from loremd.auth.admins import Role
from loremd.auth.principal import Principal, Scope
from loremd.auth.tokens import generate_token
from loremd.config import Settings
from loremd.core.models import MemoryType
from loremd.core.service import Loremd
from loremd.db import Database
from loremd.db.repositories import AgentRepository, TokenRepository
from loremd.embeddings.hash_provider import HashEmbeddingProvider

PASSWORD = "correct horse battery staple"


@dataclass
class Console:
    client: TestClient
    core: Loremd
    secrets: dict[str, str]
    seeded: dict[str, Any]

    def login(self, email: str = "admin@acme.com") -> None:
        code = totp.code_at(self.secrets[email], totp.current_step())
        response = self.client.post(
            "/admin/login",
            data={"email": email, "password": PASSWORD, "code": code},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text


async def _reset(dsn: str) -> None:
    database = Database(dsn)
    await database.connect()
    async with database.acquire() as connection:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await database.close()


async def _seed(core: Loremd, secrets: dict[str, str]) -> dict[str, Any]:
    shop = await core.spaces.create("shop", description="The shop API")
    await core.spaces.create("empty")
    for email, role in (("admin@acme.com", Role.ADMIN), ("rev@acme.com", Role.REVIEWER)):
        _, secrets[email] = await core.admins.create(email, PASSWORD, role)

    writer = Principal(
        name="claude-laptop",
        scopes=frozenset({Scope.READ, Scope.WRITE}),
        space_ids=frozenset({shop.id}),
    )
    decision = await core.memories.write(
        writer,
        content="We use Postgres with Drizzle for the shop API.",
        memory_type=MemoryType.DECISION,
        space_slug="shop",
        project="api",
        tags=["db"],
    )
    await core.memories.write(
        writer,
        content="Never log access tokens, even truncated.",
        memory_type=MemoryType.RULE,
        space_slug="shop",
    )
    await core.handoffs.save(
        writer,
        space_slug="shop",
        project="api",
        title="Auth refactor, day 1",
        summary="Login moved to JWT.",
        next_actions=["Finish the refresh endpoint"],
    )
    latest = await core.handoffs.save(
        writer,
        space_slug="shop",
        project="api",
        title="Auth refactor, day 2",
        summary="Refresh endpoint half done.",
        open_questions=["Keep the session cookie for the console?"],
        next_actions=["Write the refresh tests"],
    )

    async with core.db.transaction() as connection:
        agent = await AgentRepository(connection).create("claude-laptop", "Eros's laptop")
        parts, token_hash = generate_token()
        await TokenRepository(connection).create(
            agent_id=agent["id"],
            prefix=parts.prefix,
            token_hash=token_hash,
            scopes=["read", "write"],
            space_ids=[shop.id],
        )
    return {
        "decision": decision.memory.id,
        "handoff": latest.memory.id,
        "prefix": parts.prefix,
        "raw": parts.raw,
    }


@pytest.fixture
def console(settings: Settings, tmp_path: Path) -> Iterator[Console]:
    asyncio.run(_reset(settings.database.dsn))
    core = Loremd(settings, embeddings=HashEmbeddingProvider(settings.embeddings.dimensions))
    with TestClient(create_app(settings, core=core)) as client:
        secrets: dict[str, str] = {}
        seeded = client.portal.call(_seed, core, secrets)
        yield Console(client, core, secrets, seeded)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize(
    "path",
    ["/admin", "/admin/spaces", "/admin/spaces/shop", "/admin/sessions", "/admin/agents"],
)
def test_new_pages_need_a_session(console: Console, path: str) -> None:
    response = console.client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_the_overview_counts_memories_and_sessions(console: Console) -> None:
    console.login()
    page = console.client.get("/admin")

    assert page.status_code == 200
    text = _text(page.text)
    assert "Auth refactor, day 2" in text
    assert "Auth refactor, day 1" not in text, "only the head of a thread is a session"
    assert "Recent activity" in text


def test_a_reviewer_sees_the_overview_without_the_activity(console: Console) -> None:
    console.login("rev@acme.com")
    page = console.client.get("/admin")

    assert page.status_code == 200
    assert "Recent activity" not in page.text
    assert "/admin/agents" not in page.text


def test_the_spaces_page_lists_every_space(console: Console) -> None:
    console.login()
    text = _text(console.client.get("/admin/spaces").text)

    assert "shop" in text and "empty" in text
    assert "The shop API" in text


def test_the_space_page_browses_and_filters_memories(console: Console) -> None:
    console.login()

    everything = _text(console.client.get("/admin/spaces/shop").text)
    assert "Never log access tokens" in everything
    assert "We use Postgres" in everything

    rules = _text(console.client.get("/admin/spaces/shop?type=rule").text)
    assert "Never log access tokens" in rules
    assert "We use Postgres" not in rules

    searched = _text(console.client.get("/admin/spaces/shop?q=drizzle").text)
    assert "We use Postgres" in searched
    assert "Never log access tokens" not in searched

    by_project = _text(console.client.get("/admin/spaces/shop?project=api&type=decision").text)
    assert "We use Postgres" in by_project


def test_the_search_filter_treats_wildcards_literally(console: Console) -> None:
    console.login()
    text = _text(console.client.get("/admin/spaces/shop?q=%25").text)

    assert "No memory matches" in text


def test_an_invalid_filter_is_a_400_page(console: Console) -> None:
    console.login()
    response = console.client.get("/admin/spaces/shop?type=nonsense")

    assert response.status_code == 400


def test_an_unknown_space_is_a_404_page(console: Console) -> None:
    console.login()

    assert console.client.get("/admin/spaces/nowhere").status_code == 404


def test_the_sessions_page_shows_the_latest_handoff_of_each_project(console: Console) -> None:
    console.login()
    text = _text(console.client.get("/admin/sessions").text)

    assert "Auth refactor, day 2" in text
    assert "Refresh endpoint half done." in text
    assert "Write the refresh tests" in text
    assert "2 handoffs" in text


def test_a_thread_shows_every_handoff_newest_first(console: Console) -> None:
    console.login()
    text = _text(console.client.get("/admin/sessions/thread?space=shop&project=api").text)

    assert text.index("Auth refactor, day 2") < text.index("Auth refactor, day 1")
    assert "Keep the session cookie for the console?" in text
    assert "superseded" in text


def test_a_handoff_page_renders_its_sections(console: Console) -> None:
    console.login()
    text = _text(console.client.get(f"/admin/memories/{console.seeded['handoff']}").text)

    assert "Open questions" in text
    assert "Keep the session cookie for the console?" in text
    assert "Whole thread" in text


def test_the_agents_page_lists_tokens_by_prefix_only(console: Console) -> None:
    console.login()
    page = console.client.get("/admin/agents")

    assert page.status_code == 200
    assert "claude-laptop" in page.text
    assert console.seeded["prefix"] in page.text
    assert console.seeded["raw"] not in page.text


def test_a_reviewer_cannot_see_the_agents(console: Console) -> None:
    console.login("rev@acme.com")

    assert console.client.get("/admin/agents").status_code == 403


def test_agent_content_is_escaped_in_the_browser(console: Console) -> None:
    async def write() -> None:
        await console.core.memories.write(
            Principal.admin("x"),
            content="<script>alert(1)</script> stays text",
            memory_type=MemoryType.NOTE,
            space_slug="shop",
        )

    console.client.portal.call(write)
    console.login()
    page = console.client.get("/admin/spaces/shop")

    assert "<script>alert(1)</script>" not in page.text


def test_pages_use_no_inline_style(console: Console) -> None:
    """The CSP has no 'unsafe-inline': an inline style would be silently dropped."""
    console.login()
    for path in (
        "/admin",
        "/admin/spaces",
        "/admin/spaces/shop",
        "/admin/sessions",
        "/admin/sessions/thread?space=shop&project=api",
        "/admin/agents",
        f"/admin/memories/{console.seeded['decision']}",
    ):
        html = console.client.get(path).text
        assert "style=" not in html, path
        assert "<style" not in html and "<script" not in html, path
