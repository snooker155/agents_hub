"""Fetching the repository behind an agent import.

Two-phase on purpose. ``stage()`` clones into a scratch folder and hands back a
token; the import modal inspects that clone and shows the operator a readiness
report. Only if they go ahead does ``promote()`` move the same clone to its
permanent home — so an inspection that reveals problems leaves no half-imported
agent behind, and confirming does not pay for a second clone.

Authentication is delegated to :mod:`connectors.git.git_ops`, which injects the
configured GitHub/GitLab token per invocation so it never lands in ``.git/config``
or in error output. Public repositories need no token at all.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from common.paths import IMPORTED_AGENTS_DIR, imported_agent_dir
from connectors.git import git_ops
from connectors.git.git_ops import GitOpsError

STAGING_DIR = IMPORTED_AGENTS_DIR / ".staging"

# A staged clone is scratch space between "inspect" and "import". Anything older
# than this was abandoned (modal closed, browser tab lost) and is swept on the
# next stage() call so the state root does not accumulate dead clones.
STAGING_TTL_SECONDS = 24 * 60 * 60


class ImportSourceError(Exception):
    """Raised when the requested source cannot be turned into a local clone."""


def detect_provider(repo_url: str) -> Optional[str]:
    """Map a clone URL onto a configured git connector, or None.

    Only the provider *name* is inferred here; whether a token exists for it is
    git_ops' business. Returning None simply means "clone unauthenticated",
    which is the right behaviour for public repos and self-hosted hosts.
    """
    host = (urlparse(repo_url).hostname or "").lower()
    if not host:
        # scp-style remotes (git@github.com:owner/repo.git) have no URL scheme.
        match = re.match(r"^[^@]+@([^:]+):", repo_url.strip())
        host = match.group(1).lower() if match else ""
    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    return None


def normalize_source(repo_url: str) -> str:
    """Validate the import source and return it in a form ``git clone`` accepts.

    Accepts http(s) URLs, scp-style git remotes, and absolute local paths (the
    last of these is what makes the bundled example importable with no network).
    Anything else is rejected by name rather than handed to git, so a typo
    surfaces as a clear message instead of a git usage error.
    """
    src = (repo_url or "").strip()
    if not src:
        raise ImportSourceError("Repository URL is required")

    if src.startswith("file://"):
        src = src[len("file://"):]

    scheme = urlparse(src).scheme.lower()
    if scheme in ("http", "https"):
        return src
    if re.match(r"^[^@\s]+@[^:\s]+:", src):  # git@host:owner/repo.git
        return src
    if scheme:
        raise ImportSourceError(
            f"Unsupported URL scheme '{scheme}'. Use an https:// repository URL, "
            f"an ssh remote, or an absolute local path."
        )

    path = Path(src).expanduser()
    if path.is_absolute() and path.is_dir():
        return str(path)
    raise ImportSourceError(
        f"'{repo_url}' is neither an https:// repository URL nor an existing "
        f"absolute local path."
    )


def head_commit(repo_dir: Path) -> str:
    """Return the short HEAD sha of a clone, or '' when it cannot be read."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _sweep_staging() -> None:
    """Delete abandoned staged clones. Best-effort; never blocks an import."""
    import time

    if not STAGING_DIR.is_dir():
        return
    cutoff = time.time() - STAGING_TTL_SECONDS
    for entry in STAGING_DIR.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def stage(repo_url: str, *, branch: Optional[str] = None) -> Tuple[str, Path]:
    """Clone *repo_url* into scratch space.

    Returns ``(token, repo_dir)``. The token is what the client passes back to
    :func:`promote` (or :func:`discard`); it is an opaque uuid rather than a
    path so a caller cannot steer the promote step at an arbitrary directory.
    """
    source = normalize_source(repo_url)
    _sweep_staging()

    token = uuid.uuid4().hex
    dest = STAGING_DIR / token
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        git_ops.clone(source, dest, branch=branch, provider=detect_provider(source))
    except GitOpsError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise ImportSourceError(f"git clone failed: {exc}") from exc

    return token, dest


def staged_dir(token: str) -> Path:
    """Resolve a staging token to its clone directory, validating the token.

    The token is constrained to hex so it cannot escape ``STAGING_DIR`` via
    path traversal, and the directory must actually exist — an expired or
    already-promoted token is reported as such rather than silently re-cloning.
    """
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ImportSourceError("Invalid staging token")
    path = STAGING_DIR / token
    if not path.is_dir():
        raise ImportSourceError(
            "This inspection has expired — run the repository check again before importing."
        )
    return path


def promote(token: str, agent_id: str) -> Path:
    """Move a staged clone to the permanent folder for *agent_id*.

    Replaces any previous clone for the same agent id, which is what makes
    re-importing an agent (to pick up a newer commit) work.
    """
    source = staged_dir(token)
    dest = imported_agent_dir(agent_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(source), str(dest))
    return dest


def discard(token: str) -> None:
    """Drop a staged clone. Silent when the token is unknown or already gone."""
    try:
        shutil.rmtree(staged_dir(token), ignore_errors=True)
    except ImportSourceError:
        pass


def remove_clone(agent_id: str) -> bool:
    """Delete the stored clone for an imported agent. True when one was removed."""
    path = imported_agent_dir(agent_id)
    if not path.is_dir():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


__all__ = [
    "ImportSourceError",
    "STAGING_DIR",
    "detect_provider",
    "normalize_source",
    "head_commit",
    "stage",
    "staged_dir",
    "promote",
    "discard",
    "remove_clone",
]
