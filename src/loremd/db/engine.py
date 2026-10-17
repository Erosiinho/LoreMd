"""Pool asyncpg et application des migrations.

Postgres n'est qu'un index : il peut être vidé et reconstruit par
`loremd reindex`, à l'exception des tables agents, tokens et audit_log.
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
    """Migrations (nom, sql) triées par nom."""
    root = resources.files(MIGRATIONS_PACKAGE)
    files = [item for item in root.iterdir() if item.name.endswith(".sql")]
    ordered = sorted(files, key=lambda item: item.name)
    return [(item.name, item.read_text(encoding="utf-8")) for item in ordered]


class Database:
    """Accès Postgres partagé par le serveur et la CLI."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self.dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise ConfigError("la base n'est pas connectée : appeler connect() d'abord")
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
        """Applique les migrations manquantes. Retourne celles qui ont tourné."""
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
        """Dimension du vecteur telle que déclarée dans le schéma."""
        async with self.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT atttypmod AS dim
                FROM pg_attribute
                WHERE attrelid = 'memory_index'::regclass AND attname = 'embedding'
                """
            )
        return int(row["dim"]) if row and row["dim"] and row["dim"] > 0 else None


def dsn_from_path(path: Path) -> str:  # pragma: no cover - utilitaire de confort
    """Lit un DSN depuis un fichier (secret monté dans le conteneur)."""
    return path.read_text(encoding="utf-8").strip()
