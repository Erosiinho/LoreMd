"""Chargement de la configuration.

Ordre de précédence : variables d'environnement > config.yaml > valeurs par
défaut. Les variables suivent `LOREMD_<SECTION>__<CLE>`, par exemple
`LOREMD_DATABASE__DSN`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from .errors import ConfigError

DEFAULT_CONFIG_PATHS = (Path("config.yaml"), Path("/etc/loremd/config.yaml"))


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    public_url: str = "http://localhost:8000"


class DatabaseConfig(BaseModel):
    dsn: str = "postgresql://loremd:loremd@localhost:5432/loremd"
    pool_min_size: int = 1
    pool_max_size: int = 10


class SpacesConfig(BaseModel):
    root: Path = Path("/data/spaces")
    default_branch: str = "main"


class EmbeddingsConfig(BaseModel):
    provider: Literal["fastembed", "openai", "hash"] = "fastembed"
    model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    dimensions: int = 384
    api_base: str = "https://api.openai.com/v1"
    api_key: str | None = None


class SearchConfig(BaseModel):
    default_limit: int = 8
    max_limit: int = 50
    rrf_k: int = 60
    min_similarity: float = 0.4


class WriteConfig(BaseModel):
    max_bytes: int = 32_768
    dedup_threshold: float = 0.93
    detect_secrets: bool = True


class GitConfig(BaseModel):
    author_email_domain: str = "agents.loremd.local"
    auto_push: bool = False


class AuditConfig(BaseModel):
    enabled: bool = True


class Settings(BaseSettings):
    """Configuration complète de l'instance."""

    model_config = SettingsConfigDict(
        env_prefix="LOREMD_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    spaces: SpacesConfig = Field(default_factory=SpacesConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    write: WriteConfig = Field(default_factory=WriteConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    # Phase 0 : un unique token en variable d'environnement, accès total.
    bootstrap_token: str | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # L'environnement passe avant le YAML : en conteneur, on surcharge sans
        # reconstruire le fichier de configuration.
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config.yaml illisible : {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} ne contient pas un mapping YAML")
    return raw


def load_settings(config_path: Path | None = None) -> Settings:
    """Charge la configuration depuis un YAML explicite, l'env, ou les défauts."""
    candidates = [config_path] if config_path else list(DEFAULT_CONFIG_PATHS)
    env_path = os.environ.get("LOREMD_CONFIG")
    if config_path is None and env_path:
        candidates.insert(0, Path(env_path))

    for candidate in candidates:
        if candidate and candidate.exists():
            return Settings(**_read_yaml(candidate))
    if config_path is not None:
        raise ConfigError(f"fichier de configuration introuvable : {config_path}")
    return Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuration partagée par le processus."""
    return load_settings()
