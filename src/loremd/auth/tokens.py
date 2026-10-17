"""Fabrication et vérification des tokens.

Format : `lmd_<prefix>_<secret>`. Seul le hachage SHA-256 du token complet est
stocké ; le préfixe sert à retrouver la ligne sans révéler le secret.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import string
from dataclasses import dataclass

PREFIX_ALPHABET = string.ascii_lowercase + string.digits
PREFIX_LENGTH = 8
SECRET_BYTES = 32
TOKEN_SCHEME = "lmd"


@dataclass(frozen=True)
class TokenParts:
    prefix: str
    secret: str

    @property
    def raw(self) -> str:
        return f"{TOKEN_SCHEME}_{self.prefix}_{self.secret}"


def generate_token() -> tuple[TokenParts, str]:
    """Retourne le token en clair (à afficher une seule fois) et son hachage."""
    prefix = "".join(secrets.choice(PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))
    secret = secrets.token_urlsafe(SECRET_BYTES)
    parts = TokenParts(prefix=prefix, secret=secret)
    return parts, hash_token(parts.raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_token(raw: str) -> TokenParts | None:
    """Découpe un token présenté. Retourne None si le format ne colle pas."""
    pieces = raw.strip().split("_", 2)
    if len(pieces) != 3:
        return None
    scheme, prefix, secret = pieces
    if scheme != TOKEN_SCHEME or not prefix or not secret:
        return None
    return TokenParts(prefix=prefix, secret=secret)


def verify(raw: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(raw), expected_hash)
