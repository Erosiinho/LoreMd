"""Password hashing for console admins, with scrypt from the standard library.

Format: `scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>`. The parameters travel with
the hash, so they can be raised later without invalidating existing accounts.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1
SALT_BYTES = 16
KEY_BYTES = 32
MIN_LENGTH = 12

#: Verified against when the email is unknown, so timing does not reveal accounts.
_DUMMY = "scrypt$16384$8$1$" + "00" * SALT_BYTES + "$" + "00" * KEY_BYTES


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=2**26
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${salt.hex()}${_derive(password, salt, N, R, P).hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, expected = (stored or _DUMMY).split("$")
        if scheme != "scrypt":
            return False
        actual = _derive(password, bytes.fromhex(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return stored is not None and hmac.compare_digest(actual.hex(), expected)


def check_strength(password: str) -> str | None:
    """A reason to refuse the password, or None."""
    if len(password) < MIN_LENGTH:
        return f"the password needs at least {MIN_LENGTH} characters"
    if len(set(password)) < 5:
        return "the password is too repetitive"
    return None
