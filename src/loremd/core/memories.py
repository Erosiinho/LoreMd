"""Writing and reading memories.

A write always follows the same path: validation, secret detection,
deduplication, space lock, file, commit, index, audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ..auth.principal import Principal, Scope
from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..embeddings import EmbeddingProvider
from ..errors import ConflictError, NotFound, SecretDetected, ValidationError
from ..ids import memory_filename, new_ulid
from . import frontmatter, secrets
from .audit import AuditService
from .git import Commit
from .indexer import Indexer
from .locks import space_lock
from .models import (
    SEARCHABLE_TYPES,
    Frontmatter,
    IndexedMemory,
    Memory,
    MemoryStatus,
    MemoryType,
)
from .spaces import ResolvedSpace, SpaceService
from .sync import SyncService

_EMAIL_UNSAFE = re.compile(r"[^a-z0-9._-]+")
TITLE_MAX_LENGTH = 120


@dataclass
class WriteResult:
    """Result of a write: created, or an existing duplicate returned as is."""

    memory: Memory
    created: bool
    commit: str | None = None
    duplicate_of: str | None = None
    similarity: float | None = None
    pending: bool = False


def derive_title(content: str) -> str:
    """Title derived from the first sentence when the agent does not provide one."""
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    first_line = first_line.lstrip("# ").strip()
    if not first_line:
        raise ValidationError("content is empty")
    sentence = re.split(r"(?<=[.!?])\s", first_line)[0]
    title = sentence if len(sentence) <= TITLE_MAX_LENGTH else sentence[:TITLE_MAX_LENGTH].rstrip()
    return title


def author_email(agent_name: str, domain: str) -> str:
    local = _EMAIL_UNSAFE.sub("-", agent_name.lower()).strip("-") or "agent"
    return f"{local}@{domain}"


def commit_identity(principal: Principal, domain: str) -> dict[str, str]:
    """Who signs the commit: an admin with their own email, an agent with a derived one."""
    return {
        "author_name": principal.name,
        "author_email": principal.email or author_email(principal.name, domain),
    }


def validate_content(content: str, *, max_bytes: int, detect_secrets: bool = True) -> str:
    """Checks a memory body and returns it stripped.

    Three rules, in order: not empty, not oversized, no secret. A secret stops
    the write before anything touches the disk, so nothing is ever committed.
    """
    body = content.strip()
    if not body:
        raise ValidationError("content is empty")
    size = len(body.encode("utf-8"))
    if size > max_bytes:
        raise ValidationError(f"memory too long: {size} bytes, maximum is {max_bytes}")
    if detect_secrets:
        findings = secrets.scan(body)
        if findings:
            raise SecretDetected(
                "likely secret detected, write rejected: "
                + ", ".join(str(finding) for finding in findings)
            )
    return body


class MemoryService:
    """Memory operations exposed through MCP and the CLI."""

    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        indexer: Indexer,
        embeddings: EmbeddingProvider,
        audit: AuditService,
        settings: Settings,
        sync: SyncService | None = None,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._indexer = indexer
        self._embeddings = embeddings
        self._audit = audit
        self._settings = settings
        self._sync = sync

    def identity(self, principal: Principal) -> dict[str, str]:
        return commit_identity(principal, self._settings.git.author_email_domain)

    @staticmethod
    def review_required(resolved: ResolvedSpace, principal: Principal) -> bool:
        """Review applies per space or per agent: either one is enough."""
        return resolved.space.review_required or principal.review_required

    def committed(self, resolved: ResolvedSpace) -> None:
        """A commit landed in the space: push it in the background."""
        if self._sync is not None:
            self._sync.request_push(resolved.space)

    # -- validation ----------------------------------------------------------

    def validate_body(self, content: str) -> str:
        return validate_content(
            content,
            max_bytes=self._settings.write.max_bytes,
            detect_secrets=self._settings.write.detect_secrets,
        )

    # -- reading -------------------------------------------------------------

    async def lookup(self, principal: Principal, memory_id: str) -> IndexedMemory:
        """Finds a memory among the token's spaces, without auditing a read.

        A memory outside the token's spaces is reported as missing rather than
        forbidden: a token must not be able to probe what exists elsewhere.
        """
        spaces = await self._spaces.list(principal)
        if not spaces:
            raise NotFound(f"memory not found: {memory_id}")
        async with self._db.acquire() as connection:
            memory = await MemoryIndexRepository(connection).get(
                memory_id, [space.id for space in spaces]
            )
        if memory is None:
            raise NotFound(f"memory not found: {memory_id}")
        return memory

    async def get(self, principal: Principal, memory_id: str) -> IndexedMemory:
        principal.require_scope(Scope.READ)
        memory = await self.lookup(principal, memory_id)
        await self._audit.record(
            principal, "memory_get", target=memory_id, space_id=memory.space_id
        )
        return memory

    async def history(self, principal: Principal, memory_id: str) -> list[Commit]:
        principal.require_scope(Scope.READ)
        indexed = await self.lookup(principal, memory_id)
        resolved = await self._spaces.resolve(indexed.space, principal)
        commits = resolved.repo.history(indexed.path)
        await self._audit.record(
            principal, "memory_history", target=memory_id, space_id=indexed.space_id
        )
        return commits

    async def load_file(self, resolved: ResolvedSpace, indexed: IndexedMemory) -> Memory:
        file_path = resolved.path / indexed.path
        if not file_path.exists():
            raise NotFound(
                f"file missing for {indexed.id}: {indexed.path} — run `loremd reindex`"
            )
        return frontmatter.read_file(
            file_path, space=resolved.slug, relative_path=indexed.path
        )

    # -- writing -------------------------------------------------------------

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
        sources: list[str] | None = None,
        dedup: bool = True,
    ) -> WriteResult:
        """Creates a memory. `dedup=False` skips the near-duplicate check."""
        principal.require_scope(Scope.WRITE)
        resolved = await self._spaces.resolve(space_slug, principal)
        body = self.validate_body(content)
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
                if self.review_required(resolved, principal)
                else MemoryStatus.ACTIVE,
                verified=False,
                source=source,
                author=principal.name,
                created=now,
                updated=now,
                sources=sources,
            ),
            content=body,
            space=resolved.slug,
            path=relative_path,
        )

        duplicate = None
        if dedup:
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
                [relative_path], f"{memory_type.value}: {final_title}", **self.identity(principal)
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
        self.committed(resolved)
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
                embedding,
                resolved.space.id,
                memory_types=[t.value for t in SEARCHABLE_TYPES],
                limit=1,
            )
        if neighbours and neighbours[0][1] >= threshold:
            return neighbours[0]
        return None

    async def update(
        self, principal: Principal, memory_id: str, content: str, reason: str
    ) -> WriteResult:
        """Changes an existing memory. The reason becomes the commit message."""
        principal.require_scope(Scope.WRITE)
        if not reason.strip():
            raise ValidationError("reason is required: it becomes the commit message")
        indexed = await self.lookup(principal, memory_id)
        _refuse_conflict(indexed)
        resolved = await self._spaces.resolve(indexed.space, principal)
        body = self.validate_body(content)
        review = self.review_required(resolved, principal)

        async with space_lock(resolved.slug, resolved.path):
            memory = await self.load_file(resolved, indexed)
            memory.content = body
            memory.frontmatter.updated = datetime.now(UTC)
            # What a human verified is the old text: the new one is a claim again.
            memory.frontmatter.verified = False
            if review:
                memory.frontmatter.status = MemoryStatus.PENDING
            frontmatter.write_file(resolved.path / memory.path, memory)
            commit = resolved.repo.commit(
                [memory.path],
                f"update: {memory.frontmatter.title} — {reason.strip()}",
                **self.identity(principal),
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
        self.committed(resolved)
        return WriteResult(memory=memory, created=False, commit=commit, pending=review)

    async def supersede(
        self,
        principal: Principal,
        memory_id: str,
        new_content: str,
        reason: str,
        *,
        title: str | None = None,
        project: str | None = None,
        sources: list[str] | None = None,
    ) -> WriteResult:
        """Replaces an outdated memory: the old one becomes `superseded`.

        The replacement keeps the old title, project and sources unless new
        ones are given — a handoff carries a fresh title every session.
        """
        principal.require_scope(Scope.WRITE)
        if not reason.strip():
            raise ValidationError("reason is required: it becomes the commit message")
        indexed = await self.lookup(principal, memory_id)
        _refuse_conflict(indexed)
        resolved = await self._spaces.resolve(indexed.space, principal)
        body = self.validate_body(new_content)
        review = self.review_required(resolved, principal)

        now = datetime.now(UTC)
        new_id = new_ulid(int(now.timestamp() * 1000))
        title = (title or indexed.title).strip()
        new_path = f"{indexed.type.directory}/{memory_filename(title, new_id)}"

        async with space_lock(resolved.slug, resolved.path):
            old = await self.load_file(resolved, indexed)
            changed = [new_path]
            if not review:
                # Under review the old memory stays active until the
                # replacement is approved: searches never come back empty-handed
                # in between.
                old.frontmatter.status = MemoryStatus.SUPERSEDED
                old.frontmatter.updated = now
                frontmatter.write_file(resolved.path / old.path, old)
                changed.append(old.path)

            replacement = Memory(
                frontmatter=Frontmatter(
                    id=new_id,
                    type=indexed.type,
                    title=title,
                    project=project if project is not None else indexed.project,
                    tags=indexed.tags,
                    status=MemoryStatus.PENDING if review else MemoryStatus.ACTIVE,
                    verified=False,
                    source=f"supersedes {memory_id}",
                    author=principal.name,
                    created=now,
                    updated=now,
                    supersedes=memory_id,
                    sources=sources if sources is not None else (indexed.sources or None),
                ),
                content=body,
                space=resolved.slug,
                path=new_path,
            )
            frontmatter.write_file(resolved.path / new_path, replacement)

            commit = resolved.repo.commit(
                changed,
                f"supersede: {title} — {reason.strip()}",
                **self.identity(principal),
            )
            await self._indexer.index_memories(
                resolved.space, [replacement] if review else [old, replacement]
            )
            await self._spaces.set_last_indexed_commit(resolved.space, commit)

        await self._audit.record(
            principal,
            "memory_supersede",
            target=new_id,
            space_id=resolved.space.id,
            meta={"supersedes": memory_id, "reason": reason.strip(), "commit": commit},
        )
        self.committed(resolved)
        return WriteResult(
            memory=replacement,
            created=True,
            commit=commit,
            pending=review,
        )


def _refuse_conflict(memory: IndexedMemory) -> None:
    """Two versions of this memory wait for a human: an agent must not pick one."""
    if memory.status is MemoryStatus.CONFLICT:
        raise ConflictError(
            f"memory {memory.id} is in conflict between two versions; a human has to "
            "resolve it in the admin console before it can be changed"
        )
