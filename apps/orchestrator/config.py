"""Centralised settings loaded from environment."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    anthropic_api_key: str = ""
    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str = "sk-studio-local-master"
    model_tier_tactical: str = "haiku-4-5"
    model_tier_analysis: str = "sonnet-4-6"
    model_tier_strategic: str = "opus-4-7"

    # Budget
    daily_budget_usd: float = 3.0
    monthly_budget_usd: float = 100.0
    per_message_token_limit: int = 2000

    # Storage
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "studio"
    postgres_user: str = "studio"
    postgres_password: str = "studio-local-dev"
    redis_url: str = "redis://localhost:6379/0"

    # Tools
    tavily_api_key: str = ""

    # Observability
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # App
    log_level: str = "INFO"
    output_dir: Path = Path("./outputs")
    dry_run: bool = True

    # Language for narrative output (Discord posts, prose). Structured
    # JSON field names stay English. Supported: "en", "uk".
    studio_language: str = "en"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
