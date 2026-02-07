from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union
from tasks.config import Settings


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


def get_settings() -> Settings:
    """Return a new instance of settings loaded from environment.

    We intentionally return a new instance each time to avoid surprises in tests.
    """
    return Settings()


@dataclass
class SweAgentConfig:
    """Runtime configuration for swe_agent tools and sandbox.

    - workspace_root: Root directory where all FS ops must be contained. If None,
      current working directory will be used.
    - max_read_bytes: Hard limit for reading a single file. Files larger than this
      will be refused by read/search operations.
    - ignore_globs: Path patterns or directory names to ignore in listing/searching.
    - allow_delete: Whether destructive delete operations (e.g., in patches) are allowed.
    - binary_threshold: Number of bytes to sample for binary detection. If NUL byte is
      present or UTF-8 decoding fails in the sample, file is considered binary and is skipped.
    """

    workspace_root: Optional[Path] = None
    max_read_bytes: int = 1_000_000
    ignore_globs: List[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    allow_delete: bool = True
    binary_threshold: int = 4096


_config = SweAgentConfig()


def _normalize_workspace_root(p: Optional[Union[str, Path]]) -> Optional[Path]:
    if p is None:
        return None
    path = Path(p).resolve()
    return path


def get_config() -> SweAgentConfig:
    return _config


def update_config(
    *,
    workspace_root: Optional[Union[str, Path]] = None,
    max_read_bytes: Optional[int] = None,
    ignore_globs: Optional[List[str]] = None,
    allow_delete: Optional[bool] = None,
    binary_threshold: Optional[int] = None,
) -> SweAgentConfig:
    """Update global config selectively. Returns the updated config."""
    global _config
    ws = _normalize_workspace_root(workspace_root) if workspace_root is not None else _config.workspace_root
    max_r = max_read_bytes if max_read_bytes is not None else _config.max_read_bytes
    allow_del = allow_delete if allow_delete is not None else _config.allow_delete
    bin_thr = binary_threshold if binary_threshold is not None else _config.binary_threshold

    if ignore_globs is None:
        ig = list(_config.ignore_globs)
    else:
        # Merge unique while preserving order: new list extends defaults
        seen = set()
        ig = []
        for item in (ignore_globs or []) + DEFAULT_IGNORE:
            if item not in seen:
                seen.add(item)
                ig.append(item)

    _config = SweAgentConfig(
        workspace_root=ws,
        max_read_bytes=int(max_r),
        ignore_globs=ig,
        allow_delete=bool(allow_del),
        binary_threshold=int(bin_thr),
    )
    return _config


def workspace_root() -> Path:
    """Return the effective workspace root (resolved)."""
    if _config.workspace_root is not None:
        return _config.workspace_root
    return Path.cwd().resolve()
