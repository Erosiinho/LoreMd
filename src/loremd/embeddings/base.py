"""Interface commune aux fournisseurs d'embeddings."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """Transforme du texte en vecteurs normalisés."""

    #: Nombre de composantes du vecteur. Doit correspondre au schéma Postgres.
    dimensions: int
    #: Identifiant du modèle, journalisé pour tracer les réindexations.
    model: str

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectorise un lot de textes, dans l'ordre reçu."""

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]


def normalize(vector: Sequence[float]) -> list[float]:
    """Norme L2 à 1, pour que la distance cosinus soit comparable d'un modèle à l'autre."""
    total = sum(value * value for value in vector) ** 0.5
    if total == 0:
        return list(vector)
    return [value / total for value in vector]
