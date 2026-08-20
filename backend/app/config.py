"""Application settings, sourced from environment variables / `.env`.
See .env.example for the full list of keys this reads."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "sqlite:///./vaanireach.db"
    upload_dir: str = "./data/uploads"

    max_upload_size_mb: int = 25
    allowed_file_types: str = "pdf,docx,png,jpg,jpeg"

    cors_origins: str = "http://localhost:3000"

    provider_timeout_seconds: int = 30

    @property
    def allowed_file_types_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_file_types.split(",") if t.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
