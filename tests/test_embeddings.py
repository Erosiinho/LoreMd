from __future__ import annotations

from loremd.embeddings.base import normalize
from loremd.embeddings.hash_provider import HashEmbeddingProvider


async def test_fallback_provider_is_deterministic() -> None:
    provider = HashEmbeddingProvider(dimensions=64)

    first, second = await provider.embed(["shared memory", "shared memory"])

    assert first == second
    assert len(first) == 64


async def test_vectors_are_normalized() -> None:
    provider = HashEmbeddingProvider(dimensions=32)

    (vector,) = await provider.embed(["Postgres and Drizzle"])

    assert abs(sum(value * value for value in vector) - 1.0) < 1e-9


def test_normalize_handles_the_zero_vector() -> None:
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
