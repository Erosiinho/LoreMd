"""Assemblage du core.

Un seul objet à construire, que ce soit pour le serveur MCP ou pour la CLI.
Il ne connaît ni HTTP ni MCP.
"""

from __future__ import annotations

import logging

from ..auth.authenticator import Authenticator
from ..config import Settings, get_settings
from ..db import Database
from ..embeddings import EmbeddingProvider, build_provider
from ..errors import ConfigError
from .audit import AuditService
from .indexer import Indexer
from .memories import MemoryService
from .search import SearchService
from .spaces import SpaceService

logger = logging.getLogger(__name__)


class Loremd:
    """Point d'entrée du core : espaces, mémoires, recherche, index, audit."""

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
        self.memories = MemoryService(
            self.db, self.spaces, self.indexer, self.embeddings, self.audit, self.settings
        )
        self.search = SearchService(
            self.db, self.spaces, self.embeddings, self.audit, self.settings
        )
        self.auth = Authenticator(self.db, self.settings.bootstrap_token)

    async def start(self, *, migrate: bool = True) -> None:
        self.settings.spaces.root.mkdir(parents=True, exist_ok=True)
        await self.db.connect()
        if migrate:
            applied = await self.db.migrate(self.settings.embeddings.dimensions)
            if applied:
                logger.info("migrations appliquées : %s", ", ".join(applied))
        await self._check_embedding_dimensions()

    async def stop(self) -> None:
        await self.db.close()

    async def _check_embedding_dimensions(self) -> None:
        """Un changement de modèle sans réindexation donnerait des scores faux."""
        schema_dimensions = await self.db.embedding_dimensions()
        configured = self.settings.embeddings.dimensions
        if schema_dimensions is not None and schema_dimensions != configured:
            raise ConfigError(
                f"la colonne embedding est en vector({schema_dimensions}) alors que la "
                f"configuration déclare {configured} dimensions. Changer de modèle impose "
                "une migration et un `loremd reindex`."
            )

    async def __aenter__(self) -> Loremd:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()
