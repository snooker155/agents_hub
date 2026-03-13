from __future__ import annotations

import os
from typing import Tuple, List, Optional, Union, Literal
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings
from dataclasses import dataclass, field

DEFAULT_IGNORE: List[str] = [
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".mypy_cache",
    ".DS_Store",
]

@dataclass
class SweAgentConfig:
    workspace_root: Optional[Path] = None
    max_read_bytes: int = 1_000_000
    ignore_globs: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    allow_delete: bool = True
    binary_threshold: int = 4096

_swe_config = SweAgentConfig()

def get_swe_config() -> SweAgentConfig:
    return _swe_config

def update_swe_config(
    *,
    workspace_root: Optional[Union[str, Path]] = None,
    max_read_bytes: Optional[int] = None,
    ignore_globs: Optional[List[str]] = None,
    allow_delete: Optional[bool] = None,
    binary_threshold: Optional[int] = None,
) -> SweAgentConfig:
    global _swe_config

    ws = None
    if workspace_root is not None:
        ws = Path(workspace_root).resolve()
    else:
        ws = _swe_config.workspace_root

    max_r = max_read_bytes if max_read_bytes is not None else _swe_config.max_read_bytes
    allow_del = allow_delete if allow_delete is not None else _swe_config.allow_delete
    bin_thr = binary_threshold if binary_threshold is not None else _swe_config.binary_threshold

    if ignore_globs is None:
        ig = list(_swe_config.ignore_globs)
    else:
        seen = set()
        ig = []
        for item in (ignore_globs or []) + DEFAULT_IGNORE:
            if item not in seen:
                seen.add(item)
                ig.append(item)

    _swe_config = SweAgentConfig(
        workspace_root=ws,
        max_read_bytes=int(max_r),
        ignore_globs=ig,
        allow_delete=bool(allow_del),
        binary_threshold=int(bin_thr),
    )
    return _swe_config


class Settings(BaseSettings):
    """
    Unified environment-driven settings shared across packages.
    Merges logic from original tasks/config.py and common/config.py.
    """
    # Core LLM settings
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    model: str = Field(default="gpt-4o", env="OPENAI_MODEL")
    temperature: float = Field(default=0.0, env="LLM_TEMPERATURE")
    max_tokens: int = Field(default=15000, env="LLM_MAX_TOKENS")
    # Other cloud providers
    anthropic_api_key: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    google_api_key: Optional[str] = Field(default=None, env="GOOGLE_API_KEY")
    # Local models
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="", env="OLLAMA_MODEL")
    lmstudio_base_url: str = Field(default="http://localhost:1234", env="LMSTUDIO_BASE_URL")
    lmstudio_model: str = Field(default="", env="LMSTUDIO_MODEL")

    # Application settings
    mode_full: bool = True          # full (extensions/validations) or simple
    backend_lang_default: str = "python"  # fallback

    # Directories (defaults usually relative to running process, can be overridden)
    workspace_root: str = Field(default="./out", env="WORKSPACE_ROOT")

    # Policies / safety
    allow_shell: Tuple[str, ...] = Field(
        default_factory=lambda: tuple(("python,pytest,ruff,black").split(",")),
        env="ALLOW_SHELL",
    )

    # Orchestrator-specific settings (merged from orchestrator.config)
    orch_poll_interval: float = Field(default=5.0, env="ORCH_POLL_INTERVAL")
    orch_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", env="ORCH_LOG_LEVEL"
    )

    # Tasks storage (path to tasks.json). If None or empty, defaults to tasks/tasks.json
    tasks_file: Optional[str] = Field(default="tasks/tasks.json", env="TASKS_FILE")

    class Config:
        case_sensitive = False
        env_file = str(Path(__file__).resolve().parents[1] / ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

# Global settings instance
settings = Settings()

# Ensure API key is available (strip quotes if present)
if settings.openai_api_key:
    _api_key = settings.openai_api_key.strip('"\'')
    os.environ["OPENAI_API_KEY"] = _api_key
    settings.openai_api_key = _api_key

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


# Convenience accessors to align with previous orchestrator.config API
def get_settings() -> Settings:
    return settings


def require_openai_key(st: Settings) -> str:
    if not st.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Please export it in the environment or put it in a .env file."
        )
    return st.openai_api_key

