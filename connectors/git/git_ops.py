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
BRANCH_TIMEOUT = 15
COMMIT_TIMEOUT = 30
PUSH_TIMEOUT = 120

# Never staged by commit_all, regardless of what the caller asks to include —
# a workspace's own .gitignore is the first line of defence (git add -A never
# stages what it excludes), but an agent-initiated commit gets a second,
# independent one that does not depend on the repo having remembered to list
# its own secrets. Glob pathspec magic (":(glob)") so "**/" matches at any
# depth, not just the repo root.
DENYLIST_PATTERNS = (":(glob)**/.env", ":(glob)**/*.pem", ":(glob)**/id_rsa*")


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


def current_branch(repo_dir: Path) -> str:
    """The branch currently checked out (empty string in a detached HEAD)."""
    result = _run(["git", "branch", "--show-current"], cwd=repo_dir, timeout=10)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git branch --show-current failed")
    return result.stdout.strip()


def has_changes(repo_dir: Path) -> bool:
    """True when the working tree (tracked or untracked) differs from HEAD."""
    result = _run(["git", "status", "--porcelain"], cwd=repo_dir, timeout=10)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git status failed")
    return bool(result.stdout.strip())


def create_branch(repo_dir: Path, name: str, from_ref: Optional[str] = None) -> None:
    """Check out branch *name*, creating it from *from_ref* if it does not exist yet.

    Reusing an existing local branch of that name (instead of failing) lets a
    retried publish land on the same branch after a partial failure, rather
    than piling up agent/foo, agent/foo-2, agent/foo-3 for one task.
    """
    exists = _run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=repo_dir, timeout=10,
    )
    if exists.returncode == 0:
        result = _run(["git", "checkout", name], cwd=repo_dir, timeout=BRANCH_TIMEOUT)
    else:
        args = ["git", "checkout", "-b", name]
        if from_ref:
            args.append(from_ref)
        result = _run(args, cwd=repo_dir, timeout=BRANCH_TIMEOUT)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or f"could not switch to branch {name!r}")


def _denylisted_paths(repo_dir: Path) -> list[str]:
    """Tracked or staged paths matching DENYLIST_PATTERNS, present in the tree."""
    result = _run(
        ["git", "ls-files", "--others", "--cached", "--", *DENYLIST_PATTERNS],
        cwd=repo_dir, timeout=10,
    )
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git ls-files failed")
    return [p for p in result.stdout.splitlines() if p.strip()]


def commit_all(
    repo_dir: Path, message: str, *, author: Optional[str] = None,
    paths: Optional[list[str]] = None,
) -> Optional[str]:
    """Stage tracked and untracked changes (minus the denylist) and commit.

    ``paths`` restricts what ``git add`` touches (default: the whole tree, "."),
    but the denylist check always runs over the resulting index regardless —
    a caller cannot ask past it by naming a narrower path. Returns the new
    commit sha, or raises GitOpsError when there is nothing to commit, so a
    caller cannot mistake "no-op" for "published".
    """
    targets = list(paths) if paths else ["."]
    add_result = _run(["git", "add", "-A", "--", *targets], cwd=repo_dir, timeout=COMMIT_TIMEOUT)
    if add_result.returncode != 0:
        raise GitOpsError(add_result.stderr.strip() or "git add failed")

    denylisted = _denylisted_paths(repo_dir)
    if denylisted:
        reset_result = _run(["git", "reset", "--", *denylisted], cwd=repo_dir, timeout=COMMIT_TIMEOUT)
        if reset_result.returncode != 0:
            raise GitOpsError(reset_result.stderr.strip() or "git reset (denylist) failed")

    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_dir, timeout=10)
    if staged.returncode != 0:
        raise GitOpsError(staged.stderr.strip() or "git diff --cached failed")
    if not staged.stdout.strip():
        raise GitOpsError("nothing to commit — the working tree matches HEAD (after removing denylisted files)")

    commit_args = ["git", "commit", "-m", message]
    if author:
        commit_args.extend(["--author", author])
    commit_result = _run(commit_args, cwd=repo_dir, timeout=COMMIT_TIMEOUT)
    if commit_result.returncode != 0:
        raise GitOpsError(commit_result.stderr.strip() or "git commit failed")

    sha_result = _run(["git", "rev-parse", "HEAD"], cwd=repo_dir, timeout=10)
    if sha_result.returncode != 0:
        raise GitOpsError(sha_result.stderr.strip() or "git rev-parse HEAD failed")
    return sha_result.stdout.strip() or None


def push(repo_dir: Path, branch: str, *, provider: Optional[str] = None, set_upstream: bool = True) -> str:
    """Push *branch* to origin. Never forces — a rejected push surfaces as GitOpsError.

    Auth is the same per-invocation ``http.extraHeader`` injection clone/pull
    use, so no token ever lands in the remote URL, `.git/config`, or a subprocess
    environment variable.
    """
    args = ["git", *_auth_args(provider), "push"]
    if set_upstream:
        args.append("-u")
    args.extend(["origin", branch])
    result = _run(args, cwd=repo_dir, timeout=PUSH_TIMEOUT)
    if result.returncode != 0:
        raise GitOpsError(result.stderr.strip() or "git push failed")
    return result.stdout + result.stderr
