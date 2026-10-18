"""Authenticated identity of a caller and what it is allowed to do."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ..errors import ForbiddenError


class Scope(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


@dataclass(frozen=True)
class Principal:
    """An agent (or the administrator) behind a request.

    `space_ids = None` means "all spaces": reserved for the bootstrap token and
    for administrative calls from the CLI.
    """

    name: str
    scopes: frozenset[Scope]
    space_ids: frozenset[UUID] | None
    token_prefix: str | None = None
    ip: str | None = None

    @classmethod
    def admin(cls, name: str = "cli") -> Principal:
        return cls(name=name, scopes=frozenset(Scope), space_ids=None)

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.scopes or Scope.ADMIN in self.scopes

    def require_scope(self, scope: Scope) -> None:
        if not self.has_scope(scope):
            raise ForbiddenError(f"token lacks the {scope.value} scope")

    def can_access(self, space_id: UUID) -> bool:
        return self.space_ids is None or space_id in self.space_ids

    def require_space(self, space_id: UUID, slug: str) -> None:
        if not self.can_access(space_id):
            raise ForbiddenError(f"space not allowed for this token: {slug}")

    def filter_space_ids(self, available: list[UUID]) -> list[UUID]:
        if self.space_ids is None:
            return available
        return [space_id for space_id in available if space_id in self.space_ids]
