"""Configuration loading.

Precedence: environment variables > config.yaml > defaults. Variables follow
`LOREMD_<SECTION>__<KEY>`, for example `LOREMD_DATABASE__DSN`.
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


class LimitsConfig(BaseModel):
    """Per-token rate limit. 0 disables it."""

    requests_per_minute: int = 120
    burst: int = 40


class Settings(BaseSettings):
    """Full instance configuration."""

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
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # Phase 0: a single token from an environment variable, full access.
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
        # The environment wins over YAML: in a container, values can be overridden
        # without rebuilding the configuration file.
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"unreadable config.yaml: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} does not contain a YAML mapping")
    return raw


def load_settings(config_path: Path | None = None) -> Settings:
    """Loads configuration from an explicit YAML file, the environment, or defaults."""
    candidates = [config_path] if config_path else list(DEFAULT_CONFIG_PATHS)
    env_path = os.environ.get("LOREMD_CONFIG")
    if config_path is None and env_path:
        candidates.insert(0, Path(env_path))

    for candidate in candidates:
        if candidate and candidate.exists():
            return Settings(**_read_yaml(candidate))
    if config_path is not None:
        raise ConfigError(f"configuration file not found: {config_path}")
    return Settings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide configuration."""
    return load_settings()
