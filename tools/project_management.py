"""
Project management LangChain tools: create, inspect, modify and delete projects
(the organizational layer above tasks, persisted by ``projects.storage``).

A project lives inside a workspace, owns a folder in it, and optionally names a
git repo plus a frontend/backend the dashboard can reach. These tools cover the
same surface the Projects page does, minus the repo operations — cloning,
pushing and issue sync are outward-facing actions with credentials attached, so
they stay on the page where a person presses the button.

Creating a project also creates its folder inside the workspace, exactly as the
API does: an agent that creates a project and then cannot write into it has not
finished the job.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from common.entity_sink import record_entity
from common.workspace_context import (
    normalize_workspace_name,
    resolve_active_workspace,
)


def _json_ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def _json_err(message: str, *, code: str = "bad_request",
              extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2)


def _coerce_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


#: Valid values, mirrored from ``projects.models``. Listed in the error text so
#: a model that guessed "in_progress" is told what the statuses actually are.
_TYPES = ("general", "code", "research", "documentation")
_STATUSES = ("active", "archived", "completed")


def _store():
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    return ProjectStore(path=PROJECTS_FILE)


def _simplify(project, *, tasks_count: Optional[int] = None) -> Dict[str, Any]:
    from workspace import project_folder_name

    d = project.model_dump()
    # ``model_dump`` keeps the enum objects, and ``str()`` on one reads
    # "ProjectStatus.active" — the member name, not the value the API speaks.
    def _enum(value: Any, default: str) -> str:
        return str(getattr(value, "value", value) or default)

    out: Dict[str, Any] = {
        "project_id": d["id"], "name": d["name"],
        "description": d.get("description") or "",
        "status": _enum(d.get("status"), "active"),
        "type": _enum(d.get("type"), "general"),
        "workspace": d["workspace"],
        "folder": project_folder_name(d["name"]),
        "tags": list(d.get("tags") or []),
        "repo": {k: v for k, v in (d.get("repo") or {}).items() if v not in (None, "")},
        "frontend": {k: v for k, v in (d.get("frontend") or {}).items() if v not in (None, "")},
        "backend": {k: v for k, v in (d.get("backend") or {}).items() if v not in (None, "")},
    }
    if tasks_count is not None:
        out["tasks_count"] = tasks_count
    return out


def _enum_error(value: str, allowed: tuple, field: str) -> Optional[str]:
    if value and value not in allowed:
        return f"unknown {field} '{value}' — use one of: {', '.join(allowed)}"
    return None


# ── tools ─────────────────────────────────────────────────────────────────────

class ListProjectsInput(BaseModel):
    workspace: Optional[str] = Field(
        None, description="Workspace to list; defaults to the active workspace"
    )
    status: Optional[str] = Field(
        None, description="Filter by status: 'active', 'archived' or 'completed'"
    )


@tool("list_projects_tool", args_schema=ListProjectsInput)
def list_projects_tool(workspace: Optional[str] = None, status: Optional[str] = None) -> str:
    """List the projects in a workspace, with their type, status and task count."""
    try:
        from tasks import service as tasks_service

        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        projects = [p for p in _store().list() if not ws or p.workspace == ws]
        if status:
            projects = [p for p in projects
                        if str(getattr(p.status, "value", p.status)) == status]

        all_tasks = tasks_service.list_tasks()
        counts: Dict[str, int] = {}
        for task in all_tasks:
            pid = getattr(task, "project_id", None)
            if pid:
                counts[str(pid)] = counts.get(str(pid), 0) + 1

        return _json_ok({
            "workspace": ws,
            "count": len(projects),
            "projects": [_simplify(p, tasks_count=counts.get(p.id, 0)) for p in projects],
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to list projects: {e}")


class CreateProjectInput(BaseModel):
    name: str = Field(..., min_length=1, description="Project name (also names its folder in the workspace)")
    description: str = Field("", description="What the project is")
    type: str = Field(
        "general", description="'general', 'code', 'research' or 'documentation'"
    )
    tags: Optional[List[str]] = Field(None, description="Free-form tags")
    repo: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Git repo the project is backed by: {'type': 'github|gitlab|bitbucket|local|none', "
            "'url': '<remote url>', 'branch': 'main', 'local_path': '<path in workspace>', "
            "'remote_id': 'owner/repo'}. Records the link only — nothing is cloned."
        ),
    )
    frontend: Optional[Dict[str, Any]] = Field(
        None,
        description="{'enabled': true, 'port': 5173, 'dev_command': 'npm run dev', 'build_dir': 'dist'}",
    )
    backend: Optional[Dict[str, Any]] = Field(
        None,
        description="{'enabled': true, 'port': 8000, 'start_command': 'uvicorn main:app', 'swagger_path': '/docs'}",
    )
    workspace: Optional[str] = Field(
        None, description="Workspace to create the project in; defaults to the active workspace"
    )

    @field_validator("tags", "repo", "frontend", "backend", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


@tool("create_project_tool", args_schema=CreateProjectInput)
def create_project_tool(
    name: str,
    description: str = "",
    type: str = "general",
    tags: Optional[List[str]] = None,
    repo: Optional[Dict[str, Any]] = None,
    frontend: Optional[Dict[str, Any]] = None,
    backend: Optional[Dict[str, Any]] = None,
    workspace: Optional[str] = None,
) -> str:
    """Create a project inside a workspace, and its folder on disk.

    The workspace must already exist — projects cannot create one. `repo` only
    records where the code lives; nothing is cloned or pushed. Returns the
    created project and its `project_id`.
    """
    try:
        from projects.models import BackendConfig, FrontendConfig, Project, RepoConfig
        from workspace import get_workspace_folder, project_folder_name, resolve_project_root

        ws = normalize_workspace_name(workspace) or resolve_active_workspace()
        if not ws:
            return _json_err(
                "No workspace given and no active workspace — pass `workspace`.",
                code="no_workspace",
            )
        if get_workspace_folder(ws) is None:
            return _json_err(f"Workspace '{ws}' not found", code="not_found")

        bad = _enum_error(type, _TYPES, "type")
        if bad:
            return _json_err(bad, code="invalid")

        store = _store()
        clash = next(
            (p for p in store.list()
             if p.workspace == ws and p.name.strip().lower() == name.strip().lower()),
            None,
        )
        if clash:
            return _json_err(
                f"Workspace '{ws}' already has a project named '{name}' — projects "
                "share a folder namespace, so the name must be unique.",
                code="conflict", extra={"project_id": clash.id},
            )

        project = Project(
            name=name.strip(), description=description or "", type=type,
            workspace=ws, tags=list(tags or []),
        )
        if repo:
            project.repo = RepoConfig(**repo)
        if frontend:
            project.frontend = FrontendConfig(**frontend)
        if backend:
            project.backend = BackendConfig(**backend)

        store.add(project)

        # The folder is what makes the project usable by an agent; a failure to
        # create it is worth reporting rather than swallowing.
        folder_error = None
        try:
            resolve_project_root(project.workspace, project_folder_name(project.name))
        except Exception as e:  # noqa: BLE001
            folder_error = str(e)

        record_entity("project", project.id, "created", project.name)
        out: Dict[str, Any] = {
            "message": f"Project '{project.name}' created successfully",
            "project_id": project.id,
            "project": _simplify(project),
        }
        if folder_error:
            out["warnings"] = [f"project folder could not be created: {folder_error}"]
        return _json_ok(out)
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to create project: {e}")


class GetProjectInput(BaseModel):
    project_id: str = Field(..., min_length=1, description="ID of the project (from list_projects_tool)")


@tool("get_project_tool", args_schema=GetProjectInput)
def get_project_tool(project_id: str) -> str:
    """Get a project's full record: workspace, folder, repo link, frontend/backend config and task count."""
    try:
        from tasks import service as tasks_service

        project = _store().get(project_id)
        if not project:
            return _json_err("Project not found", code="not_found",
                             extra={"project_id": project_id})
        count = sum(1 for t in tasks_service.list_tasks()
                    if str(getattr(t, "project_id", "") or "") == project_id)
        record_entity("project", project_id, "viewed", project.name)
        return _json_ok({"project": _simplify(project, tasks_count=count)})
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to get project: {e}")


class ModifyProjectInput(BaseModel):
    project_id: str = Field(..., min_length=1, description="ID of the project to modify")
    name: Optional[str] = Field(None, description="New name (does NOT rename the existing folder)")
    description: Optional[str] = Field(None, description="New description")
    status: Optional[str] = Field(None, description="'active', 'archived' or 'completed'")
    type: Optional[str] = Field(None, description="'general', 'code', 'research' or 'documentation'")
    tags: Optional[List[str]] = Field(None, description="Replacement tag list")
    repo: Optional[Dict[str, Any]] = Field(None, description="Replacement repo config")
    frontend: Optional[Dict[str, Any]] = Field(None, description="Replacement frontend config")
    backend: Optional[Dict[str, Any]] = Field(None, description="Replacement backend config")

    @field_validator("tags", "repo", "frontend", "backend", mode="before")
    @classmethod
    def coerce(cls, v):
        return _coerce_json(v)


@tool("modify_project_tool", args_schema=ModifyProjectInput)
def modify_project_tool(
    project_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    tags: Optional[List[str]] = None,
    repo: Optional[Dict[str, Any]] = None,
    frontend: Optional[Dict[str, Any]] = None,
    backend: Optional[Dict[str, Any]] = None,
) -> str:
    """Change an existing project. Only the fields you pass are touched.

    Renaming changes the project's display name but not the folder already on
    disk, so the work stays where the agents left it. `status: 'archived'` is
    the reversible alternative to deleting.
    """
    try:
        store = _store()
        if not store.get(project_id):
            return _json_err("Project not found", code="not_found",
                             extra={"project_id": project_id})

        for value, allowed, field in ((status, _STATUSES, "status"), (type, _TYPES, "type")):
            bad = _enum_error(value or "", allowed, field)
            if bad:
                return _json_err(bad, code="invalid")

        fields: Dict[str, Any] = {}
        if name is not None:
            fields["name"] = name.strip()
        if description is not None:
            fields["description"] = description
        if status is not None:
            fields["status"] = status
        if type is not None:
            fields["type"] = type
        if tags is not None:
            fields["tags"] = list(tags)
        if repo is not None:
            fields["repo"] = repo
        if frontend is not None:
            fields["frontend"] = frontend
        if backend is not None:
            fields["backend"] = backend

        if not fields:
            return _json_err("Nothing to change — pass at least one field", code="invalid")

        updated = store.update(project_id, **fields)
        if not updated:
            return _json_err("Project not found", code="not_found",
                             extra={"project_id": project_id})
        record_entity("project", project_id, "updated", updated.name)
        return _json_ok({
            "message": f"Project '{updated.name}' updated successfully",
            "project_id": project_id, "project": _simplify(updated),
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to modify project: {e}")


class DeleteProjectInput(BaseModel):
    project_id: str = Field(..., min_length=1, description="ID of the project to delete")


@tool("delete_project_tool", args_schema=DeleteProjectInput)
def delete_project_tool(project_id: str) -> str:
    """Delete a project record and its saved structure graphs.

    The project's folder and files on disk are NOT removed, and neither are its
    tasks — they are simply left without a project. Refuses while tasks are
    still attached: archive the project instead, or move the tasks first.
    Confirm with the user before calling this.
    """
    try:
        from tasks import service as tasks_service

        store = _store()
        project = store.get(project_id)
        if not project:
            return _json_err("Project not found", code="not_found",
                             extra={"project_id": project_id})

        attached = sum(1 for t in tasks_service.list_tasks()
                       if str(getattr(t, "project_id", "") or "") == project_id)
        if attached:
            return _json_err(
                f"Project '{project.name}' still has {attached} task(s). Move or close "
                "them first, or set status='archived' instead of deleting.",
                code="conflict", extra={"tasks_count": attached},
            )

        if not store.delete(project_id):
            return _json_err("Project not found", code="not_found",
                             extra={"project_id": project_id})

        # The structure graphs are keyed by project id and become orphans
        # otherwise — the API's delete does the same.
        try:
            from common.paths import PROJECT_GRAPHS_FILE
            from projects.graph_store import ProjectGraphStore
            ProjectGraphStore(path=PROJECT_GRAPHS_FILE).delete_project(project_id)
        except Exception:
            pass

        return _json_ok({
            "message": f"Project '{project.name}' deleted successfully (its folder on disk was kept)",
            "project_id": project_id,
        })
    except Exception as e:  # noqa: BLE001
        return _json_err(f"Failed to delete project: {e}")


PROJECT_MANAGEMENT_TOOLS = [
    list_projects_tool,
    create_project_tool,
    get_project_tool,
    modify_project_tool,
    delete_project_tool,
]

__all__ = [
    "list_projects_tool",
    "create_project_tool",
    "get_project_tool",
    "modify_project_tool",
    "delete_project_tool",
    "PROJECT_MANAGEMENT_TOOLS",
]
