"""
Workspace-related API routes.
"""
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from typing import List, Optional
from pathlib import Path
import mimetypes
import os
import shutil

from tasks import service as tasks_service
from common.bootstrap import ensure_initial_state
from common.session_broker import notify_change
from common import audit, identity
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
from models import WorkspaceCreate, WorkspaceAttach, WorkspaceAgentAction, WorkspaceFlowAction, WorkspaceListItem, WorkspacePersonalMemoryUpdate, WorkspaceRoleUpdate
import logging

log = logging.getLogger(__name__)


router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _visible_to_caller(request, roots: List[Path]) -> List[Path]:
    """Filter a workspace listing down to what the caller may see.

    Only ``multi`` filters: in ``single`` and ``token`` mode there is one
    operator and every workspace is theirs.
    """
    from common import identity
    if identity.current_mode() != "multi":
        return roots
    principal = identity.request_principal(request)
    if principal is None or principal.is_admin:
        return roots
    allowed = set(identity.workspaces_for_user(principal.id))
    return [p for p in roots if p.name in allowed]


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


def _person_label(user_id: str) -> str:
    """Whose personal workspace this is, for the picker."""
    user = identity.get_user(user_id) if identity.current_mode() == "multi" else None
    return (user or {}).get("display_name") or (user or {}).get("username") or user_id


@router.get("", response_model=List[WorkspaceListItem])
async def list_workspaces(request: Request):
    roots = list_workspace_folders()
    if not roots:
        # Create default workspace if none exists
        try:
            p = create_workspace_folder("default")
            roots = [p]
        except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
            log.debug("list_workspaces: best-effort step failed", exc_info=True)

    # A person in multi mode gets their personal workspace the first time they
    # look (common/personal_workspace.py); listed first, as theirs.
    from common import personal_workspace
    own_personal = personal_workspace.ensure_for_principal(identity.request_principal(request))
    if own_personal and all(p.name != own_personal for p in roots):
        roots = list_workspace_folders()

    # Under AUTH_MODE=multi a user sees the workspaces they are a member of and
    # nothing else; an admin sees all of them. A no-op in the single-operator
    # modes, where there is nobody to hide anything from. See common/identity.py.
    roots = _visible_to_caller(request, roots)

    all_tasks = tasks_service.list_tasks()
    items = []
    for p in roots:
        name = p.name
        ws_tasks = [t for t in all_tasks if (t.workspace or "").strip() == name]
        # `path` stays the entry under the workspaces root so it keeps naming the
        # workspace; `target` is where an attached one actually lives.
        attached = p.is_symlink()
        personal_of = personal_workspace.owner_of(name)
        items.append({
            "name": name,
            "path": str(p),
            "tasks_count": len(ws_tasks),
            "attached": attached,
            "target": str(p.resolve()) if attached else None,
            "personal": bool(personal_of),
            "personal_of": personal_of,
            "personal_label": _person_label(personal_of) if personal_of else None,
            "own_personal": bool(own_personal) and name == own_personal,
        })

    def _order(item):
        # The caller's own personal workspace, then default, then the rest by
        # name, other people's personal ones (an admin sees them) last.
        if item["own_personal"]:
            return (0, "")
        if item["name"] == "default":
            return (1, "")
        return (3 if item["personal"] else 2, item["name"])

    items.sort(key=_order)
    return items


def _refuse_personal_name(name: Optional[str]) -> None:
    """Names under ``personal-`` belong to personal workspaces, which only
    common/personal_workspace.py creates."""
    from common import personal_workspace
    if name and personal_workspace.is_reserved_name(name):
        raise HTTPException(
            status_code=400,
            detail=f"'{personal_workspace.PREFIX}' is reserved for personal workspaces")


@router.post("")
async def create_workspace(payload: WorkspaceCreate):
    if payload.name and not _is_safe_workspace_name(payload.name):
        raise HTTPException(status_code=400, detail=f"'{payload.name}' is not a valid workspace name")
    _refuse_personal_name(payload.name)
    try:
        p = create_workspace_folder(payload.name)
        # Let live listeners (e.g. the header workspace picker) refresh their list.
        notify_change("workspaces")
        return {"name": p.name, "path": str(p)}
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
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
    # The workspace takes the target folder's own name (workspace/storage.py).
    _refuse_personal_name(Path(str(payload.path or "")).name)
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
    from common import personal_workspace
    if personal_workspace.is_personal(name):
        raise HTTPException(status_code=403, detail="A personal workspace cannot be deleted")
    # Detaching only drops the link; the directory behind an attached workspace
    # is the user's and is never deleted from here.
    was_attached = is_attached_workspace(name)
    target = str(workspace_target(name)) if was_attached else None
    deleted = delete_workspace_folder(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workspace not found")
    notify_change("workspaces")
    return {"deleted": True, "detached": was_attached, "target_kept": target}


def _target_rel_path(name: str, path: Optional[str], file_id: Optional[str]) -> str:
    """The workspace-relative path a file route acts on. A link names the
    file by ``file_id`` (files/service.py); ``path`` stays for old links and
    for folders, which have no id."""
    fid = (file_id or "").strip()
    if fid:
        from files import service as files_service
        rel = files_service.folder_path_of(fid, name)
        if rel is None:
            raise HTTPException(status_code=404, detail="File not found")
        return rel
    rel_path = (path or "").strip()
    if not rel_path:
        raise HTTPException(status_code=400, detail="Query parameter 'file_id' or 'path' is required")
    return rel_path


@router.get("/{name}/files")
async def list_workspace_files_by_name(name: str, glob: Optional[str] = "**/*"):
    """The folder's files and directories as workspace-relative paths, and
    ``ids``: path to file id for each file the registry knows, the id a link
    to the file carries."""
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
            except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
                log.debug("list_workspace_files_by_name: best-effort step failed", exc_info=True)
        if pattern and pattern not in {"**/*", "*"}:
            import fnmatch
            files = [p for p in files if fnmatch.fnmatch(p, pattern)]
            directories = [p for p in directories if fnmatch.fnmatch(p, pattern)]
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))
    from files import service as files_service
    known = files_service.folder_ids(name)
    ids = {rel: known[rel] for rel in files if rel in known}
    return {"files": sorted(files), "directories": sorted(directories), "ids": ids}


@router.get("/{name}/file-id")
async def get_workspace_file_id(name: str, path: str):
    """The file id of a folder file, registering the file when nothing wrote
    it through the registry yet, so the page can put the id in its address."""
    from files import service as files_service

    root = _require_workspace_folder(name).resolve()
    rel_path = (path or "").strip()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not rel_path or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        record = files_service.ensure_folder_record(name, rel_path)
    except files_service.FileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"file_id": record["file_id"], "path": rel_path}


@router.get("/{name}/file-content")
async def get_workspace_file_content(name: str, file_id: Optional[str] = None, path: Optional[str] = None):
    root = _require_workspace_folder(name).resolve()
    rel_path = _target_rel_path(name, path, file_id)

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
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))


def _extract_pdf_preview(candidate: Path) -> str:
    """Extract text from a PDF for dashboard preview."""
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
        raise HTTPException(
            status_code=415,
            detail="PDF preview is unavailable (pypdf not installed on the server).",
        )
    try:
        reader = PdfReader(str(candidate))
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=422, detail=f"Failed to parse PDF: {e}")

    pages = []
    has_text = False
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
            log.debug("_extract_pdf_preview: falling back after a failure", exc_info=True)
            text = ""
        if text:
            has_text = True
        pages.append(f"--- Page {i + 1} ---\n{text}")
    if not has_text:
        return "[This PDF contains no extractable text (it may be scanned/image-only).]"
    return "\n\n".join(pages).strip()


@router.get("/{name}/file-raw")
async def get_workspace_file_raw(name: str, file_id: Optional[str] = None, path: Optional[str] = None):
    """Serve a workspace file's raw bytes (e.g. for in-browser PDF rendering)."""
    root = _require_workspace_folder(name).resolve()
    rel_path = _target_rel_path(name, path, file_id)

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
async def delete_workspace_file(name: str, file_id: Optional[str] = None, path: Optional[str] = None):
    """Delete one file (by ``file_id``, or ``path`` from an old client) or a
    directory (by ``path``) from a workspace. The registry records of what
    was deleted are tombstoned, so an id link to it answers 404."""
    _ensure_writable_workspace(name)
    root = create_workspace_folder(name).resolve()
    rel_path = _target_rel_path(name, path, file_id).strip("/")

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
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))

    from files import service as files_service
    try:
        if deleted_type == "directory":
            files_service.unregister_tree(name, rel_path)
        else:
            files_service.unregister_path(name, rel_path)
    except files_service.FileError:
        pass  # a path the registry never follows has no record to tombstone
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
    except Exception as e:  # noqa: BLE001 - reported to the client as an HTTP error
        raise HTTPException(status_code=500, detail=str(e))

    rel = dest.relative_to(root).as_posix()
    from files import service as files_service
    file_id = None
    try:
        if files_service.is_indexable(rel):
            file_id = files_service.register_path(name, rel, source="upload")["file_id"]
    except files_service.FileError:
        pass  # over a limit or under a skipped folder: the file is there, without an id
    return {
        "path": rel,
        "size": len(content),
        "file_id": file_id,
    }


@router.get("/{name}/model")
async def get_workspace_model(name: str):
    """Return workspace model state including the resolved global default from .env.
    - global_default: DEFAULT_PROVIDER + its model from .env (lowest-priority fallback)
    - workspace_default: default model resolved from workspace settings
    - override: explicitly set via UI picker (provider='global' forces global over workspace_default)
    """
    return _model_state(name, get_workspace_metadata(name))


@router.get("/{name}/summary")
async def get_workspace_summary(name: str):
    """What every page shows about the selected workspace, in one answer.

    The header (model picker, "Isolated" badge), the palette and the chat
    page each used to ask on their own: ``/{name}`` (which also builds the
    task list), ``/{name}/model``, ``/{name}/isolation`` and
    ``/{name}/settings-overrides``. Readable by any member: it carries only
    the isolation switch and the palette, not the owner scoped settings
    around them.
    """
    from memory import personal
    root = _require_workspace_folder(name)
    metadata = get_workspace_metadata(root.name) or {}
    settings = metadata.get("settings") or {}
    palette = settings.get("palette")
    return {
        "name": root.name,
        "allowed_agents": metadata.get("allowed_agents") or [],
        "personal_memory_enabled": personal.workspace_enabled(root.name),
        "isolated": bool(settings.get("isolated")),
        "palette": palette if isinstance(palette, dict) else None,
        "model": _model_state(root.name, metadata),
    }


def _model_state(name: str, metadata: dict) -> dict:
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

    override = metadata.get("model_override") or {}
    ws_default = get_workspace_default_model_config(metadata)
    from common.personal_workspace import model_source
    source = model_source(name)
    return {
        "global_default": {
            "provider": global_provider,
            "model": global_model,
        },
        "workspace_default": {
            "provider": ws_default.get("provider", ""),
            "model": ws_default.get("model", ""),
            # Set when a personal workspace runs with default's model.
            "inherited_from": source if source != name else None,
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


# ``settings`` keys written by their own routes or editors (the tool policy
# block below, the agent mode, the loop settings of the agent loop's
# extensions, the workspace palette, the web domain policy), which the
# settings-overrides Save must not drop. A caller clears one by naming it
# with a null value.
_SETTINGS_OWNED_ELSEWHERE = (
    "require_tool_approval", "tool_policy", "tool_policy_model", "agent_mode", "loop",
    "palette", "web_domain_policy_enabled", "web_allow_domains", "web_deny_domains",
)


@router.get("/{name}/settings-overrides")
async def get_workspace_settings_overrides(name: str):
    """Return workspace-scoped settings (raw values, not resolved)."""
    metadata = get_workspace_metadata(name)
    overrides = metadata.get("settings", {})
    return {"overrides": overrides}


@router.put("/{name}/settings-overrides")
async def update_workspace_settings_overrides(request: Request, name: str, payload: dict):
    """Replace workspace-scoped settings."""
    _ensure_writable_workspace(name)
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise HTTPException(status_code=400, detail="overrides must be a key-value object")
    # Strip empty-string values (treat as cleared)
    cleaned = {k: v for k, v in overrides.items() if v is not None and str(v).strip() != ""}
    # Keys other routes own live in the same ``settings`` block, and the
    # Settings page sends only the keys of its own form. Carry them over unless
    # the payload names them, or every Save would switch off the approval gate
    # and drop the tool policy and the loop settings.
    current = get_workspace_metadata(name).get("settings") or {}
    for key in _SETTINGS_OWNED_ELSEWHERE:
        if key not in overrides and key in current:
            cleaned[key] = current[key]
    previous = dict(current)
    update_workspace_metadata(name, {"settings": cleaned})
    audit.record("workspace.settings", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request), details={"keys": sorted(cleaned)})
    # The log level is the one override this process acts on itself, so re-read
    # it now instead of making the user restart the backend. Resolving through
    # the active workspace means editing some *other* workspace is a no-op here.
    try:
        from common.logging_config import configure_logging_for_active_workspace
        configure_logging_for_active_workspace()
    except Exception:  # noqa: BLE001 - best-effort step, the request goes on without it
        log.debug("update_workspace_settings_overrides: best-effort step failed", exc_info=True)
    out = {"overrides": cleaned}
    # A first key for a provider switches on one default model with its catalog
    # price (common/default_model.py); only the default workspace speaks for the hub's default.
    from common import default_model
    switched = []
    for provider in default_model.DEFAULT_MODELS:
        field = f"{provider}_api_key"
        if cleaned.get(field) and cleaned.get(field) != previous.get(field):
            found = default_model.ensure_default(provider, workspace=name, set_global=name == "default")
            if found:
                switched.append(found)
    if switched:
        out["default_models"] = switched
    return out


# ── Tool policy: the approval gate and the hooks that run around a tool call ──
#
# Both live in the workspace metadata and are read live by the agent process:
# ``tools.approval.approval_gate_enabled`` reads ``settings.require_tool_approval``
# and ``agents.hooks.load_hooks`` reads the ``hooks`` key (a ``.hooks.json``
# file in the folder is never run; the owner may import one). They are edited together here because an
# operator thinks of them as one thing: what happens around a tool call.

# The tool events, their other names (agents/hooks.py EVENT_ALIASES) and the
# run events. Each is stored under the name the operator wrote.
_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "before_tool_call", "after_tool_call",
                "before_run", "after_run")
_HOOK_TYPES = ("command", "http")


def _validate_hooks(raw) -> dict:
    """Return the hook config to store, or raise 400 naming the bad entry.

    A hook that never runs is worse than no hook at all, so the shape is checked
    here rather than discovered at the first tool call: a malformed entry is
    dropped silently by ``agents.hooks.load_hooks``.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="hooks must be an object keyed by event")
    unknown = [k for k in raw if k not in _HOOK_EVENTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown hook event(s) {sorted(unknown)}; expected {', '.join(_HOOK_EVENTS)}",
        )
    out: dict = {}
    for event in _HOOK_EVENTS:
        entries = raw.get(event)
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise HTTPException(status_code=400, detail=f"{event} must be a list of hook entries")
        cleaned = []
        for index, entry in enumerate(entries):
            where = f"{event}[{index}]"
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail=f"{where}: each hook must be an object")
            kind = str(entry.get("type") or "command").strip().lower()
            if kind not in _HOOK_TYPES:
                raise HTTPException(
                    status_code=400,
                    detail=f"{where}: type must be one of {', '.join(_HOOK_TYPES)}",
                )
            matcher = entry.get("matcher", "")
            if not isinstance(matcher, str):
                raise HTTPException(status_code=400, detail=f"{where}: matcher must be a string")
            if kind == "command" and not str(entry.get("command") or "").strip():
                raise HTTPException(status_code=400, detail=f"{where}: a command hook needs a command")
            if kind == "http" and not str(entry.get("url") or "").strip():
                raise HTTPException(status_code=400, detail=f"{where}: an http hook needs a url")
            if "timeout" in entry and entry["timeout"] is not None:
                try:
                    float(entry["timeout"])
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400, detail=f"{where}: timeout must be a number of seconds",
                    )
            cleaned.append({**entry, "type": kind, "matcher": matcher})
        out[event] = cleaned
    return out


def _policy_payload(name: str) -> dict:
    """What the Settings tool policy block shows. ``tool_policy`` and
    ``tool_policy_model`` are the per-tool modes and the model that decides
    ``auto`` calls (tools/permission_policy.py); they sit in ``settings`` next
    to the gate flag, where the agent process reads them."""
    from tools.permission_policy import clean_policy
    metadata = get_workspace_metadata(name)
    settings = metadata.get("settings") or {}
    hooks = metadata.get("hooks")
    from agents.hooks import ignored_hooks_file
    return {
        "require_tool_approval": bool(settings.get("require_tool_approval")),
        "hooks": hooks if isinstance(hooks, dict) else {},
        # A .hooks.json in the folder is never run (agents write there); the
        # owner sees it here and may import it.
        "ignored_hooks_file": ignored_hooks_file(name) is not None,
        "tool_policy": clean_policy(settings.get("tool_policy")),
        "tool_policy_model": str(settings.get("tool_policy_model") or "").strip() or None,
        # Only when set: absent means the default (common/tool_approvals.py).
        **({"tool_approval_timeout": settings["tool_approval_timeout"]}
           if settings.get("tool_approval_timeout") is not None else {}),
    }


# ── Web domain policy of a workspace ──────────────────────────────────────────
#
# The three keys tools/web.py reads from the workspace's ``settings`` block:
# ``web_deny_domains``, ``web_domain_policy_enabled``, ``web_allow_domains``.
# A workspace list, when set, replaces the global one of the same name for
# runs in this workspace; an empty list means "use the global one". The
# switch turns the allow list on for this workspace even when it is off
# globally. Edited on Settings, "Web search", and read live by every process.

_WEB_POLICY_KEYS = ("web_domain_policy_enabled", "web_allow_domains", "web_deny_domains")


def _web_policy_payload(name: str) -> dict:
    from tools.web import global_domain_policy
    settings = get_workspace_metadata(name).get("settings") or {}
    return {
        "workspace": name,
        "policy": {
            "enabled": bool(settings.get("web_domain_policy_enabled")),
            "allow_domains": [str(h) for h in (settings.get("web_allow_domains") or [])],
            "deny_domains": [str(h) for h in (settings.get("web_deny_domains") or [])],
        },
        "global": global_domain_policy(),
    }


@router.get("/{name}/web-policy")
async def get_workspace_web_policy(name: str):
    """The workspace's web domain policy next to the global one it overrides."""
    return _web_policy_payload(name)


@router.put("/{name}/web-policy")
async def update_workspace_web_policy(request: Request, name: str, payload: dict):
    """Set the workspace's web domain policy. Each key is optional; an empty
    list (or a missing key left as it was) falls back to the global list."""
    _ensure_writable_workspace(name)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="web policy must be a key-value object")
    from tools.web import clean_host_list
    settings = dict(get_workspace_metadata(name).get("settings") or {})
    if "enabled" in payload:
        settings["web_domain_policy_enabled"] = bool(payload.get("enabled"))
    for key, field in (("allow_domains", "web_allow_domains"), ("deny_domains", "web_deny_domains")):
        if key in payload:
            raw = payload.get(key) or []
            if not isinstance(raw, list):
                raise HTTPException(status_code=400, detail=f"{key} must be a list of host names")
            try:
                settings[field] = clean_host_list(raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{key}: {exc}")
    update_workspace_metadata(name, {"settings": settings})
    audit.record("workspace.web_policy", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request),
                 details={k: settings.get(k) for k in _WEB_POLICY_KEYS})
    return _web_policy_payload(name)


@router.get("/{name}/policy")
async def get_workspace_policy(name: str):
    """The workspace's tool policy: the approval gate and the hook config."""
    return _policy_payload(name)


@router.post("/{name}/policy/import-hooks-file")
async def import_workspace_hooks_file(request: Request, name: str):
    """Store the workspace folder's ``.hooks.json`` as the workspace's hooks.

    The file itself is never run (agents write into the folder,
    agents/hooks.py ``load_hooks``); importing it is the owner reading it and
    choosing to keep it. Validated like a PUT, and replaces the stored hooks.
    The file is left where it is.
    """
    _ensure_writable_workspace(name)
    from agents.hooks import ignored_hooks_file
    raw = ignored_hooks_file(name)
    if raw is None:
        raise HTTPException(status_code=404, detail="this workspace has no readable .hooks.json")
    hooks = _validate_hooks(raw)
    update_workspace_metadata(name, {"hooks": hooks})
    audit.record("workspace.hooks_import", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request), details={"events": sorted(hooks)})
    return _policy_payload(name)


@router.put("/{name}/policy")
async def update_workspace_policy(request: Request, name: str, payload: dict):
    """Replace the workspace's tool policy.

    ``require_tool_approval``, ``tool_policy`` and ``tool_policy_model`` are
    written into the same ``settings`` block the Settings page edits, so the
    gate and the tool policy read them without a second lookup; ``hooks`` is a
    top-level metadata key, validated entry by entry before it is stored. Each
    key is optional: a request that names one leaves the others as they are.
    """
    _ensure_writable_workspace(name)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="policy must be a key-value object")

    updates: dict = {}
    settings_keys = ("require_tool_approval", "tool_policy", "tool_policy_model", "tool_approval_timeout")
    if any(key in payload for key in settings_keys):
        from tools.permission_policy import modes as _policy_modes, split_model_id
        settings = dict(get_workspace_metadata(name).get("settings") or {})
        if "require_tool_approval" in payload:
            settings["require_tool_approval"] = bool(payload.get("require_tool_approval"))
        if "tool_approval_timeout" in payload:
            # How long a call held in a dashboard chat turn waits for a person
            # (common/tool_approvals.py); empty means the default.
            raw_timeout = payload.get("tool_approval_timeout")
            if raw_timeout in (None, ""):
                settings.pop("tool_approval_timeout", None)
            else:
                from common.tool_approvals import MAX_TIMEOUT_SECONDS, MIN_TIMEOUT_SECONDS
                try:
                    seconds = int(float(raw_timeout))
                except (TypeError, ValueError):
                    seconds = -1
                if not MIN_TIMEOUT_SECONDS <= seconds <= MAX_TIMEOUT_SECONDS:
                    raise HTTPException(
                        status_code=400,
                        detail=(f"tool_approval_timeout must be a number of seconds from "
                                f"{int(MIN_TIMEOUT_SECONDS)} to {int(MAX_TIMEOUT_SECONDS)}"),
                    )
                settings["tool_approval_timeout"] = seconds
        if "tool_policy" in payload:
            # Per-tool modes: tool id (or "*" for every other tool) to a mode.
            # A bad entry is refused rather than dropped, so what the page saved
            # is what the agent process will read.
            raw = payload.get("tool_policy") or {}
            if not isinstance(raw, dict):
                raise HTTPException(status_code=400, detail="tool_policy must be an object of tool id to mode")
            valid = _policy_modes()
            cleaned: dict = {}
            for key, mode in raw.items():
                tool = str(key or "").strip()
                value = str(mode or "").strip().lower()
                if not tool:
                    raise HTTPException(status_code=400, detail="tool_policy: a tool id cannot be empty")
                if value not in valid:
                    raise HTTPException(
                        status_code=400,
                        detail=f"tool_policy[{tool}]: mode must be one of {', '.join(valid)}",
                    )
                cleaned[tool] = value
            settings["tool_policy"] = cleaned
        if "tool_policy_model" in payload:
            model_id = str(payload.get("tool_policy_model") or "").strip()
            if model_id:
                provider, model = split_model_id(model_id)
                if not provider or not model:
                    raise HTTPException(
                        status_code=400,
                        detail="tool_policy_model must be a catalog id of the form provider/model",
                    )
                settings["tool_policy_model"] = model_id
            else:
                settings.pop("tool_policy_model", None)
        updates["settings"] = settings
    if "hooks" in payload:
        updates["hooks"] = _validate_hooks(payload.get("hooks"))
    if updates:
        update_workspace_metadata(name, updates)
        audit.record("workspace.policy", principal=identity.request_principal(request),
                     object_type="workspace", object_id=name, workspace=name,
                     ip=identity.client_ip(request),
                     details={"keys": sorted(k for k in payload if k in (*settings_keys, "hooks"))})
    return _policy_payload(name)


@router.get("/{name}/personal-memory")
async def get_workspace_personal_memory(name: str):
    """Personal memory in this workspace (memory/personal.py): the workspace
    switch, its main agent and each agent's setting (missing = off)."""
    from memory import personal
    return personal.agent_settings(name)


@router.put("/{name}/personal-memory")
async def update_workspace_personal_memory(request: Request, name: str, data: WorkspacePersonalMemoryUpdate):
    """Turn personal memory in this workspace on or off. Off, every agent has
    it off; the agents' own switches are kept for when it is turned back on."""
    from memory import personal
    _ensure_writable_workspace(name)
    if data.enabled:
        # The personal pool is shared with the person's other workspaces:
        # writing to it from an isolated one would carry data out.
        from common import isolation
        try:
            isolation.ensure_not_isolated(name, "the personal memory pool, shared with other workspaces,")
        except isolation.IsolationError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    result = personal.set_workspace_enabled(name, data.enabled)
    audit.record("workspace.personal_memory", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request), details={"enabled": data.enabled})
    return result


@router.get("/{name}/roles")
async def get_workspace_roles(name: str):
    """Which agent holds each role in this workspace (agents/roles.py): the
    bound one or the role's default, and the agents that call the role."""
    from agents import roles
    _require_workspace_folder(name)
    return {"workspace": name, "roles": roles.describe(name)}


@router.put("/{name}/roles/{role}")
async def update_workspace_role(request: Request, name: str, role: str, data: WorkspaceRoleUpdate):
    """Give a role to one of the workspace's agents, or back to its default
    (empty ``agent_id``). Refused when the agent is not in the workspace, or
    when an agent that calls the role would then reach a capability
    combination the guard blocks."""
    from agents import roles
    _ensure_writable_workspace(name)
    try:
        roles.set_binding(name, role, data.agent_id)
    except roles.RoleError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    audit.record("workspace.role", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request),
                 details={"role": role, "agent_id": data.agent_id or None})
    notify_change("workspaces")
    return {"workspace": name, "roles": roles.describe(name)}


def _special_models_payload(name: str) -> dict:
    from providers import special
    return {
        "workspace": name,
        "own": special.masked(special.stored(name)),
        "effective": special.masked(special.effective(name)),
        "options": special.options_payload(),
    }


@router.get("/{name}/special-models")
async def get_workspace_special_models(name: str):
    """The special models of this workspace (providers/special.py): its own
    choices, what it uses after the ``default`` workspace fills the gaps, and
    the purposes, providers and model suggestions for the form."""
    _require_workspace_folder(name)
    return _special_models_payload(name)


@router.get("/{name}/special-models/discover")
async def discover_workspace_special_models(name: str, purpose: str, provider: str):
    """The models ``provider`` has that fit ``purpose``, asked with this
    workspace's connection settings. Nothing is stored: the form offers them."""
    import asyncio
    from providers import special
    _require_workspace_folder(name)
    try:
        return await asyncio.to_thread(special.discover, purpose, provider, name)
    except special.SpecialModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{name}/special-models/voices")
async def workspace_special_model_voices(name: str, request: Request, provider: str, model: str = "",
                                         purpose: str = "speech"):
    """The voices of one model the form holds, so picking a model offers
    them: the model's own (the hub runtime reads them from its files; of a
    cloning model's recorded voices, the person's own and the shared
    ones), else the ones known for the provider's API shape, with their
    languages."""
    import asyncio
    from providers import special
    _require_workspace_folder(name)
    try:
        return await asyncio.to_thread(special.voices_for, purpose, provider, model,
                                       identity.request_principal(request))
    except special.SpecialModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/special-models/sample")
async def workspace_special_model_sample(name: str, payload: dict):
    """A short line read by a speech model the form holds (saved or not),
    as audio: in the voice's own language, else in ``language`` (the
    page's). Writers only, like the check: it runs the model, and a cloud
    model charges for it (a fraction of a cent, not counted on a run). The
    line and its language come in ``X-Sample-Text`` (URL-encoded) and
    ``X-Sample-Language``."""
    import asyncio
    from urllib.parse import quote
    from fastapi import Response
    from providers import special
    _ensure_writable_workspace(name)
    try:
        audio, lang, text = await asyncio.to_thread(
            special.voice_sample, payload, name, str(payload.get("language") or "") or None)
    except special.SpecialModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=audio.data, media_type=audio.mime_type,
                    headers={"Cache-Control": "no-store", "X-Sample-Language": lang,
                             "X-Sample-Text": quote(text)})


@router.post("/{name}/special-models/check")
async def check_workspace_special_model(name: str, payload: dict):
    """Whether one model the form holds (saved or not) can be reached with
    this workspace's connection settings, without running it. Writers only:
    it sends requests to the address typed in, with the stored headers."""
    import asyncio
    from providers import special
    _ensure_writable_workspace(name)
    return await asyncio.to_thread(special.check, payload, name)


@router.put("/{name}/special-models")
async def update_workspace_special_models(request: Request, name: str, payload: dict):
    """Replace this workspace's own special model choices. A purpose left
    out uses the ``default`` workspace's choice."""
    from providers import special
    _ensure_writable_workspace(name)
    try:
        config = special.save(name, payload.get("special_models", payload))
    except special.SpecialModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Purposes and model ids only: a custom model's headers may carry a token.
    audit.record("workspace.special_models", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request),
                 details={"purposes": sorted(k for k in config if k != "custom"),
                          "custom": [c["id"] for c in config.get("custom") or []]})
    notify_change("workspaces")
    return _special_models_payload(name)


@router.get("/{name}/env")
async def get_workspace_env(name: str):
    """Return workspace-scoped environment variables."""
    metadata = get_workspace_metadata(name)
    return {"env_vars": metadata.get("env_vars", {})}


@router.put("/{name}/env")
async def update_workspace_env(request: Request, name: str, payload: dict):
    """Replace workspace-scoped environment variables."""
    _ensure_writable_workspace(name)
    env_vars = payload.get("env_vars", {})
    if not isinstance(env_vars, dict):
        raise HTTPException(status_code=400, detail="env_vars must be a key-value object")
    update_workspace_metadata(name, {"env_vars": env_vars})
    # Names only, never values: an env block is exactly where a secret lives.
    audit.record("workspace.env", principal=identity.request_principal(request),
                 object_type="workspace", object_id=name, workspace=name,
                 ip=identity.client_ip(request), details={"keys": sorted(env_vars)})
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
