"""Hybrid search.

Full-text and vector search run over the same filtered set, then get merged
with RRF. Below the relevance threshold we answer `no_match` rather than return
noise: that is a product principle, not an implementation detail.
"""

from __future__ import annotations

from uuid import UUID

from ..auth.principal import Principal, Scope
from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..embeddings import EmbeddingProvider
from ..errors import ValidationError
from .audit import AuditService
from .models import (
    SEARCHABLE_STATUSES,
    SEARCHABLE_TYPES,
    MemoryStatus,
    MemoryType,
    SearchHit,
    SearchResponse,
)
from .spaces import SpaceService


def relevant_hits(hits: list[SearchHit], min_similarity: float) -> list[SearchHit]:
    """Keeps only the hits backed by an absolute relevance signal.

    The RRF score only reflects ranks: the best of three bad results still
    ranks first. So a hit survives on a full-text match, or on a cosine
    similarity above the threshold — and RRF is used purely for ordering.
    """
    return [
        hit
        for hit in hits
        if hit.rank_text is not None
        or (hit.similarity is not None and hit.similarity >= min_similarity)
    ]


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
        """Searches the memory. Without a type, handoffs are left out."""
        principal.require_scope(Scope.READ)
        if not query.strip():
            raise ValidationError("query is empty")

        if space_slug:
            resolved = await self._spaces.resolve(space_slug, principal)
            space_ids = [resolved.space.id]
        else:
            space_ids = [space.id for space in await self._spaces.list(principal)]

        kept = await self.relevant(
            query=query,
            space_ids=space_ids,
            project=project,
            memory_types=[memory_type] if memory_type else list(SEARCHABLE_TYPES),
            limit=limit,
            statuses=statuses,
        )
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

    async def relevant(
        self,
        *,
        query: str,
        space_ids: list[UUID],
        memory_types: list[MemoryType],
        project: str | None = None,
        include_unscoped: bool = False,
        limit: int | None = None,
        statuses: list[MemoryStatus] | None = None,
        semantic_query: str | None = None,
    ) -> list[SearchHit]:
        """The relevant hits for a query, with no ACL check and no audit.

        Callers resolve the spaces against the token first and record their
        own audit entry. `semantic_query`, when given, is what gets embedded
        instead of `query`, which then only drives the keyword side.
        """
        if not space_ids or not query.strip():
            return []
        config = self._settings.search
        effective_limit = min(limit or config.default_limit, config.max_limit)
        embedding = await self._embeddings.embed_one(semantic_query or query)

        async with self._db.acquire() as connection:
            hits = await MemoryIndexRepository(connection).search(
                query=query,
                embedding=embedding,
                space_ids=space_ids,
                statuses=statuses or list(SEARCHABLE_STATUSES),
                project=project,
                memory_types=[t.value for t in memory_types],
                include_unscoped=include_unscoped,
                limit=effective_limit,
                rrf_k=config.rrf_k,
                candidate_pool=max(effective_limit * 5, config.max_limit),
            )
        return relevant_hits(hits, config.min_similarity)
