"""
Workspace-related API routes.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path

from common import tasks_service
from common.workspace import (
    create_workspace_folder, 
    list_workspace_folders, 
    get_workspace_metadata, 
    update_workspace_metadata
)
from models import WorkspaceCreate, WorkspaceAgentAction


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def task_to_dict(task):
    """Convert task object to dictionary."""
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data


def _calc_task_progress(task, all_tasks):
    """Calculate task progress based on subtask completion."""
    # Only count direct subtasks of this task
    subs = [st for st in all_tasks if st.parent_id == task.id]
    if not subs:
        return 100 if getattr(task, "status", None) == "done" or getattr(task, "status", None).value == "done" else 0
    done = 0
    for st in subs:
        st_status = st.status.value if hasattr(st.status, "value") else str(st.status)
        if st_status == "done":
            done += 1
    return int(round((done / len(subs)) * 100)) if subs else 0


@router.get("")
async def list_workspaces():
    roots = list_workspace_folders()
    if not roots:
        # Create default workspace if none exists
        try:
            p = create_workspace_folder("default")
            roots = [p]
        except Exception:
            pass

    all_tasks = tasks_service.list_tasks()
    items = []
    for p in roots:
        name = p.name
        ws_tasks = [t for t in all_tasks if (t.workspace or "").strip() == name]
        items.append({
            "name": name,
            "path": str(p),
            "tasks_count": len(ws_tasks),
        })
    return items


@router.post("")
async def create_workspace(payload: WorkspaceCreate):
    try:
        p = create_workspace_folder(payload.name)
        return {"name": p.name, "path": str(p)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{name}")
async def get_workspace(name: str):
    root = create_workspace_folder(name)
    all_tasks = tasks_service.list_tasks()
    ws_tasks = [t for t in all_tasks if (t.workspace or "").strip() == root.name and not t.parent_id]
    tasks_info = []
    for t in ws_tasks:
        tasks_info.append({
            **task_to_dict(t),
            "progress": _calc_task_progress(t, all_tasks),
        })

    metadata = get_workspace_metadata(root.name)

    # files separately via endpoint, here basic info
    return {
        "name": root.name,
        "path": str(root),
        "tasks": tasks_info,
        "metadata": metadata
    }


@router.get("/{name}/files")
async def list_workspace_files_by_name(name: str, glob: Optional[str] = "**/*"):
    root = create_workspace_folder(name)
    pattern = glob or "**/*"
    files = []
    try:
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    files.append(p.relative_to(root).as_posix())
                except Exception:
                    pass
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"files": files}


@router.get("/{name}/file-content")
async def get_workspace_file_content(name: str, path: str):
    root = create_workspace_folder(name).resolve()
    rel_path = (path or "").strip()
    if not rel_path:
        raise HTTPException(status_code=400, detail="Query parameter 'path' is required")

    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    max_bytes = 256_000
    try:
        size = candidate.stat().st_size
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File is too large to preview ({size} bytes). Limit is {max_bytes} bytes.",
            )
        content = candidate.read_text(encoding="utf-8", errors="replace")
        return {
            "path": rel_path,
            "size": size,
            "content": content,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/agents")
async def add_agent_to_workspace(name: str, action: WorkspaceAgentAction):
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    if action.agent_id not in allowed:
        allowed.append(action.agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
    return {"allowed_agents": allowed}


@router.delete("/{name}/agents/{agent_id}")
async def remove_agent_from_workspace(name: str, agent_id: str):
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    if agent_id in allowed:
        allowed.remove(agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
    return {"allowed_agents": allowed}
