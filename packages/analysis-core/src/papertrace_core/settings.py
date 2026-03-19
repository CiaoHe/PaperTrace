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
    enable_live_by_default: bool = Field(default=False, alias="ENABLE_LIVE_BY_DEFAULT")
    enable_live_paper_fetch: bool = Field(default=False, alias="ENABLE_LIVE_PAPER_FETCH")
    arxiv_api_base_url: str = Field(default="https://export.arxiv.org", alias="ARXIV_API_BASE_URL")
    arxiv_timeout_seconds: float = Field(default=15.0, alias="ARXIV_TIMEOUT_SECONDS")
    enable_live_repo_trace: bool = Field(default=False, alias="ENABLE_LIVE_REPO_TRACE")
    enable_live_repo_analysis: bool = Field(default=False, alias="ENABLE_LIVE_REPO_ANALYSIS")
    github_api_base_url: str = Field(
        default="https://api.github.com",
        alias="GITHUB_API_BASE_URL",
    )
    github_timeout_seconds: float = Field(default=15.0, alias="GITHUB_TIMEOUT_SECONDS")
    repo_clone_timeout_seconds: float = Field(default=45.0, alias="REPO_CLONE_TIMEOUT_SECONDS")
    repo_max_file_size_bytes: int = Field(default=200_000, alias="REPO_MAX_FILE_SIZE_BYTES")
    repo_max_files: int = Field(default=200, alias="REPO_MAX_FILES")
    repo_analysis_exclude_dirs: tuple[str, ...] = Field(
        default=("docs", "doc", "examples", "notebooks", "assets", ".github"),
        alias="REPO_ANALYSIS_EXCLUDE_DIRS",
    )
    repo_analysis_exclude_filenames: tuple[str, ...] = Field(
        default=(
            "readme.md",
            "license",
            "pnpm-lock.yaml",
            "package-lock.json",
            "yarn.lock",
            "poetry.lock",
            "uv.lock",
        ),
        alias="REPO_ANALYSIS_EXCLUDE_FILENAMES",
    )
    repo_analysis_include_dirs: tuple[str, ...] = Field(
        default=(),
        alias="REPO_ANALYSIS_INCLUDE_DIRS",
    )
    repo_analysis_extensions: tuple[str, ...] = Field(
        default=(
            ".py",
            ".pyi",
            ".md",
            ".txt",
            ".yaml",
            ".yml",
            ".json",
            ".toml",
            ".cu",
            ".cuh",
            ".cc",
            ".cpp",
            ".h",
            ".rs",
        ),
        alias="REPO_ANALYSIS_EXTENSIONS",
    )
    github_token: str | None = Field(default=None, alias="GITHUB_TOKEN")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_base_url: str | None = Field(default=None, alias="LLM_BASE_URL")
    llm_model: str | None = Field(default=None, alias="LLM_MODEL")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS")

    def use_live_paper_fetch(self) -> bool:
        return self.enable_live_paper_fetch or self.enable_live_by_default

    def use_live_repo_trace(self) -> bool:
        return self.enable_live_repo_trace or self.enable_live_by_default

    def use_live_repo_analysis(self) -> bool:
        return self.enable_live_repo_analysis or self.enable_live_by_default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
