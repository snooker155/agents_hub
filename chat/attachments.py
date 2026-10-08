"""
Chat attachment handling.

Validates and (optionally) materializes uploaded attachments into the request's
workspace before the agent runs, enforcing per-file and total size limits.

Two kinds of attachment reach a turn:

- an upload, whose bytes travel in the request (``content`` or
  ``content_b64``). With ``store_to_workspace`` it is written into the
  workspace's ``chat_uploads`` folder (the prompt names that path, so an agent
  with filesystem tools can open it) and becomes a workspace file
  (``files/service.py``, source ``chat``), whose id is set on the attachment;
- a workspace file attached by id (``file_id``): its record must belong to the
  request's workspace. Its text (plain text, code, PDF) is loaded into
  ``content`` the way an upload's would be; a binary file is named instead and
  copied into ``chat_uploads`` so it can still be opened there. The 5 MB
  request limit does not apply: the bytes never travel in the request, and
  the prompt caps each attachment's text anyway (``chat.context``).

Every workspace file a turn uses is remembered against the conversation
(``files.service.record_use``), which is how a file's "where used" list knows
about chats.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4
import base64
import logging
import re

from fastapi import HTTPException

from chat.models import ChatAttachment, ChatRequest

log = logging.getLogger(__name__)

#: Text of one workspace file loaded into an attachment; ``chat.context``
#: cuts each attachment at the same length when it builds the prompt.
FILE_TEXT_CHARS = 40_000


def safe_attachment_filename(filename: str, idx: int) -> str:
    base = Path(filename or "").name.strip()
    if not base:
        base = f"attachment_{idx}.txt"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not safe:
        safe = f"attachment_{idx}.txt"
    return safe[:120]


def _uploads_dir(workspace: str) -> Path:
    from workspace import create_workspace_folder
    uploads_dir = create_workspace_folder(workspace) / "chat_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


def _resolve_file_attachment(request: ChatRequest, att: ChatAttachment) -> None:
    """Fill an attachment given by ``file_id`` from the workspace file."""
    from files import service as files

    record = files.get_file(str(att.file_id or "").strip())
    if record is None:
        raise HTTPException(status_code=404, detail=f"Workspace file '{att.file_id}' not found")
    if not request.workspace:
        raise HTTPException(
            status_code=400,
            detail=f"Workspace file '{record['name']}' can only be attached in its workspace "
                   f"('{record['workspace']}'); no workspace is selected.")
    if record["workspace"] != request.workspace:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace file '{att.file_id}' belongs to another workspace.")
    att.file_id = record["file_id"]
    att.filename = record["name"]
    att.mime_type = record["mime_type"]
    att.content_b64 = None
    try:
        text = files.extract_text(record["file_id"])
    except KeyError:
        raise HTTPException(status_code=410, detail=f"The content of '{record['name']}' is no longer available")
    if text is not None:
        att.content = text if len(text) <= FILE_TEXT_CHARS else text[:FILE_TEXT_CHARS] + "\n...[truncated]"
    # A binary file (and any file the user asked to keep in the workspace
    # folder) is copied into chat_uploads, so the path the prompt names exists.
    if text is None or att.store_to_workspace:
        uploads = _uploads_dir(request.workspace)
        copies = files.materialize([record["file_id"]], uploads)
        if copies:
            att.stored_workspace_path = copies[0].relative_to(uploads.parent).as_posix()
    if text is None:
        where = f" A copy is at {att.stored_workspace_path}." if att.stored_workspace_path else ""
        att.content = (f"[{files.describe(record)}: binary, not inlined.{where} "
                       f"Tools that read workspace files can open it by id.]")


def _store_upload_as_file(request: ChatRequest, att: ChatAttachment, data: bytes) -> None:
    """Turn a ``store_to_workspace`` upload into a workspace file (source
    ``chat``) and set its id on the attachment. A refusal (a limit) leaves
    the chat_uploads copy in place and the turn going: the user asked to keep
    the file, not to fail the message over the catalogue."""
    from files import service as files
    try:
        from common.identity import current_user_id
        created_by: Optional[str] = current_user_id()
    except Exception:  # noqa: BLE001 - authorship is a label; the file is stored without it
        created_by = None
    try:
        record = files.create_file(
            request.workspace, att.filename, data, mime_type=att.mime_type, source="chat",
            created_by=created_by,
            meta={"conversation_id": request.conversation_id} if request.conversation_id else None,
        )
    except files.FileError as exc:
        log.warning("chat attachment '%s' not saved as a workspace file: %s", att.filename, exc)
        return
    att.file_id = record["file_id"]


def _record_uses(request: ChatRequest) -> None:
    ids = [a.file_id for a in request.attachments if a.file_id]
    if not ids or not request.conversation_id:
        return
    from files import service as files
    label = request.conversation_title or (request.message or "").strip().replace("\n", " ")[:80]
    for fid in dict.fromkeys(ids):
        files.record_use(fid, "chat", request.conversation_id, label or None)


def materialize_attachments(request: ChatRequest) -> None:
    """Validate attachment sizes, resolve workspace files attached by id, and
    write any ``store_to_workspace`` uploads into the workspace's
    ``chat_uploads`` folder (and the workspace files), recording their
    stored path and file id on the attachment. Raises ``HTTPException`` on
    invalid base64, size violations or a file from another workspace."""
    if not request.attachments:
        return

    max_file_bytes = 5 * 1024 * 1024   # 5 MB per file
    max_total_bytes = 5 * 1024 * 1024  # 5 MB total
    total = 0
    workspace_root = None

    # A conversation id now rather than when the run is created, so the
    # workspace files this turn uses can be filed under it; the pipelines use
    # the request's id when it has one.
    if not request.conversation_id and any(a.file_id or a.store_to_workspace for a in request.attachments):
        request.conversation_id = str(uuid4())

    for idx, att in enumerate(request.attachments, start=1):
        if att.file_id:
            _resolve_file_attachment(request, att)
            continue

        att.filename = safe_attachment_filename(att.filename, idx)

        # Resolve the actual payload bytes — binary (content_b64) takes priority
        # over text (content) when both are present.
        binary_bytes: bytes | None = None
        if att.content_b64:
            try:
                binary_bytes = base64.b64decode(att.content_b64, validate=False)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attachment '{att.filename}' has invalid base64: {e}",
                )
            content_bytes = len(binary_bytes)
        else:
            content_bytes = len((att.content or "").encode("utf-8", errors="ignore"))

        if content_bytes > max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment '{att.filename}' is too large ({content_bytes} bytes). Limit is 5 MB per file.",
            )
        total += content_bytes
        if total > max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail="Total attachment size exceeds 5 MB.",
            )

        if att.store_to_workspace:
            if not request.workspace:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attachment '{att.filename}' is marked to store in workspace, but no workspace is selected.",
                )
            if workspace_root is None:
                from workspace import create_workspace_folder
                workspace_root = create_workspace_folder(request.workspace)

            uploads_dir = workspace_root / "chat_uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target = uploads_dir / f"{stamp}_{uuid4().hex[:8]}_{att.filename}"
            try:
                if binary_bytes is not None:
                    target.write_bytes(binary_bytes)
                else:
                    target.write_text(att.content or "", encoding="utf-8")
                att.stored_workspace_path = target.relative_to(workspace_root).as_posix()
            except (OSError, ValueError) as e:
                raise HTTPException(status_code=500, detail=f"Failed to store attachment '{att.filename}': {e}")
            data = binary_bytes if binary_bytes is not None else (att.content or "").encode("utf-8")
            _store_upload_as_file(request, att, data)

    _record_uses(request)
