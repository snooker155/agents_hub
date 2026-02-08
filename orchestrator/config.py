"""
Basic configuration for the orchestrator package.
Inherits from unified common.config.Settings.
"""
from __future__ import annotations

from typing import Optional, Literal
from common.config import Settings as BaseSettings

class Settings(BaseSettings):
    """Environment-driven settings for the orchestrator."""
    
    # Orchestrator-specific settings
    orch_poll_interval: float = 5.0
    orch_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    tasks_file: Optional[str] = None

def get_settings() -> Settings:
    return Settings()

def require_openai_key(settings: Settings) -> str:
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please export it in the environment or put it in a .env file."
        )
    return settings.openai_api_key
