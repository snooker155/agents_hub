"""Repo, attach, git and swagger/api-proxy routes of a project."""
from ._common import (_project_to_dict, store)
import asyncio
import os
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from workspace import get_workspace_folder, resolve_project_root, project_folder_name
from models import ProjectAttach, ProjectApiRequest, ProjectImportFromRepo, ProjectConnectRepo
from projects.models import Project, RepoConfig
from common.paths import PROJECT_ROOT, AGENTS_HUB_ROOT
from projects import git_service, proxy_service
from projects.errors import ServiceError
import logging

log = logging.getLogger(__name__)


router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────── Attach allowlist ────────────────────────────
#
# POST /attach symlinks an arbitrary directory into a workspace. Without a
# check, any directory the backend process can read (/etc, $HOME, ...) could
# be exposed this way, whether the caller is a dashboard user or an agent
# using the api tool.


def _attach_allowed_roots() -> list[Path]:
    """Directories a project may be attached from.

    ``AGENTS_HUB_ATTACH_ROOTS`` (os.pathsep separated absolute directories)
    replaces the default entirely when set. Unset, the default is the user's
    home directory plus this service's own repo and state roots — enough for
    the common "attach my project" case without allowing the whole filesystem.
    """
    raw = (os.environ.get("AGENTS_HUB_ATTACH_ROOTS") or "").strip()
    if raw:
        return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    return [Path.home().resolve(), PROJECT_ROOT.resolve(), AGENTS_HUB_ROOT.resolve()]


def _check_attach_allowed(target: Path) -> None:
    """Reject an attach target outside the configured allowlist.

    Not a general sandbox: a permissively configured ``AGENTS_HUB_ATTACH_ROOTS``,
    or a root that itself holds sensitive data (e.g. the home directory), still
    lets that data be attached. It only closes the "any readable path" case.
    """
    target = target.resolve()
    for root in _attach_allowed_roots():
        if target == root or root in target.parents:
            return
    raise HTTPException(
        status_code=403,
        detail=(
            f"{target} is outside the allowed attach roots. Set the "
            "AGENTS_HUB_ATTACH_ROOTS environment variable to allow it."
        ),
    )


# POST /{project_id}/api-request proxies an HTTP request to whatever base_url
# it is given. The allowlist (scheme + cloud metadata address) and the
# container host rewrite live in projects/proxy_service.py — see
# proxy_service.validated_api_base_url for what is and isn't checked.


# ─────────────────────────── REPO ────────────────────────────
#
# The git mechanics (clone, status, pull, issue sync, publish) live in
# projects/git_service.py; these handlers resolve the project/workspace and
# map a ServiceError to the matching HTTPException.

@router.post("/import-from-repo")
async def import_from_repo(payload: ProjectImportFromRepo):
    """Import a GitHub/GitLab repo as a new project: clone + optional issue import."""
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    try:
        repo_info = await asyncio.to_thread(
            git_service.resolve_repo_info, payload.provider, payload.remote_id, payload.workspace)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    name = (payload.name or "").strip() or repo_info["name"]
    branch = (payload.branch or "").strip() or repo_info["default_branch"]
    folder = project_folder_name(name)
    local_path = f"{folder}/repo"
    clone_dir = ws_folder / local_path

    if clone_dir.exists():
        raise HTTPException(status_code=409, detail=f"Target directory already exists: {clone_dir}")
    if any(p.workspace == payload.workspace and project_folder_name(p.name) == folder for p in store().list()):
        raise HTTPException(status_code=409, detail=f"A project named '{name}' already exists in this workspace")

    resolve_project_root(payload.workspace, folder)
    try:
        await asyncio.to_thread(
            git_service.clone_from_provider, repo_info["clone_url"], clone_dir,
            branch=branch, provider_name=payload.provider, workspace=payload.workspace,
        )
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    project = Project(
        name=name,
        description=repo_info.get("description"),
        type="code",
        workspace=payload.workspace,
        repo=RepoConfig(
            type=payload.provider,
            url=repo_info["clone_url"],
            branch=branch,
            local_path=local_path,
            remote_id=repo_info["remote_id"],
        ),
    )
    store().add(project)

    issues = None
    if payload.import_issues:
        issues = await asyncio.to_thread(git_service.run_issue_sync, project)

    skills = await asyncio.to_thread(git_service.sync_project_skills, project)

    d = _project_to_dict(project)
    d["folder"] = folder
    return {"project": d, "cloned": True, "issues": issues, "skills": skills}


@router.post("/{project_id}/connect-repo")
async def connect_repo(project_id: str, payload: ProjectConnectRepo):
    """Connect an existing project to a GitHub/GitLab repo: clone + optional issue import."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{project.workspace}' not found")

    try:
        repo_info = await asyncio.to_thread(
            git_service.resolve_repo_info, payload.provider, payload.remote_id, project.workspace)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    branch = (payload.branch or "").strip() or repo_info["default_branch"]
    folder = project_folder_name(project.name)
    local_path = project.repo.local_path or f"{folder}/repo"
    clone_dir = ws_folder / local_path

    cloned = False
    if clone_dir.exists():
        from connectors.git import git_ops
        existing_remote = git_ops.remote_url(clone_dir)
        if existing_remote not in (repo_info["clone_url"], repo_info["web_url"]):
            raise HTTPException(
                status_code=409,
                detail=f"Directory {clone_dir} already exists and is not a clone of this repo",
            )
    else:
        try:
            await asyncio.to_thread(
                git_service.clone_from_provider, repo_info["clone_url"], clone_dir,
                branch=branch, provider_name=payload.provider, workspace=project.workspace,
            )
            cloned = True
        except ServiceError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    project = store().update(project_id, repo={
        "type": payload.provider,
        "url": repo_info["clone_url"],
        "branch": branch,
        "local_path": local_path,
        "remote_id": repo_info["remote_id"],
    })

    issues = None
    if payload.import_issues:
        issues = await asyncio.to_thread(git_service.run_issue_sync, project)

    skills = (await asyncio.to_thread(git_service.sync_project_skills, project)
              if cloned else None)

    d = _project_to_dict(project)
    d["folder"] = folder
    return {"project": d, "cloned": cloned, "issues": issues, "skills": skills}


@router.post("/{project_id}/sync-issues")
async def sync_issues(project_id: str):
    """Import new and refresh previously imported issues as project tasks."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return await asyncio.to_thread(git_service.sync_issues, project)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/{project_id}/clone-repo")
async def clone_repo(project_id: str):
    """Clone the project's git repo into the workspace directory."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{project.workspace}' not found")

    try:
        result = await asyncio.to_thread(git_service.clone_repo, project, ws_folder)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    # Update local_path in project
    rel_path = project.repo.local_path or "repo"
    store().update(project_id, repo={"local_path": rel_path})

    return result


@router.post("/attach")
async def attach_project(payload: ProjectAttach):
    """Register an existing directory as a project in a workspace, in place.

    The project folder inside the workspace becomes a link to that directory, so
    agents work on the real files and a git checkout keeps its history. If the
    directory is already a git repo, git-status and git-pull work on it right
    away — no clone step.

    `path` is resolved by *this process*: in a container it must be a path inside
    the container, so bind mount the host directory first.
    """
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    target = Path(payload.path).expanduser().resolve()
    if not target.exists():
        raise HTTPException(status_code=400, detail=f"No such directory: {target}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {target}")
    _check_attach_allowed(target)

    # The folder name has to survive project_folder_name() unchanged, otherwise
    # the link, the project's derived folder and repo.local_path would disagree.
    folder = project_folder_name(payload.name or target.name)
    link = ws_folder / folder
    # Linking a directory into itself, or into one it already contains, makes a
    # cycle: ws/proj -> ws means ws/proj/proj/proj resolves forever, and any
    # recursive walk of the workspace hangs. Refuse all three overlapping cases.
    ws_real = ws_folder.resolve()
    if target == ws_real:
        raise HTTPException(
            status_code=400,
            detail=f"{target} is the workspace itself — it is already where agents work, "
                   "so it does not also need to be a project inside it",
        )
    if target in ws_real.parents:
        raise HTTPException(
            status_code=400,
            detail=f"{target} contains workspace '{payload.workspace}' — linking it inside "
                   "that workspace would create a loop",
        )
    if ws_real in target.parents:
        raise HTTPException(
            status_code=400,
            detail=f"{target} is already inside workspace '{payload.workspace}' — create the "
                   "project normally instead of attaching it",
        )
    if link.is_symlink():
        if link.resolve() != target:
            raise HTTPException(
                status_code=409,
                detail=f"'{folder}' in this workspace already points at {link.resolve()}",
            )
    elif link.exists():
        raise HTTPException(
            status_code=409,
            detail=f"'{folder}' already exists in workspace '{payload.workspace}' as a real directory",
        )
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"Could not link: {e}")

    is_repo = (target / ".git").exists()
    project = Project(
        name=folder,
        description=payload.description,
        type=payload.type or "code",
        workspace=payload.workspace,
        repo=RepoConfig(
            type="local" if is_repo else "none",
            local_path=folder,
            branch=None,
        ),
    )
    store().add(project)

    d = _project_to_dict(project)
    d["folder"] = folder
    d["attached"] = True
    d["target"] = str(target)
    d["is_git_repo"] = is_repo
    return d


@router.get("/{project_id}/git-status")
async def git_status(project_id: str):
    """Return git status for the project's local repo."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    try:
        return await asyncio.to_thread(git_service.git_status, project, ws_folder)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


@router.post("/{project_id}/git-pull")
async def git_pull(project_id: str):
    """Pull latest changes from remote."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    try:
        return await asyncio.to_thread(git_service.git_pull, project, ws_folder)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


class GitPublishRequest(BaseModel):
    branch: Optional[str] = None
    title: str
    body: str = ""
    base: Optional[str] = None
    draft: bool = True
    open_pr: bool = True


@router.post("/{project_id}/git/publish")
async def git_publish_route(project_id: str, payload: GitPublishRequest):
    """Commit, push a branch and open a PR/MR: the dashboard button for git_publish.

    Delegates to git_service.publish, which itself delegates to
    tools.git_publish.run_git_publish — the same function the git_publish
    agent tool calls, so the branch-protection refusals (never the default
    branch, never a PR/MR from a branch onto itself) apply exactly the same
    way here as they do to an agent's own call.
    """
    try:
        return await asyncio.to_thread(
            git_service.publish, project_id,
            branch=payload.branch, title=payload.title, body=payload.body,
            base=payload.base, draft=payload.draft, open_pr=payload.open_pr,
        )
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


# ─────────────────────────── BACKEND / SWAGGER ────────────────────────────

@router.get("/{project_id}/swagger-spec")
async def get_swagger_spec(project_id: str, base_url: Optional[str] = Query(None)):
    """Fetch the OpenAPI JSON spec from the project's backend."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    backend = project.backend
    if not backend.enabled:
        raise HTTPException(status_code=400, detail="Backend is not enabled for this project")

    base = base_url or backend.base_url or (f"http://localhost:{backend.port}" if backend.port else None)
    if not base:
        raise HTTPException(status_code=400, detail="No backend URL configured. Provide base_url parameter.")
    # Try common OpenAPI spec paths
    for spec_path in ["/openapi.json", "/swagger.json", "/api-docs"]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base}{spec_path}")
                if resp.status_code == 200:
                    return resp.json()
        except Exception:  # noqa: BLE001 - one unreadable entry must not stop the rest of the listing
            log.debug("get_swagger_spec: falling back after a failure", exc_info=True)
            continue

    raise HTTPException(status_code=502, detail="Could not fetch OpenAPI spec from backend")


@router.post("/{project_id}/api-request")
async def proxy_api_request(project_id: str, payload: ProjectApiRequest):
    """Proxy an HTTP request to the project's backend."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    backend = project.backend
    if not backend.enabled:
        raise HTTPException(status_code=400, detail="Backend is not enabled for this project")

    base = payload.base_url or backend.base_url or (f"http://localhost:{backend.port}" if backend.port else None)
    if not base:
        raise HTTPException(status_code=400, detail="No backend URL configured. Set base_url in the API tab.")

    try:
        return await proxy_service.proxy_api_request(
            base, payload.method, payload.path, payload.headers, payload.body)
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)


# ── The project registry chat ────────────────────────────────────────────────
#
# The other two project chats are about ONE project: the architect builds its
# graph, the planner turns that into tasks. This one is about the registry — it
# creates projects, renames them, retires them — so it hangs off the list page
