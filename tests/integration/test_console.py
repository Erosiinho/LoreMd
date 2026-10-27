"""The admin console and the git webhook, over HTTP.

The app runs in a TestClient; its portal runs setup code on the app's own
event loop, against the very core that serves the requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import subprocess
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
from loremd.config import Settings
from loremd.core import frontmatter
from loremd.core.git import GitRepo
from loremd.core.models import MemoryStatus, MemoryType
from loremd.core.service import Loremd
from loremd.db import Database
from loremd.db.repositories import AuditRepository
from loremd.embeddings.hash_provider import HashEmbeddingProvider

PASSWORD = "correct horse battery staple"
WEBHOOK_SECRET = "webhook-secret-for-tests"
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


@dataclass
class Console:
    client: TestClient
    core: Loremd
    secrets: dict[str, str]

    def run(self, function: Any, *args: Any) -> Any:
        """Runs a coroutine function on the app's event loop."""
        return self.client.portal.call(function, *args)

    def login(self, email: str = "admin@acme.com", *, step_offset: int = 0) -> str:
        """Logs in and returns the CSRF token of the session."""
        code = totp.code_at(self.secrets[email], totp.current_step() + step_offset)
        response = self.client.post(
            "/admin/login",
            data={"email": email, "password": PASSWORD, "code": code},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        return csrf_of(self.client.get("/admin").text)

    def pending(self, content: str) -> str:
        async def write() -> str:
            space = await self.core.spaces.get("regulated")
            writer = Principal(
                name="codex-ci",
                scopes=frozenset({Scope.READ, Scope.WRITE}),
                space_ids=frozenset({space.id}),
            )
            result = await self.core.memories.write(
                writer, content=content, memory_type=MemoryType.DECISION, space_slug="regulated"
            )
            return result.memory.id

        return str(self.run(write))

    def status(self, memory_id: str) -> MemoryStatus:
        async def get() -> MemoryStatus:
            return (await self.core.memories.get(Principal.admin(), memory_id)).status

        return MemoryStatus(self.run(get))


def csrf_of(html: str) -> str:
    match = CSRF.search(html)
    assert match, "the page must carry a CSRF token"
    return match.group(1)


async def _reset(dsn: str) -> None:
    database = Database(dsn)
    await database.connect()
    async with database.acquire() as connection:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await database.close()


@pytest.fixture
def console(settings: Settings, tmp_path: Path) -> Iterator[Console]:
    asyncio.run(_reset(settings.database.dsn))
    configured = settings.model_copy(
        update={"sync": settings.sync.model_copy(update={"webhook_secret": WEBHOOK_SECRET})}
    )
    core = Loremd(configured, embeddings=HashEmbeddingProvider(settings.embeddings.dimensions))
    with TestClient(create_app(configured, core=core)) as client:
        secrets: dict[str, str] = {}

        async def setup() -> None:
            await core.spaces.create("regulated", review_required=True)
            for email, role in (("admin@acme.com", Role.ADMIN), ("rev@acme.com", Role.REVIEWER)):
                _, secrets[email] = await core.admins.create(email, PASSWORD, role)

        client.portal.call(setup)
        yield Console(client, core, secrets)


# --------------------------------------------------------------------- login


def test_pages_need_a_session(console: Console) -> None:
    response = console.client.get("/admin/review", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_the_console_does_not_need_a_bearer_token(console: Console) -> None:
    """The MCP catch-all turns away requests without a token; /admin is not behind it."""
    response = console.client.get("/admin/login")

    assert response.status_code == 200
    assert "Authenticator code" in response.text


def test_a_wrong_code_is_refused_without_saying_which_field(console: Console) -> None:
    response = console.client.post(
        "/admin/login",
        data={"email": "admin@acme.com", "password": PASSWORD, "code": "000000"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "invalid email, password or code" in response.text
    assert "loremd_session" not in response.cookies


def test_a_wrong_password_is_refused(console: Console) -> None:
    code = totp.code_at(console.secrets["admin@acme.com"], totp.current_step())
    response = console.client.post(
        "/admin/login",
        data={"email": "admin@acme.com", "password": "not the password", "code": code},
    )

    assert response.status_code == 401


def test_login_attempts_are_counted_per_client_behind_the_proxy(console: Console) -> None:
    # Behind Caddy every request comes from the proxy: the client is the first
    # X-Forwarded-For entry, so one client's failures do not lock out the others.
    attempts = console.core.settings.console.login_attempts_per_minute
    for _ in range(attempts):
        console.client.post(
            "/admin/login",
            data={"email": "rev@acme.com", "password": "wrong", "code": "000000"},
            headers={"x-forwarded-for": "203.0.113.7"},
        )
    blocked = console.client.post(
        "/admin/login",
        data={"email": "admin@acme.com", "password": "wrong", "code": "000000"},
        headers={"x-forwarded-for": "203.0.113.7"},
    )
    assert blocked.status_code == 429

    console.login()

    async def failed_ips() -> set[str]:
        async with console.core.db.acquire() as connection:
            rows = await AuditRepository(connection).tail(100, action="console_login_failed")
        return {str(row["ip"]) for row in rows}

    assert console.run(failed_ips) == {"203.0.113.7"}


def test_login_sets_a_strict_http_only_cookie(console: Console) -> None:
    code = totp.code_at(console.secrets["admin@acme.com"], totp.current_step())
    response = console.client.post(
        "/admin/login",
        data={"email": "admin@acme.com", "password": PASSWORD, "code": code},
        follow_redirects=False,
    )

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/admin" in cookie


def test_a_totp_code_cannot_be_replayed(console: Console) -> None:
    console.login()
    console.client.cookies.clear()
    code = totp.code_at(console.secrets["admin@acme.com"], totp.current_step())

    response = console.client.post(
        "/admin/login", data={"email": "admin@acme.com", "password": PASSWORD, "code": code}
    )

    assert response.status_code == 401


def test_logout_ends_the_session(console: Console) -> None:
    csrf = console.login()
    session = console.client.cookies.get("loremd_session")

    console.client.post("/admin/logout", data={"csrf_token": csrf}, follow_redirects=False)
    console.client.cookies.set("loremd_session", session or "", path="/admin")

    assert console.client.get("/admin", follow_redirects=False).status_code == 303


def test_pages_carry_a_strict_content_security_policy(console: Console) -> None:
    console.login()

    response = console.client.get("/admin")

    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


# -------------------------------------------------------------------- review


def test_approving_from_the_console(console: Console) -> None:
    memory_id = console.pending("Audit logs are kept ten years.")
    csrf = console.login()

    page = console.client.get("/admin/review")
    assert "Audit logs are kept ten years." in page.text
    response = console.client.post(
        f"/admin/memories/{memory_id}/approve",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert console.status(memory_id) is MemoryStatus.ACTIVE

    async def last_commit() -> str:
        space = await console.core.spaces.get("regulated")
        memory = await console.core.memories.get(Principal.admin(), memory_id)
        return console.core.spaces.repo_for(space).history(memory.path)[0].email

    assert console.run(last_commit) == "admin@acme.com"


def test_a_form_without_the_csrf_token_changes_nothing(console: Console) -> None:
    memory_id = console.pending("Audit logs are kept ten years.")
    console.login()

    response = console.client.post(f"/admin/memories/{memory_id}/approve", data={})

    assert response.status_code == 403
    assert console.status(memory_id) is MemoryStatus.PENDING


def test_rejecting_from_the_console(console: Console) -> None:
    memory_id = console.pending("We store passwords in plain text.")
    csrf = console.login()

    console.client.post(
        f"/admin/memories/{memory_id}/reject",
        data={"csrf_token": csrf, "reason": "against policy"},
    )

    assert console.status(memory_id) is MemoryStatus.REJECTED


def test_agent_content_is_escaped(console: Console) -> None:
    console.pending("<script>alert('pwned')</script> is how we deploy.")
    console.login()

    page = console.client.get("/admin/review").text

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert" in page


def test_the_memory_page_shows_its_history_and_toggles_verified(console: Console) -> None:
    memory_id = console.pending("Backups run nightly.")
    csrf = console.login()
    console.client.post(f"/admin/memories/{memory_id}/approve", data={"csrf_token": csrf})

    page = console.client.get(f"/admin/memories/{memory_id}")
    assert "review: approve" in page.text
    console.client.post(
        f"/admin/memories/{memory_id}/verify", data={"csrf_token": csrf, "verified": "0"}
    )

    async def verified() -> bool:
        return (await console.core.memories.get(Principal.admin(), memory_id)).verified

    assert console.run(verified) is False


def test_an_unknown_memory_is_a_404_page(console: Console) -> None:
    console.login()

    response = console.client.get("/admin/memories/01JA8KZ3M9X2QH7R4T6VABCDEF")

    assert response.status_code == 404


# --------------------------------------------------------------------- roles


def test_a_reviewer_cannot_read_the_audit_log(console: Console) -> None:
    console.login("rev@acme.com")

    assert console.client.get("/admin/review").status_code == 200
    assert console.client.get("/admin/audit").status_code == 403


def test_an_admin_reads_the_audit_log(console: Console) -> None:
    console.login()

    page = console.client.get("/admin/audit", params={"action": "console_login"})

    assert page.status_code == 200
    assert "admin@acme.com" in page.text


def test_a_disabled_admin_loses_their_session(console: Console) -> None:
    console.login()

    console.run(console.core.admins.set_disabled, "admin@acme.com", True)

    assert console.client.get("/admin", follow_redirects=False).status_code == 303


# ------------------------------------------------------------------ conflicts


def test_resolving_a_conflict_from_the_console(console: Console, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True)
    admin = Principal.admin("test-agent")

    async def make_conflict() -> str:
        core = console.core
        space = await core.spaces.create("product", remote_url=str(remote))
        written = await core.memories.write(
            admin, content="We use Postgres.", memory_type=MemoryType.DECISION,
            space_slug="product", title="Database",
        )
        await core.sync.sync_space(space)
        clone = tmp_path / "alice"
        subprocess.run(["git", "clone", str(remote), str(clone)], check=True)
        human = GitRepo(clone)
        memory = frontmatter.read_file(
            clone / written.memory.path, space="product", relative_path=written.memory.path
        )
        memory.content = "We use Postgres 15."
        frontmatter.write_file(clone / written.memory.path, memory)
        human.commit([written.memory.path], "human", author_name="a", author_email="a@a.a")
        human.push()
        await core.memories.update(admin, written.memory.id, "We use Postgres 16.", "upgrade")
        report = await core.sync.sync_space(space)
        assert report.conflicts
        return written.memory.id

    memory_id = console.run(make_conflict)
    csrf = console.login()

    listing = console.client.get("/admin/conflicts")
    assert "Database" in listing.text
    page = console.client.get(f"/admin/conflicts/{memory_id}")
    assert "We use Postgres 15." in page.text and "We use Postgres 16." in page.text
    response = console.client.post(
        f"/admin/conflicts/{memory_id}/resolve",
        data={"csrf_token": csrf, "keep": "copy", "content": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert console.status(memory_id) is MemoryStatus.ACTIVE

    async def content() -> str:
        return (await console.core.memories.get(admin, memory_id)).content

    assert console.run(content) == "We use Postgres 16."


# ------------------------------------------------------------------- webhook


def _signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def test_the_webhook_refuses_a_bad_signature(console: Console) -> None:
    body = b'{"ref": "refs/heads/main"}'

    response = console.client.post(
        "/hooks/git/regulated", content=body, headers={"X-Hub-Signature-256": "sha256=00"}
    )

    assert response.status_code == 401


def test_the_webhook_ignores_other_branches(console: Console) -> None:
    body = b'{"ref": "refs/heads/feature"}'

    response = console.client.post("/hooks/git/regulated", content=body, headers=_signed(body))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_a_signed_push_schedules_a_sync_that_reindexes(console: Console) -> None:
    async def hand_edit() -> str:
        core = console.core
        space = await core.spaces.get("regulated")
        written = await core.memories.write(
            Principal.admin("test-agent"),
            content="Retention is ten years.",
            memory_type=MemoryType.DECISION,
            space_slug="regulated",
        )
        path = Path(space.path) / written.memory.path
        memory = frontmatter.read_file(path, space="regulated", relative_path=written.memory.path)
        memory.content = "Retention is twelve years, per the auditors."
        frontmatter.write_file(path, memory)
        core.spaces.repo_for(space).commit(
            [written.memory.path], "auditors", author_name="a", author_email="a@a.a"
        )
        return written.memory.id

    memory_id = console.run(hand_edit)
    body = b'{"ref": "refs/heads/main"}'

    response = console.client.post("/hooks/git/regulated", content=body, headers=_signed(body))
    assert response.status_code == 202
    assert console.run(console.core.sync.wait, 30)

    async def content() -> str:
        return (await console.core.memories.get(Principal.admin(), memory_id)).content

    assert console.run(content) == "Retention is twelve years, per the auditors."


def test_the_webhook_is_off_without_a_secret(settings: Settings) -> None:
    core = Loremd(settings, embeddings=HashEmbeddingProvider(settings.embeddings.dimensions))
    with TestClient(create_app(settings, core=core)) as client:
        response = client.post("/hooks/git/anything", content=b"{}")

    assert response.status_code == 404
