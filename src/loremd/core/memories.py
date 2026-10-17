"""Écriture et lecture des mémoires.

Une écriture suit toujours le même chemin : validation, détection de secrets,
déduplication, verrou d'espace, fichier, commit, index, audit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ..auth.principal import Principal, Scope
from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..embeddings import EmbeddingProvider
from ..errors import NotFound, SecretDetected, ValidationError
from ..ids import memory_filename, new_ulid
from . import frontmatter, secrets
from .audit import AuditService
from .git import Commit
from .indexer import Indexer
from .locks import space_lock
from .models import Frontmatter, IndexedMemory, Memory, MemoryStatus, MemoryType
from .spaces import ResolvedSpace, SpaceService

logger = logging.getLogger(__name__)

_EMAIL_UNSAFE = re.compile(r"[^a-z0-9._-]+")
TITLE_MAX_LENGTH = 120


@dataclass
class WriteResult:
    """Résultat d'une écriture : créée, ou doublon renvoyé tel quel."""

    memory: Memory
    created: bool
    commit: str | None = None
    duplicate_of: str | None = None
    similarity: float | None = None
    pending: bool = False


def derive_title(content: str) -> str:
    """Titre déduit de la première phrase quand l'agent n'en fournit pas."""
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    first_line = first_line.lstrip("# ").strip()
    if not first_line:
        raise ValidationError("content est vide")
    sentence = re.split(r"(?<=[.!?])\s", first_line)[0]
    title = sentence if len(sentence) <= TITLE_MAX_LENGTH else sentence[:TITLE_MAX_LENGTH].rstrip()
    return title


def author_email(agent_name: str, domain: str) -> str:
    local = _EMAIL_UNSAFE.sub("-", agent_name.lower()).strip("-") or "agent"
    return f"{local}@{domain}"


class MemoryService:
    """Opérations de mémoire exposées par MCP et par la CLI."""

    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        indexer: Indexer,
        embeddings: EmbeddingProvider,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._indexer = indexer
        self._embeddings = embeddings
        self._audit = audit
        self._settings = settings

    # -- validation ----------------------------------------------------------

    def _validate_content(self, content: str) -> str:
        body = content.strip()
        if not body:
            raise ValidationError("content est vide")
        size = len(body.encode("utf-8"))
        if size > self._settings.write.max_bytes:
            raise ValidationError(
                f"mémoire trop longue : {size} octets pour un maximum de "
                f"{self._settings.write.max_bytes}"
            )
        if self._settings.write.detect_secrets:
            findings = secrets.scan(body)
            if findings:
                raise SecretDetected(
                    "secret probable détecté, écriture refusée : "
                    + ", ".join(str(finding) for finding in findings)
                )
        return body

    # -- lecture -------------------------------------------------------------

    async def get(self, principal: Principal, memory_id: str) -> IndexedMemory:
        principal.require_scope(Scope.READ)
        spaces = await self._spaces.list(principal)
        if not spaces:
            raise NotFound(f"mémoire introuvable : {memory_id}")
        async with self._db.acquire() as connection:
            memory = await MemoryIndexRepository(connection).get(
                memory_id, [space.id for space in spaces]
            )
        if memory is None:
            raise NotFound(f"mémoire introuvable : {memory_id}")
        await self._audit.record(
            principal, "memory_get", target=memory_id, space_id=memory.space_id
        )
        return memory

    async def history(self, principal: Principal, memory_id: str) -> list[Commit]:
        indexed = await self.get(principal, memory_id)
        resolved = await self._spaces.resolve(indexed.space, principal)
        commits = resolved.repo.history(indexed.path)
        await self._audit.record(
            principal, "memory_history", target=memory_id, space_id=indexed.space_id
        )
        return commits

    async def _load_file(self, resolved: ResolvedSpace, indexed: IndexedMemory) -> Memory:
        file_path = resolved.path / indexed.path
        if not file_path.exists():
            raise NotFound(
                f"fichier absent pour {indexed.id} : {indexed.path} — lancer `loremd reindex`"
            )
        return frontmatter.read_file(
            file_path, space=resolved.slug, relative_path=indexed.path
        )

    # -- écriture ------------------------------------------------------------

    async def write(
        self,
        principal: Principal,
        *,
        content: str,
        memory_type: MemoryType,
        space_slug: str,
        title: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        source: str | None = None,
    ) -> WriteResult:
        principal.require_scope(Scope.WRITE)
        resolved = await self._spaces.resolve(space_slug, principal)
        body = self._validate_content(content)
        final_title = (title or derive_title(body)).strip()

        now = datetime.now(UTC)
        memory_id = new_ulid(int(now.timestamp() * 1000))
        relative_path = f"{memory_type.directory}/{memory_filename(final_title, memory_id)}"

        candidate = Memory(
            frontmatter=Frontmatter(
                id=memory_id,
                type=memory_type,
                title=final_title,
                project=project,
                tags=tags or [],
                status=MemoryStatus.PENDING
                if resolved.space.review_required
                else MemoryStatus.ACTIVE,
                verified=False,
                source=source,
                author=principal.name,
                created=now,
                updated=now,
            ),
            content=body,
            space=resolved.slug,
            path=relative_path,
        )

        embedding = await self._embeddings.embed_one(candidate.embedding_text())

        duplicate = await self._find_duplicate(resolved, embedding)
        if duplicate is not None:
            existing, similarity = duplicate
            await self._audit.record(
                principal,
                "memory_write_duplicate",
                target=existing.id,
                space_id=resolved.space.id,
                meta={"similarity": round(similarity, 4)},
            )
            return WriteResult(
                memory=Memory(
                    frontmatter=Frontmatter(
                        id=existing.id,
                        type=existing.type,
                        title=existing.title,
                        project=existing.project,
                        tags=existing.tags,
                        status=existing.status,
                        verified=existing.verified,
                        source=existing.source,
                        author=existing.author,
                        created=existing.created,
                        updated=existing.updated,
                    ),
                    content=existing.content,
                    space=existing.space,
                    path=existing.path,
                ),
                created=False,
                duplicate_of=existing.id,
                similarity=similarity,
            )

        async with space_lock(resolved.slug, resolved.path):
            frontmatter.write_file(resolved.path / relative_path, candidate)
            commit = resolved.repo.commit(
                [relative_path],
                f"{memory_type.value}: {final_title}",
                author_name=principal.name,
                author_email=author_email(
                    principal.name, self._settings.git.author_email_domain
                ),
            )
            await self._indexer.index_memory(resolved.space, candidate)
            await self._spaces.set_last_indexed_commit(resolved.space, commit)

        await self._audit.record(
            principal,
            "memory_write",
            target=memory_id,
            space_id=resolved.space.id,
            meta={"path": relative_path, "commit": commit, "type": memory_type.value},
        )
        self._maybe_push(resolved)
        return WriteResult(
            memory=candidate,
            created=True,
            commit=commit,
            pending=candidate.frontmatter.status is MemoryStatus.PENDING,
        )

    async def _find_duplicate(
        self, resolved: ResolvedSpace, embedding: list[float]
    ) -> tuple[IndexedMemory, float] | None:
        threshold = self._settings.write.dedup_threshold
        if threshold >= 1.0:
            return None
        async with self._db.acquire() as connection:
            neighbours = await MemoryIndexRepository(connection).nearest(
                embedding, resolved.space.id, limit=1
            )
        if neighbours and neighbours[0][1] >= threshold:
            return neighbours[0]
        return None

    async def update(
        self, principal: Principal, memory_id: str, content: str, reason: str
    ) -> WriteResult:
        """Modifie une mémoire existante. Le motif devient le message de commit."""
        principal.require_scope(Scope.WRITE)
        if not reason.strip():
            raise ValidationError("reason est obligatoire : il devient le message du commit")
        indexed = await self.get(principal, memory_id)
        resolved = await self._spaces.resolve(indexed.space, principal)
        body = self._validate_content(content)

        async with space_lock(resolved.slug, resolved.path):
            memory = await self._load_file(resolved, indexed)
            memory.content = body
            memory.frontmatter.updated = datetime.now(UTC)
            frontmatter.write_file(resolved.path / memory.path, memory)
            commit = resolved.repo.commit(
                [memory.path],
                f"update: {memory.frontmatter.title} — {reason.strip()}",
                author_name=principal.name,
                author_email=author_email(
                    principal.name, self._settings.git.author_email_domain
                ),
            )
            await self._indexer.index_memory(resolved.space, memory)
            await self._spaces.set_last_indexed_commit(resolved.space, commit)

        await self._audit.record(
            principal,
            "memory_update",
            target=memory_id,
            space_id=resolved.space.id,
            meta={"reason": reason.strip(), "commit": commit},
        )
        self._maybe_push(resolved)
        return WriteResult(memory=memory, created=False, commit=commit)

    async def supersede(
        self, principal: Principal, memory_id: str, new_content: str, reason: str
    ) -> WriteResult:
        """Remplace une mémoire obsolète : l'ancienne passe en `superseded`."""
        principal.require_scope(Scope.WRITE)
        if not reason.strip():
            raise ValidationError("reason est obligatoire : il devient le message du commit")
        indexed = await self.get(principal, memory_id)
        resolved = await self._spaces.resolve(indexed.space, principal)
        body = self._validate_content(new_content)

        now = datetime.now(UTC)
        new_id = new_ulid(int(now.timestamp() * 1000))
        title = indexed.title
        new_path = f"{indexed.type.directory}/{memory_filename(title, new_id)}"

        async with space_lock(resolved.slug, resolved.path):
            old = await self._load_file(resolved, indexed)
            old.frontmatter.status = MemoryStatus.SUPERSEDED
            old.frontmatter.updated = now
            frontmatter.write_file(resolved.path / old.path, old)

            replacement = Memory(
                frontmatter=Frontmatter(
                    id=new_id,
                    type=indexed.type,
                    title=title,
                    project=indexed.project,
                    tags=indexed.tags,
                    status=MemoryStatus.PENDING
                    if resolved.space.review_required
                    else MemoryStatus.ACTIVE,
                    verified=False,
                    source=f"supersede de {memory_id}",
                    author=principal.name,
                    created=now,
                    updated=now,
                    supersedes=memory_id,
                ),
                content=body,
                space=resolved.slug,
                path=new_path,
            )
            frontmatter.write_file(resolved.path / new_path, replacement)

            commit = resolved.repo.commit(
                [old.path, new_path],
                f"supersede: {title} — {reason.strip()}",
                author_name=principal.name,
                author_email=author_email(
                    principal.name, self._settings.git.author_email_domain
                ),
            )
            await self._indexer.index_memories(resolved.space, [old, replacement])
            await self._spaces.set_last_indexed_commit(resolved.space, commit)

        await self._audit.record(
            principal,
            "memory_supersede",
            target=new_id,
            space_id=resolved.space.id,
            meta={"supersedes": memory_id, "reason": reason.strip(), "commit": commit},
        )
        self._maybe_push(resolved)
        return WriteResult(
            memory=replacement,
            created=True,
            commit=commit,
            pending=replacement.frontmatter.status is MemoryStatus.PENDING,
        )

    # -- remote --------------------------------------------------------------

    def _maybe_push(self, resolved: ResolvedSpace) -> None:
        """Push best-effort vers le remote (la synchro robuste arrive en phase 2)."""
        if not self._settings.git.auto_push or not resolved.repo.has_remote():
            return
        try:
            resolved.repo.push()
        except Exception:  # noqa: BLE001 - le remote ne doit pas bloquer l'écriture
            logger.warning("push vers le remote impossible pour %s", resolved.slug, exc_info=True)
