"""
Workspace-related API routes.
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import Optional
from pathlib import Path
import mimetypes
import os
import shutil

from tasks import service as tasks_service
from common.bootstrap import ensure_initial_state
from common.session_broker import notify_change
from workspace import (
    create_workspace_folder,
    attach_workspace_folder,
    is_attached_workspace,
    workspace_target,
    list_workspace_folders,
    get_workspace_metadata,
    get_workspace_folder,
    get_workspace_default_model_config,
    is_system_agent,
    update_workspace_metadata,
    delete_workspace_folder,
    get_workspace_instructions,
    set_workspace_instructions,
)
from workspace import storage as _workspace_storage
from models import WorkspaceCreate, WorkspaceAttach, WorkspaceAgentAction, WorkspaceFlowAction


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _is_safe_workspace_name(name: str) -> bool:
    """Whether a caller-supplied name is safe to hand to create_workspace_folder.

    A workspace name must be a single, ordinary path component: no "..", no
    ".", no embedded separator and no absolute path — anything else would make
    ``WORKSPACES_ROOT / name`` land somewhere other than a direct child of the
    workspaces root, and ``create_workspace_folder`` mkdirs it unconditionally.
    """
    if not name or name in (".", ".."):
        return False
    candidate = Path(name)
    return candidate.name == name and not candidate.is_absolute()


def _is_direct_workspace_entry(name: str) -> bool:
    """Whether ``name`` names an entry directly inside ``WORKSPACES_ROOT``.

    ``WORKSPACES_ROOT / name`` can *exist* on disk without ``name`` actually
    being a workspace: a name of ".." lexically walks up to the workspaces
    root's own parent (``AGENTS_HUB_ROOT``), which — once the service has run
    at all — always exists, so an existence check alone would pass with no
    workspace folder ever having been created there. Comparing the *lexical*
    join (``os.path.normpath``, which collapses ".."/"." without following
    symlinks) against the root catches that, while a genuinely attached
    workspace still passes: its entry is a symlink that lives directly in the
    root even though it legitimately *resolves* somewhere else entirely.
    """
    root = _workspace_storage.WORKSPACES_ROOT.resolve()
    entry = Path(os.path.normpath(str(root / name)))
    return entry.parent == root


def _ensure_writable_workspace(name: str) -> None:
    """Verify the workspace exists before writes; auto-create 'default' on demand.

    Re-runs bootstrap so a missing default workspace is seeded from `bootstrap/`.
    For any other name, raises 404 instead of silently writing nowhere — and,
    same as `_require_workspace_folder` below, a name that only looks like it
    exists by lexically escaping the workspaces root (see
    `_is_direct_workspace_entry`) is treated as not existing, not as a green
    light to delete or overwrite files outside it.
    """
    if get_workspace_folder(name) and _is_direct_workspace_entry(name):
        return
    if name == "default":
        ensure_initial_state()
        if get_workspace_folder(name):
            return
        create_workspace_folder(name)
        return
    raise HTTPException(status_code=404, detail=f"Workspace '{name}' does not exist")


def _require_workspace_folder(name: str) -> Path:
    """Look up an existing workspace for a read-only route, creating nothing.

    A read route must never resolve a caller-supplied name with
    ``create_workspace_folder``: that function mkdirs
    ``WORKSPACES_ROOT / name`` unconditionally, so a name such as
    "../../etc" walks the folder creation (and any later join under it)
    outside the workspaces root. Looking the workspace up instead means an
    unknown or traversal-shaped name simply 404s, and nothing is created.
    """
    folder = get_workspace_folder(name)
    if folder is None or not _is_direct_workspace_entry(name):
        raise HTTPException(status_code=404, detail=f"Workspace '{name}' does not exist")
    return folder


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
        # `path` stays the entry under the workspaces root so it keeps naming the
        # workspace; `target` is where an attached one actually lives.
        attached = p.is_symlink()
        items.append({
            "name": name,
            "path": str(p),
            "tasks_count": len(ws_tasks),
            "attached": attached,
            "target": str(p.resolve()) if attached else None,
        })
    return items


@router.post("")
async def create_workspace(payload: WorkspaceCreate):
    if payload.name and not _is_safe_workspace_name(payload.name):
        raise HTTPException(status_code=400, detail=f"'{payload.name}' is not a valid workspace name")
    try:
        p = create_workspace_folder(payload.name)
        # Let live listeners (e.g. the header workspace picker) refresh their list.
        notify_change("workspaces")
        return {"name": p.name, "path": str(p)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/attach")
async def attach_workspace(payload: WorkspaceAttach):
    """Register an existing directory on this machine as a workspace.

    Nothing is copied: the workspace becomes a link to that directory, so agents
    read and write the real files in place and a git checkout stays intact.

    `path` is resolved by *this process*, so it must exist for the backend. When
    the backend runs in a container that means a path inside the container — bind
    mount the host directory first, and pass the in-container path.
    """
    try:
        link = attach_workspace_folder(payload.path, payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Could not attach: {e}")
    notify_change("workspaces")
    return {
        "name": link.name,
        "path": str(link),
        "target": str(link.resolve()),
        "attached": True,
    }


@router.get("/{name}")
async def get_workspace(name: str):
    root = _require_workspace_folder(name)
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
    # Detaching only drops the link; the directory behind an attached workspace
    # is the user's and is never deleted from here.
    was_attached = is_attached_workspace(name)
    target = str(workspace_target(name)) if was_attached else None
    deleted = delete_workspace_folder(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")
    notify_change("workspaces")
    return {"deleted": True, "detached": was_attached, "target_kept": target}


@router.get("/{name}/files")
async def list_workspace_files_by_name(name: str, glob: Optional[str] = "**/*"):
    root = _require_workspace_folder(name)
    pattern = glob or "**/*"
    files = []
    directories = []
    try:
        for p in root.rglob("*"):
            try:
                rel = p.relative_to(root).as_posix()
                if p.is_dir():
                    directories.append(rel)
                elif p.is_file():
                    files.append(rel)
            except Exception:
                pass
        if pattern and pattern not in {"**/*", "*"}:
            import fnmatch
            files = [p for p in files if fnmatch.fnmatch(p, pattern)]
            directories = [p for p in directories if fnmatch.fnmatch(p, pattern)]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"files": sorted(files), "directories": sorted(directories)}


@router.get("/{name}/file-content")
async def get_workspace_file_content(name: str, path: str):
    root = _require_workspace_folder(name).resolve()
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

    is_pdf = candidate.suffix.lower() == ".pdf"
    # PDFs are binary and typically larger than text files, so allow a bigger
    # cap before extracting their text content for preview.
    max_bytes = 5_000_000 if is_pdf else 256_000
    try:
        size = candidate.stat().st_size
        if size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File is too large to preview ({size} bytes). Limit is {max_bytes} bytes.",
            )
        if is_pdf:
            content = _extract_pdf_preview(candidate)
        else:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        return {
            "path": rel_path,
            "size": size,
            "content": content,
            "is_pdf": is_pdf,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _extract_pdf_preview(candidate: Path) -> str:
    """Extract text from a PDF for dashboard preview."""
    try:
        from pypdf import PdfReader
    except Exception:
        raise HTTPException(
            status_code=415,
            detail="PDF preview is unavailable (pypdf not installed on the server).",
        )
    try:
        reader = PdfReader(str(candidate))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {e}")

    pages = []
    has_text = False
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            has_text = True
        pages.append(f"--- Page {i + 1} ---\n{text}")
    if not has_text:
        return "[This PDF contains no extractable text (it may be scanned/image-only).]"
    return "\n\n".join(pages).strip()


@router.get("/{name}/file-raw")
async def get_workspace_file_raw(name: str, path: str):
    """Serve a workspace file's raw bytes (e.g. for in-browser PDF rendering)."""
    root = _require_workspace_folder(name).resolve()
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

    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    # inline so browsers render (PDF/image) instead of forcing a download
    return FileResponse(
        str(candidate),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
    )


@router.delete("/{name}/files")
async def delete_workspace_file(name: str, path: str):
    """Delete one file or directory from a workspace."""
    _ensure_writable_workspace(name)
    root = create_workspace_folder(name).resolve()
    rel_path = (path or "").strip().strip("/")
    if not rel_path:
        raise HTTPException(status_code=400, detail="Query parameter 'path' is required")

    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if candidate.is_dir():
            shutil.rmtree(candidate)
            deleted_type = "directory"
        elif candidate.is_file():
            candidate.unlink()
            deleted_type = "file"
        else:
            raise HTTPException(status_code=400, detail="Path is neither a file nor a directory")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"deleted": True, "path": rel_path, "type": deleted_type}


@router.post("/{name}/files/upload")
async def upload_workspace_file(
    name: str,
    file: UploadFile = File(...),
    path: Optional[str] = Form(""),
):
    """Upload a file into the workspace, optionally under a subdirectory.

    `path` is an optional relative directory (e.g. "docs/notes"); the file is
    written as `<workspace>/<path>/<filename>`. Path traversal is rejected.
    """
    _ensure_writable_workspace(name)
    root = create_workspace_folder(name).resolve()

    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=400, detail="A valid filename is required")

    rel_dir = (path or "").strip().strip("/")
    dest = (root / rel_dir / filename).resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        dest.write_bytes(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "path": dest.relative_to(root).as_posix(),
        "size": len(content),
    }


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
    _ensure_writable_workspace(name)
    provider = payload.get("provider", "")
    model = payload.get("model", "")
    if not provider or provider == "workspace_default":
        update_workspace_metadata(name, {"model_override": {}})
    else:
        update_workspace_metadata(name, {"model_override": {"provider": provider, "model": model}})
    return {"provider": provider, "model": model}


@router.put("/{name}/default-model")
async def set_workspace_default_model(name: str, payload: dict):
    """Set the workspace's default model (the per-workspace default tier).

    Stored top-level as ``model_default`` so it survives Settings-page saves that
    replace ``settings``. Setting a default also clears any transient header
    ``model_override`` so the new default takes effect immediately. An empty
    provider clears the workspace default (falls back to the global .env default).
    """
    _ensure_writable_workspace(name)
    provider = (payload.get("provider") or "").strip()
    model = (payload.get("model") or "").strip()
    if not provider or provider in ("global", "workspace_default"):
        update_workspace_metadata(name, {"model_default": {}, "model_override": {}})
        return {"provider": "", "model": ""}
    update_workspace_metadata(name, {"model_default": {"provider": provider, "model": model}, "model_override": {}})
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
    _ensure_writable_workspace(name)
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="overrides must be a key-value object")
    # Strip empty-string values (treat as cleared)
    cleaned = {k: v for k, v in overrides.items() if v is not None and str(v).strip() != ""}
    update_workspace_metadata(name, {"settings": cleaned})
    # The log level is the one override this process acts on itself, so re-read
    # it now instead of making the user restart the backend. Resolving through
    # the active workspace means editing some *other* workspace is a no-op here.
    try:
        from common.logging_config import configure_logging_for_active_workspace
        configure_logging_for_active_workspace()
    except Exception:
        pass
    return {"overrides": cleaned}


@router.get("/{name}/env")
async def get_workspace_env(name: str):
    """Return workspace-scoped environment variables."""
    metadata = get_workspace_metadata(name)
    return {"env_vars": metadata.get("env_vars", {})}


@router.put("/{name}/env")
async def update_workspace_env(name: str, payload: dict):
    """Replace workspace-scoped environment variables."""
    _ensure_writable_workspace(name)
    env_vars = payload.get("env_vars", {})
    if not isinstance(env_vars, dict):
        raise HTTPException(status_code=400, detail="env_vars must be a key-value object")
    update_workspace_metadata(name, {"env_vars": env_vars})
    return {"env_vars": env_vars}


@router.post("/{name}/agents")
async def add_agent_to_workspace(name: str, action: WorkspaceAgentAction):
    _ensure_writable_workspace(name)
    from agents.registry import get_agent as reg_get_agent
    spec = reg_get_agent(action.agent_id)
    if name != "default":
        if spec and spec.default_workspace_only:
            raise HTTPException(
                status_code=403,
                detail=f"Agent '{action.agent_id}' is restricted to the default workspace and cannot be added to other workspaces.",
            )
    # A workspace-owned agent that is not shared cannot be added to any other
    # workspace. Expose it from the agent's detail page to make it available
    # everywhere.
    if (
        spec
        and spec.owner_workspace
        and not spec.shared
        and spec.owner_workspace != name
        and not is_system_agent(action.agent_id)
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Agent '{action.agent_id}' belongs to workspace "
                f"'{spec.owner_workspace}' and is not shared. Expose it from the "
                f"agent's details page to add it to other workspaces."
            ),
        )
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    if action.agent_id not in allowed:
        allowed.append(action.agent_id)
        update_workspace_metadata(name, {"allowed_agents": allowed})
    return {"allowed_agents": allowed}


@router.post("/{name}/flows")
async def add_flow_to_workspace(name: str, action: WorkspaceFlowAction):
    """Add a marketplace flow to a workspace.

    Clones the flow into the workspace (``origin_flow_id`` points back at the
    published flow) and adds every non-system agent the flow uses to the
    workspace's ``allowed_agents``, so the whole pipeline is runnable there.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    from agents.registry import get_agent as reg_get_agent
    from flow import store as flow_store

    _ensure_writable_workspace(name)

    flow = flow_store.get_flow(action.flow_id)
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    if not flow.get("shared") and flow.get("workspace") not in (None, "", name):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Flow '{action.flow_id}' belongs to workspace "
                f"'{flow.get('workspace')}' and is not published to the marketplace."
            ),
        )

    # Validate the flow's agents against the same rules as adding them one by one.
    agent_ids = flow_store.flow_agent_ids(flow)
    blockers = []
    for agent_id in agent_ids:
        if is_system_agent(agent_id):
            continue
        spec = reg_get_agent(agent_id)
        if not spec:
            blockers.append(f"agent '{agent_id}' is not registered")
        elif spec.default_workspace_only and name != "default":
            blockers.append(f"agent '{agent_id}' is restricted to the default workspace")
        elif spec.owner_workspace and not spec.shared and spec.owner_workspace != name:
            blockers.append(
                f"agent '{agent_id}' belongs to workspace '{spec.owner_workspace}' and is not shared"
            )
    if blockers:
        raise HTTPException(
            status_code=403,
            detail="Cannot add flow to this workspace: " + "; ".join(blockers),
        )

    metadata = get_workspace_metadata(name)
    allowed = list(metadata.get("allowed_agents") or [])
    added_agents = []
    for agent_id in agent_ids:
        if is_system_agent(agent_id):
            continue
        if agent_id not in allowed:
            allowed.append(agent_id)
            added_agents.append(agent_id)
    if added_agents:
        update_workspace_metadata(name, {"allowed_agents": allowed})

    # Reuse an existing copy instead of cloning the same flow twice.
    existing = None
    if flow.get("workspace") == name:
        existing = flow
    else:
        existing = next(
            (
                f for f in flow_store.list_flows()
                if f.get("workspace") == name and f.get("origin_flow_id") == flow["id"]
            ),
            None,
        )
    # Authorize the workspace-local flow so it can be assigned to tasks here.
    def _authorize_flow(flow_id: str) -> None:
        allowed_flows = list(get_workspace_metadata(name).get("allowed_flows") or [])
        if flow_id not in allowed_flows:
            allowed_flows.append(flow_id)
            update_workspace_metadata(name, {"allowed_flows": allowed_flows})

    if existing:
        _authorize_flow(existing["id"])
        return {"flow": existing, "added_agents": added_agents, "already_present": True}

    now = datetime.now(timezone.utc).isoformat()
    clone = {
        **flow,
        "id": str(uuid4()),
        "workspace": name,
        "shared": False,
        "origin_flow_id": flow["id"],
        "task_id": None,
        "running": False,
        "created_at": now,
        "updated_at": now,
    }
    flow_store.save_flow(clone)
    _authorize_flow(clone["id"])
    return {"flow": clone, "added_agents": added_agents, "already_present": False}


@router.delete("/{name}/flows/{flow_id}")
async def remove_flow_from_workspace(name: str, flow_id: str):
    """Revoke a flow's authorization in a workspace (removes it from allowed_flows)."""
    _ensure_writable_workspace(name)
    metadata = get_workspace_metadata(name)
    allowed = list(metadata.get("allowed_flows") or [])
    if flow_id in allowed:
        allowed.remove(flow_id)
        update_workspace_metadata(name, {"allowed_flows": allowed})
    return {"allowed_flows": allowed}


@router.delete("/{name}/agents/{agent_id}")
async def remove_agent_from_workspace(name: str, agent_id: str):
    _ensure_writable_workspace(name)
    if is_system_agent(agent_id):
        raise HTTPException(
            status_code=403,
            detail=f"Agent '{agent_id}' is a system agent and cannot be removed from a workspace.",
        )
    metadata = get_workspace_metadata(name)
    allowed = metadata.get("allowed_agents", [])
    updates = {}
    if agent_id in allowed:
        allowed.remove(agent_id)
        updates["allowed_agents"] = allowed
    # Drop this workspace's memory assignment for the agent along with it.
    mem_overrides = dict(metadata.get("agent_memory_overrides", {}))
    if agent_id in mem_overrides:
        mem_overrides.pop(agent_id)
        updates["agent_memory_overrides"] = mem_overrides
    if updates:
        update_workspace_metadata(name, updates)
    return {"allowed_agents": allowed}


@router.put("/{name}/agents/{agent_id}/capacity")
async def set_workspace_agent_capacity(name: str, agent_id: str, payload: dict):
    _ensure_writable_workspace(name)
    capacity = payload.get("capacity")
    metadata = get_workspace_metadata(name)
    overrides = dict(metadata.get("agent_capacity_overrides", {}))
    if capacity is None:
        overrides.pop(agent_id, None)
    else:
        overrides[agent_id] = int(capacity)
    update_workspace_metadata(name, {"agent_capacity_overrides": overrides})
    return {"agent_id": agent_id, "capacity": capacity}


@router.get("/{name}/instructions")
async def get_workspace_instructions_route(name: str):
    """Return the workspace-level instructions markdown."""
    content = get_workspace_instructions(name)
    return {"instructions": content}


@router.put("/{name}/instructions")
async def set_workspace_instructions_route(name: str, payload: dict):
    """Save the workspace-level instructions markdown."""
    content = payload.get("instructions", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="instructions must be a string")
    set_workspace_instructions(name, content)
    return {"instructions": content}


@router.delete("/{name}/agents/{agent_id}/capacity")
async def remove_workspace_agent_capacity(name: str, agent_id: str):
    _ensure_writable_workspace(name)
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
    _ensure_writable_workspace(name)
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
