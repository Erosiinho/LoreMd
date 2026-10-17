"""Fixtures partagées.

Les tests de cette suite ne touchent ni Postgres ni le réseau : ils portent sur
les briques qui décident du contenu des fichiers et des droits.
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
            title="Stack API — Postgres + Drizzle",
            project="shop",
            tags=["backend", "db"],
            source="session claude-code",
            author="claude-code-macbook",
            created=now,
            updated=now,
        ),
        content="On utilise Postgres avec Drizzle comme ORM pour l'API du shop.",
        space="produit",
        path="decisions/stack-api-postgres-drizzle-abcdef.md",
    )
