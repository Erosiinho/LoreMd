"""Read-only views for the console: browse memories, sessions, agents and tokens.

Nothing here writes. Every call requires the admin scope and filters on the
spaces the principal may see, like the rest of the core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from typing import Any
from uuid import UUID

from ..auth.principal import Principal, Scope
from ..db import Database
from ..db.repositories import AgentRepository, MemoryIndexRepository, TokenRepository
from ..errors import ValidationError
from .handoffs import SECTIONS, parse_sections
from .models import IndexedMemory, MemoryStatus, MemoryType, Space
from .spaces import SpaceService

PER_PAGE = 25

#: Statuses of a handoff thread's head: a pending handoff is still where the
#: session stopped, it is only waiting for a human.
OPEN_HANDOFF_STATUSES = (MemoryStatus.ACTIVE, MemoryStatus.PENDING)


@dataclass(frozen=True)
class MemoryFilter:
    memory_type: MemoryType | None = None
    status: MemoryStatus | None = None
    project: str | None = None
    query: str | None = None

    @classmethod
    def parse(cls, params: Any) -> MemoryFilter:
        """Reads the filter from query parameters; unknown values are refused."""
        try:
            memory_type = MemoryType(params["type"]) if params.get("type") else None
            status = MemoryStatus(params["status"]) if params.get("status") else None
        except ValueError as error:
            raise ValidationError(f"invalid filter: {error}") from error
        query = (params.get("q") or "").strip()[:200] or None
        return cls(memory_type, status, params.get("project") or None, query)

    def query_string(self, **override: Any) -> dict[str, str]:
        """The filter as query parameters, for links that change one of them."""
        values = {
            "type": self.memory_type.value if self.memory_type else "",
            "status": self.status.value if self.status else "",
            "project": self.project or "",
            "q": self.query or "",
        }
        values.update({key: "" if value is None else str(value) for key, value in override.items()})
        return {key: value for key, value in values.items() if value}


@dataclass(frozen=True)
class MemoryPage:
    items: list[IndexedMemory]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.per_page))


@dataclass(frozen=True)
class Session:
    """The head of a handoff thread: where a project's last session stopped."""

    handoff: IndexedMemory
    sections: dict[str, str]
    length: int = 1

    @property
    def summary(self) -> str:
        return self.sections.get("summary", "")


@dataclass(frozen=True)
class TokenView:
    prefix: str
    scopes: list[str]
    spaces: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def state(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= datetime.now(UTC):
            return "expired"
        return "active"


@dataclass(frozen=True)
class AgentView:
    name: str
    description: str | None
    review_required: bool
    created_at: datetime
    disabled_at: datetime | None
    tokens: list[TokenView] = field(default_factory=list)

    @property
    def active_tokens(self) -> int:
        return sum(1 for token in self.tokens if token.state == "active")

    @property
    def last_used_at(self) -> datetime | None:
        used = [token.last_used_at for token in self.tokens if token.last_used_at]
        return max(used) if used else None


@dataclass(frozen=True)
class Overview:
    spaces: int
    active: int
    pending: int
    conflicts: int
    sessions: int
    by_type: dict[str, int]


def sections_of(memory: IndexedMemory) -> dict[str, str]:
    """Sections of a handoff, in file order, empty ones left out."""
    parsed = parse_sections(memory.content)
    return {key: parsed[key] for key, _ in SECTIONS if parsed.get(key)}


class BrowseService:
    def __init__(self, database: Database, spaces: SpaceService) -> None:
        self._db = database
        self._spaces = spaces

    async def _space_ids(self, principal: Principal, space_slug: str | None) -> list[UUID]:
        principal.require_scope(Scope.ADMIN)
        if space_slug:
            return [(await self._spaces.resolve(space_slug, principal)).space.id]
        return [space.id for space in await self._spaces.list(principal)]

    async def space(self, principal: Principal, slug: str) -> Space:
        principal.require_scope(Scope.ADMIN)
        return (await self._spaces.resolve(slug, principal)).space

    async def overview(self, principal: Principal) -> Overview:
        space_ids = await self._space_ids(principal, None)
        async with self._db.acquire() as connection:
            repository = MemoryIndexRepository(connection)
            statuses = await repository.count_by_status(space_ids)
            by_type = await repository.count_by_type(space_ids)
            sessions = await repository.latest_per_project(
                space_ids,
                MemoryType.HANDOFF.value,
                any_project=True,
                statuses=OPEN_HANDOFF_STATUSES,
                limit=10_000,
            )

        def total(status: MemoryStatus) -> int:
            return sum(n for (_, name), n in statuses.items() if name == status.value)

        return Overview(
            spaces=len(space_ids),
            active=total(MemoryStatus.ACTIVE),
            pending=total(MemoryStatus.PENDING),
            conflicts=total(MemoryStatus.CONFLICT),
            sessions=len(sessions),
            by_type=by_type,
        )

    async def memories(
        self,
        principal: Principal,
        space_slug: str,
        memory_filter: MemoryFilter,
        *,
        page: int = 1,
        per_page: int = PER_PAGE,
    ) -> MemoryPage:
        space_ids = await self._space_ids(principal, space_slug)
        page = max(1, page)
        async with self._db.acquire() as connection:
            items, total = await MemoryIndexRepository(connection).browse(
                space_ids,
                memory_type=memory_filter.memory_type.value if memory_filter.memory_type else None,
                status=memory_filter.status.value if memory_filter.status else None,
                project=memory_filter.project,
                query=memory_filter.query,
                limit=per_page,
                offset=(page - 1) * per_page,
            )
        return MemoryPage(items=items, total=total, page=page, per_page=per_page)

    async def projects(self, principal: Principal, space_slug: str) -> list[tuple[str, int]]:
        space_ids = await self._space_ids(principal, space_slug)
        async with self._db.acquire() as connection:
            return await MemoryIndexRepository(connection).projects(space_ids)

    async def type_counts(self, principal: Principal, space_slug: str) -> dict[str, int]:
        space_ids = await self._space_ids(principal, space_slug)
        async with self._db.acquire() as connection:
            return await MemoryIndexRepository(connection).count_by_type(space_ids)

    async def sessions(
        self, principal: Principal, *, space_slug: str | None = None, limit: int = 200
    ) -> list[Session]:
        """Latest handoff of every project, most recent session first."""
        space_ids = await self._space_ids(principal, space_slug)
        if not space_ids:
            return []
        async with self._db.acquire() as connection:
            repository = MemoryIndexRepository(connection)
            heads = await repository.latest_per_project(
                space_ids,
                MemoryType.HANDOFF.value,
                any_project=True,
                statuses=OPEN_HANDOFF_STATUSES,
                limit=limit,
            )
            lengths = await repository.handoff_thread_lengths(space_ids)
        return [
            Session(
                handoff=head,
                sections=sections_of(head),
                length=lengths.get((head.space_id, head.project), 1),
            )
            for head in heads
        ]

    async def thread(
        self, principal: Principal, space_slug: str, project: str | None
    ) -> list[IndexedMemory]:
        """Every handoff of one project, newest first."""
        space_ids = await self._space_ids(principal, space_slug)
        async with self._db.acquire() as connection:
            return await MemoryIndexRepository(connection).handoff_thread(space_ids[0], project)

    async def agents(self, principal: Principal) -> list[AgentView]:
        """Agents and their tokens. Tokens are listed by prefix, never in full."""
        principal.require_scope(Scope.ADMIN)
        slugs = {space.id: space.slug for space in await self._spaces.list()}
        async with self._db.acquire() as connection:
            agents = await AgentRepository(connection).list()
            tokens = await TokenRepository(connection).list()
        by_agent: dict[str, list[TokenView]] = {}
        for token in tokens:
            by_agent.setdefault(token["agent_name"], []).append(
                TokenView(
                    prefix=token["prefix"],
                    scopes=sorted(token["scopes"] or []),
                    spaces=sorted(
                        slugs.get(space_id, "?") for space_id in token["space_ids"] or ()
                    ),
                    created_at=token["created_at"],
                    expires_at=token["expires_at"],
                    last_used_at=token["last_used_at"],
                    revoked_at=token["revoked_at"],
                )
            )
        return [
            AgentView(
                name=agent["name"],
                description=agent["description"],
                review_required=agent["review_required"],
                created_at=agent["created_at"],
                disabled_at=agent["disabled_at"],
                tokens=by_agent.get(agent["name"], []),
            )
            for agent in agents
        ]
