"""asyncpg pool and migration runner.

Postgres is only an index: it can be wiped and rebuilt with `loremd reindex`,
except for the agents, tokens and audit_log tables.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

import asyncpg

from ..errors import ConfigError

MIGRATIONS_PACKAGE = "loremd.db.migrations"
EMBEDDING_DIM_PLACEHOLDER = "__EMBEDDING_DIM__"


def _migration_files() -> list[tuple[str, str]]:
    """Migrations as (name, sql), sorted by name."""
    root = resources.files(MIGRATIONS_PACKAGE)
    files = [item for item in root.iterdir() if item.name.endswith(".sql")]
    ordered = sorted(files, key=lambda item: item.name)
    return [(item.name, item.read_text(encoding="utf-8")) for item in ordered]


class Database:
    """Postgres access shared by the server and the CLI."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise ConfigError("database not connected: call connect() first")
        return self._pool

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.dsn, min_size=self._min_size, max_size=self._max_size
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection, connection.transaction():
            yield connection

    async def migrate(self, embedding_dimensions: int) -> Sequence[str]:
        """Applies pending migrations. Returns the ones that ran."""
        applied: list[str] = []
        async with self.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name       text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            done = {
                row["name"] for row in await connection.fetch("SELECT name FROM schema_migrations")
            }
            for name, sql in _migration_files():
                if name in done:
                    continue
                statement = sql.replace(EMBEDDING_DIM_PLACEHOLDER, str(embedding_dimensions))
                async with connection.transaction():
                    await connection.execute(statement)
                    await connection.execute(
                        "INSERT INTO schema_migrations (name) VALUES ($1)", name
                    )
                applied.append(name)
        return applied

    async def embedding_dimensions(self) -> int | None:
        """Vector dimension as declared in the schema."""
        async with self.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT atttypmod AS dim
                FROM pg_attribute
                WHERE attrelid = 'memory_index'::regclass AND attname = 'embedding'
                """
            )
        return int(row["dim"]) if row and row["dim"] and row["dim"] > 0 else None


def dsn_from_path(path: Path) -> str:  # pragma: no cover - convenience helper
    """Reads a DSN from a file (a secret mounted in the container)."""
    return path.read_text(encoding="utf-8").strip()
