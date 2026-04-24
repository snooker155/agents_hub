"""
Projects API routes.

Projects are an organizational layer above tasks.
A project:
  - Lives inside a workspace
  - Can be backed by a git repo (GitHub, GitLab, local, etc.)
  - Can have a frontend (with live preview via iframe)
  - Can have a backend (with Swagger docs + request proxy)
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

# Ensure project root is on sys.path
_project_root = Path(__file__).resolve().parents[3]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common import tasks_service
from common.workspace import get_workspace_folder, resolve_project_root, project_folder_name
from models import ProjectCreate, ProjectUpdate, ProjectApiRequest
from projects.models import Project
from projects.storage import ProjectStore

router = APIRouter(prefix="/api/projects", tags=["projects"])

_store = ProjectStore(path=_project_root / "projects" / "projects.json")


def _project_to_dict(project: Project) -> dict:
    if hasattr(project, "model_dump"):
        return project.model_dump()
    return project.dict()


def _task_to_dict(task) -> dict:
    data = task.model_dump() if hasattr(task, "model_dump") else task.dict()
    data["id"] = str(data["id"])
    if data.get("parent_id"):
        data["parent_id"] = str(data["parent_id"])
    return data


# ─────────────────────────── CRUD ────────────────────────────

@router.get("")
async def list_projects(workspace: Optional[str] = Query(None)):
    projects = _store.list()
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

    _store.add(project)

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
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    d = _project_to_dict(project)
    all_tasks = tasks_service.list_tasks()
    d["tasks_count"] = sum(1 for t in all_tasks if t.project_id == project_id)
    d["folder"] = project_folder_name(project.name)
    return d


@router.put("/{project_id}")
async def update_project(project_id: str, payload: ProjectUpdate):
    project = _store.get(project_id)
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

    updated = _store.update(project_id, **fields)
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_dict(updated)


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    deleted = _store.delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": True}


# ─────────────────────────── FILES ────────────────────────────

def _project_root_path(project) -> Optional[Path]:
    """Return the resolved project subfolder path, or None if the workspace doesn't exist."""
    ws = get_workspace_folder(project.workspace)
    if ws is None:
        return None
    folder = project_folder_name(project.name)
    root = (ws / folder).resolve()
    return root if root.exists() else None


@router.get("/{project_id}/files")
async def list_project_files(project_id: str):
    """List all files inside the project's subfolder (hides dot-files/folders)."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        return {"files": []}
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(seg.startswith(".") for seg in rel.split("/")):
                continue
            files.append(rel)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"files": sorted(files)}


@router.get("/{project_id}/file-content")
async def get_project_file_content(project_id: str, path: str):
    """Return the content of a single file inside the project folder."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        raise HTTPException(status_code=404, detail="Project folder not found")
    target = (root / path).resolve()
    if not str(target).startswith(str(root) + "/") and str(target) != str(root):
        raise HTTPException(status_code=403, detail="Path outside project folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"path": path, "content": content, "size": target.stat().st_size}


# ─────────────────────────── SPEC FROM CODE ────────────────────────────

_EXTRACT_SCRIPT = """
import sys, json, os, importlib
sys.path.insert(0, os.getcwd())

spec = None
for mod_name in ["main", "app", "api", "server", "application", "backend"]:
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        continue
    for attr in ["app", "application", "api"]:
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        if hasattr(obj, "openapi"):
            try:
                spec = obj.openapi()
                break
            except Exception:
                pass
    if spec:
        break
    # factory pattern
    factory = getattr(mod, "create_app", None)
    if callable(factory):
        try:
            obj = factory()
            if hasattr(obj, "openapi"):
                spec = obj.openapi()
                break
        except Exception:
            pass

print(json.dumps(spec) if spec else "null")
"""


def _detect_port_from_source(root: Path) -> Optional[int]:
    """Scan source files for uvicorn/gunicorn port hints."""
    patterns = [
        re.compile(r'uvicorn\.run\([^)]*port\s*=\s*(\d+)'),
        re.compile(r'port\s*=\s*int\s*\(\s*os\.(?:getenv|environ\.get)\s*\([^)]+\)\s*\w*\s*(\d{4,5})'),
        re.compile(r'PORT\s*=\s*(\d{4,5})'),
        re.compile(r'--port[=\s]+(\d{4,5})'),
    ]
    for py_file in list(root.rglob("*.py"))[:40]:
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in patterns:
            m = pat.search(text)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
    return None


@router.get("/{project_id}/spec-from-code")
async def get_spec_from_code(project_id: str):
    """Extract OpenAPI spec from project source code (no running server needed)."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    root = _project_root_path(project)
    if root is None:
        raise HTTPException(status_code=404, detail="Project folder not found")

    # Strategy 1: look for exported spec files
    for spec_file in ["openapi.json", "swagger.json", "api-docs.json", "api-spec.json"]:
        candidate = root / spec_file
        if candidate.is_file():
            try:
                spec = json.loads(candidate.read_text(encoding="utf-8"))
                detected_port = _detect_port_from_source(root)
                return {"spec": spec, "source": spec_file, "detected_port": detected_port}
            except Exception:
                pass

    # Strategy 2: dynamic import via subprocess
    python_exec = sys.executable
    for venv_dir in ["venv", ".venv", "env"]:
        venv_python = root / venv_dir / "bin" / "python"
        if venv_python.exists():
            python_exec = str(venv_python)
            break

    result = None
    try:
        result = subprocess.run(
            [python_exec, "-c", _EXTRACT_SCRIPT],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            if raw and raw != "null":
                spec = json.loads(raw)
                detected_port = _detect_port_from_source(root)
                return {"spec": spec, "source": "dynamic_import", "detected_port": detected_port}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timed out trying to import app")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    stderr = result.stderr.strip() if result else ""
    if stderr:
        raise HTTPException(
            status_code=422,
            detail=f"Could not import app: {stderr[:400]}",
        )
    raise HTTPException(status_code=422, detail="No OpenAPI spec found in project source code")


# ─────────────────────────── TASKS ────────────────────────────

@router.get("/{project_id}/tasks")
async def get_project_tasks(project_id: str):
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    all_tasks = tasks_service.list_tasks()
    project_tasks = [t for t in all_tasks if t.project_id == project_id]
    return [_task_to_dict(t) for t in project_tasks]


# ─────────────────────────── REPO ────────────────────────────

@router.post("/{project_id}/clone-repo")
async def clone_repo(project_id: str):
    """Clone the project's git repo into the workspace directory."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    repo = project.repo
    if not repo.url:
        raise HTTPException(status_code=400, detail="No repo URL configured")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{project.workspace}' not found")

    clone_dir = ws_folder / (repo.local_path or "repo")

    if clone_dir.exists():
        raise HTTPException(status_code=409, detail=f"Target directory already exists: {clone_dir}")

    try:
        result = subprocess.run(
            ["git", "clone", "--branch", repo.branch or "main", repo.url, str(clone_dir)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr.strip())

        # Update local_path in project
        rel_path = repo.local_path or "repo"
        _store.update(project_id, repo={"local_path": rel_path})

        return {"cloned": True, "path": str(clone_dir), "output": result.stdout}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git clone timed out")


@router.get("/{project_id}/git-status")
async def git_status(project_id: str):
    """Return git status for the project's local repo."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise HTTPException(status_code=404, detail="Repo directory not found. Clone first.")

    try:
        status = subprocess.run(
            ["git", "status", "--short"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-10"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=10
        )
        return {
            "branch": branch.stdout.strip(),
            "status": status.stdout.strip(),
            "recent_commits": log.stdout.strip(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{project_id}/git-pull")
async def git_pull(project_id: str):
    """Pull latest changes from remote."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    ws_folder = get_workspace_folder(project.workspace)
    if ws_folder is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    repo_path = ws_folder / (project.repo.local_path or "repo")
    if not repo_path.exists():
        raise HTTPException(status_code=404, detail="Repo directory not found")

    try:
        result = subprocess.run(
            ["git", "pull"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=60
        )
        return {"output": result.stdout + result.stderr, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Git pull timed out")


# ─────────────────────────── BACKEND / SWAGGER ────────────────────────────

@router.get("/{project_id}/swagger-spec")
async def get_swagger_spec(project_id: str, base_url: Optional[str] = Query(None)):
    """Fetch the OpenAPI JSON spec from the project's backend."""
    project = _store.get(project_id)
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
        except Exception:
            continue

    raise HTTPException(status_code=502, detail="Could not fetch OpenAPI spec from backend")


@router.post("/{project_id}/api-request")
async def proxy_api_request(project_id: str, payload: ProjectApiRequest):
    """Proxy an HTTP request to the project's backend."""
    project = _store.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    backend = project.backend
    if not backend.enabled:
        raise HTTPException(status_code=400, detail="Backend is not enabled for this project")

    base = payload.base_url or backend.base_url or (f"http://localhost:{backend.port}" if backend.port else None)
    if not base:
        raise HTTPException(status_code=400, detail="No backend URL configured. Set base_url in the API tab.")
    url = f"{base}{payload.path}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=payload.method.upper(),
                url=url,
                headers=payload.headers or {},
                json=payload.body if payload.method.upper() not in ("GET", "DELETE") else None,
            )
            try:
                body = response.json()
            except Exception:
                body = response.text

            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": body,
            }
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to backend at {base}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request to backend timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
