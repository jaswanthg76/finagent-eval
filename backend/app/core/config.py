from typing import Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://finagent:finagent@localhost:5432/finagent_eval"
    redis_url: str = "redis://localhost:6379/0"
    frontend_url: str = "http://localhost:5173"
    sec_user_agent: str = "FinAgentEval/0.1"
    openai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 512
    embedding_batch_size: int = 32

    @field_validator("embedding_model", mode="before")
    @classmethod
    def default_embedding_model(cls, value: Any) -> str:
        return str(value).strip() if value and str(value).strip() else "text-embedding-3-small"

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def empty_api_key_is_unconfigured(cls, value: Any) -> Any:
        return value if value and str(value).strip() else None


settings = Settings()
