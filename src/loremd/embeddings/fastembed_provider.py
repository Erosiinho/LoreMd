"""Local embeddings with fastembed (ONNX, CPU)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from ..errors import ConfigError
from .base import EmbeddingProvider, normalize


class FastEmbedProvider(EmbeddingProvider):
    """Loads the model on first use, then reuses it."""

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
                except ImportError as exc:  # pragma: no cover - optional dependency
                    raise ConfigError(
                        "fastembed is not installed: `pip install 'loremd[embeddings]'` "
                        "or pick another embedding provider"
                    ) from exc
                self._engine = await asyncio.to_thread(TextEmbedding, model_name=self.model)
        return self._engine

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        engine = await self._ensure_loaded()
        raw = await asyncio.to_thread(lambda: list(engine.embed(list(texts))))
        return [normalize([float(value) for value in vector]) for vector in raw]
