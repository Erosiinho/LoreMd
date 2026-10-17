from __future__ import annotations

from uuid import uuid4

import pytest

from loremd.auth.principal import Principal, Scope
from loremd.errors import ForbiddenError

PRODUIT = uuid4()
SUPPORT = uuid4()


def lecteur() -> Principal:
    return Principal(
        name="codex-ci", scopes=frozenset({Scope.READ}), space_ids=frozenset({PRODUIT})
    )


def test_un_token_lecture_ne_peut_pas_ecrire() -> None:
    with pytest.raises(ForbiddenError):
        lecteur().require_scope(Scope.WRITE)


def test_un_token_ne_voit_que_ses_espaces() -> None:
    principal = lecteur()

    assert principal.can_access(PRODUIT)
    assert not principal.can_access(SUPPORT)
    with pytest.raises(ForbiddenError):
        principal.require_space(SUPPORT, "support")


def test_le_scope_admin_implique_lecture_et_ecriture() -> None:
    principal = Principal(name="admin", scopes=frozenset({Scope.ADMIN}), space_ids=None)

    principal.require_scope(Scope.READ)
    principal.require_scope(Scope.WRITE)


def test_space_ids_none_signifie_tous_les_espaces() -> None:
    principal = Principal.admin()

    assert principal.filter_space_ids([PRODUIT, SUPPORT]) == [PRODUIT, SUPPORT]


def test_le_filtrage_retire_les_espaces_interdits() -> None:
    assert lecteur().filter_space_ids([PRODUIT, SUPPORT]) == [PRODUIT]
