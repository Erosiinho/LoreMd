from __future__ import annotations

from loremd.embeddings.base import normalize
from loremd.embeddings.hash_provider import HashEmbeddingProvider


async def test_le_fournisseur_de_secours_est_deterministe() -> None:
    provider = HashEmbeddingProvider(dimensions=64)

    premier, second = await provider.embed(["mémoire partagée", "mémoire partagée"])

    assert premier == second
    assert len(premier) == 64


async def test_les_vecteurs_sont_normalises() -> None:
    provider = HashEmbeddingProvider(dimensions=32)

    (vector,) = await provider.embed(["Postgres et Drizzle"])

    assert abs(sum(value * value for value in vector) - 1.0) < 1e-9


def test_normalize_supporte_le_vecteur_nul() -> None:
    assert normalize([0.0, 0.0]) == [0.0, 0.0]
