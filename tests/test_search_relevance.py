"""The `no_match` rule: a weak result is worse than no result."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from loremd.core.models import IndexedMemory, MemoryStatus, MemoryType, SearchHit
from loremd.core.search import relevant_hits

SPACE = uuid4()


def hit(*, rank_text: int | None, rank_vector: int | None, similarity: float | None) -> SearchHit:
    now = datetime(2026, 10, 14, tzinfo=UTC)
    memory = IndexedMemory(
        id="01JA8KZ3M9X2QH7R4T6VABCDEF",
        space_id=SPACE,
        space="product",
        path="decisions/a.md",
        type=MemoryType.DECISION,
        title="API stack",
        project="shop",
        tags=[],
        status=MemoryStatus.ACTIVE,
        verified=False,
        author="agent",
        source=None,
        content="We use Postgres.",
        created=now,
        updated=now,
    )
    return SearchHit(
        memory=memory,
        score=0.5,
        rank_text=rank_text,
        rank_vector=rank_vector,
        similarity=similarity,
    )


def test_keyword_hit_is_always_kept() -> None:
    # A full-text match is an absolute signal: the words are in the text.
    weak = hit(rank_text=1, rank_vector=None, similarity=None)

    assert relevant_hits([weak], 0.9) == [weak]


def test_semantic_hit_below_the_threshold_is_dropped() -> None:
    far = hit(rank_text=None, rank_vector=1, similarity=0.21)

    assert relevant_hits([far], 0.4) == []


def test_semantic_hit_above_the_threshold_is_kept() -> None:
    close = hit(rank_text=None, rank_vector=1, similarity=0.55)

    assert relevant_hits([close], 0.4) == [close]


def test_threshold_is_inclusive() -> None:
    exact = hit(rank_text=None, rank_vector=1, similarity=0.4)

    assert relevant_hits([exact], 0.4) == [exact]


def test_order_is_preserved() -> None:
    first = hit(rank_text=1, rank_vector=None, similarity=None)
    dropped = hit(rank_text=None, rank_vector=1, similarity=0.1)
    last = hit(rank_text=None, rank_vector=2, similarity=0.8)

    assert relevant_hits([first, dropped, last], 0.4) == [first, last]


def test_nothing_relevant_yields_nothing() -> None:
    assert relevant_hits([], 0.4) == []
