from __future__ import annotations

from typing import Optional, Tuple
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Base environment-driven settings shared across packages.

    Variables:
      - OPENAI_API_KEY: API key for OpenAI.
      - OPENAI_MODEL: Default model name (e.g., "gpt-4o-mini").
      - LLM_TEMPERATURE: Default temperature as float.
      - LLM_MAX_TOKENS: Default max tokens as int.
    """

    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    model: str = Field(default_factory=lambda: "gpt-4o-mini", alias="OPENAI_MODEL")

    temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    max_tokens: int = Field(default=20000, alias="LLM_MAX_TOKENS")

    # Policies / safety toggles
    allow_shell: Tuple[str, ...] = Field(
        default_factory=lambda: tuple(("python,pytest,ruff,black").split(",")),
        alias="ALLOW_SHELL",
    )

    class Config:
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True
        extra = "ignore"
