"""Access to the index tables.

Every query filters explicitly on the allowed spaces: isolation is never left
to the caller.
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


_SPACE_COLUMNS = """
    id, slug, path, description, remote_url, review_required, last_indexed_commit,
    last_synced_at, last_sync_error
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
            f"""
            INSERT INTO spaces (slug, path, description, remote_url, review_required)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (slug) DO UPDATE
                SET path = EXCLUDED.path,
                    description = COALESCE(EXCLUDED.description, spaces.description),
                    remote_url = COALESCE(EXCLUDED.remote_url, spaces.remote_url),
                    review_required = EXCLUDED.review_required
            RETURNING {_SPACE_COLUMNS}
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
            f"SELECT {_SPACE_COLUMNS} FROM spaces WHERE slug = $1",
            slug,
        )
        return Space(**dict(row)) if row else None

    async def list(self, space_ids: Sequence[UUID] | None = None) -> list[Space]:
        if space_ids is None:
            rows = await self.c.fetch(f"SELECT {_SPACE_COLUMNS} FROM spaces ORDER BY slug")
        else:
            rows = await self.c.fetch(
                f"SELECT {_SPACE_COLUMNS} FROM spaces WHERE id = ANY($1::uuid[]) ORDER BY slug",
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

    async def set_config(
        self, space_id: UUID, *, description: str | None, review_required: bool
    ) -> None:
        """Mirrors `.loremd/space.yaml`, which is the truth."""
        await self.c.execute(
            "UPDATE spaces SET description = $2, review_required = $3 WHERE id = $1",
            space_id,
            description,
            review_required,
        )

    async def record_sync(self, space_id: UUID, error: str | None) -> None:
        await self.c.execute(
            "UPDATE spaces SET last_synced_at = now(), last_sync_error = $2 WHERE id = $1",
            space_id,
            error,
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
        # A human can replace the file at a path with one carrying a new id.
        # (space_id, path) is unique, so the stale row has to go first, or the
        # insert below would hit a constraint instead of reindexing.
        await self.c.execute(
            "DELETE FROM memory_index WHERE space_id = $1 AND path = $2 AND id <> $3",
            space_id,
            memory.path,
            fm.id,
        )
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

    async def list_by_status(
        self, space_ids: Sequence[UUID], statuses: Sequence[MemoryStatus], *, limit: int = 200
    ) -> list[IndexedMemory]:
        """Oldest first: a review queue is worked from the front."""
        rows = await self.c.fetch(
            f"""
            SELECT {_MEMORY_COLUMNS}
            FROM memory_index m JOIN spaces s ON s.id = m.space_id
            WHERE m.space_id = ANY($1::uuid[]) AND m.status = ANY($2::text[])
            ORDER BY m.updated ASC, m.id
            LIMIT $3
            """,
            list(space_ids),
            [status.value for status in statuses],
            limit,
        )
        return [_to_indexed(row) for row in rows]

    async def count_by_status(self, space_ids: Sequence[UUID]) -> dict[tuple[UUID, str], int]:
        rows = await self.c.fetch(
            """
            SELECT space_id, status, count(*) AS n
            FROM memory_index WHERE space_id = ANY($1::uuid[])
            GROUP BY space_id, status
            """,
            list(space_ids),
        )
        return {(row["space_id"], row["status"]): int(row["n"]) for row in rows}

    async def nearest(
        self,
        embedding: Sequence[float],
        space_id: UUID,
        *,
        statuses: Sequence[MemoryStatus] = (MemoryStatus.ACTIVE, MemoryStatus.PENDING),
        limit: int = 1,
    ) -> list[tuple[IndexedMemory, float]]:
        """Nearest neighbours within a space, with their cosine similarity."""
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
        """Hybrid search: full-text and vector results merged with RRF."""
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
                SELECT id, distance, row_number() OVER (ORDER BY distance ASC) AS rank
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
                    1 - v.distance AS similarity,
                    COALESCE(1.0 / ($8 + t.rank), 0) + COALESCE(1.0 / ($8 + v.rank), 0) AS score
                FROM text_hits t
                FULL OUTER JOIN vector_hits v ON t.id = v.id
            )
            SELECT {_MEMORY_COLUMNS}, fused.score, fused.rank_text, fused.rank_vector,
                   fused.similarity
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
                similarity=float(row["similarity"]) if row["similarity"] is not None else None,
            )
            for row in rows
        ]


_AGENT_COLUMNS = "id, name, description, review_required, created_at, disabled_at"


class AgentRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(
        self, name: str, description: str | None = None, *, review_required: bool | None = None
    ) -> asyncpg.Record:
        return await self.c.fetchrow(
            f"""
            INSERT INTO agents (name, description, review_required)
            VALUES ($1, $2, COALESCE($3, false))
            ON CONFLICT (name) DO UPDATE SET
                description = COALESCE($2, agents.description),
                review_required = COALESCE($3, agents.review_required)
            RETURNING {_AGENT_COLUMNS}
            """,
            name,
            description,
            review_required,
        )

    async def get_by_name(self, name: str) -> asyncpg.Record | None:
        return await self.c.fetchrow(
            f"SELECT {_AGENT_COLUMNS} FROM agents WHERE name = $1", name
        )

    async def list(self) -> list[asyncpg.Record]:
        return list(await self.c.fetch(f"SELECT {_AGENT_COLUMNS} FROM agents ORDER BY name"))

    async def set_review_required(self, name: str, review_required: bool) -> None:
        await self.c.execute(
            "UPDATE agents SET review_required = $2 WHERE name = $1", name, review_required
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
                   a.disabled_at AS agent_disabled_at,
                   a.review_required AS agent_review_required
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

    async def tail(
        self, limit: int = 50, actor: str | None = None, action: str | None = None
    ) -> list[asyncpg.Record]:
        return list(
            await self.c.fetch(
                """
                SELECT a.id, a.actor, a.action, a.target, a.space_id, s.slug AS space,
                       a.ip, a.meta, a.created_at
                FROM audit_log a LEFT JOIN spaces s ON s.id = a.space_id
                WHERE ($2::text IS NULL OR a.actor = $2)
                  AND ($3::text IS NULL OR a.action = $3)
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT $1
                """,
                limit,
                actor,
                action,
            )
        )


_ADMIN_COLUMNS = """
    id, email, password_hash, totp_secret, totp_last_step, role, created_at,
    last_login_at, disabled_at
"""


class AdminRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(
        self, email: str, password_hash: str, totp_secret: str, role: str
    ) -> asyncpg.Record:
        return await self.c.fetchrow(
            f"""
            INSERT INTO admins (email, password_hash, totp_secret, role)
            VALUES ($1, $2, $3, $4)
            RETURNING {_ADMIN_COLUMNS}
            """,
            email,
            password_hash,
            totp_secret,
            role,
        )

    async def get_by_email(self, email: str) -> asyncpg.Record | None:
        return await self.c.fetchrow(
            f"SELECT {_ADMIN_COLUMNS} FROM admins WHERE email = $1", email
        )

    async def list(self) -> list[asyncpg.Record]:
        return list(await self.c.fetch(f"SELECT {_ADMIN_COLUMNS} FROM admins ORDER BY email"))

    async def accept_totp_step(self, admin_id: UUID, step: int) -> bool:
        """Records a used TOTP step. False if it, or a later one, was already used."""
        result = await self.c.execute(
            """
            UPDATE admins SET totp_last_step = $2, last_login_at = now()
            WHERE id = $1 AND (totp_last_step IS NULL OR totp_last_step < $2)
            """,
            admin_id,
            step,
        )
        return result.endswith("1")

    async def set_password(self, email: str, password_hash: str) -> bool:
        result = await self.c.execute(
            "UPDATE admins SET password_hash = $2 WHERE email = $1", email, password_hash
        )
        return result.endswith("1")

    async def set_totp_secret(self, email: str, totp_secret: str) -> bool:
        result = await self.c.execute(
            "UPDATE admins SET totp_secret = $2, totp_last_step = NULL WHERE email = $1",
            email,
            totp_secret,
        )
        return result.endswith("1")

    async def set_disabled(self, email: str, disabled: bool) -> bool:
        result = await self.c.execute(
            "UPDATE admins SET disabled_at = CASE WHEN $2 THEN now() ELSE NULL END "
            "WHERE email = $1",
            email,
            disabled,
        )
        if disabled:
            await self.c.execute(
                "DELETE FROM admin_sessions WHERE admin_id = "
                "(SELECT id FROM admins WHERE email = $1)",
                email,
            )
        return result.endswith("1")


class AdminSessionRepository:
    def __init__(self, connection: asyncpg.Connection) -> None:
        self.c = connection

    async def create(
        self,
        session_hash: str,
        admin_id: UUID,
        csrf_token: str,
        *,
        ip: str | None,
        expires_at: datetime,
    ) -> None:
        await self.c.execute(
            """
            INSERT INTO admin_sessions (id, admin_id, csrf_token, ip, expires_at)
            VALUES ($1, $2, $3, $4::inet, $5)
            """,
            session_hash,
            admin_id,
            csrf_token,
            ip,
            expires_at,
        )

    async def get(self, session_hash: str) -> asyncpg.Record | None:
        """A live session and its admin; expired sessions and disabled admins yield None."""
        return await self.c.fetchrow(
            """
            SELECT s.id, s.csrf_token, s.expires_at, a.id AS admin_id, a.email, a.role
            FROM admin_sessions s JOIN admins a ON a.id = s.admin_id
            WHERE s.id = $1 AND s.expires_at > now() AND a.disabled_at IS NULL
            """,
            session_hash,
        )

    async def delete(self, session_hash: str) -> None:
        await self.c.execute("DELETE FROM admin_sessions WHERE id = $1", session_hash)

    async def purge_expired(self) -> None:
        await self.c.execute("DELETE FROM admin_sessions WHERE expires_at <= now()")
