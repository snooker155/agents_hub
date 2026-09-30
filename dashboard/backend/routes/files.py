"""
Workspace files API: upload once, reference by id (docs/files.md).

``/api/files`` is the HTTP face of ``files/service.py``: upload a file into a
workspace, list and search a workspace's files, read one's metadata, text or
content, delete it, and ask where it is used (chat conversations, memory
pools, tasks, eval cases).

Access follows the workspace the file belongs to, the same two checks every
workspace-scoped route makes: reading needs the workspace to be visible to
the caller (``common.access``), writing (upload, delete) needs the ``editor``
role there (``identity.require_role``). A file id from another workspace is
refused the same way a task id from one would be. Upload, delete and a folder
index leave an audit row (``file.upload``, ``file.delete``, ``file.index``).

``POST /api/files/index`` registers every file of the workspace folder (what
agents wrote with their filesystem tools before the registry followed them,
or what a process outside the tools wrote) and tombstones the records of
folder files that are gone; see ``files.service.index_workspace``.

Content is served as a download (``Content-Disposition: attachment``) with
``X-Content-Type-Options: nosniff`` and a sandboxing CSP, and a type a browser
would run as a page (HTML, SVG, XML, script) goes out as plain text, so a file
someone uploaded can never execute as a page of this origin.
"""
from __future__ import annotations

import hashlib
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from common import access, audit, identity
from common.auth import WS_EDITOR
from files import service
from files.usage import where_used

router = APIRouter(prefix="/api/files", tags=["files"])

#: Types a browser renders as an active document; served as plain text instead.
_ACTIVE_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml",
    "text/javascript", "application/javascript", "application/x-javascript",
    "application/ecmascript", "text/ecmascript",
})

#: Upload chunk size: the body is read in pieces so a file past the limit is
#: refused after limit + one chunk, not after the whole thing is in memory.
_CHUNK = 1024 * 1024

#: Longest text the preview endpoint returns.
_MAX_PREVIEW_CHARS = 200_000


def _principal(request: Request):
    return identity.request_principal(request)


def _raise(exc: service.FileError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


def _workspace_or_400(value: Optional[str]) -> str:
    name = (value or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="workspace is required")
    return name


def _load_visible(request: Request, file_id: str, *, include_deleted: bool = False) -> dict:
    record = service.get_file(file_id, include_deleted=include_deleted)
    if record is None:
        raise HTTPException(status_code=404, detail="File not found")
    access.require_visible(_principal(request), record["workspace"])
    return record


def _audit(request: Request, action: str, record: dict, details: Optional[dict] = None) -> None:
    audit.record(action, principal=_principal(request), object_type="file",
                 object_id=record["file_id"], workspace=record["workspace"],
                 ip=identity.client_ip(request),
                 details={"name": record["name"], "size": record["size"], **(details or {})})


@router.get("")
async def list_workspace_files(
    request: Request,
    workspace: str = Query(...),
    source: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
):
    """A workspace's files, newest first, with its usage against the limits."""
    ws = _workspace_or_400(workspace)
    access.require_visible(_principal(request), ws)
    return {
        "workspace": ws,
        "files": service.list_files(ws, source=source or None, q=q or None, limit=limit),
        "usage_bytes": service.workspace_usage(ws),
        "limits": service.limits(),
    }


@router.post("")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    workspace: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    workspace_q: Optional[str] = Query(None, alias="workspace"),
):
    """Upload one file (multipart ``file``) into a workspace, named by the
    ``workspace`` query parameter or form field. The same bytes already in
    the workspace return the existing record with ``deduplicated: true``."""
    ws = _workspace_or_400(workspace_q or workspace)
    principal = _principal(request)
    access.require_visible(principal, ws)
    identity.require_role(principal, workspace=ws, role=WS_EDITOR)

    limit = service.max_file_bytes()
    chunks = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"'{file.filename}' is larger than the limit of {limit} bytes per file")
        chunks.append(chunk)
    data = b"".join(chunks)

    existing = service.find_duplicate(ws, hashlib.sha256(data).hexdigest())
    if existing is not None:
        return {**existing, "deduplicated": True}
    created_by = getattr(principal, "id", None) or identity.current_user_id()
    try:
        record = service.create_file(
            ws, file.filename or "file", data, mime_type=file.content_type,
            source=(source or "upload").strip() or "upload", created_by=created_by,
        )
    except service.FileError as exc:
        _raise(exc)
    _audit(request, "file.upload", record, {"source": record["source"]})
    return {**record, "deduplicated": False}


@router.post("/index")
async def index_workspace_folder(request: Request, workspace: str = Query(...)):
    """Register the files of the workspace folder as workspace files (source
    by folder: ``knowledge/`` memory, ``chat_uploads/`` chat, ``task_files/``
    task, the rest agent) and tombstone the records of folder files that no
    longer exist. Returns ``{workspace, added, updated, unchanged, removed,
    skipped: [{path, reason}]}``."""
    ws = _workspace_or_400(workspace)
    principal = _principal(request)
    access.require_visible(principal, ws)
    identity.require_role(principal, workspace=ws, role=WS_EDITOR)
    created_by = getattr(principal, "id", None) or identity.current_user_id()
    try:
        summary = service.index_workspace(ws, created_by=created_by)
    except service.FileError as exc:
        _raise(exc)
    audit.record("file.index", principal=principal, object_type="workspace", object_id=ws,
                 workspace=ws, ip=identity.client_ip(request),
                 details={k: summary[k] for k in ("added", "updated", "unchanged", "removed")}
                 | {"skipped": len(summary["skipped"])})
    return summary


@router.get("/{file_id}")
async def get_workspace_file(file_id: str, request: Request):
    return _load_visible(request, file_id)


@router.get("/{file_id}/text")
async def get_file_text(file_id: str, request: Request, max_chars: int = 50_000):
    """The file's text as the agents see it (plain text, code, PDF), for a
    preview. ``text`` is null for a binary file."""
    record = _load_visible(request, file_id)
    try:
        text = service.extract_text(file_id)
    except KeyError:
        raise HTTPException(status_code=410, detail="The content of this file is no longer available")
    cap = max(1, min(int(max_chars or 50_000), _MAX_PREVIEW_CHARS))
    truncated = text is not None and len(text) > cap
    return {
        "file_id": file_id,
        "name": record["name"],
        "mime_type": record["mime_type"],
        "kind": "pdf" if service.is_pdf(record) else ("text" if text is not None else "binary"),
        "text": text[:cap] if text is not None else None,
        "truncated": truncated,
    }


@router.get("/{file_id}/content")
async def download_file(file_id: str, request: Request):
    """The file's bytes, as a download."""
    record = _load_visible(request, file_id)
    try:
        path = service.local_path(file_id)
    except KeyError:
        raise HTTPException(status_code=410, detail="The content of this file is no longer available")
    mime = record["mime_type"] or "application/octet-stream"
    if mime in _ACTIVE_TYPES:
        mime = "text/plain; charset=utf-8"
    ascii_name = record["name"].encode("ascii", "replace").decode("ascii").replace('"', "_").replace("?", "_")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(record['name'])}"
    return FileResponse(
        str(path),
        media_type=mime,
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, max-age=0",
        },
    )


@router.get("/{file_id}/usage")
async def file_usage(file_id: str, request: Request):
    """Where the file is referenced: chats, memory pools, tasks, eval cases."""
    _load_visible(request, file_id, include_deleted=True)
    return where_used(file_id)


@router.delete("/{file_id}")
async def delete_workspace_file(file_id: str, request: Request):
    """Delete the file: its content goes, its record stays as a tombstone so
    what referenced it can still name it."""
    record = _load_visible(request, file_id)
    identity.require_role(_principal(request), workspace=record["workspace"], role=WS_EDITOR)
    usage = where_used(file_id)
    if not service.delete_file(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    _audit(request, "file.delete", record, {"references": usage.get("total", 0)})
    return {"file_id": file_id, "deleted": True, "references": usage.get("total", 0)}
