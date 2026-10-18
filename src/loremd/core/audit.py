"""Audit log.

Every access leaves a trace. The log is append-only in Postgres; a failure to
write it must never fail the business operation.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from ..auth.principal import Principal
from ..config import Settings
from ..db import Database
from ..db.repositories import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._db = database
        self._enabled = settings.audit.enabled

    async def record(
        self,
        principal: Principal,
        action: str,
        *,
        target: str | None = None,
        space_id: UUID | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        if not self._enabled:
            return
        payload = dict(meta or {})
        if principal.token_prefix:
            payload.setdefault("token_prefix", principal.token_prefix)
        try:
            async with self._db.acquire() as connection:
                await AuditRepository(connection).record(
                    actor=principal.name,
                    action=action,
                    target=target,
                    space_id=space_id,
                    ip=principal.ip,
                    meta=payload,
                )
        except Exception:  # noqa: BLE001 - auditing must not break the call
            logger.exception("could not write audit log entry (action=%s)", action)
