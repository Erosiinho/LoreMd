"""Choix du fournisseur d'embeddings depuis la configuration."""

from __future__ import annotations

from ..config import EmbeddingsConfig
from .base import EmbeddingProvider
from .hash_provider import HashEmbeddingProvider


def build_provider(config: EmbeddingsConfig) -> EmbeddingProvider:
    match config.provider:
        case "fastembed":
            from .fastembed_provider import FastEmbedProvider

            return FastEmbedProvider(config.model, config.dimensions)
        case "openai":
            from .openai_provider import OpenAIProvider

            return OpenAIProvider(
                config.model, config.dimensions, config.api_base, config.api_key
            )
        case "hash":
            return HashEmbeddingProvider(config.dimensions)
    raise ValueError(f"fournisseur d'embeddings inconnu : {config.provider}")
