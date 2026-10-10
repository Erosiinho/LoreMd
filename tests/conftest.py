"""Shared fixtures.

The tests in this suite touch neither Postgres nor the network: they cover the
building blocks that decide file contents and permissions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from loremd.core.models import Frontmatter, Memory, MemoryType


@pytest.fixture
def memory() -> Memory:
    now = datetime(2026, 10, 14, 10, 42, tzinfo=UTC)
    return Memory(
        frontmatter=Frontmatter(
            id="01JA8KZ3M9X2QH7R4T6VABCDEF",
            type=MemoryType.DECISION,
            title="API stack — Postgres + Drizzle",
            project="shop",
            tags=["backend", "db"],
            source="claude-code session",
            author="claude-code-macbook",
            created=now,
            updated=now,
        ),
        content="We use Postgres with Drizzle as the ORM for the shop API.",
        space="product",
        path="decisions/api-stack-postgres-drizzle-abcdef.md",
    )
