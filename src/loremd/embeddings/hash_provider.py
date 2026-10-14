"""Deterministic hash-based embeddings.

No semantic quality at all: used for tests and cold starts without a model
download. Search stays usable thanks to full-text matching.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from .base import EmbeddingProvider, normalize

_WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dimensions: int = 384) -> None:
        self.model = "hash"
        self.dimensions = dimensions

    def _embed_sync(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return normalize(vector)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_sync(text) for text in texts]
