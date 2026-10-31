"""Handoffs and session resume.

A handoff is where a work session stopped: a memory of type `handoff`, under
`handoffs/`, whose body has five fixed sections so it reads the same in
Obsidian as in an agent's context. One handoff thread per project: a new
handoff supersedes the project's current one, so only the latest is active and
the rest of the thread stays in git.

`resume` answers "where were we?": the latest active handoff(s), plus the
decisions and rules most relevant to them. It only reads the index, which
`loremd reindex` rebuilds from the files, so the answer is the same after a
rebuild.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from ..auth.principal import Principal, Scope
from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository
from ..errors import ConflictError, NotFound, ValidationError
from .audit import AuditService
from .memories import MemoryService, WriteResult
from .models import IndexedMemory, MemoryStatus, MemoryType
from .search import SearchService
from .spaces import SpaceService

#: Field name and heading of each section, in file order.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("summary", "Summary"),
    ("decisions", "Decisions"),
    ("progress", "Progress"),
    ("open_questions", "Open questions"),
    ("next_actions", "Next actions"),
)

#: What an empty section holds, so every heading is always there.
EMPTY_SECTION = "_None._"

#: Types `resume` brings along with the handoffs.
CONTEXT_TYPES = (MemoryType.DECISION, MemoryType.RULE)

MAX_SOURCES = 50

#: Below this many characters left, the next entry is dropped rather than cut
#: down to a stub nobody can use.
MIN_EXCERPT = 200

#: Most distinctive words of a summary turned into a keyword query.
MAX_KEYWORDS = 12

_WORD = re.compile(r"[^\W_][\w-]*[^\W_]|[^\W_]")
_STOPWORDS = frozenset(
    """
    all and any are but can did for had has her his how its not now our out own she the
    too was who why yet you
    about after again also because been before being between both could does doing done
    each from have having here into just more most much must need next only other over
    same should some still such than that their them then there these they this those
    through under until very want were what when where which while will with would your
    """.split()
)

_HEADING = re.compile(r"^ {0,3}#")
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_HEADING_TO_KEY = {heading.lower(): key for key, heading in SECTIONS}

SectionValue = str | list[str] | None


# -- rendering ---------------------------------------------------------------


def _escape(text: str) -> str:
    """A line starting with `#` would open a section of its own: make it literal."""
    return "\n".join(
        "\\" + line.lstrip(" ") if _HEADING.match(line) else line
        for line in text.strip().splitlines()
    )


def _render_section(key: str, value: SectionValue) -> str:
    if value is None:
        return EMPTY_SECTION
    if isinstance(value, str):
        return _escape(value) or EMPTY_SECTION
    marker = "- [ ] " if key == "next_actions" else "- "
    items = [_escape(str(item)) for item in value if str(item).strip()]
    if not items:
        return EMPTY_SECTION
    return "\n".join(marker + item.replace("\n", "\n  ") for item in items)


def render_sections(
    *,
    summary: str,
    decisions: SectionValue = None,
    progress: SectionValue = None,
    open_questions: SectionValue = None,
    next_actions: SectionValue = None,
) -> str:
    """Renders a handoff body. Lists become bullets, next actions checkboxes."""
    if not summary or not summary.strip():
        raise ValidationError("summary is required: one paragraph on where the work stands")
    values = {
        "summary": summary,
        "decisions": decisions,
        "progress": progress,
        "open_questions": open_questions,
        "next_actions": next_actions,
    }
    blocks = [
        f"## {heading}\n\n{_render_section(key, values[key])}" for key, heading in SECTIONS
    ]
    return "\n\n".join(blocks)


def parse_sections(body: str) -> dict[str, str]:
    """Reads the sections back from a handoff body, human edits included.

    Unknown headings stay inside the section above them; a missing section
    comes back empty.
    """
    sections = {key: "" for key, _ in SECTIONS}
    current: str | None = None
    lines: dict[str, list[str]] = {key: [] for key in sections}
    for line in body.splitlines():
        match = _SECTION_HEADING.match(line)
        if match and match.group(1).lower() in _HEADING_TO_KEY:
            current = _HEADING_TO_KEY[match.group(1).lower()]
            continue
        if current is not None:
            lines[current].append(line)
    for key, collected in lines.items():
        text = "\n".join(collected).strip()
        sections[key] = "" if text == EMPTY_SECTION else text
    return sections


def keyword_query(text: str) -> str:
    """An any-of keyword query from the distinctive words of a text.

    Full-text search requires every word of a query, which a whole summary
    never satisfies: its significant words are joined with `or` instead, and
    ranking does the rest.
    """
    words: list[str] = []
    for word in _WORD.findall(text.lower()):
        if len(word) >= 3 and word not in _STOPWORDS and word not in words:
            words.append(word)
        if len(words) == MAX_KEYWORDS:
            break
    return " or ".join(words)


# -- budget ------------------------------------------------------------------


@dataclass
class ResumeEntry:
    """A memory as `resume` returns it: its content may have been cut."""

    memory: IndexedMemory
    kind: Literal["handoff", "memory"]
    content: str
    truncated: bool = False
    score: float | None = None


@dataclass
class ResumeResult:
    handoffs: list[ResumeEntry] = field(default_factory=list)
    memories: list[ResumeEntry] = field(default_factory=list)
    #: Entries left out entirely because the budget ran out.
    omitted: list[IndexedMemory] = field(default_factory=list)
    query: str | None = None

    @property
    def status(self) -> str:
        return "ok" if self.handoffs or self.memories or self.omitted else "no_match"

    @property
    def truncated(self) -> bool:
        entries = self.handoffs + self.memories
        return bool(self.omitted) or any(entry.truncated for entry in entries)


def fit_to_budget(entries: list[ResumeEntry], max_chars: int) -> list[IndexedMemory]:
    """Cuts contents so that together they fit in `max_chars`.

    Entries are kept in order: the first one is always returned, cut if need
    be; later ones are cut while at least `MIN_EXCERPT` characters remain,
    and dropped after that. Returns the dropped memories, removed from
    `entries`.
    """
    remaining = max_chars
    kept: list[ResumeEntry] = []
    omitted: list[IndexedMemory] = []
    for entry in entries:
        if len(entry.content) <= remaining:
            kept.append(entry)
            remaining -= len(entry.content)
            continue
        if remaining > 0 and (not kept or remaining >= MIN_EXCERPT):
            entry.content = entry.content[: max(remaining - 1, 0)].rstrip() + "…"
            entry.truncated = True
            kept.append(entry)
            remaining = 0
            continue
        omitted.append(entry.memory)
    entries[:] = kept
    return omitted


# -- service -----------------------------------------------------------------


class HandoffService:
    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        memories: MemoryService,
        search: SearchService,
        audit: AuditService,
        settings: Settings,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._memories = memories
        self._search = search
        self._audit = audit
        self._settings = settings

    async def save(
        self,
        principal: Principal,
        *,
        space_slug: str,
        title: str,
        summary: str,
        decisions: SectionValue = None,
        progress: SectionValue = None,
        open_questions: SectionValue = None,
        next_actions: SectionValue = None,
        project: str | None = None,
        sources: list[str] | None = None,
        supersedes: str | None = None,
    ) -> WriteResult:
        """Writes a handoff; it supersedes the project's current one.

        `supersedes` names the previous handoff explicitly; without it, the
        latest active handoff of the same project (or of no project) is
        taken. Secret detection, review and audit are those of any write.
        """
        principal.require_scope(Scope.WRITE)
        if not title or not title.strip():
            raise ValidationError("title is required")
        resolved = await self._spaces.resolve(space_slug, principal)
        content = render_sections(
            summary=summary,
            decisions=decisions,
            progress=progress,
            open_questions=open_questions,
            next_actions=next_actions,
        )
        cleaned_sources = await self._check_sources(resolved.space.id, sources)
        project = project.strip() if project and project.strip() else None

        previous = await self._previous(principal, resolved.space.id, project, supersedes)
        if previous is None:
            return await self._memories.write(
                principal,
                content=content,
                memory_type=MemoryType.HANDOFF,
                space_slug=resolved.slug,
                title=title,
                project=project,
                sources=cleaned_sources or None,
                source="handoff_save",
                # Two handoffs of the same thread look alike on purpose: the
                # second must not come back as a duplicate of the first.
                dedup=False,
            )
        return await self._memories.supersede(
            principal,
            previous.id,
            content,
            "new handoff",
            title=title,
            project=project if project is not None else previous.project,
            sources=cleaned_sources,
        )

    async def _previous(
        self,
        principal: Principal,
        space_id: UUID,
        project: str | None,
        supersedes: str | None,
    ) -> IndexedMemory | None:
        if supersedes:
            try:
                previous = await self._memories.lookup(principal, supersedes)
            except NotFound as error:
                raise ValidationError(f"supersedes: no handoff with id {supersedes}") from error
            if previous.space_id != space_id or previous.type is not MemoryType.HANDOFF:
                raise ValidationError(
                    f"supersedes: {supersedes} is not a handoff of this space"
                )
            if previous.status not in (MemoryStatus.ACTIVE, MemoryStatus.PENDING):
                raise ConflictError(
                    f"handoff {supersedes} is {previous.status.value}; supersede the "
                    "latest handoff of the thread, or leave supersedes out"
                )
            return previous
        async with self._db.acquire() as connection:
            current = await MemoryIndexRepository(connection).latest_per_project(
                [space_id], MemoryType.HANDOFF.value, project=project, limit=1
            )
        return current[0] if current else None

    async def _check_sources(self, space_id: UUID, sources: list[str] | None) -> list[str]:
        """Sources must be memories of the space: an invented id is refused."""
        cleaned: list[str] = []
        for source in sources or []:
            if source.strip() and source.strip() not in cleaned:
                cleaned.append(source.strip())
        if len(cleaned) > MAX_SOURCES:
            raise ValidationError(f"too many sources: {len(cleaned)}, maximum is {MAX_SOURCES}")
        unknown: list[str] = []
        async with self._db.acquire() as connection:
            repository = MemoryIndexRepository(connection)
            for source in cleaned:
                if await repository.get(source, [space_id]) is None:
                    unknown.append(source)
        if unknown:
            raise ValidationError(
                "sources: unknown memory ids in this space: " + ", ".join(unknown)
            )
        return cleaned

    async def resume(
        self,
        principal: Principal,
        *,
        space_slug: str,
        project: str | None = None,
        query: str | None = None,
        limit: int = 5,
        max_chars: int = 12_000,
    ) -> ResumeResult:
        """The latest active handoff(s) and the decisions and rules that matter.

        With a project, its current handoff; without, the current handoff of
        each project. Decisions and rules come from a hybrid search on `query`,
        or else on the latest handoff's summary.
        """
        principal.require_scope(Scope.READ)
        if limit < 1:
            raise ValidationError("limit must be at least 1")
        if max_chars < 1:
            raise ValidationError("max_chars must be at least 1")
        limit = min(limit, self._settings.search.max_limit)
        resolved = await self._spaces.resolve(space_slug, principal)
        project = project.strip() if project and project.strip() else None

        async with self._db.acquire() as connection:
            handoffs = await MemoryIndexRepository(connection).latest_per_project(
                [resolved.space.id],
                MemoryType.HANDOFF.value,
                project=project,
                any_project=project is None,
                limit=limit,
            )

        search_text = (query or "").strip()
        keywords = search_text
        if not search_text and handoffs:
            latest = handoffs[0]
            search_text = f"{latest.title}\n{parse_sections(latest.content)['summary']}".strip()
            keywords = keyword_query(search_text)
        hits = await self._search.relevant(
            query=keywords,
            semantic_query=search_text,
            space_ids=[resolved.space.id],
            memory_types=list(CONTEXT_TYPES),
            project=project,
            include_unscoped=True,
            limit=limit,
        )

        entries = [ResumeEntry(memory=h, kind="handoff", content=h.content) for h in handoffs]
        entries += [
            ResumeEntry(
                memory=hit.memory, kind="memory", content=hit.memory.content, score=hit.score
            )
            for hit in hits
        ]
        omitted = fit_to_budget(entries, max_chars)
        result = ResumeResult(
            handoffs=[entry for entry in entries if entry.kind == "handoff"],
            memories=[entry for entry in entries if entry.kind == "memory"],
            omitted=omitted,
            query=search_text or None,
        )

        await self._audit.record(
            principal,
            "context_resume",
            target=project,
            space_id=resolved.space.id,
            meta={
                "query": (query or "")[:200] or None,
                "handoffs": [entry.memory.id for entry in result.handoffs],
                "memories": len(result.memories),
                "truncated": result.truncated,
            },
        )
        return result
