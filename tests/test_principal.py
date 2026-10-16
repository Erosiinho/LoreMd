from __future__ import annotations

from uuid import uuid4

import pytest

from loremd.auth.principal import Principal, Scope
from loremd.errors import ForbiddenError

PRODUCT = uuid4()
SUPPORT = uuid4()


def reader() -> Principal:
    return Principal(
        name="codex-ci", scopes=frozenset({Scope.READ}), space_ids=frozenset({PRODUCT})
    )


def test_read_token_cannot_write() -> None:
    with pytest.raises(ForbiddenError):
        reader().require_scope(Scope.WRITE)


def test_token_only_sees_its_spaces() -> None:
    principal = reader()

    assert principal.can_access(PRODUCT)
    assert not principal.can_access(SUPPORT)
    with pytest.raises(ForbiddenError):
        principal.require_space(SUPPORT, "support")


def test_admin_scope_implies_read_and_write() -> None:
    principal = Principal(name="admin", scopes=frozenset({Scope.ADMIN}), space_ids=None)

    principal.require_scope(Scope.READ)
    principal.require_scope(Scope.WRITE)


def test_space_ids_none_means_all_spaces() -> None:
    principal = Principal.admin()

    assert principal.filter_space_ids([PRODUCT, SUPPORT]) == [PRODUCT, SUPPORT]


def test_filtering_removes_forbidden_spaces() -> None:
    assert reader().filter_space_ids([PRODUCT, SUPPORT]) == [PRODUCT]
