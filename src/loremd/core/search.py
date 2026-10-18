"""Recherche hybride.

Plein texte et vectoriel sont lancés sur le même ensemble filtré, puis fusionnés
par RRF. En dessous du seuil de pertinence, on répond `no_match` plutôt que de
renvoyer du bruit : c'est un principe du produit, pas un détail d'implémentation.
"""

from __future__ import annotations

from ..auth.principal import Principal, Scope
from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..embeddings import EmbeddingProvider
from ..errors import ValidationError
from .audit import AuditService
from .models import SEARCHABLE_STATUSES, MemoryStatus, MemoryType, SearchResponse
from .spaces import SpaceService


class SearchService:
    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        embeddings: EmbeddingProvider,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._embeddings = embeddings
        self._audit = audit
        self._settings = settings

    async def search(
        self,
        principal: Principal,
        *,
        query: str,
        space_slug: str | None = None,
        project: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int | None = None,
        statuses: list[MemoryStatus] | None = None,
    ) -> SearchResponse:
        principal.require_scope(Scope.READ)
        if not query.strip():
            raise ValidationError("query est vide")

        config = self._settings.search
        effective_limit = min(limit or config.default_limit, config.max_limit)

        if space_slug:
            resolved = await self._spaces.resolve(space_slug, principal)
            space_ids = [resolved.space.id]
        else:
            space_ids = [space.id for space in await self._spaces.list(principal)]

        if not space_ids:
            return SearchResponse(matches=[], status="no_match")

        embedding = await self._embeddings.embed_one(query)

        async with self._db.acquire() as connection:
            hits = await MemoryIndexRepository(connection).search(
                query=query,
                embedding=embedding,
                space_ids=space_ids,
                statuses=statuses or list(SEARCHABLE_STATUSES),
                project=project,
                memory_type=memory_type.value if memory_type else None,
                limit=effective_limit,
                rrf_k=config.rrf_k,
                candidate_pool=max(effective_limit * 5, config.max_limit),
            )

        # Le score RRF ne reflète que des rangs : il ne dit pas si un résultat est
        # pertinent. On filtre donc sur des signaux absolus — un match plein texte,
        # ou une similarité cosinus suffisante — et le RRF ne sert qu'à ordonner.
        kept = [
            hit
            for hit in hits
            if hit.rank_text is not None
            or (hit.similarity is not None and hit.similarity >= config.min_similarity)
        ]
        await self._audit.record(
            principal,
            "memory_search",
            target=query[:200],
            meta={
                "space": space_slug,
                "project": project,
                "type": memory_type.value if memory_type else None,
                "results": len(kept),
            },
        )
        if not kept:
            return SearchResponse(matches=[], status="no_match")
        return SearchResponse(matches=kept, status="ok")
