"""Runtime configuration, read from the environment (and a local .env file in development).

Secrets never live in the repo. In AWS they come from SSM Parameter Store into the environment.
Pack-specific settings (for example the SEC User-Agent) are read by the pack itself.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://vrag:vrag@localhost:5433/vrag",
        description="Postgres connection URL. The default matches docker-compose.yml.",
    )
    packs_dir: Path = Field(default=Path("packs"), description="Folder holding domain packs.")
    raw_dir: Path = Field(
        default=Path("data/raw"), description="Raw document store for local development."
    )
    query_timeout_ms: int = Field(default=3000, ge=100, le=60_000)
    query_max_rows: int = Field(default=500, ge=1, le=10_000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
