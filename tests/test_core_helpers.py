from __future__ import annotations

import pytest

from loremd.core.indexer import is_memory_path
from loremd.core.memories import author_email, derive_title
from loremd.core.models import MemoryType
from loremd.core.spaces import validate_slug
from loremd.errors import ValidationError


def test_le_titre_se_deduit_de_la_premiere_phrase() -> None:
    titre = derive_title("On utilise Postgres. Raison : typage de bout en bout.")

    assert titre == "On utilise Postgres."


def test_le_titre_ignore_le_balisage_markdown() -> None:
    assert derive_title("# Stack API\n\nDétails") == "Stack API"


def test_un_contenu_vide_na_pas_de_titre() -> None:
    with pytest.raises(ValidationError):
        derive_title("   \n\n  ")


def test_l_email_d_auteur_est_toujours_valide() -> None:
    email = author_email("Claude Code (MacBook)", "agents.local")

    assert email == "claude-code-macbook@agents.local"
    assert author_email("", "agents.local") == "agent@agents.local"


def test_chaque_type_a_son_dossier() -> None:
    assert MemoryType.DECISION.directory == "decisions"
    assert {t.directory for t in MemoryType} == {
        "decisions",
        "preferences",
        "rules",
        "facts",
        "notes",
    }


@pytest.mark.parametrize(
    "path,attendu",
    [
        ("decisions/stack-api-abcdef.md", True),
        ("notes/sous-dossier/note.md", True),
        ("README.md", False),
        (".loremd/space.yaml", False),
        ("decisions/brouillon.txt", False),
    ],
)
def test_seuls_les_fichiers_de_memoire_sont_indexes(path: str, attendu: bool) -> None:
    assert is_memory_path(path) is attendu


def test_les_slugs_d_espace_sont_contraints() -> None:
    assert validate_slug("produit-interne") == "produit-interne"
    for invalide in ("Produit", "produit/sous", "-produit", "pro duit", ""):
        with pytest.raises(ValidationError):
            validate_slug(invalide)
