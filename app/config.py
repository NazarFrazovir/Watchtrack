"""Конфігурація застосунку. Усі значення можна перевизначити змінними середовища."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "WatchTrack"
    database_url: str = "postgresql+asyncpg://watchtrack:watchtrack@localhost:5432/watchtrack"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_echo: bool = False
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
