"""
Agent tools over the workspace files (files/service.py, docs/files.md).

The same objects a person uploads on the Files page, attaches in the chat,
adds to a memory pool or gives a task, seen from inside a run:

* ``list_workspace_files`` names the files of the run's workspace (search by
  name, filter by source) with their ids;
* ``read_workspace_file`` returns one file's text (plain text, code, PDF),
  bounded and pageable with ``offset``; a binary file is described instead;
* ``save_workspace_file`` turns text the agent produced into a workspace file
  (source ``agent``), recorded on the run's entity sink so the chat reply
  links it, and returns its id for a person or another tool to use.

The workspace always comes from the run (``common.workspace_context``), never
from an argument: an agent reads and writes the files of the workspace it runs
in, and an id from another workspace is refused like a missing one.

Capabilities (tools/capabilities.py): the two readers grant ``reads_private``
like ``read_file``; the writer returns only the new record, like
``write_file``, and grants nothing.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools._json import json_err, json_ok

#: Largest slice of a file's text one read returns.
MAX_READ_CHARS = 50_000
DEFAULT_READ_CHARS = 20_000


def _workspace() -> Optional[str]:
    from common.workspace_context import resolve_active_workspace
    return resolve_active_workspace()


def _brief(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "file_id": record["file_id"], "name": record["name"], "mime_type": record["mime_type"],
        "size": record["size"], "source": record["source"], "created_at": record["created_at"],
        "url": f"/files?file={record['file_id']}",
    }


class ListWorkspaceFilesInput(BaseModel):
    query: str = Field(default="", description="Part of a file name to search for (empty lists the newest files)")
    source: str = Field(default="", description="Only files from this source: upload, chat, agent, memory, task, eval")
    limit: int = Field(default=50, description="How many files to return, at most 200")


@tool("list_workspace_files", args_schema=ListWorkspaceFilesInput)
def list_workspace_files(query: str = "", source: str = "", limit: int = 50) -> str:
    """List the files stored in this workspace (newest first), with their ids.

    Use the id with read_workspace_file to read one. These are the files people
    uploaded or attached in the chat and files agents saved, not the working
    directory: for that, use the filesystem tools.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    from files import service
    records = service.list_files(workspace, source=(source or "").strip() or None,
                                 q=(query or "").strip() or None,
                                 limit=max(1, min(int(limit or 50), 200)))
    return json_ok({"workspace": workspace, "files": [_brief(r) for r in records],
                    "count": len(records)})


class ReadWorkspaceFileInput(BaseModel):
    file_id: str = Field(..., description="Workspace file id, e.g. file_0123456789abcdef")
    offset: int = Field(default=0, description="Character offset to start reading at, for a long file")
    max_chars: int = Field(default=DEFAULT_READ_CHARS,
                           description=f"How many characters to return, at most {MAX_READ_CHARS}")


@tool("read_workspace_file", args_schema=ReadWorkspaceFileInput)
def read_workspace_file(file_id: str, offset: int = 0, max_chars: int = DEFAULT_READ_CHARS) -> str:
    """Read a workspace file's text by id: plain text, markdown, JSON, CSV, code
    and PDF. A long file comes back in slices: pass the returned `next_offset`
    to read on. A binary file (an image, an archive) is described, not read.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    from files import service
    record = service.get_file(str(file_id or "").strip())
    if record is None or record["workspace"] != workspace:
        return json_err(f"No file '{file_id}' in workspace '{workspace}'.", code="not_found")
    try:
        text = service.extract_text(record["file_id"])
    except KeyError:
        return json_err(f"The content of '{record['name']}' is no longer available.", code="gone")
    if text is None:
        return json_ok({**_brief(record), "text": None,
                        "note": f"{service.describe(record)} is binary; its text cannot be read."})
    start = max(0, int(offset or 0))
    size = max(1, min(int(max_chars or DEFAULT_READ_CHARS), MAX_READ_CHARS))
    chunk = text[start:start + size]
    end = start + len(chunk)
    body: Dict[str, Any] = {**_brief(record), "offset": start, "text": chunk,
                            "total_chars": len(text)}
    if end < len(text):
        body["next_offset"] = end
    return json_ok(body)


class SaveWorkspaceFileInput(BaseModel):
    name: str = Field(..., description="File name with an extension, e.g. summary.md or data.csv")
    content: str = Field(..., description="The file's full text content")


@tool("save_workspace_file", args_schema=SaveWorkspaceFileInput)
def save_workspace_file(name: str, content: str) -> str:
    """Save text you produced (a report, a table, a draft) as a workspace file
    and get its id back. The file shows on the Files page and can be attached
    to a chat, a task, a memory pool or an eval case. Saving the same content
    twice returns the file that already exists.
    """
    workspace = _workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")
    from files import service
    agent_id = None
    run_id = None
    session_id = None
    try:
        from common.agent_context import current_agent_id, current_session_id
        agent_id = current_agent_id.get()
        session_id = current_session_id.get()
    except Exception:  # noqa: BLE001 - provenance is a label; the file is saved without it
        agent_id = None
    agent_id = agent_id or os.environ.get("AGENT_ID") or None
    try:
        from common.stream_sink import current_run_id
        run_id = current_run_id()
    except Exception:  # noqa: BLE001 - provenance is a label; the file is saved without it
        run_id = None
    run_id = run_id or os.environ.get("AGENT_RUN_ID") or None
    meta = {k: v for k, v in (("agent_id", agent_id), ("run_id", run_id),
                              ("session_id", session_id)) if v}
    try:
        record = service.create_file(workspace, name, (content or "").encode("utf-8"),
                                     source="agent", created_by=agent_id, meta=meta)
    except service.FileError as exc:
        return json_err(str(exc), code="refused")
    from common.entity_sink import record_entity
    record_entity("workspace_file", record["file_id"], "created", label=record["name"],
                  workspace=workspace)
    return json_ok({**_brief(record), "saved": True})


WORKSPACE_FILE_TOOLS = [list_workspace_files, read_workspace_file, save_workspace_file]

__all__ = ["list_workspace_files", "read_workspace_file", "save_workspace_file",
           "WORKSPACE_FILE_TOOLS"]
