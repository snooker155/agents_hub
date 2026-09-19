"""
Token-safe git subprocess helpers.

Auth is injected per-invocation via `git -c http.extraHeader=...`, so the
token never lands in `.git/config`, the remote URL, or error output. When no
token is configured for the provider, commands run unauthenticated (public
repos keep working).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from . import store
from .providers import get_provider, GitProviderError

CLONE_TIMEOUT = 300
PULL_TIMEOUT = 120


class GitOpsError(Exception):
    """Raised when a git command fails (message is UI-safe)."""


def _auth_args(provider: Optional[str]) -> list[str]:
    if provider in store.PROVIDERS and store.has_token(provider):
        try:
            header = get_provider(provider).auth_header()
            return ["-c", f"http.extraHeader={header}"]
        except GitProviderError:
            pass
    return []


def _run(args: list[str], *, cwd: Optional[Path] = None, timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            args, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GitOpsError(f"git operation timed out after {timeout}s") from e


def clone(clone_url: str, dest: Path, *, branch: Optional[str] = None, provider: Optional[str] = None) -> str:
    """Clone a repo. Retries without --branch when the requested branch is missing.

    Returns combined git output. Raises GitOpsError on failure.
    """
    base = ["git", *_auth_args(provider), "clone"]
    if branch:
        result = _run([*base, "--branch", branch, clone_url, str(dest)], timeout=CLONE_TIMEOUT)
        if result.returncode == 0:
            return result.stdout + result.stderr
        # A missing branch is the common failure here (e.g. empty repo or
        # default branch mismatch) — fall through to a plain clone.
    result = _run([*base, clone_url, str(dest)], timeout=CLONE_TIMEOUT)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git clone failed")
    return result.stdout + result.stderr


def pull(repo_dir: Path, *, provider: Optional[str] = None) -> str:
    result = _run(["git", *_auth_args(provider), "pull"], cwd=repo_dir, timeout=PULL_TIMEOUT)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git pull failed")
    return result.stdout + result.stderr


def remote_url(repo_dir: Path) -> Optional[str]:
    """Return the origin URL of an existing clone, or None."""
    try:
        result = _run(["git", "remote", "get-url", "origin"], cwd=repo_dir, timeout=10)
    except GitOpsError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
