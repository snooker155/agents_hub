"""Project registry CRUD: list, create, read, update and delete."""
from ._common import (_graph_store, _project_to_dict, store)
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from tasks import service as tasks_service
from workspace import get_workspace_folder, resolve_project_root, project_folder_name
from models import ProjectCreate, ProjectUpdate
from projects.models import Project


router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────── CRUD ────────────────────────────

@router.get("")
async def list_projects(workspace: Optional[str] = Query(None)):
    projects = store().list()
    if workspace:
        projects = [p for p in projects if p.workspace == workspace]
    result = []
    all_tasks = tasks_service.list_tasks()
    for p in projects:
        d = _project_to_dict(p)
        d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == p.id)
        d["folder"] = project_folder_name(p.name)
        result.append(d)
    return result


@router.post("")
async def create_project(payload: ProjectCreate):
    # Validate workspace exists
    ws_folder = get_workspace_folder(payload.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{payload.workspace}' not found")

    project = Project(
        name=payload.name,
        description=payload.description,
        type=payload.type or "general",
        workspace=payload.workspace,
        tags=payload.tags or [],
    )
    if payload.repo:
        from projects.models import RepoConfig
        project.repo = RepoConfig(**payload.repo)
    if payload.frontend:
        from projects.models import FrontendConfig
        project.frontend = FrontendConfig(**payload.frontend)
    if payload.backend:
        from projects.models import BackendConfig
        project.backend = BackendConfig(**payload.backend)

    store().add(project)

    # Create the project subfolder inside the workspace so agents have a place to work
    try:
        folder = project_folder_name(project.name)
        resolve_project_root(project.workspace, folder)
    except Exception:
        pass

    d = _project_to_dict(project)
    d["folder"] = project_folder_name(project.name)
    return d


@router.get("/{project_id}")
async def get_project(project_id: str):
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    d = _project_to_dict(project)
    all_tasks = tasks_service.list_tasks()
    d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == project_id)
    d["folder"] = project_folder_name(project.name)
    return d


@router.put("/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdate):
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    fields = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.description is not None:
        fields["description"] = payload.description
    if payload.status is not None:
        fields["status"] = payload.status
    if payload.type is not None:
        fields["type"] = payload.type
    if payload.tags is not None:
        fields["tags"] = payload.tags
    if payload.repo is not None:
        fields["repo"] = payload.repo
    if payload.frontend is not None:
        fields["frontend"] = payload.frontend
    if payload.backend is not None:
        fields["backend"] = payload.backend

    updated = store().update(project_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_dict(updated)


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    deleted = store().delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    _graph_store.delete_project(project_id)
    return {"deleted": True}


