from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACCSTRACE_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    app_name: str = "ACCS-Trace"
    environment: str = "development"
    data_dir: Path = Field(default=PROJECT_ROOT / "data")
    database_url: str | None = None
    frontend_origin: str = "http://127.0.0.1:5173"
    max_upload_bytes: int = 20 * 1024 * 1024
    worker_poll_seconds: float = 1.0
    allow_inline_processing: bool = False

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'accstrace.db').as_posix()}"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "projects").mkdir(exist_ok=True)
        (self.data_dir / "exports").mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
