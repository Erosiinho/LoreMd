"""Fixtures for the tests that need Postgres with pgvector.

Point `LOREMD_TEST_DSN` at an instance (`docker compose up postgres` gives
you one) and these tests run; without it they are skipped, so the default
suite stays dependency-free.

Each test gets a fresh schema and a fresh spaces directory: the index is
disposable by design, and these tests rely on it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from loremd.auth.principal import Principal
from loremd.config import Settings
from loremd.core.service import Loremd
from loremd.db import Database
from loremd.embeddings.hash_provider import HashEmbeddingProvider

#: Small enough to keep the tests fast, large enough for hash embeddings to
#: separate different texts.
TEST_DIMENSIONS = 64

DSN_ENV = "LOREMD_TEST_DSN"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        pytest.skip(f"{DSN_ENV} is not set: no Postgres available for this test")
    return dsn


@pytest.fixture
async def settings(tmp_path: Path) -> Settings:
    return Settings(
        database={"dsn": _dsn()},
        spaces={"root": tmp_path / "spaces", "default_branch": "main"},
        embeddings={"provider": "hash", "dimensions": TEST_DIMENSIONS},
        # Hash embeddings are not semantic: only identical texts come out close,
        # so the thresholds have to be read as "nearly the same text".
        search={"min_similarity": 0.5, "default_limit": 8, "max_limit": 50},
        write={"max_bytes": 4096, "dedup_threshold": 0.95, "detect_secrets": True},
        git={"author_email_domain": "agents.test", "auto_push": False},
        limits={"requests_per_minute": 0, "burst": 0},
    )


async def _drop_schema(dsn: str) -> None:
    database = Database(dsn)
    await database.connect()
    async with database.acquire() as connection:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await database.close()


@pytest.fixture
async def core(settings: Settings) -> AsyncIterator[Loremd]:
    """A started core on an empty schema, with deterministic embeddings."""
    await _drop_schema(settings.database.dsn)
    service = Loremd(
        settings, embeddings=HashEmbeddingProvider(dimensions=TEST_DIMENSIONS)
    )
    await service.start()
    try:
        yield service
    finally:
        await service.stop()


@pytest.fixture
def admin() -> Principal:
    return Principal.admin("test-agent")
