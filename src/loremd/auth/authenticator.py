"""Resolves an Authorization header into a Principal."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import ip_address, ip_network

from ..db import Database
from ..db.repositories import TokenRepository
from ..errors import AuthError
from .principal import Principal, Scope
from .ratelimit import RateLimiter
from .tokens import parse_token, verify


def _ip_allowed(ip: str | None, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    if ip is None:
        return False
    try:
        candidate = ip_address(ip)
    except ValueError:
        return False
    return any(candidate in ip_network(entry, strict=False) for entry in allowlist)


def _scopes(values: list[str] | None) -> frozenset[Scope]:
    """Known scopes only: an unknown string in the row must not break the call."""
    known = set()
    for value in values or []:
        try:
            known.add(Scope(value))
        except ValueError:
            continue
    return frozenset(known)


class Authenticator:
    """Checks the tokens presented by agents."""

    def __init__(
        self,
        database: Database,
        bootstrap_token: str | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._db = database
        self._bootstrap_token = bootstrap_token
        self._limiter = limiter

    def _limit(self, key: str) -> None:
        """Rate limit, applied only once the token is known to be valid.

        Checking after verification means an attacker cannot fill the bucket
        table with made-up prefixes.
        """
        if self._limiter is not None:
            self._limiter.check(key)

    async def authenticate(self, raw_token: str | None, *, ip: str | None = None) -> Principal:
        if not raw_token:
            raise AuthError("missing token: use the Authorization: Bearer header")

        if self._bootstrap_token and raw_token == self._bootstrap_token:
            # Phase 0: full access, with no database row.
            self._limit("bootstrap")
            return Principal(
                name="bootstrap",
                scopes=frozenset({Scope.READ, Scope.WRITE}),
                space_ids=None,
                token_prefix="bootstrap",
                ip=ip,
            )

        parts = parse_token(raw_token)
        if parts is None:
            raise AuthError("invalid token format")

        async with self._db.acquire() as connection:
            row = await TokenRepository(connection).get_by_prefix(parts.prefix)
            if row is None or not verify(raw_token, row["hash"]):
                raise AuthError("unknown token")
            if row["revoked_at"] is not None:
                raise AuthError("token revoked")
            if row["agent_disabled_at"] is not None:
                raise AuthError("agent disabled")
            expires_at = row["expires_at"]
            if expires_at is not None and expires_at <= datetime.now(UTC):
                raise AuthError("token expired")
            if not _ip_allowed(ip, list(row["ip_allowlist"] or [])):
                raise AuthError("IP address not allowed for this token")
            self._limit(parts.prefix)
            await TokenRepository(connection).touch(row["id"])

        return Principal(
            name=row["agent_name"],
            scopes=_scopes(list(row["scopes"] or [])),
            space_ids=frozenset(row["space_ids"] or []),
            token_prefix=parts.prefix,
            ip=ip,
        )
