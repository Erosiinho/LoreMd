"""Human governance: the review queue, the `verified` flag, conflict resolution.

The console and the CLI both go through here. Every decision is a commit
signed by the human who made it, like any other change to a space.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ..auth.principal import Principal, Scope
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..errors import ConflictError, NotFound, ValidationError
from . import frontmatter
from .audit import AuditService
from .indexer import Indexer
from .locks import space_lock
from .memories import MemoryService
from .models import IndexedMemory, Memory, MemoryStatus
from .spaces import ResolvedSpace, SpaceService
from .sync import CONFLICT_COPY, CONFLICT_OF

#: Which version a conflict resolution keeps.
Keep = Literal["memory", "copy", "discard"]


@dataclass
class ConflictView:
    """A memory in conflict and, when both versions were kept, the other one.

    `memory` holds the path humans see (the remote's version); `copy` is the
    server's version, kept alongside it.
    """

    memory: Memory
    copy: Memory | None
    space: str

    @property
    def id(self) -> str:
        return self.memory.id


class ReviewService:
    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        indexer: Indexer,
        memories: MemoryService,
        audit: AuditService,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._indexer = indexer
        self._memories = memories
        self._audit = audit

    # -- reading -------------------------------------------------------------

    async def queue(
        self,
        principal: Principal,
        *,
        space_slug: str | None = None,
        status: MemoryStatus = MemoryStatus.PENDING,
        limit: int = 200,
    ) -> list[IndexedMemory]:
        """Memories in a given status, oldest first."""
        principal.require_scope(Scope.ADMIN)
        if space_slug:
            space_ids = [(await self._spaces.resolve(space_slug, principal)).space.id]
        else:
            space_ids = [space.id for space in await self._spaces.list(principal)]
        if not space_ids:
            return []
        async with self._db.acquire() as connection:
            return await MemoryIndexRepository(connection).list_by_status(
                space_ids, [status], limit=limit
            )

    async def counts(self, principal: Principal) -> dict[str, dict[str, int]]:
        """Per space, the number of memories in each status."""
        principal.require_scope(Scope.ADMIN)
        spaces = await self._spaces.list(principal)
        async with self._db.acquire() as connection:
            raw = await MemoryIndexRepository(connection).count_by_status(
                [space.id for space in spaces]
            )
        return {
            space.slug: {
                status.value: raw.get((space.id, status.value), 0) for status in MemoryStatus
            }
            for space in spaces
        }

    async def conflicts(
        self, principal: Principal, *, space_slug: str | None = None
    ) -> list[ConflictView]:
        """Conflicts, each pair folded into a single entry."""
        indexed = await self.queue(principal, space_slug=space_slug, status=MemoryStatus.CONFLICT)
        loaded: dict[str, tuple[Memory, str]] = {}
        for entry in indexed:
            resolved = await self._spaces.resolve(entry.space, principal)
            try:
                loaded[entry.id] = (await self._memories.load_file(resolved, entry), entry.space)
            except NotFound:
                continue

        views: list[ConflictView] = []
        paired: set[str] = set()
        for memory, space in loaded.values():
            copy_id = memory.frontmatter.extra(CONFLICT_COPY)
            if copy_id and copy_id in loaded:
                views.append(ConflictView(memory=memory, copy=loaded[copy_id][0], space=space))
                paired.update({memory.id, copy_id})
        for memory, space in loaded.values():
            if memory.id not in paired:
                views.append(ConflictView(memory=memory, copy=None, space=space))
        return views

    async def conflict(self, principal: Principal, memory_id: str) -> ConflictView:
        """The conflict a memory belongs to, whichever of the two versions it is."""
        for view in await self.conflicts(principal):
            if memory_id in {view.memory.id, view.copy.id if view.copy else None}:
                return view
        raise NotFound(f"no conflict for memory {memory_id}")

    # -- decisions -----------------------------------------------------------

    async def approve(self, principal: Principal, memory_id: str) -> Memory:
        """Publishes a pending memory, verified. A replacement retires what it supersedes."""
        principal.require_scope(Scope.ADMIN)
        indexed = await self._memories.lookup(principal, memory_id)
        if indexed.status is not MemoryStatus.PENDING:
            raise ConflictError(f"memory {memory_id} is {indexed.status.value}, not pending")
        resolved = await self._spaces.resolve(indexed.space, principal)
        now = datetime.now(UTC)

        async with space_lock(resolved.slug, resolved.path):
            memory = await self._memories.load_file(resolved, indexed)
            memory.frontmatter.status = MemoryStatus.ACTIVE
            memory.frontmatter.verified = True
            memory.frontmatter.updated = now
            changed = [memory]

            retired = await self._superseded_target(resolved, principal, memory)
            if retired is not None:
                retired.frontmatter.status = MemoryStatus.SUPERSEDED
                retired.frontmatter.updated = now
                changed.append(retired)

            commit = await self._commit(
                resolved, principal, changed, f"review: approve {memory.frontmatter.title}"
            )

        await self._audit.record(
            principal,
            "memory_approve",
            target=memory_id,
            space_id=resolved.space.id,
            meta={"commit": commit, "supersedes": retired.id if retired else None},
        )
        self._memories.committed(resolved)
        return memory

    async def reject(self, principal: Principal, memory_id: str, reason: str = "") -> Memory:
        """Turns a pending memory down. The file stays, `rejected`, for the record."""
        principal.require_scope(Scope.ADMIN)
        indexed = await self._memories.lookup(principal, memory_id)
        if indexed.status is not MemoryStatus.PENDING:
            raise ConflictError(f"memory {memory_id} is {indexed.status.value}, not pending")
        resolved = await self._spaces.resolve(indexed.space, principal)

        async with space_lock(resolved.slug, resolved.path):
            memory = await self._memories.load_file(resolved, indexed)
            memory.frontmatter.status = MemoryStatus.REJECTED
            memory.frontmatter.updated = datetime.now(UTC)
            message = f"review: reject {memory.frontmatter.title}"
            if reason.strip():
                message += f" — {reason.strip()}"
            commit = await self._commit(resolved, principal, [memory], message)

        await self._audit.record(
            principal,
            "memory_reject",
            target=memory_id,
            space_id=resolved.space.id,
            meta={"commit": commit, "reason": reason.strip() or None},
        )
        self._memories.committed(resolved)
        return memory

    async def set_verified(self, principal: Principal, memory_id: str, verified: bool) -> Memory:
        """A human vouches for a memory, or takes it back."""
        principal.require_scope(Scope.ADMIN)
        indexed = await self._memories.lookup(principal, memory_id)
        resolved = await self._spaces.resolve(indexed.space, principal)

        async with space_lock(resolved.slug, resolved.path):
            memory = await self._memories.load_file(resolved, indexed)
            if memory.frontmatter.verified == verified:
                return memory
            memory.frontmatter.verified = verified
            memory.frontmatter.updated = datetime.now(UTC)
            verb = "verify" if verified else "unverify"
            commit = await self._commit(
                resolved, principal, [memory], f"review: {verb} {memory.frontmatter.title}"
            )

        await self._audit.record(
            principal,
            "memory_verify" if verified else "memory_unverify",
            target=memory_id,
            space_id=resolved.space.id,
            meta={"commit": commit},
        )
        self._memories.committed(resolved)
        return memory

    async def resolve(
        self,
        principal: Principal,
        memory_id: str,
        keep: Keep,
        *,
        content: str | None = None,
    ) -> Memory | None:
        """Ends a conflict.

        `keep="memory"` keeps the version at the path, `keep="copy"` the other
        one; `content` replaces the body with a hand-merged text. Either way a
        single active, verified memory remains, at the original path and id.
        `keep="discard"` removes both. Git still has every version.
        """
        principal.require_scope(Scope.ADMIN)
        view = await self.conflict(principal, memory_id)
        if keep == "copy" and view.copy is None:
            raise ValidationError("this conflict has a single version: keep it or discard it")
        body = (
            self._memories.validate_body(content) if content and content.strip() else None
        )
        resolved = await self._spaces.resolve(view.space, principal)

        async with space_lock(resolved.slug, resolved.path):
            removed = [view.copy.path] if view.copy is not None else []
            if keep == "discard":
                removed.append(view.memory.path)
                kept: Memory | None = None
                title = view.memory.frontmatter.title
            else:
                kept = view.memory.model_copy(deep=True)
                if keep == "copy" and view.copy is not None:
                    chosen = view.copy.frontmatter
                    kept.content = view.copy.content
                    kept.frontmatter.title = chosen.title
                    kept.frontmatter.project = chosen.project
                    kept.frontmatter.tags = list(chosen.tags)
                    kept.frontmatter.author = chosen.author
                    kept.frontmatter.source = chosen.source
                if body is not None:
                    kept.content = body
                kept.frontmatter.status = MemoryStatus.ACTIVE
                kept.frontmatter.verified = True
                kept.frontmatter.updated = datetime.now(UTC)
                kept.frontmatter.drop_extra(CONFLICT_COPY)
                kept.frontmatter.drop_extra(CONFLICT_OF)
                title = kept.frontmatter.title

            for path in removed:
                (resolved.path / path).unlink(missing_ok=True)
            label = "edited" if body is not None else keep
            commit = await self._commit(
                resolved,
                principal,
                [kept] if kept else [],
                f"conflict: resolve {title} — {label}",
                removed=removed,
            )

        await self._audit.record(
            principal,
            "memory_resolve_conflict",
            target=view.memory.id,
            space_id=resolved.space.id,
            meta={
                "keep": keep,
                "edited": body is not None,
                "copy": view.copy.id if view.copy else None,
                "commit": commit,
            },
        )
        self._memories.committed(resolved)
        return kept

    # -- plumbing ------------------------------------------------------------

    async def _superseded_target(
        self, resolved: ResolvedSpace, principal: Principal, memory: Memory
    ) -> Memory | None:
        target = memory.frontmatter.supersedes
        if not target:
            return None
        async with self._db.acquire() as connection:
            indexed = await MemoryIndexRepository(connection).get(target, [resolved.space.id])
        if indexed is None or indexed.status is MemoryStatus.SUPERSEDED:
            return None
        try:
            return await self._memories.load_file(resolved, indexed)
        except NotFound:
            return None

    async def _commit(
        self,
        resolved: ResolvedSpace,
        principal: Principal,
        memories: list[Memory],
        message: str,
        *,
        removed: list[str] | None = None,
    ) -> str | None:
        """Writes, commits, reindexes. Must run under the space lock."""
        removed = removed or []
        for memory in memories:
            frontmatter.write_file(resolved.path / memory.path, memory)
        commit = resolved.repo.commit(
            [memory.path for memory in memories] + removed,
            message,
            **self._memories.identity(principal),
        )
        for path in removed:
            await self._indexer.remove_path(resolved.space, path)
        if memories:
            await self._indexer.index_memories(resolved.space, memories)
        await self._spaces.set_last_indexed_commit(resolved.space, commit)
        return commit
