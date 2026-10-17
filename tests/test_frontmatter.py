from __future__ import annotations

import pytest

from loremd.core import frontmatter
from loremd.core.models import Memory, MemoryStatus, MemoryType
from loremd.errors import ValidationError


def test_render_puis_parse_conserve_la_memoire(memory: Memory) -> None:
    rendered = frontmatter.render(memory)
    reparsed = frontmatter.parse(rendered, space=memory.space, path=memory.path)

    assert reparsed.frontmatter == memory.frontmatter
    assert reparsed.content == memory.content


def test_render_est_deterministe(memory: Memory) -> None:
    assert frontmatter.render(memory) == frontmatter.render(memory)


def test_render_respecte_l_ordre_des_cles(memory: Memory) -> None:
    lines = frontmatter.render(memory).splitlines()
    keys = [line.split(":", 1)[0] for line in lines[1:] if not line.startswith((" ", "-", "---"))]

    assert keys[:4] == ["id", "type", "title", "project"]


def test_parse_valeurs_par_defaut() -> None:
    document = """---
id: 01JA8KZ3M9X2QH7R4T6VABCDEF
type: rule
title: Pas de secrets en mémoire
created: 2026-10-14T10:42:00+02:00
updated: 2026-10-14T10:42:00+02:00
---

Les clés d'API ne sont jamais écrites dans un espace.
"""
    parsed = frontmatter.parse(document, space="produit", path="rules/pas-de-secrets-abcdef.md")

    assert parsed.frontmatter.type is MemoryType.RULE
    assert parsed.frontmatter.status is MemoryStatus.ACTIVE
    assert parsed.frontmatter.verified is False
    assert parsed.frontmatter.tags == []


def test_parse_refuse_un_fichier_sans_frontmatter() -> None:
    with pytest.raises(ValidationError):
        frontmatter.parse("juste du texte", space="produit", path="notes/x.md")


def test_parse_refuse_un_frontmatter_incomplet() -> None:
    document = "---\ntype: note\ntitle: sans id\n---\n\ncorps\n"

    with pytest.raises(ValidationError):
        frontmatter.parse(document, space="produit", path="notes/x.md")


def test_champs_additionnels_survivent_a_un_aller_retour(memory: Memory) -> None:
    memory.frontmatter.obsidian_link = "[[autre-note]]"  # type: ignore[attr-defined]

    reparsed = frontmatter.parse(
        frontmatter.render(memory), space=memory.space, path=memory.path
    )

    assert reparsed.frontmatter.model_dump()["obsidian_link"] == "[[autre-note]]"
