from __future__ import annotations

from pathlib import Path

import pytest

from loremd.config import load_settings
from loremd.errors import ConfigError


def test_defaults_are_enough_to_start() -> None:
    settings = load_settings(None)

    assert settings.search.rrf_k == 60
    assert settings.embeddings.provider == "fastembed"


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        "server:\n  port: 9100\nsearch:\n  min_similarity: 0.5\n", encoding="utf-8"
    )

    settings = load_settings(config)

    assert settings.server.port == 9100
    assert settings.search.min_similarity == 0.5
    assert settings.server.host == "0.0.0.0"


def test_environment_has_the_last_word(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("database:\n  dsn: postgresql://file/db\n", encoding="utf-8")
    monkeypatch.setenv("LOREMD_DATABASE__DSN", "postgresql://env/db")

    assert load_settings(config).database.dsn == "postgresql://env/db"


def test_missing_explicit_path_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "missing.yaml")


def test_an_empty_variable_does_not_erase_the_file(tmp_path: Path, monkeypatch) -> None:
    # docker-compose turns an unset `${VAR:-}` into an empty string.
    config = tmp_path / "config.yaml"
    config.write_text("sync:\n  webhook_secret: from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("LOREMD_SYNC__WEBHOOK_SECRET", "")

    assert load_settings(config).sync.webhook_secret == "from-the-file"
