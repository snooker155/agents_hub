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
    get_workspace_default_model_config,
    update_workspace_metadata,
    delete_workspace_folder,
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


@router.delete("/{name}")
async def delete_workspace(name: str):
    if name == "default":
        raise HTTPException(status_code=403, detail="The default workspace cannot be deleted")
    deleted = delete_workspace_folder(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {"deleted": True}


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


@router.get("/{name}/model")
async def get_workspace_model(name: str):
    """Return workspace model state including the resolved global default from .env.
    - global_default: DEFAULT_PROVIDER + its model from .env (lowest-priority fallback)
    - workspace_default: default model resolved from workspace settings
    - override: explicitly set via UI picker (provider='global' forces global over workspace_default)
    """
    from pathlib import Path as _Path
    from common.config import settings as _cfg

    # Read .env directly so we always get the current on-disk value
    # parents[3] = agents_hub/ (service root): routes/ → backend/ → dashboard/ → agents_hub/
    env_file = _Path(__file__).resolve().parents[3] / ".env"
    env: dict = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip("\"'")

    global_provider = env.get("DEFAULT_PROVIDER") or "openai"
    provider_model_map = {
        "openai":    env.get("OPENAI_MODEL") or _cfg.model,
        "anthropic": env.get("ANTHROPIC_MODEL") or "",
        "google":    env.get("GOOGLE_MODEL") or "",
        "ollama":    env.get("OLLAMA_MODEL") or "",
        "lmstudio":  env.get("LMSTUDIO_MODEL") or "",
    }
    global_model = provider_model_map.get(global_provider) or ""

    metadata = get_workspace_metadata(name)
    override = metadata.get("model_override") or {}
    ws_default = get_workspace_default_model_config(metadata)
    return {
        "global_default": {
            "provider": global_provider,
            "model": global_model,
        },
        "workspace_default": {
            "provider": ws_default.get("provider", ""),
            "model": ws_default.get("model", ""),
        },
        "override": {
            "provider": override.get("provider", ""),
            "model": override.get("model", ""),
        },
    }


@router.put("/{name}/model")
async def set_workspace_model(name: str, payload: dict):
    """Set workspace-scoped model override.
    provider='global' forces global DEFAULT_PROVIDER even if workspace has a default model.
    provider='' or 'workspace_default' clears the override (falls back to workspace settings then global).
    """
    provider = payload.get("provider", "")
    model = payload.get("model", "")
    if not provider or provider == "workspace_default":
        update_workspace_metadata(name, {"model_override": {}})
    else:
        update_workspace_metadata(name, {"model_override": {"provider": provider, "model": model}})
    return {"provider": provider, "model": model}


@router.get("/{name}/settings-overrides")
async def get_workspace_settings_overrides(name: str):
    """Return workspace-scoped settings (raw values, not resolved)."""
    metadata = get_workspace_metadata(name)
    overrides = metadata.get("settings", {})
    return {"overrides": overrides}


@router.put("/{name}/settings-overrides")
async def update_workspace_settings_overrides(name: str, payload: dict):
    """Replace workspace-scoped settings."""
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="overrides must be a key-value object")
    # Strip empty-string values (treat as cleared)
    cleaned = {k: v for k, v in overrides.items() if v is not None and str(v).strip() != ""}
    update_workspace_metadata(name, {"settings": cleaned})
    return {"overrides": cleaned}


@router.get("/{name}/env")
async def get_workspace_env(name: str):
    """Return workspace-scoped environment variables."""
    metadata = get_workspace_metadata(name)
    return {"env_vars": metadata.get("env_vars", {})}


@router.put("/{name}/env")
async def update_workspace_env(name: str, payload: dict):
    """Replace workspace-scoped environment variables."""
    env_vars = payload.get("env_vars", {})
    if not isinstance(env_vars, dict):
        raise HTTPException(status_code=400, detail="env_vars must be a key-value object")
    update_workspace_metadata(name, {"env_vars": env_vars})
    return {"env_vars": env_vars}


@router.post("/{name}/agents")
async def add_agent_to_workspace(name: str, action: WorkspaceAgentAction):
    if name != "default":
        from agents.registry import get_agent as reg_get_agent
        spec = reg_get_agent(action.agent_id)
        if spec and spec.default_workspace_only:
            raise HTTPException(
                status_code=403,
                detail=f"Agent '{action.agent_id}' is restricted to the default workspace and cannot be added to other workspaces.",
            )
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


@router.put("/{name}/agents/{agent_id}/capacity")
async def set_workspace_agent_capacity(name: str, agent_id: str, payload: dict):
    capacity = payload.get("capacity")
    metadata = get_workspace_metadata(name)
    overrides = dict(metadata.get("agent_capacity_overrides", {}))
    if capacity is None:
        overrides.pop(agent_id, None)
    else:
        overrides[agent_id] = int(capacity)
    update_workspace_metadata(name, {"agent_capacity_overrides": overrides})
    return {"agent_id": agent_id, "capacity": capacity}


@router.delete("/{name}/agents/{agent_id}/capacity")
async def remove_workspace_agent_capacity(name: str, agent_id: str):
    metadata = get_workspace_metadata(name)
    overrides = dict(metadata.get("agent_capacity_overrides", {}))
    overrides.pop(agent_id, None)
    update_workspace_metadata(name, {"agent_capacity_overrides": overrides})
    return {"agent_id": agent_id, "capacity": None}


@router.get("/{name}/agent-mode")
async def get_workspace_agent_mode(name: str):
    """Return the agent mode configured for this workspace.

    Returns the workspace-specific override when set, otherwise null (meaning
    the global AGENT_EXECUTION_MODE setting applies).
    """
    metadata = get_workspace_metadata(name)
    return {"agent_mode": (metadata.get("settings") or {}).get("agent_mode") or None}


@router.put("/{name}/agent-mode")
async def set_workspace_agent_mode(name: str, payload: dict):
    """Set the agent mode for this workspace ('local' or 'docker').

    Pass agent_mode=null to clear the override and fall back to the global setting.
    """
    mode = payload.get("agent_mode")
    if mode is not None and mode not in ("local", "docker"):
        raise HTTPException(status_code=400, detail="agent_mode must be 'local', 'docker', or null")
    metadata = get_workspace_metadata(name)
    settings = dict(metadata.get("settings") or {})
    if mode is None:
        settings.pop("agent_mode", None)
    else:
        settings["agent_mode"] = mode
    update_workspace_metadata(name, {"settings": settings})
    return {"agent_mode": mode}
