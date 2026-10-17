"""Accès aux tables de l'index.

Toutes les requêtes filtrent explicitement sur les espaces autorisés : le
cloisonnement n'est jamais laissé à l'appelant.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from ..core.models import (
    IndexedMemory,
    Memory,
    MemoryStatus,
    SearchHit,
    Space,
    content_hash,
)
from .vectors import to_literal

_MEMORY_COLUMNS = """
    m.id, m.space_id, s.slug AS space, m.path, m.type, m.title, m.project,
    m.tags, m.status, m.verified, m.author, m.source, m.content,
    m.created, m.updated
"""


def _to_indexed(row: asyncpg.Record) -> IndexedMemory:
    return IndexedMemory(
        id=row["id"],
        space_id=row["space_id"],
        space=row["space"],
        path=row["path"],
        type=row["type"],
        title=row["title"],
        project=row["project"],
        tags=list(row["tags"] or []),
        status=row["status"],
        verified=row["verified"],
        author=row["author"],
        source=row["source"],
        content=row["content"],
        created=row["created"],
        updated=row["updated"],
    )


class SpaceRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(
        self,
        slug: str,
        path: str,
        *,
        description: str | None = None,
        remote_url: str | None = None,
        review_required: bool = False,
    ) -> Space:
        row = await self.c.fetchrow(
            """
            INSERT INTO spaces (slug, path, description, remote_url, review_required)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (slug) DO UPDATE
                SET path = EXCLUDED.path,
                    description = COALESCE(EXCLUDED.description, spaces.description),
                    remote_url = COALESCE(EXCLUDED.remote_url, spaces.remote_url),
                    review_required = EXCLUDED.review_required
            RETURNING id, slug, path, description, remote_url, review_required,
                      last_indexed_commit
            """,
            slug,
            path,
            description,
            remote_url,
            review_required,
        )
        return Space(**dict(row))

    async def get_by_slug(self, slug: str) -> Space | None:
        row = await self.c.fetchrow(
            """
            SELECT id, slug, path, description, remote_url, review_required,
                   last_indexed_commit
            FROM spaces WHERE slug = $1
            """,
            slug,
        )
        return Space(**dict(row)) if row else None

    async def list(self, space_ids: Sequence[UUID] | None = None) -> list[Space]:
        if space_ids is None:
            rows = await self.c.fetch(
                """
                SELECT id, slug, path, description, remote_url, review_required,
                       last_indexed_commit
                FROM spaces ORDER BY slug
                """
            )
        else:
            rows = await self.c.fetch(
                """
                SELECT id, slug, path, description, remote_url, review_required,
                       last_indexed_commit
                FROM spaces WHERE id = ANY($1::uuid[]) ORDER BY slug
                """,
                list(space_ids),
            )
        return [Space(**dict(row)) for row in rows]

    async def set_last_indexed_commit(self, space_id: UUID, commit: str | None) -> None:
        await self.c.execute(
            "UPDATE spaces SET last_indexed_commit = $2 WHERE id = $1", space_id, commit
        )

    async def set_remote(self, space_id: UUID, remote_url: str | None) -> None:
        await self.c.execute(
            "UPDATE spaces SET remote_url = $2 WHERE id = $1", space_id, remote_url
        )

    async def delete(self, space_id: UUID) -> None:
        await self.c.execute("DELETE FROM spaces WHERE id = $1", space_id)


class MemoryIndexRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def upsert(
        self, memory: Memory, space_id: UUID, embedding: Sequence[float] | None
    ) -> None:
        fm = memory.frontmatter
        await self.c.execute(
            """
            INSERT INTO memory_index (
                id, space_id, path, type, title, project, tags, status, verified,
                author, source, supersedes, content, content_hash, embedding, tsv,
                created, updated, indexed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15::vector, to_tsvector('simple', $16), $17, $18, now())
            ON CONFLICT (id) DO UPDATE SET
                space_id = EXCLUDED.space_id,
                path = EXCLUDED.path,
                type = EXCLUDED.type,
                title = EXCLUDED.title,
                project = EXCLUDED.project,
                tags = EXCLUDED.tags,
                status = EXCLUDED.status,
                verified = EXCLUDED.verified,
                author = EXCLUDED.author,
                source = EXCLUDED.source,
                supersedes = EXCLUDED.supersedes,
                content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash,
                embedding = COALESCE(EXCLUDED.embedding, memory_index.embedding),
                tsv = EXCLUDED.tsv,
                created = EXCLUDED.created,
                updated = EXCLUDED.updated,
                indexed_at = now()
            """,
            fm.id,
            space_id,
            memory.path,
            fm.type.value,
            fm.title,
            fm.project,
            fm.tags,
            fm.status.value,
            fm.verified,
            fm.author,
            fm.source,
            fm.supersedes,
            memory.content,
            content_hash(memory.content),
            to_literal(embedding) if embedding is not None else None,
            f"{fm.title}\n{' '.join(fm.tags)}\n{memory.content}",
            fm.created,
            fm.updated,
        )

    async def get(self, memory_id: str, space_ids: Sequence[UUID]) -> IndexedMemory | None:
        row = await self.c.fetchrow(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memory_index m JOIN spaces s ON s.id = m.space_id
            WHERE m.id = $1 AND m.space_id = ANY($2::uuid[])
            """,
            memory_id,
            list(space_ids),
        )
        return _to_indexed(row) if row else None

    async def get_by_path(self, space_id: UUID, path: str) -> IndexedMemory | None:
        row = await self.c.fetchrow(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memory_index m JOIN spaces s ON s.id = m.space_id
            WHERE m.space_id = $1 AND m.path = $2
            """,
            space_id,
            path,
        )
        return _to_indexed(row) if row else None

    async def delete(self, memory_id: str) -> None:
        await self.c.execute("DELETE FROM memory_index WHERE id = $1", memory_id)

    async def delete_by_path(self, space_id: UUID, path: str) -> None:
        await self.c.execute(
            "DELETE FROM memory_index WHERE space_id = $1 AND path = $2", space_id, path
        )

    async def clear_space(self, space_id: UUID) -> None:
        await self.c.execute("DELETE FROM memory_index WHERE space_id = $1", space_id)

    async def count(self, space_id: UUID) -> int:
        return await self.c.fetchval(
            "SELECT count(*) FROM memory_index WHERE space_id = $1", space_id
        )

    async def nearest(
        self,
        embedding: Sequence[float],
        space_id: UUID,
        *,
        statuses: Sequence[MemoryStatus] = (MemoryStatus.ACTIVE, MemoryStatus.PENDING),
        limit: int = 1,
    ) -> list[tuple[IndexedMemory, float]]:
        """Voisins les plus proches dans un espace, avec leur similarité cosinus."""
        rows = await self.c.fetch(
            f"""
            SELECT {_MEMORY_COLUMNS}, 1 - (m.embedding <=> $1::vector) AS similarity
            FROM memory_index m JOIN spaces s ON s.id = m.space_id
            WHERE m.space_id = $2
              AND m.status = ANY($3::text[])
              AND m.embedding IS NOT NULL
            ORDER BY m.embedding <=> $1::vector
            LIMIT $4
            """,
            to_literal(embedding),
            space_id,
            [status.value for status in statuses],
            limit,
        )
        return [(_to_indexed(row), float(row["similarity"])) for row in rows]

    async def search(
        self,
        *,
        query: str,
        embedding: Sequence[float] | None,
        space_ids: Sequence[UUID],
        statuses: Sequence[MemoryStatus],
        project: str | None = None,
        memory_type: str | None = None,
        limit: int = 8,
        rrf_k: int = 60,
        candidate_pool: int = 50,
    ) -> list[SearchHit]:
        """Recherche hybride : plein texte et vectoriel fusionnés par RRF."""
        rows = await self.c.fetch(
            f"""
            WITH filtered AS (
                SELECT m.*
                FROM memory_index m
                WHERE m.space_id = ANY($3::uuid[])
                  AND m.status = ANY($4::text[])
                  AND ($5::text IS NULL OR m.project = $5)
                  AND ($6::text IS NULL OR m.type = $6)
            ),
            text_hits AS (
                SELECT id, row_number() OVER (ORDER BY rank_score DESC, updated DESC) AS rank
                FROM (
                    SELECT f.id, f.updated, ts_rank_cd(f.tsv, q.query) AS rank_score
                    FROM filtered f, websearch_to_tsquery('simple', $1) AS q(query)
                    WHERE $1 <> '' AND f.tsv @@ q.query
                    ORDER BY rank_score DESC, f.updated DESC
                    LIMIT $7
                ) t
            ),
            vector_hits AS (
                SELECT id, row_number() OVER (ORDER BY distance ASC) AS rank
                FROM (
                    SELECT f.id, f.embedding <=> ($2::text)::vector AS distance
                    FROM filtered f
                    WHERE $2::text IS NOT NULL AND f.embedding IS NOT NULL
                    ORDER BY distance ASC
                    LIMIT $7
                ) v
            ),
            fused AS (
                SELECT
                    COALESCE(t.id, v.id) AS id,
                    t.rank AS rank_text,
                    v.rank AS rank_vector,
                    COALESCE(1.0 / ($8 + t.rank), 0) + COALESCE(1.0 / ($8 + v.rank), 0) AS score
                FROM text_hits t
                FULL OUTER JOIN vector_hits v ON t.id = v.id
            )
            SELECT {_MEMORY_COLUMNS}, fused.score, fused.rank_text, fused.rank_vector
            FROM fused
            JOIN memory_index m ON m.id = fused.id
            JOIN spaces s ON s.id = m.space_id
            ORDER BY fused.score DESC, m.updated DESC
            LIMIT $9
            """,
            query,
            to_literal(embedding) if embedding is not None else None,
            list(space_ids),
            [status.value for status in statuses],
            project,
            memory_type,
            candidate_pool,
            rrf_k,
            limit,
        )
        return [
            SearchHit(
                memory=_to_indexed(row),
                score=float(row["score"]),
                rank_text=row["rank_text"],
                rank_vector=row["rank_vector"],
            )
            for row in rows
        ]


class AgentRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(self, name: str, description: str | None = None) -> asyncpg.Record:
        return await self.c.fetchrow(
            """
            INSERT INTO agents (name, description) VALUES ($1, $2)
            ON CONFLICT (name) DO UPDATE SET description = COALESCE($2, agents.description)
            RETURNING id, name, description, created_at, disabled_at
            """,
            name,
            description,
        )

    async def get_by_name(self, name: str) -> asyncpg.Record | None:
        return await self.c.fetchrow(
            "SELECT id, name, description, created_at, disabled_at FROM agents WHERE name = $1",
            name,
        )

    async def list(self) -> list[asyncpg.Record]:
        return list(
            await self.c.fetch(
                "SELECT id, name, description, created_at, disabled_at FROM agents ORDER BY name"
            )
        )

    async def set_disabled(self, name: str, disabled: bool) -> None:
        await self.c.execute(
            "UPDATE agents SET disabled_at = CASE WHEN $2 THEN now() ELSE NULL END WHERE name = $1",
            name,
            disabled,
        )


class TokenRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(
        self,
        *,
        agent_id: UUID,
        prefix: str,
        token_hash: str,
        scopes: Sequence[str],
        space_ids: Sequence[UUID],
        expires_at: datetime | None = None,
        ip_allowlist: Sequence[str] = (),
    ) -> asyncpg.Record:
        return await self.c.fetchrow(
            """
            INSERT INTO tokens (agent_id, prefix, hash, scopes, space_ids, expires_at,
                                ip_allowlist)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, prefix, scopes, space_ids, expires_at, created_at
            """,
            agent_id,
            prefix,
            token_hash,
            list(scopes),
            list(space_ids),
            expires_at,
            list(ip_allowlist),
        )

    async def get_by_prefix(self, prefix: str) -> asyncpg.Record | None:
        return await self.c.fetchrow(
            """
            SELECT t.id, t.agent_id, t.prefix, t.hash, t.scopes, t.space_ids,
                   t.ip_allowlist, t.expires_at, t.revoked_at, a.name AS agent_name,
                   a.disabled_at AS agent_disabled_at
            FROM tokens t JOIN agents a ON a.id = t.agent_id
            WHERE t.prefix = $1
            """,
            prefix,
        )

    async def touch(self, token_id: UUID) -> None:
        await self.c.execute("UPDATE tokens SET last_used_at = now() WHERE id = $1", token_id)

    async def revoke(self, prefix: str) -> bool:
        result = await self.c.execute(
            "UPDATE tokens SET revoked_at = now() WHERE prefix = $1 AND revoked_at IS NULL",
            prefix,
        )
        return result.endswith("1")

    async def list(self, agent_name: str | None = None) -> list[asyncpg.Record]:
        return list(
            await self.c.fetch(
                """
                SELECT t.prefix, a.name AS agent_name, t.scopes, t.space_ids,
                       t.expires_at, t.last_used_at, t.revoked_at, t.created_at
                FROM tokens t JOIN agents a ON a.id = t.agent_id
                WHERE $1::text IS NULL OR a.name = $1
                ORDER BY t.created_at DESC
                """,
                agent_name,
            )
        )


class AuditRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def record(
        self,
        *,
        actor: str,
        action: str,
        target: str | None = None,
        space_id: UUID | None = None,
        ip: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        await self.c.execute(
            """
            INSERT INTO audit_log (actor, action, target, space_id, ip, meta)
            VALUES ($1, $2, $3, $4, $5::inet, $6::jsonb)
            """,
            actor,
            action,
            target,
            space_id,
            ip,
            json.dumps(meta or {}),
        )

    async def tail(self, limit: int = 50, actor: str | None = None) -> list[asyncpg.Record]:
        return list(
            await self.c.fetch(
                """
                SELECT id, actor, action, target, space_id, ip, meta, created_at
                FROM audit_log
                WHERE $2::text IS NULL OR actor = $2
                ORDER BY created_at DESC, id DESC
                LIMIT $1
                """,
                limit,
                actor,
            )
        )
