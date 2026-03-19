from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    web_port: int = Field(default=3000, alias="WEB_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://papertrace:papertrace@127.0.0.1:5432/papertrace",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0", alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://127.0.0.1:6379/0",
        alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://127.0.0.1:6379/1",
        alias="CELERY_RESULT_BACKEND",
    )
    celery_task_always_eager: bool = Field(default=False, alias="CELERY_TASK_ALWAYS_EAGER")
    next_public_api_base_url: str = Field(
        default="http://127.0.0.1:8000",
        alias="NEXT_PUBLIC_API_BASE_URL",
    )
    local_cache_dir: Path = Field(default=Path(".cache"), alias="LOCAL_CACHE_DIR")
    local_data_dir: Path = Field(default=Path(".local"), alias="LOCAL_DATA_DIR")
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_model: str | None = Field(default=None, alias="LLM_MODEL")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
