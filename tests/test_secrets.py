from __future__ import annotations

import pytest

from loremd.core import secrets


@pytest.mark.parametrize(
    "content",
    [
        "La clé est AKIAIOSFODNN7EXAMPLE, à ne pas committer.",
        "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "token: ghp_0123456789abcdefghijklmnopqrstuvwxyzAB",
        "connexion: postgres://user:sup3rS3cretPwd@db.acme.com:5432/app",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_detecte_les_secrets_evidents(content: str) -> None:
    assert secrets.scan(content)


@pytest.mark.parametrize(
    "content",
    [
        "On utilise Postgres avec Drizzle comme ORM.",
        "api_key = <votre-clé>",
        "password: change-me",
        "La variable OPENAI_API_KEY est lue depuis l'environnement.",
        "Le DSN de dev est postgres://loremd@localhost:5432/loremd",
    ],
)
def test_ne_bloque_pas_le_texte_ordinaire(content: str) -> None:
    assert secrets.scan(content) == []


def test_le_secret_n_est_jamais_recopie_en_clair() -> None:
    findings = secrets.scan("AKIAIOSFODNN7EXAMPLE")

    assert "AKIAIOSFODNN7EXAMPLE" not in str(findings[0])
