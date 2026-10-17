from __future__ import annotations

from pathlib import Path

import pytest

from loremd.config import load_settings
from loremd.errors import ConfigError


def test_les_valeurs_par_defaut_suffisent_a_demarrer() -> None:
    settings = load_settings(None)

    assert settings.search.rrf_k == 60
    assert settings.embeddings.provider == "fastembed"


def test_le_yaml_surcharge_les_defauts(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  port: 9100\nsearch:\n  min_score: 0.5\n", encoding="utf-8"
    )

    settings = load_settings(config)

    assert settings.server.port == 9100
    assert settings.search.min_score == 0.5
    assert settings.server.host == "0.0.0.0"


def test_l_environnement_a_le_dernier_mot(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("database:\n  dsn: postgresql://fichier/db\n", encoding="utf-8")
    monkeypatch.setenv("LOREMD_DATABASE__DSN", "postgresql://env/db")

    assert load_settings(config).database.dsn == "postgresql://env/db"


def test_un_chemin_explicite_absent_est_une_erreur(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "absent.yaml")
