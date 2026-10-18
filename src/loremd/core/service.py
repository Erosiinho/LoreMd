"""Core assembly.

A single object to build, for the MCP server and the CLI alike. It knows
nothing about HTTP or MCP.
"""

from __future__ import annotations

import logging

from ..auth.admins import AdminService
from ..auth.authenticator import Authenticator
from ..auth.ratelimit import RateLimiter
from ..config import Settings, get_settings
from ..db import Database
from ..embeddings import EmbeddingProvider, build_provider
from ..errors import ConfigError
from .audit import AuditService
from .indexer import Indexer
from .memories import MemoryService
from .review import ReviewService
from .search import SearchService
from .spaces import SpaceService
from .sync import SyncService

logger = logging.getLogger(__name__)


class Loremd:
    """Core entry point: spaces, memories, search, index, sync, review, audit."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        database: Database | None = None,
        embeddings: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = database or Database(
            self.settings.database.dsn,
            min_size=self.settings.database.pool_min_size,
            max_size=self.settings.database.pool_max_size,
        )
        self.embeddings = embeddings or build_provider(self.settings.embeddings)

        self.audit = AuditService(self.db, self.settings)
        self.spaces = SpaceService(self.db, self.settings)
        self.indexer = Indexer(self.db, self.spaces, self.embeddings, self.settings)
        self.sync = SyncService(self.db, self.spaces, self.indexer, self.audit, self.settings)
        self.memories = MemoryService(
            self.db,
            self.spaces,
            self.indexer,
            self.embeddings,
            self.audit,
            self.settings,
            sync=self.sync,
        )
        self.review = ReviewService(
            self.db, self.spaces, self.indexer, self.memories, self.audit
        )
        self.search = SearchService(
            self.db, self.spaces, self.embeddings, self.audit, self.settings
        )
        self.limiter = RateLimiter(
            requests_per_minute=self.settings.limits.requests_per_minute,
            burst=self.settings.limits.burst,
        )
        self.auth = Authenticator(self.db, self.settings.bootstrap_token, self.limiter)
        self.admins = AdminService(self.db, self.settings)

    async def start(self, *, migrate: bool = True) -> None:
        self.settings.spaces.root.mkdir(parents=True, exist_ok=True)
        if self.settings.bootstrap_token:
            logger.warning(
                "LOREMD_BOOTSTRAP_TOKEN is set: it grants read and write access to every "
                "space, bypassing the tokens table. Create real agent tokens with "
                "`loremd agent create` and unset it."
            )
        await self.db.connect()
        if migrate:
            applied = await self.db.migrate(self.settings.embeddings.dimensions)
            if applied:
                logger.info("applied migrations: %s", ", ".join(applied))
        await self._check_embedding_dimensions()

    async def stop(self) -> None:
        # Pending pushes first: they still need the database to record the sync.
        await self.sync.stop()
        await self.db.close()

    async def _check_embedding_dimensions(self) -> None:
        """Switching models without reindexing would produce wrong scores."""
        schema_dimensions = await self.db.embedding_dimensions()
        configured = self.settings.embeddings.dimensions
        if schema_dimensions is not None and schema_dimensions != configured:
            raise ConfigError(
                f"the embedding column is vector({schema_dimensions}) but the "
                f"configuration declares {configured} dimensions. Changing models requires "
                "a migration and a `loremd reindex`."
            )

    async def __aenter__(self) -> Loremd:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
