"""
Chat attachment handling.

Validates and (optionally) materializes uploaded attachments into the request's
workspace before the agent runs, enforcing per-file and total size limits.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import base64
import re

from fastapi import HTTPException

from chat.models import ChatRequest


def safe_attachment_filename(filename: str, idx: int) -> str:
    base = Path(filename or "").name.strip()
    if not base:
        base = f"attachment_{idx}.txt"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not safe:
        safe = f"attachment_{idx}.txt"
    return safe[:120]


def materialize_attachments(request: ChatRequest) -> None:
    """Validate attachment sizes and write any ``store_to_workspace`` files into
    the workspace's ``chat_uploads`` folder, recording their stored path on the
    attachment. Raises ``HTTPException`` on invalid base64 or size violations."""
    if not request.attachments:
        return

    max_file_bytes = 5 * 1024 * 1024   # 5 MB per file
    max_total_bytes = 5 * 1024 * 1024  # 5 MB total
    total = 0
    workspace_root = None

    for idx, att in enumerate(request.attachments, start=1):
        att.filename = safe_attachment_filename(att.filename, idx)

        # Resolve the actual payload bytes — binary (content_b64) takes priority
        # over text (content) when both are present.
        binary_bytes: bytes | None = None
        if att.content_b64:
            try:
                binary_bytes = base64.b64decode(att.content_b64, validate=False)
            except Exception as e:
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
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to store attachment '{att.filename}': {e}")
