"""
Basic configuration for the orchestrator package.

This module defines Settings using pydantic BaseSettings so values can
be configured via environment variables. Importing the package
("import orchestrator") should not trigger any heavy/runtime logic.
"""
from __future__ import annotations

from typing import Optional, Literal
from domains.swe.orchestrator.tasks.config import Settings as BaseSettings


class Settings(BaseSettings):
    """Environment-driven settings for the orchestrator."""

    orch_poll_interval: float = 5.0  # default value
    orch_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    tasks_file: Optional[str] = None


def get_settings() -> Settings:
    """Return a new instance of settings loaded from environment.

    We intentionally return a new instance each time to avoid surprises in tests.
    """
    return Settings()


def require_openai_key(settings: Settings) -> str:
    """Return API key or raise a clear, user-friendly error if missing."""
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please export it in the environment or put it in a .env file."
        )
    return settings.openai_api_key
