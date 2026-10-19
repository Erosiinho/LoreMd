"""Handoffs: frontmatter, rendering of the sections, the resume budget."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from loremd.core import frontmatter
from loremd.core.handoffs import (
    EMPTY_SECTION,
    MIN_EXCERPT,
    ResumeEntry,
    fit_to_budget,
    keyword_query,
    parse_sections,
    render_sections,
)
from loremd.core.models import (
    SEARCHABLE_TYPES,
    Frontmatter,
    IndexedMemory,
    Memory,
    MemoryStatus,
    MemoryType,
)
from loremd.errors import ValidationError

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def _handoff(**overrides: object) -> Memory:
    fields: dict[str, object] = {
        "id": "01JAHANDOFF0000000000000001",
        "type": MemoryType.HANDOFF,
        "title": "Shop API — auth refactor",
        "project": "shop",
        "author": "claude-code-macbook",
        "created": NOW,
        "updated": NOW,
        "sources": ["01JA8KZ3M9X2QH7R4T6VABCDEF"],
    }
    fields.update(overrides)
    return Memory(
        frontmatter=Frontmatter(**fields),  # type: ignore[arg-type]
        content=render_sections(summary="Halfway through the auth refactor."),
        space="product",
        path="handoffs/shop-api-auth-refactor-000001.md",
    )


# -- frontmatter -------------------------------------------------------------


def test_handoffs_live_in_their_own_directory() -> None:
    assert MemoryType.HANDOFF.value == "handoff"
    assert MemoryType.HANDOFF.directory == "handoffs"


def test_search_leaves_handoffs_out_by_default() -> None:
    assert MemoryType.HANDOFF not in SEARCHABLE_TYPES
    assert MemoryType.DECISION in SEARCHABLE_TYPES


def test_a_handoff_round_trips_with_its_sources() -> None:
    handoff = _handoff(supersedes="01JAHANDOFF0000000000000000")
    rendered = frontmatter.render(handoff)
    reparsed = frontmatter.parse(rendered, space=handoff.space, path=handoff.path)

    assert reparsed.frontmatter == handoff.frontmatter
    assert reparsed.frontmatter.type is MemoryType.HANDOFF
    assert reparsed.frontmatter.sources == ["01JA8KZ3M9X2QH7R4T6VABCDEF"]
    header = rendered.split("---")[1]
    keys = [line.split(":", 1)[0] for line in header.splitlines() if line[:1].isalpha()]
    assert keys[-2:] == ["supersedes", "sources"]


def test_a_memory_without_sources_keeps_its_header(memory: Memory) -> None:
    assert "sources" not in frontmatter.render(memory)


def test_empty_sources_are_not_written() -> None:
    assert "sources" not in frontmatter.render(_handoff(sources=[]))


def test_sources_are_cleaned() -> None:
    handoff = _handoff(sources=[" A ", "B", "A", ""])

    assert handoff.frontmatter.sources == ["A", "B"]


@pytest.mark.parametrize("sources", [[1, 2], {"a": 1}, 42])
def test_sources_must_be_memory_ids(sources: object) -> None:
    document = f"""---
id: 01JAHANDOFF0000000000000001
type: handoff
title: Broken
sources: {sources!r}
created: 2026-10-19T09:00:00+00:00
updated: 2026-10-19T09:00:00+00:00
---

## Summary
"""
    with pytest.raises(ValidationError):
        frontmatter.parse(document, space="product", path="handoffs/broken.md")


def test_sources_are_refused_outside_a_handoff() -> None:
    with pytest.raises(ValueError):
        _handoff(type=MemoryType.DECISION)


def test_a_handoff_written_by_hand_parses() -> None:
    document = """---
id: 01JAHANDOFF0000000000000001
type: handoff
title: Shop API — auth refactor
project: shop
status: active
author: claude-code-macbook
created: 2026-10-19T09:00:00+00:00
updated: 2026-10-19T09:00:00+00:00
supersedes: null
sources: [01JA8KZ3M9X2QH7R4T6VABCDEF]
---
## Summary
Auth refactor half done.
"""
    parsed = frontmatter.parse(document, space="product", path="handoffs/x.md")

    assert parsed.frontmatter.type is MemoryType.HANDOFF
    assert parsed.frontmatter.status is MemoryStatus.ACTIVE
    assert parsed.frontmatter.supersedes is None
    assert parsed.frontmatter.sources == ["01JA8KZ3M9X2QH7R4T6VABCDEF"]


# -- sections ----------------------------------------------------------------


def test_every_section_is_rendered_in_order() -> None:
    body = render_sections(
        summary="Moving the shop API to JWT.",
        decisions=["Tokens expire after 15 minutes."],
        progress=["Login endpoint migrated", "Refresh endpoint started"],
        open_questions="Do we keep the session cookie for the admin?",
        next_actions=["Finish the refresh endpoint", "Remove the old middleware"],
    )

    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Summary",
        "## Decisions",
        "## Progress",
        "## Open questions",
        "## Next actions",
    ]
    assert "- Tokens expire after 15 minutes." in body
    assert "- Login endpoint migrated\n- Refresh endpoint started" in body
    assert "- [ ] Finish the refresh endpoint" in body
    assert "Do we keep the session cookie for the admin?" in body


def test_empty_sections_keep_their_heading() -> None:
    body = render_sections(summary="Nothing else yet.", decisions=[], progress=None)

    assert body.count(EMPTY_SECTION) == 4
    assert "## Next actions" in body


def test_rendering_is_deterministic() -> None:
    fields = {"summary": "Same.", "decisions": ["One", "Two"]}

    assert render_sections(**fields) == render_sections(**fields)


def test_a_summary_is_required() -> None:
    with pytest.raises(ValidationError):
        render_sections(summary="   ")


def test_a_heading_inside_a_field_cannot_open_a_section() -> None:
    body = render_sections(summary="Context.\n## Next actions\nfake", next_actions=["real"])

    assert body.splitlines().count("## Next actions") == 1
    assert parse_sections(body)["next_actions"] == "- [ ] real"
    assert "\\## Next actions" in parse_sections(body)["summary"]


def test_multiline_items_stay_inside_their_bullet() -> None:
    body = render_sections(summary="S.", progress=["first line\nsecond line"])

    assert "- first line\n  second line" in body


def test_sections_parse_back() -> None:
    body = render_sections(
        summary="Moving the shop API to JWT.",
        decisions=["Tokens expire after 15 minutes."],
        next_actions=["Finish the refresh endpoint"],
    )

    sections = parse_sections(body)

    assert sections["summary"] == "Moving the shop API to JWT."
    assert sections["decisions"] == "- Tokens expire after 15 minutes."
    assert sections["progress"] == ""
    assert sections["next_actions"] == "- [ ] Finish the refresh endpoint"


def test_human_edits_to_the_sections_are_tolerated() -> None:
    body = "## summary\nEdited in Obsidian.\n\n### Detail\nmore\n\n## Next Actions ##\n- call Bob"

    sections = parse_sections(body)

    assert sections["summary"] == "Edited in Obsidian.\n\n### Detail\nmore"
    assert sections["next_actions"] == "- call Bob"
    assert sections["open_questions"] == ""


def test_a_summary_becomes_an_any_of_keyword_query() -> None:
    query = keyword_query("Moving the shop API to JWT, and the JWT refresh is next.")

    assert query == "moving or shop or api or jwt or refresh"


def test_keyword_query_is_bounded() -> None:
    text = " ".join(f"word{index}" for index in range(100))

    assert keyword_query(text).count(" or ") == 11


# -- budget ------------------------------------------------------------------


def _entry(content: str, kind: str = "memory") -> ResumeEntry:
    memory = IndexedMemory(
        id=f"id-{len(content)}-{kind}",
        space_id=uuid4(),
        space="product",
        path="decisions/x.md",
        type=MemoryType.HANDOFF if kind == "handoff" else MemoryType.DECISION,
        title="t",
        project=None,
        tags=[],
        status=MemoryStatus.ACTIVE,
        verified=False,
        author="a",
        source=None,
        content=content,
        created=NOW,
        updated=NOW,
    )
    return ResumeEntry(memory=memory, kind=kind, content=content)  # type: ignore[arg-type]


def test_everything_is_kept_within_budget() -> None:
    entries = [_entry("a" * 100, "handoff"), _entry("b" * 200)]

    omitted = fit_to_budget(entries, 1000)

    assert omitted == []
    assert [len(entry.content) for entry in entries] == [100, 200]
    assert not any(entry.truncated for entry in entries)


def test_the_first_entry_is_cut_rather_than_dropped() -> None:
    entries = [_entry("a" * 5000, "handoff"), _entry("b" * 300)]

    omitted = fit_to_budget(entries, 100)

    assert len(entries) == 1
    assert entries[0].truncated
    assert len(entries[0].content) <= 100
    assert entries[0].content.endswith("…")
    assert [memory.id for memory in omitted] == ["id-300-memory"]


def test_a_later_entry_is_cut_while_enough_room_is_left() -> None:
    entries = [_entry("a" * 500, "handoff"), _entry("b" * 1000)]

    fit_to_budget(entries, 500 + MIN_EXCERPT)

    assert entries[1].truncated
    assert sum(len(entry.content) for entry in entries) <= 500 + MIN_EXCERPT


def test_a_later_entry_is_dropped_when_only_a_stub_would_fit() -> None:
    entries = [_entry("a" * 500, "handoff"), _entry("b" * 1000)]

    omitted = fit_to_budget(entries, 500 + MIN_EXCERPT - 1)

    assert len(entries) == 1 and not entries[0].truncated
    assert len(omitted) == 1
