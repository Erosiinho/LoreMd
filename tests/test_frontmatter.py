from __future__ import annotations

import pytest

from loremd.core import frontmatter
from loremd.core.models import Memory, MemoryStatus, MemoryType
from loremd.errors import ValidationError


def test_render_then_parse_preserves_the_memory(memory: Memory) -> None:
    rendered = frontmatter.render(memory)
    reparsed = frontmatter.parse(rendered, space=memory.space, path=memory.path)

    assert reparsed.frontmatter == memory.frontmatter
    assert reparsed.content == memory.content


def test_render_is_deterministic(memory: Memory) -> None:
    assert frontmatter.render(memory) == frontmatter.render(memory)


def test_render_respects_key_order(memory: Memory) -> None:
    lines = frontmatter.render(memory).splitlines()
    keys = [line.split(":", 1)[0] for line in lines[1:] if not line.startswith((" ", "-", "---"))]

    assert keys[:4] == ["id", "type", "title", "project"]


def test_parse_applies_defaults() -> None:
    document = """---
id: 01JA8KZ3M9X2QH7R4T6VABCDEF
type: rule
title: No secrets in memory
created: 2026-10-14T10:42:00+02:00
updated: 2026-10-14T10:42:00+02:00
---

API keys are never written to a space.
"""
    parsed = frontmatter.parse(document, space="product", path="rules/no-secrets-abcdef.md")

    assert parsed.frontmatter.type is MemoryType.RULE
    assert parsed.frontmatter.status is MemoryStatus.ACTIVE
    assert parsed.frontmatter.verified is False
    assert parsed.frontmatter.tags == []


def test_parse_rejects_a_file_without_frontmatter() -> None:
    with pytest.raises(ValidationError):
        frontmatter.parse("just some text", space="product", path="notes/x.md")


def test_parse_rejects_incomplete_frontmatter() -> None:
    document = "---\ntype: note\ntitle: no id\n---\n\nbody\n"

    with pytest.raises(ValidationError):
        frontmatter.parse(document, space="product", path="notes/x.md")


def test_extra_fields_survive_a_round_trip(memory: Memory) -> None:
    memory.frontmatter.obsidian_link = "[[other-note]]"  # type: ignore[attr-defined]

    reparsed = frontmatter.parse(
        frontmatter.render(memory), space=memory.space, path=memory.path
    )

    assert reparsed.frontmatter.model_dump()["obsidian_link"] == "[[other-note]]"
