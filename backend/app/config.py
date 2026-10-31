from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://support:local-development-only@localhost:5432/support"
    )
    app_env: Literal["development", "production"] = "development"
    cookie_secure: bool = False
    allowed_origins: list[str] = [
        "http://localhost:58000",
        "http://localhost:8000",
        "http://localhost:5173",
    ]
    session_hours: int = Field(default=8, ge=1, le=24)
    agent_provider: Literal["openai", "demo"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    agent_timeout_seconds: float = Field(default=30, ge=1, le=30)
    provider_timeout_seconds: float = Field(default=6, ge=0.1, le=8)
    model_input_token_limit: int = Field(default=4096, ge=256, le=16000)
    model_output_token_limit: int = Field(default=256, ge=64, le=2048)
    model_input_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    model_cached_input_usd_per_million: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    model_output_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    embedding_usd_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provider_retries: int = Field(default=2, ge=0, le=2)
    embedding_provider: Literal["demo", "openai"] = "demo"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=256, ge=64, le=3072)
    chunk_tokens: int = Field(default=350, ge=100, le=1000)
    chunk_overlap: int = Field(default=30, ge=0, le=50)
    evidence_coverage: float = Field(default=0.7, ge=0.7, le=1)
    run_attempts: int = Field(default=3, ge=1, le=3)
    conversation_max_turns: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def production_settings(self) -> Settings:
        if self.app_env == "production":
            if (
                not self.cookie_secure
                or self.agent_provider == "demo"
                or self.embedding_provider == "demo"
            ):
                raise ValueError("Production requires secure cookies and the OpenAI provider")
            if not self.allowed_origins or any(
                not origin.startswith("https://") for origin in self.allowed_origins
            ):
                raise ValueError("Production requires explicit HTTPS origins")
        return self
