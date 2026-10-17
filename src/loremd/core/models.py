"""Modèles du domaine.

Le frontmatter d'un fichier `.md` est la source de vérité ; ces modèles le
valident à la lecture comme à l'écriture.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryType(StrEnum):
    DECISION = "decision"
    PREFERENCE = "preference"
    RULE = "rule"
    FACT = "fact"
    NOTE = "note"

    @property
    def directory(self) -> str:
        """Dossier de l'espace où vit ce type de mémoire."""
        return _TYPE_DIRECTORIES[self]


_TYPE_DIRECTORIES: dict[MemoryType, str] = {
    MemoryType.DECISION: "decisions",
    MemoryType.PREFERENCE: "preferences",
    MemoryType.RULE: "rules",
    MemoryType.FACT: "facts",
    MemoryType.NOTE: "notes",
}

MEMORY_DIRECTORIES = tuple(_TYPE_DIRECTORIES.values())


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    CONFLICT = "conflict"


#: Statuts renvoyés par la recherche quand l'appelant ne demande rien de précis.
SEARCHABLE_STATUSES = (MemoryStatus.ACTIVE,)


class Frontmatter(BaseModel):
    """En-tête YAML d'un fichier mémoire."""

    model_config = ConfigDict(extra="allow", use_enum_values=False)

    id: str
    type: MemoryType
    title: str
    project: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    verified: bool = False
    source: str | None = None
    author: str | None = None
    created: datetime
    updated: datetime
    supersedes: str | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [tag.strip() for tag in value.split(",") if tag.strip()]
        return [str(tag) for tag in value]

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title ne peut pas être vide")
        return value.strip()


class Memory(BaseModel):
    """Une mémoire : son frontmatter, son corps, et où elle se trouve."""

    frontmatter: Frontmatter
    content: str
    space: str
    path: str

    @property
    def id(self) -> str:
        return self.frontmatter.id

    @property
    def content_hash(self) -> str:
        return content_hash(self.content)

    def embedding_text(self) -> str:
        """Texte soumis au modèle d'embeddings : titre + tags + corps."""
        fm = self.frontmatter
        parts = [fm.title]
        if fm.project:
            parts.append(fm.project)
        if fm.tags:
            parts.append(" ".join(fm.tags))
        parts.append(self.content)
        return "\n".join(parts)


class IndexedMemory(BaseModel):
    """Ligne de `memory_index`, telle que relue par la recherche."""

    id: str
    space_id: UUID
    space: str
    path: str
    type: MemoryType
    title: str
    project: str | None
    tags: list[str]
    status: MemoryStatus
    verified: bool
    author: str | None
    source: str | None
    content: str
    created: datetime
    updated: datetime


class SearchHit(BaseModel):
    """Résultat de recherche, provenance comprise."""

    memory: IndexedMemory
    score: float
    rank_text: int | None = None
    rank_vector: int | None = None


class SearchResponse(BaseModel):
    matches: list[SearchHit]
    status: str = "ok"  # ok | no_match


class Space(BaseModel):
    id: UUID
    slug: str
    path: str
    description: str | None = None
    remote_url: str | None = None
    review_required: bool = False
    last_indexed_commit: str | None = None


def content_hash(content: str) -> str:
    """Hash stable du corps, utilisé pour éviter les réindexations inutiles."""
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
