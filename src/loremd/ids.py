"""ULID identifiers and file slugs.

A ULID sorts by creation time, which gives stable, ordered file names with
no external dependency.
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
    """Returns a 26-character ULID (48 bits of time + 80 random bits)."""
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp, 10) + _encode(randomness, 16)


def is_ulid(value: str) -> bool:
    return len(value) == 26 and all(c in _CROCKFORD for c in value.upper())


def slugify(text: str, max_length: int = 60) -> str:
    """Transliterates a title into an ASCII slug usable as a file name."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_text).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit("-", 1)[0] or slug[:max_length]
    return slug or "memory"


def memory_filename(title: str, memory_id: str, suffix_length: int = 6) -> str:
    """`title-slug-01ja8k.md`: readable for humans, unique thanks to the ID."""
    return f"{slugify(title)}-{memory_id[-suffix_length:].lower()}.md"
