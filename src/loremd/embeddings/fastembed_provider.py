"""Embeddings locaux via fastembed (ONNX, CPU)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ..errors import ConfigError
from .base import EmbeddingProvider, normalize


class FastEmbedProvider(EmbeddingProvider):
    """Charge le modèle à la première utilisation, puis le réutilise."""

    def __init__(self, model: str, dimensions: int) -> None:
        self.model = model
        self.dimensions = dimensions
        self._engine = None
        self._load_lock = asyncio.Lock()

    async def _ensure_loaded(self):
        if self._engine is not None:
            return self._engine
        async with self._load_lock:
            if self._engine is None:
                try:
                    from fastembed import TextEmbedding
                except ImportError as exc:  # pragma: no cover - dépendance optionnelle
                    raise ConfigError(
                        "fastembed n'est pas installé : `pip install 'loremd[embeddings]'` "
                        "ou choisir un autre fournisseur d'embeddings"
                    ) from exc
                self._engine = await asyncio.to_thread(TextEmbedding, model_name=self.model)
        return self._engine

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        engine = await self._ensure_loaded()
        raw = await asyncio.to_thread(lambda: list(engine.embed(list(texts))))
        return [normalize([float(value) for value in vector]) for vector in raw]
