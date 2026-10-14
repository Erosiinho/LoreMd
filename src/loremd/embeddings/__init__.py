"""Embedding providers.

Defaults to fastembed, running locally: no key to provide, and memory text
never leaves the server.
"""

from .base import EmbeddingProvider
from .factory import build_provider

__all__ = ["EmbeddingProvider", "build_provider"]
