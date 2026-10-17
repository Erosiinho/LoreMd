"""Fournisseurs d'embeddings.

Par défaut fastembed, en local : aucune clé à fournir et les textes de mémoire
ne quittent pas le serveur.
"""

from .base import EmbeddingProvider
from .factory import build_provider

__all__ = ["EmbeddingProvider", "build_provider"]
