from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "sqlite:///./runcoach.db"


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is read once per process.

    Tests clear the cache after changing the environment; see
    tests/conftest.py.
    """
    return Settings()
