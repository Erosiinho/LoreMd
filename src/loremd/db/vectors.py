"""Converts embeddings to the literal pgvector expects."""

from __future__ import annotations

from collections.abc import Sequence


def to_literal(values: Sequence[float]) -> str:
    """`[0.1,0.2,…]` — passed as text, then cast with `::vector` in queries."""
    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"
