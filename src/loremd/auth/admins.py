"""Console admins: accounts, login with password + TOTP, server-side sessions.

Agents never come through here: they use tokens. A human logs in with a
password and a code from their authenticator app, and gets a session whose
cookie holds a random value; only its hash is stored.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..config import Settings
from ..db import Database
from ..db.repositories import AdminRepository, AdminSessionRepository
from ..errors import AuthError, ConflictError, ValidationError
from . import passwords, totp
from .principal import Principal
from .ratelimit import RateLimiter

SESSION_BYTES = 32


class Role(StrEnum):
    #: Everything the console offers, audit log included.
    ADMIN = "admin"
    #: The review queue and conflicts only.
    REVIEWER = "reviewer"


@dataclass(frozen=True)
class AdminSession:
    admin_id: Any
    email: str
    role: Role
    csrf_token: str

    def principal(self, ip: str | None = None) -> Principal:
        """Console calls run with full rights, and commits are signed with the admin's email."""
        return Principal.admin(self.email, email=self.email, ip=ip)

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValidationError(f"invalid email address: {email!r}")
    return email


def _session_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AdminService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._session_lifetime = timedelta(hours=settings.console.session_hours)
        attempts = settings.console.login_attempts_per_minute
        self._limiter = RateLimiter(requests_per_minute=attempts, burst=attempts)

    # -- accounts ------------------------------------------------------------

    async def create(
        self, email: str, password: str, role: Role = Role.ADMIN
    ) -> tuple[str, str]:
        """Creates an admin. Returns the email and the TOTP secret, to show once."""
        email = normalize_email(email)
        problem = passwords.check_strength(password)
        if problem:
            raise ValidationError(problem)
        secret = totp.generate_secret()
        async with self._db.acquire() as connection:
            repository = AdminRepository(connection)
            if await repository.get_by_email(email) is not None:
                raise ConflictError(f"an admin already exists with this email: {email}")
            await repository.create(email, passwords.hash_password(password), secret, role.value)
        return email, secret

    async def set_password(self, email: str, password: str) -> None:
        problem = passwords.check_strength(password)
        if problem:
            raise ValidationError(problem)
        async with self._db.acquire() as connection:
            if not await AdminRepository(connection).set_password(
                normalize_email(email), passwords.hash_password(password)
            ):
                raise AuthError(f"unknown admin: {email}")

    async def reset_totp(self, email: str) -> str:
        secret = totp.generate_secret()
        async with self._db.acquire() as connection:
            if not await AdminRepository(connection).set_totp_secret(
                normalize_email(email), secret
            ):
                raise AuthError(f"unknown admin: {email}")
        return secret

    async def set_disabled(self, email: str, disabled: bool) -> None:
        """Disabling an admin also ends their open sessions."""
        async with self._db.acquire() as connection:
            if not await AdminRepository(connection).set_disabled(
                normalize_email(email), disabled
            ):
                raise AuthError(f"unknown admin: {email}")

    # -- sessions ------------------------------------------------------------

    async def login(
        self, email: str, password: str, code: str, *, ip: str | None = None
    ) -> tuple[str, AdminSession]:
        """Checks password and TOTP. Returns the cookie value and the session.

        Every failure gives the same message: the form must not tell an
        attacker which of the three fields was wrong.
        """
        self._limiter.check(f"ip:{ip or 'unknown'}")
        email = email.strip().lower()
        self._limiter.check(f"email:{email}")
        failure = AuthError("invalid email, password or code")

        async with self._db.acquire() as connection:
            admins = AdminRepository(connection)
            row = await admins.get_by_email(email)
            # Hash even for an unknown email: the response time stays the same.
            password_ok = passwords.verify_password(
                password, row["password_hash"] if row else None
            )
            if row is None or not password_ok or row["disabled_at"] is not None:
                raise failure
            step = totp.match(row["totp_secret"], code)
            if step is None or not await admins.accept_totp_step(row["id"], step):
                raise failure

            raw = secrets.token_urlsafe(SESSION_BYTES)
            session = AdminSession(
                admin_id=row["id"],
                email=row["email"],
                role=Role(row["role"]),
                csrf_token=secrets.token_urlsafe(24),
            )
            sessions = AdminSessionRepository(connection)
            await sessions.purge_expired()
            await sessions.create(
                _session_hash(raw),
                row["id"],
                session.csrf_token,
                ip=ip,
                expires_at=datetime.now(UTC) + self._session_lifetime,
            )
        return raw, session

    async def session(self, raw: str | None) -> AdminSession | None:
        if not raw:
            return None
        async with self._db.acquire() as connection:
            row = await AdminSessionRepository(connection).get(_session_hash(raw))
        if row is None:
            return None
        return AdminSession(
            admin_id=row["admin_id"],
            email=row["email"],
            role=Role(row["role"]),
            csrf_token=row["csrf_token"],
        )

    async def logout(self, raw: str | None) -> None:
        if raw:
            async with self._db.acquire() as connection:
                await AdminSessionRepository(connection).delete(_session_hash(raw))
