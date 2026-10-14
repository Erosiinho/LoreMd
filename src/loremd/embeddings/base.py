"""Common interface for embedding providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """Turns text into normalized vectors."""

    #: Number of vector components. Must match the Postgres schema.
    dimensions: int
    #: Model identifier, logged to keep track of reindexing.
    model: str

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embeds a batch of texts, in the order received."""

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]


def normalize(vector: Sequence[float]) -> list[float]:
    """Scales to unit L2 norm so cosine distance is comparable across models."""
    total = sum(value * value for value in vector) ** 0.5
    if total == 0:
        return list(vector)
    return [value / total for value in vector]
