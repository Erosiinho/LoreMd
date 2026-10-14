"""Embeddings through an OpenAI-compatible API (optional: text leaves the server)."""

from __future__ import annotations

from collections.abc import Sequence

from ..errors import ConfigError
from .base import EmbeddingProvider, normalize


class OpenAIProvider(EmbeddingProvider):
    def __init__(self, model: str, dimensions: int, api_base: str, api_key: str | None) -> None:
        if not api_key:
            raise ConfigError("embeddings.api_key is required when provider = openai")
        self.model = model
        self.dimensions = dimensions
        self.api_base = api_base.rstrip("/")
        self._api_key = api_key

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigError("httpx is required: `pip install 'loremd[openai]'`") from exc

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.api_base}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self.model, "input": list(texts)},
            )
            response.raise_for_status()
            payload = response.json()
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [normalize(item["embedding"]) for item in ordered]
