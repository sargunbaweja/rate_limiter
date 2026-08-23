"""Application configuration (env vars, limiter settings, Redis connection info)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    redis_url: str = "redis://localhost:6379/0"

    rate_limit_algo: Literal[
        "fixed_window",
        "sliding_window",
        "sliding_window_counter",
        "token_bucket",
    ] = "token_bucket"

    # used by fixed_window, sliding_window, sliding_window_counter
    rate_limit_limit: int = 5
    rate_limit_window_seconds: int = 1

    # used by token_bucket
    rate_limit_capacity: int = 5
    rate_limit_refill_rate: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
