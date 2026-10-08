"""Project files, spec extraction from code and the task list."""
from ._common import (_project_root_path, _task_to_dict,
    package, store)
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from tasks import service as tasks_service
from workspace import project_folder_name


router = APIRouter(prefix="/api/projects", tags=["projects"])

# ─────────────────────────── FILES ────────────────────────────

def _skip_dir(name: str) -> bool:
    """Folders the file list never enters: hidden ones, dependencies and
    build output, the same ones the workspace's file index skips."""
    from files.service import INDEX_SKIP_DIRS

    return name.startswith(".") or name in INDEX_SKIP_DIRS


_MAX_LISTED_FILES = 5000
#: A PDF is read whole to extract its text; anything else is cut at the
#: preview length (files.service.preview_path).
_MAX_PDF_PREVIEW_BYTES = 20 * 1024 * 1024
# Types a browser would run as this origin: served as plain text, like the
# workspace files are (routes/files.py).
_ACTIVE_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml",
    "text/javascript", "application/javascript", "application/x-javascript",
    "application/ecmascript", "text/ecmascript",
})


@router.get("/{project_id}/files")
async def list_project_files(project_id: str):
    """List the files inside the project's subfolder: hidden entries and
    dependency or build folders (node_modules, venv, dist...) left out,
    at most 5000. ``truncated`` says the list was cut."""
    import os

    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        return {"files": [], "truncated": False}
    files = []
    truncated = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not _skip_dir(d))
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if len(files) >= _MAX_LISTED_FILES:
                    truncated = True
                    break
                files.append((Path(dirpath) / name).relative_to(root).as_posix())
            if truncated:
                break
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    from files import service as files_service
    prefix = project_folder_name(project.name) + "/"
    known = files_service.folder_ids(project.workspace)
    listed = set(files)
    ids = {rel[len(prefix):]: fid for rel, fid in known.items()
           if rel.startswith(prefix) and rel[len(prefix):] in listed}
    return {"files": sorted(files), "truncated": truncated, "ids": ids}


def _project_file(project_id: str, path: Optional[str], file_id: Optional[str] = None) -> Path:
    """The file a route names inside the project folder, or an HTTP error:
    by ``file_id`` (files/service.py, what a link carries), or by ``path``
    for old links and files the registry does not follow."""
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    root = _project_root_path(project)
    if root is None:
        raise HTTPException(status_code=404, detail="Project folder not found")
    rel = (path or "").strip()
    fid = (file_id or "").strip()
    if fid:
        from files import service as files_service
        prefix = project_folder_name(project.name) + "/"
        in_ws = files_service.folder_path_of(fid, project.workspace) or ""
        if not in_ws.startswith(prefix):
            raise HTTPException(status_code=404, detail="File not found")
        rel = in_ws[len(prefix):]
    if not rel:
        raise HTTPException(status_code=400, detail="Query parameter 'file_id' or 'path' is required")
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside project folder")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


@router.get("/{project_id}/file-id")
async def get_project_file_id(project_id: str, path: str):
    """The file id of a project file, registering the file when nothing wrote
    it through the registry yet, so the page can put the id in its address."""
    from files import service as files_service

    target = _project_file(project_id, path)
    project = store().get(project_id)
    rel = target.relative_to(_project_root_path(project)).as_posix()
    try:
        record = files_service.ensure_folder_record(
            project.workspace, f"{project_folder_name(project.name)}/{rel}")
    except files_service.FileError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))
    return {"file_id": record["file_id"], "path": rel}


@router.get("/{project_id}/file-content")
async def get_project_file_content(project_id: str, path: Optional[str] = None, file_id: Optional[str] = None):
    """A file of the project folder for preview, read the way workspace files
    are (files.service.preview_path): ``kind`` is ``text``, ``pdf`` (its
    text extracted) or ``binary`` (``content`` null); ``mime_type`` lets the
    page render an image, a PDF or an HTML page from ``file-raw``."""
    from files.service import preview_path

    target = _project_file(project_id, path, file_id)
    size = target.stat().st_size
    if target.suffix.lower() == ".pdf" and size > _MAX_PDF_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail=(
            f"PDF is too large to extract its text ({size} bytes). "
            f"Limit is {_MAX_PDF_PREVIEW_BYTES} bytes."))
    try:
        preview = await asyncio.to_thread(preview_path, target, max_chars=package()._PREVIEW_CHARS)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    root = _project_root_path(store().get(project_id))
    return {"path": target.relative_to(root).as_posix(), "size": size, "content": preview["text"], "kind": preview["kind"],
            "mime_type": preview["mime_type"], "truncated": preview["truncated"]}


@router.get("/{project_id}/file-raw")
async def get_project_file_raw(project_id: str, path: Optional[str] = None, file_id: Optional[str] = None):
    """The bytes of a project file, for an image, a PDF or an HTML page shown
    in the browser. Types that would run as this origin come back as plain
    text with a sandbox policy; the page renders HTML from a blob in a
    sandboxed frame instead."""
    from urllib.parse import quote

    from fastapi.responses import FileResponse
    from files.service import guess_mime

    target = _project_file(project_id, path, file_id)
    mime = guess_mime(target.name)
    if mime in _ACTIVE_TYPES:
        mime = "text/plain; charset=utf-8"
    ascii_name = target.name.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("?", "_")
    return FileResponse(
        str(target),
        media_type=mime,
        headers={
            "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(target.name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, max-age=0",
        },
    )


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
    project = store().get(project_id)
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
        result = await asyncio.to_thread(
            subprocess.run,
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
    project = store().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    all_tasks = tasks_service.list_tasks()
    project_tasks = [t for t in all_tasks if t.project_id == project_id]
    return [_task_to_dict(t) for t in project_tasks]


