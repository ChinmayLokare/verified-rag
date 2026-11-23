"""Runtime configuration, read from the environment (and a local .env file in development).

Secrets never live in the repo. In AWS they come from SSM Parameter Store into the environment.
Pack-specific settings (for example the SEC User-Agent) are read by the pack itself.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    # --- answer model (ADR-7: cheapest model that meets the correctness target) ---------------
    llm_provider: Literal["ollama", "anthropic"] = "ollama"
    llm_model: str = "qwen2.5:3b"  # fits a 4 GB GPU fully; see docs/adr/0010
    ollama_base_url: str = "http://127.0.0.1:11434"
    # Sampling parameters are optional: newer Claude models reject some of them. Leave None to
    # use the provider default.
    llm_temperature: float | None = 0.0
    llm_reasoning: bool | None = Field(
        default=False, description="Ollama 'thinking' mode; slower on small GPUs"
    )
    llm_num_ctx: int = Field(default=4096, ge=2048, description="Ollama context window")
    llm_max_tokens: int = Field(default=2048, ge=256)
    # Prices for the cost cap, USD per million tokens. Zero for local models.
    llm_input_usd_per_mtok: float = 0.0
    llm_output_usd_per_mtok: float = 0.0

    # --- agent limits (design.md 8.2) ----------------------------------------------------------
    agent_max_model_calls: int = Field(default=12, ge=2, le=50)
    agent_timeout_s: float = Field(default=300.0, gt=0)
    agent_max_cost_usd: float = Field(default=0.05, ge=0)
    agent_max_retries: int = Field(default=1, ge=0, le=3)


@lru_cache
def get_settings() -> Settings:
    return Settings()
