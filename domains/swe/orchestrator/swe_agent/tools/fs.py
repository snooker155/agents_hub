from __future__ import annotations

import fnmatch
import io
import os
import re
from pathlib import Path
from typing import Iterable, List, Dict, Any, Optional

# Centralized settings for sandbox and limits
from ..config import get_config, workspace_root as _cfg_workspace_root


def _workspace_root(workspace: Optional[Path] = None) -> Path:
    # Use explicit workspace if provided, otherwise config-provided or CWD
    if workspace:
        return workspace.resolve()
    return _cfg_workspace_root()


def _resolve_within_workspace(path: str, workspace: Optional[Path] = None) -> Path:
    """Resolve a user-provided path to an absolute path inside workspace.

    - Normalizes path (resolves '..', symlinks, etc.).
    - Treats relative paths as relative to workspace root from config.
    - Absolute paths are allowed only if they are within the workspace root.
    - Raises ValueError if the resolved path escapes the workspace.
    """
    root = _workspace_root(workspace)
    p = Path(path)
    abs_path = (root / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        abs_path.relative_to(root)
    except Exception:
        raise ValueError(f"Path escapes workspace: {path}")
    return abs_path


esscape_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _rel(path: Path, workspace: Optional[Path] = None) -> str:
    """Return workspace-relative posix path string for a resolved path."""
    root = _workspace_root(workspace)
    try:
        return str(path.relative_to(root).as_posix())
    except Exception:
        # Fallback: should not happen if we only pass resolved paths inside workspace
        return path.as_posix()


def _is_binary(sample: bytes) -> bool:
    """Heuristic: detect NUL or decoding issues in a sample of bytes."""
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
        return False
    except Exception:
        return True


def read_file(path: str, workspace: Optional[Path] = None, config: Optional[Any] = None) -> str:
    """Read UTF-8 text file from the workspace respecting config limits.

    Raises if file size exceeds max_read_bytes or looks binary.
    """
    cfg = config or get_config()
    abs_path = _resolve_within_workspace(path, workspace=workspace)
    if not abs_path.is_file():
        raise FileNotFoundError(f"File not found: {_rel(abs_path, workspace=workspace)}")

    try:
        size = abs_path.stat().st_size
    except Exception:
        size = None
    if size is not None and size > int(cfg.max_read_bytes):
        raise ValueError(f"File too large to read (>{cfg.max_read_bytes} bytes): {_rel(abs_path, workspace=workspace)}")

    # Read once, then check for binary content
    content_bytes = abs_path.read_bytes()
    if _is_binary(content_bytes[: int(cfg.binary_threshold)]):
        raise ValueError(f"Binary or non-UTF8 file: {_rel(abs_path, workspace=workspace)}")

    return content_bytes.decode("utf-8")


def write_file(path: str, content: str, create_dirs: bool = True, workspace: Optional[Path] = None) -> str:
    """Write UTF-8 text to a file in the workspace.

    Returns workspace-relative path to the written file.
    """
    abs_path = _resolve_within_workspace(path, workspace=workspace)
    if create_dirs:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic-like write: write to temp file then replace
    tmp_path: Optional[Path] = None
    try:
        tmp_dir = abs_path.parent
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # Use os to avoid race on NamedTemporaryFile on Windows
        import tempfile, os as _os
        fd, tmp_name = tempfile.mkstemp(prefix=".tmp_write_", dir=str(tmp_dir))
        _os.close(fd)
        tmp_path = Path(tmp_name)
        tmp_path.write_text(content, encoding="utf-8")
        _os.replace(tmp_path, abs_path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
    return _rel(abs_path, workspace=workspace)


def _is_ignored(rel_posix: str, ignore: Iterable[str]) -> bool:
    """Check whether a relative posix path should be ignored.

    Matches if any of the patterns in ignore match any path segment or the
    whole path via fnmatch. Default ignores like '.git' and '.venv' are handled
    by segment check as well.
    """
    parts = rel_posix.split("/")
    ig_set = set(ignore)
    if any(p in ig_set for p in parts):
        return True
    for pat in ignore:
        if fnmatch.fnmatch(rel_posix, pat):
            return True
        # If pattern looks like a directory name, also match subtree
        if rel_posix.startswith(pat.rstrip("/") + "/"):
            return True
    return False


def list_files(glob: str = "**/*", ignore: Iterable[str] | None = None, workspace: Optional[Path] = None, config: Optional[Any] = None) -> List[str]:
    """List files in the workspace matching a glob, excluding ignored paths.

    If ignore is None, uses ignore_globs from domains.swe.orchestrator.swe_agent.config.
    """
    cfg = config or get_config()
    if ignore is None:
        ignore = cfg.ignore_globs

    root = _workspace_root(workspace)

    # Normalize glob: treat absolute glob as workspace-relative equivalent
    try:
        gpath = Path(glob)
        if gpath.is_absolute():
            try:
                glob_rel = str(gpath.resolve().relative_to(root).as_posix())
            except Exception:
                return []
        else:
            glob_rel = glob
    except Exception:
        glob_rel = glob

    results: List[str] = []
    for p in root.glob(glob_rel):
        try:
            rp = _rel(p.resolve(), workspace=workspace)
        except Exception:
            continue
        if _is_ignored(rp, ignore):
            continue
        if p.is_file():
            results.append(rp)
    results.sort()
    return results


def search_text(pattern: str, file_glob: str, workspace: Optional[Path] = None, config: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Search for a regex pattern across files matched by file_glob.

    Respects max_read_bytes and binary detection; such files are skipped.
    """
    cfg = config or get_config()
    regex = re.compile(pattern)
    results: List[Dict[str, Any]] = []
    for rel in list_files(file_glob, workspace=workspace, config=cfg):
        abs_path = _resolve_within_workspace(rel, workspace=workspace)
        # Skip large or binary files
        try:
            st = abs_path.stat()
            if st.st_size > int(cfg.max_read_bytes):
                continue
        except Exception:
            pass
        try:
            sample = abs_path.read_bytes()[: int(cfg.binary_threshold)]
        except Exception:
            continue
        if _is_binary(sample):
            continue
        try:
            with io.open(abs_path, "r", encoding="utf-8", errors="strict") as fh:
                for idx, line in enumerate(fh, start=1):
                    if regex.search(line):
                        results.append({
                            "path": rel,
                            "line": idx,
                            "match": line.rstrip("\n\r"),
                        })
        except UnicodeDecodeError:
            continue
    return results
