"""Identifiants ULID et slugs de fichiers.

Un ULID est trié par date de création, ce qui donne des noms de fichiers
stables et ordonnés sans dépendance externe.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(now_ms: int | None = None) -> str:
    """Retourne un ULID de 26 caractères (48 bits de temps + 80 bits aléatoires)."""
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp, 10) + _encode(randomness, 16)


def is_ulid(value: str) -> bool:
    return len(value) == 26 and all(c in _CROCKFORD for c in value.upper())


def slugify(text: str, max_length: int = 60) -> str:
    """Translittère un titre en slug ASCII utilisable comme nom de fichier."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_text).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0] or slug[:max_length]
    return slug or "memoire"


def memory_filename(title: str, memory_id: str, suffix_length: int = 6) -> str:
    """`slug-du-titre-01JA8K.md` : lisible pour l'humain, unique grâce à l'ID."""
    return f"{slugify(title)}-{memory_id[-suffix_length:].lower()}.md"
