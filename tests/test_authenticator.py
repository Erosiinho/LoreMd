"""Token checks, against a fake connection: no Postgres needed.

What is covered here is the security contract of phase 1 — expiry, revocation,
disabled agent, IP allowlist, scopes and space isolation.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from loremd.auth.authenticator import Authenticator
from loremd.auth.principal import Scope
from loremd.auth.ratelimit import RateLimiter
from loremd.auth.tokens import generate_token, hash_token
from loremd.errors import AuthError, RateLimited

PRODUCT = uuid4()


class FakeConnection:
    """Answers the two queries the authenticator runs."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row
        self.touched: list[Any] = []

    async def fetchrow(self, _query: str, *args: Any) -> dict[str, Any] | None:
        if self._row is None or self._row["prefix"] != args[0]:
            return None
        return self._row

    async def execute(self, _query: str, *args: Any) -> None:
        self.touched.append(args)


class FakeDatabase:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.connection = FakeConnection(row)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def token_row(**overrides: Any) -> tuple[str, dict[str, Any]]:
    """A valid token and its row; overrides change one field at a time."""
    parts, digest = generate_token()
    row = {
        "id": uuid4(),
        "agent_id": uuid4(),
        "prefix": parts.prefix,
        "hash": digest,
        "scopes": ["read", "write"],
        "space_ids": [PRODUCT],
        "ip_allowlist": [],
        "expires_at": None,
        "revoked_at": None,
        "agent_name": "codex-ci",
        "agent_disabled_at": None,
    }
    row.update(overrides)
    return parts.raw, row


async def authenticate(raw: str, row: dict[str, Any] | None, **kwargs: Any):
    return await Authenticator(FakeDatabase(row)).authenticate(raw, **kwargs)


async def test_valid_token_yields_its_agent_and_spaces() -> None:
    raw, row = token_row()

    principal = await authenticate(raw, row)

    assert principal.name == "codex-ci"
    assert principal.scopes == frozenset({Scope.READ, Scope.WRITE})
    assert principal.space_ids == frozenset({PRODUCT})
    assert principal.token_prefix == row["prefix"]


async def test_missing_token_is_rejected() -> None:
    with pytest.raises(AuthError, match="missing token"):
        await authenticate("", None)


async def test_malformed_token_is_rejected() -> None:
    with pytest.raises(AuthError, match="invalid token format"):
        await authenticate("not-a-loremd-token", None)


async def test_unknown_prefix_is_rejected() -> None:
    raw, _ = token_row()

    with pytest.raises(AuthError, match="unknown token"):
        await authenticate(raw, None)


async def test_wrong_secret_with_a_known_prefix_is_rejected() -> None:
    raw, row = token_row()
    row["hash"] = hash_token("lmd_" + row["prefix"] + "_another-secret")

    with pytest.raises(AuthError, match="unknown token"):
        await authenticate(raw, row)


async def test_revoked_token_is_rejected() -> None:
    raw, row = token_row(revoked_at=datetime.now(UTC))

    with pytest.raises(AuthError, match="revoked"):
        await authenticate(raw, row)


async def test_expired_token_is_rejected() -> None:
    raw, row = token_row(expires_at=datetime.now(UTC) - timedelta(seconds=1))

    with pytest.raises(AuthError, match="expired"):
        await authenticate(raw, row)


async def test_token_valid_until_its_expiry() -> None:
    raw, row = token_row(expires_at=datetime.now(UTC) + timedelta(days=1))

    assert (await authenticate(raw, row)).name == "codex-ci"


async def test_disabled_agent_is_rejected() -> None:
    raw, row = token_row(agent_disabled_at=datetime.now(UTC))

    with pytest.raises(AuthError, match="disabled"):
        await authenticate(raw, row)


async def test_ip_allowlist_filters_callers() -> None:
    raw, row = token_row(ip_allowlist=["10.0.0.0/8"])

    assert (await authenticate(raw, row, ip="10.1.2.3")).ip == "10.1.2.3"
    with pytest.raises(AuthError, match="IP address"):
        await authenticate(raw, row, ip="203.0.113.1")
    with pytest.raises(AuthError, match="IP address"):
        await authenticate(raw, row, ip=None)


async def test_empty_allowlist_accepts_every_ip() -> None:
    raw, row = token_row()

    assert (await authenticate(raw, row, ip="203.0.113.1")).name == "codex-ci"


async def test_unknown_scope_in_the_row_is_ignored() -> None:
    raw, row = token_row(scopes=["read", "teleport"])

    assert (await authenticate(raw, row)).scopes == frozenset({Scope.READ})


async def test_successful_call_updates_last_used() -> None:
    raw, row = token_row()
    database = FakeDatabase(row)

    await Authenticator(database).authenticate(raw)

    assert database.connection.touched == [(row["id"],)]


async def test_bootstrap_token_grants_every_space() -> None:
    principal = await Authenticator(FakeDatabase(None), "boot-secret").authenticate(
        "boot-secret"
    )

    assert principal.space_ids is None
    assert principal.scopes == frozenset({Scope.READ, Scope.WRITE})


async def test_rate_limit_applies_per_token() -> None:
    raw, row = token_row()
    limiter = RateLimiter(requests_per_minute=60, burst=1)
    authenticator = Authenticator(FakeDatabase(row), None, limiter)

    await authenticator.authenticate(raw)

    with pytest.raises(RateLimited):
        await authenticator.authenticate(raw)


async def test_an_invalid_token_does_not_consume_the_bucket() -> None:
    raw, row = token_row()
    limiter = RateLimiter(requests_per_minute=60, burst=1)
    authenticator = Authenticator(FakeDatabase(row), None, limiter)

    with pytest.raises(AuthError):
        await authenticator.authenticate("lmd_unknown1_secret-value")

    await authenticator.authenticate(raw)
