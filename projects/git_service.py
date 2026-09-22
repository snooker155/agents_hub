"""Git operations for a project: status, pull, clone, issue sync, publish.

Split out of ``routes/projects.py``: these are the parts of the projects API
that shell out to git or call a provider (GitHub/GitLab) — everything else
(project CRUD, the workspace symlink, the store) stays a route concern. Every
function here is synchronous plain Python; the routes are the ones that know
about ``asyncio.to_thread`` and ``HTTPException``.

Failures are reported as ``ServiceError(status, detail)`` — the same status
codes the routes raised before this split — so a route becomes a lookup, a
call into here, and one ``except ServiceError`` that maps it to
``HTTPException``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from connectors.git import git_ops
from connectors.git.git_ops import GitOpsError
from connectors.git.issue_sync import sync_issues as _sync_project_issues
from connectors.git.providers import GitProviderError, get_provider
from projects.errors import ServiceError
from tools.git_publish import run_git_publish


def repo_provider(project) -> Optional[str]:
    """Provider name for git auth injection, when the project's repo type
    is one a provider is registered for."""
    repo_type = str(project.repo.type.value if hasattr(project.repo.type, "value")
                    else project.repo.type)
    return repo_type if repo_type in ("github", "gitlab") else None


def run_issue_sync(project) -> Dict[str, Any]:
    """Run issue sync, mapping failures to a UI-safe error payload rather than
    raising — used where a caller wants ``{error: ...}`` back, not a 4xx."""
    try:
        return _sync_project_issues(project)
    except (GitProviderError, ValueError) as e:
        return {"imported": 0, "updated": 0, "total": 0, "error": str(e)}


def sync_issues(project) -> Dict[str, Any]:
    """``POST /{project_id}/sync-issues``: import/refresh issues as tasks."""
    if repo_provider(project) is None or not project.repo.remote_id:
        raise ServiceError(400, "Project is not connected to a GitHub/GitLab repo")
    try:
        return _sync_project_issues(project)
    except (GitProviderError, ValueError) as e:
        raise ServiceError(400, str(e))


def resolve_repo_info(provider_name: str, remote_id: str) -> Dict[str, Any]:
    """``get_provider(provider_name).get_repo(remote_id)``, mapped to a 400
    ``ServiceError`` instead of letting ``GitProviderError`` escape."""
    try:
        return get_provider(provider_name).get_repo(remote_id)
    except GitProviderError as e:
        raise ServiceError(400, str(e))


def clone_from_provider(clone_url: str, clone_dir: Path, *,
                        branch: Optional[str], provider_name: Optional[str]) -> str:
    """Clone a repo URL into ``clone_dir``. Raises a 500 ``ServiceError`` on
    failure — the shape ``import_from_repo`` / ``connect_repo`` used before
    this split (``clone_repo`` below has its own, slightly different, mapping
    for a timeout, and keeps that distinction)."""
    try:
        return git_ops.clone(clone_url, clone_dir, branch=branch, provider=provider_name)
    except GitOpsError as e:
        raise ServiceError(500, f"Clone failed: {e}")


def clone_repo(project, ws_folder: Path) -> Dict[str, Any]:
    """``POST /{project_id}/clone-repo``: clone the project's configured repo
    URL into its workspace folder."""
    repo = project.repo
    if not repo.url:
        raise ServiceError(400, "No repo URL configured")

    clone_dir = ws_folder / (repo.local_path or "repo")
    if clone_dir.exists():
        raise ServiceError(409, f"Target directory already exists: {clone_dir}")

    try:
        output = git_ops.clone(repo.url, clone_dir, branch=repo.branch or None,
                               provider=repo_provider(project))
    except GitOpsError as e:
        status = 504 if "timed out" in str(e) else 500
        raise ServiceError(status, str(e))

    return {"cloned": True, "path": str(clone_dir), "output": output}


def git_status(project, ws_folder: Path) -> Dict[str, Any]:
    """``GET /{project_id}/git-status``: branch, short status and recent log
    for the project's local repo."""
    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise ServiceError(404, "Repo directory not found. Clone first.")

    try:
        status = subprocess.run(
            ["git", "status", "--short"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10,
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-10"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:  # noqa: BLE001
        raise ServiceError(500, str(e))

    return {
        "branch": branch.stdout.strip(),
        "status": status.stdout.strip(),
        "recent_commits": log.stdout.strip(),
    }


def git_pull(project, ws_folder: Path) -> Dict[str, Any]:
    """``POST /{project_id}/git-pull``: pull the project's local repo."""
    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise ServiceError(404, "Repo directory not found")

    try:
        output = git_ops.pull(repo_path, provider=repo_provider(project))
    except GitOpsError as e:
        status = 504 if "timed out" in str(e) else 500
        raise ServiceError(status, str(e))

    return {"output": output, "returncode": 0}


def publish(
    project_id: str, *, branch: Optional[str], title: str, body: str = "",
    base: Optional[str] = None, draft: bool = True, open_pr: bool = True,
) -> Dict[str, Any]:
    """``POST /{project_id}/git/publish``: commit, push a branch and open a
    PR/MR, via ``tools.git_publish.run_git_publish`` — the same function the
    ``git_publish`` agent tool calls, so the branch-protection refusals apply
    identically here."""
    result = run_git_publish(
        project_id, branch=branch, title=title, body=body,
        base=base, draft=draft, open_pr=open_pr,
    )
    if not result.get("ok"):
        status = 404 if result.get("code") in ("not_found", "unresolved_remote", "no_remote") else 400
        raise ServiceError(status, result.get("error"))
    return result


__all__ = [
    "repo_provider", "run_issue_sync", "sync_issues", "resolve_repo_info",
    "clone_from_provider", "clone_repo", "git_status", "git_pull", "publish",
]
