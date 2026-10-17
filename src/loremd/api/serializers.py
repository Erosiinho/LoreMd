"""Mise en forme des objets du core pour les réponses MCP.

Les outils renvoient du JSON simple : un agent doit pouvoir lire le résultat
sans connaître les modèles internes.
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
    return payload


def _matched_by(hit: SearchHit) -> str:
    if hit.rank_text is not None and hit.rank_vector is not None:
        return "hybride"
    if hit.rank_text is not None:
        return "mots-clés"
    return "sémantique"


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
            "Une mémoire quasi identique existait déjà ; elle est renvoyée telle quelle. "
            "Utiliser memory_update pour la préciser ou memory_supersede pour la remplacer."
        )
    if result.pending:
        payload["note"] = (
            "L'espace exige une validation humaine : la mémoire est en status pending "
            "et n'apparaîtra pas dans les recherches avant relecture."
        )
    return payload


def commit_to_dict(commit: Commit) -> dict[str, Any]:
    return {
        "sha": commit.sha,
        "author": commit.author,
        "date": commit.date.isoformat(),
        "message": commit.message,
    }
