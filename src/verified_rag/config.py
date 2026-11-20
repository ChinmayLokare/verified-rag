"""Runtime configuration, read from the environment (and a local .env file in development).

Secrets never live in the repo. In AWS they come from SSM Parameter Store into the environment.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql://vrag:vrag@localhost:5433/vrag",
        description="Postgres connection URL. The default matches docker-compose.yml.",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
