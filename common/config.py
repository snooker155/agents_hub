from __future__ import annotations

import os
from typing import Tuple, List, Optional
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    """
    Unified environment-driven settings shared across packages.
    Merges logic from original tasks/config.py and common/config.py.
    """
    # Core LLM settings
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    max_tokens: int = Field(default=20000, alias="LLM_MAX_TOKENS")

    # Application settings
    mode_full: bool = True          # full (extensions/validations) or simple
    backend_lang_default: str = "python"  # fallback

    # Directories (defaults usually relative to running process, can be overridden)
    workspace_root: str = Field(default="./out", alias="WORKSPACE_ROOT")

    # Policies / safety
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

# Global settings instance
settings = Settings()

class Paths:
    """Helper to resolve standardized paths based on settings.workspace_root."""
    def __init__(self):
        self.root = Path(settings.workspace_root).resolve()
        self.out = str(self.root)
        self.docs = str(self.root / "docs")
        self.plan = str(self.root / "plan")
        self.logs = str(self.root / "logs")
        self.code_be = str(self.root / "code" / "backend")
        self.code_fe = str(self.root / "code" / "frontend")
        self.ops = str(self.root / "ops")
        self.tests = str(self.root / "tests")

class Models:
    """Helper for role-based model names (defaults to main model)."""
    def __init__(self):
        m = settings.model
        self.pm = m
        self.ba_json = m
        self.ba_md = m
        self.sd = m
        self.tl = m
        self.dev = m
