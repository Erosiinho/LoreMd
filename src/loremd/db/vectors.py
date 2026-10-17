"""Conversion des embeddings vers le littéral attendu par pgvector."""

from __future__ import annotations

from collections.abc import Sequence


def to_literal(values: Sequence[float]) -> str:
    """`[0.1,0.2,…]` — passé en texte puis casté `::vector` dans les requêtes."""
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"
