"""Formats core objects for MCP responses.

Tools return plain JSON: an agent must be able to read the result without
knowing the internal models.
"""

from __future__ import annotations

from typing import Any

from ..core.git import Commit
from ..core.memories import WriteResult
from ..core.models import IndexedMemory, SearchHit, Space


def space_to_dict(space: Space) -> dict[str, Any]:
    return {
        "slug": space.slug,
        "description": space.description,
        "review_required": space.review_required,
    }


def provenance(memory: IndexedMemory) -> dict[str, Any]:
    return {
        "author": memory.author,
        "source": memory.source,
        "space": memory.space,
        "path": memory.path,
        "created": memory.created.isoformat(),
        "updated": memory.updated.isoformat(),
    }


def memory_to_dict(memory: IndexedMemory, *, include_content: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": memory.id,
        "type": memory.type.value,
        "title": memory.title,
        "project": memory.project,
        "tags": memory.tags,
        "status": memory.status.value,
        "verified": memory.verified,
        "provenance": provenance(memory),
    }
    if include_content:
        payload["content"] = memory.content
    return payload


def hit_to_dict(hit: SearchHit) -> dict[str, Any]:
    payload = memory_to_dict(hit.memory)
    payload["score"] = round(hit.score, 6)
    payload["matched_by"] = _matched_by(hit)
    if hit.similarity is not None:
        payload["similarity"] = round(hit.similarity, 4)
    return payload


def _matched_by(hit: SearchHit) -> str:
    if hit.rank_text is not None and hit.rank_vector is not None:
        return "hybrid"
    if hit.rank_text is not None:
        return "keyword"
    return "semantic"


def write_result_to_dict(result: WriteResult) -> dict[str, Any]:
    memory = result.memory
    payload: dict[str, Any] = {
        "id": memory.id,
        "created": result.created,
        "title": memory.frontmatter.title,
        "type": memory.frontmatter.type.value,
        "status": memory.frontmatter.status.value,
        "space": memory.space,
        "path": memory.path,
        "commit": result.commit,
    }
    if result.duplicate_of:
        payload["duplicate_of"] = result.duplicate_of
        payload["similarity"] = round(result.similarity or 0.0, 4)
        payload["note"] = (
            "A near-identical memory already existed; it is returned as is. "
            "Use memory_update to refine it or memory_supersede to replace it."
        )
    if result.pending:
        payload["note"] = (
            "This space requires human review: the memory has status pending "
            "and will not show up in searches until it is reviewed."
        )
    return payload


def commit_to_dict(commit: Commit) -> dict[str, Any]:
    return {
        "sha": commit.sha,
        "author": commit.author,
        "date": commit.date.isoformat(),
        "message": commit.message,
    }
