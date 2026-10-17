"""Reconstruction de l'index Postgres à partir des fichiers.

L'index est jetable : `reindex` le refait entièrement, et le watcher
n'applique que les fichiers modifiés depuis `last_indexed_commit`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..db import Database
from ..db.repositories import MemoryIndexRepository, SpaceRepository
from ..embeddings import EmbeddingProvider
from ..errors import ValidationError
from . import frontmatter
from .models import MEMORY_DIRECTORIES, Memory, Space
from .spaces import SpaceService

EMBED_BATCH_SIZE = 16


@dataclass
class IndexReport:
    """Ce qu'une passe d'indexation a fait, pour l'afficher dans la CLI."""

    space: str
    indexed: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    commit: str | None = None


def iter_memory_files(root: Path) -> list[Path]:
    """Fichiers `.md` d'un espace, en ignorant README et dossiers techniques."""
    files: list[Path] = []
    for directory in MEMORY_DIRECTORIES:
        base = root / directory
        if base.is_dir():
            files.extend(sorted(p for p in base.rglob("*.md") if p.is_file()))
    return files


def is_memory_path(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    return (
        len(parts) >= 2
        and parts[0] in MEMORY_DIRECTORIES
        and relative_path.endswith(".md")
    )


class Indexer:
    """Lit les fichiers d'un espace et met l'index à jour."""

    def __init__(
        self,
        database: Database,
        spaces: SpaceService,
        embeddings: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self._db = database
        self._spaces = spaces
        self._embeddings = embeddings
        self._settings = settings

    async def index_memories(self, space: Space, memories: list[Memory]) -> int:
        """Indexe des mémoires déjà lues, par lots pour l'embedding."""
        count = 0
        for start in range(0, len(memories), EMBED_BATCH_SIZE):
            batch = memories[start : start + EMBED_BATCH_SIZE]
            vectors = await self._embeddings.embed([m.embedding_text() for m in batch])
            async with self._db.transaction() as connection:
                repository = MemoryIndexRepository(connection)
                for memory, vector in zip(batch, vectors, strict=True):
                    await repository.upsert(memory, space.id, vector)
                    count += 1
        return count

    async def index_memory(self, space: Space, memory: Memory) -> None:
        await self.index_memories(space, [memory])

    async def remove_path(self, space: Space, relative_path: str) -> None:
        async with self._db.acquire() as connection:
            await MemoryIndexRepository(connection).delete_by_path(space.id, relative_path)

    async def reindex_space(self, space: Space, *, full: bool = True) -> IndexReport:
        """Relit tous les fichiers de l'espace et remplace l'index."""
        report = IndexReport(space=space.slug)
        root = Path(space.path)
        memories: list[Memory] = []

        for file_path in iter_memory_files(root):
            relative = file_path.relative_to(root).as_posix()
            try:
                memories.append(
                    frontmatter.read_file(file_path, space=space.slug, relative_path=relative)
                )
            except ValidationError as exc:
                report.skipped += 1
                report.errors.append(f"{relative} : {exc}")

        if full:
            async with self._db.acquire() as connection:
                await MemoryIndexRepository(connection).clear_space(space.id)

        report.indexed = await self.index_memories(space, memories)

        repo = self._spaces.repo_for(space)
        report.commit = repo.head() if repo.exists else None
        async with self._db.acquire() as connection:
            await SpaceRepository(connection).set_last_indexed_commit(space.id, report.commit)
        return report

    async def sync_space(self, space: Space) -> IndexReport:
        """Réindexe seulement ce qui a changé depuis `last_indexed_commit`.

        C'est le chemin utilisé après un pull : une édition faite dans Obsidian
        arrive ici sous forme de diff git.
        """
        repo = self._spaces.repo_for(space)
        head = repo.head()
        if head is None:
            return IndexReport(space=space.slug)
        if space.last_indexed_commit == head:
            return IndexReport(space=space.slug, commit=head)

        report = IndexReport(space=space.slug, commit=head)
        root = Path(space.path)
        to_index: list[Memory] = []

        for status, relative in repo.changed_files(space.last_indexed_commit):
            if not is_memory_path(relative):
                continue
            if status == "D":
                await self.remove_path(space, relative)
                report.deleted += 1
                continue
            file_path = root / relative
            if not file_path.exists():
                await self.remove_path(space, relative)
                report.deleted += 1
                continue
            try:
                to_index.append(
                    frontmatter.read_file(file_path, space=space.slug, relative_path=relative)
                )
            except ValidationError as exc:
                report.skipped += 1
                report.errors.append(f"{relative} : {exc}")

        report.indexed = await self.index_memories(space, to_index)
        async with self._db.acquire() as connection:
            await SpaceRepository(connection).set_last_indexed_commit(space.id, head)
        return report
