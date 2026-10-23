"""
Entity links — turn the records a run's :class:`common.entity_sink.EntitySink`
collected into links back into the dashboard.

One place owns the mapping "entity kind → page URL + display label": the web
chat renders the records as clickable chips under the reply
(:func:`entity_payloads`).

Label resolution is best-effort and lazy: a tool that just created a task
already holds its title and passes it as ``label``; anything without one is
looked up in its store here, and a record whose entity no longer exists (or
whose store errors) is dropped rather than linked into a 404.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

log = logging.getLogger(__name__)

# ── Kind registry ────────────────────────────────────────────────────────────
#
# ``path``  builds the site-relative URL for one record.
# ``label`` resolves a display name when the recording tool did not supply one;
#           returning None means "this entity is gone" and drops the record.
# ``icon``/``noun`` are what the UI and the text lines show.


def _task_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from uuid import UUID
    from tasks.service import get_task
    task = get_task(UUID(entity_id))
    if task is None:
        return None
    return (task.title or "").strip() or f"Task {entity_id[:8]}"


def _view_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from views.store import view_index_row
    row = view_index_row(entity_id)
    if not row:
        return None
    return (row.get("title") or "").strip() or "Untitled view"


def _flow_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from flow.store import get_flow
    flow = get_flow(entity_id)
    if not flow:
        return None
    return (flow.get("name") or "").strip() or entity_id


def _agent_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from agents.registry import get_agent
    spec = get_agent(entity_id)
    if spec is None:
        return None
    return (getattr(spec, "name", "") or "").strip() or entity_id


def _job_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from plans.service import get_job
    job = get_job(entity_id)
    if job is None:
        return None
    return (getattr(job, "title", "") or "").strip() or f"Job {entity_id[:8]}"


def _project_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    project = ProjectStore(path=PROJECTS_FILE).get(entity_id)
    if project is None:
        return None
    return (project.name or "").strip() or entity_id


def _file_path(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    """Files live under a workspace, so a link needs the workspace name.

    Without one there is no page to point at, so the record is dropped: the
    reply still shows the change in the file/diff panel. The link opens the
    file on the Artifacts page by its registry id (files/service.py), and a
    file nothing registered yet is registered here. A path the registry does
    not follow (a hidden folder) has no place there, so it keeps the Files
    tab of its workspace, linked by the path.
    """
    workspace = (meta.get("workspace") or "").strip()
    if not workspace:
        return None
    from files.service import FileError, ensure_folder_record
    try:
        record = ensure_folder_record(workspace, entity_id)
    except FileError:
        record = None
    if record:
        return f"/artifacts?file={quote(record['file_id'])}"
    return f"/workspaces/{quote(workspace)}?tab=files&file={quote(entity_id)}"


def file_url(path: str, workspace: str) -> Optional[str]:
    """The dashboard page of a file in ``workspace`` (the link a ``file``
    entity gets), for a chat that lists the files a turn changed."""
    return _file_path(path, {"workspace": workspace})


def _workspace_file_label(entity_id: str, meta: Dict[str, Any]) -> Optional[str]:
    """A workspace file (files/service.py) is linked by id; a deleted one
    has no page left, so it drops out of the list."""
    from files.service import get_file
    record = get_file(entity_id)
    return record["name"] if record else None


KindSpec = Dict[str, Any]

KINDS: Dict[str, KindSpec] = {
    "task": {
        "icon": "✅",
        "noun": "Task",
        "path": lambda eid, meta: f"/tasks/{quote(eid)}",
        "label": _task_label,
    },
    "view": {
        "icon": "📊",
        "noun": "View",
        "path": lambda eid, meta: f"/views/{quote(eid)}",
        "label": _view_label,
    },
    "flow": {
        "icon": "🔀",
        "noun": "Flow",
        "path": lambda eid, meta: f"/flows/{quote(eid)}",
        "label": _flow_label,
    },
    "agent": {
        "icon": "🤖",
        "noun": "Agent",
        "path": lambda eid, meta: f"/agents/{quote(eid)}",
        "label": _agent_label,
    },
    "job": {
        "icon": "⏰",
        # Short, because the text line reads "<noun> <action>" — "Job scheduled".
        "noun": "Job",
        "path": lambda eid, meta: f"/plan?tab=jobs&job={quote(eid)}",
        "label": _job_label,
    },
    "project": {
        "icon": "📁",
        "noun": "Project",
        "path": lambda eid, meta: f"/projects/{quote(eid)}",
        "label": _project_label,
    },
    "file": {
        "icon": "📄",
        "noun": "File",
        "path": _file_path,
        "label": lambda eid, meta: eid,
    },
    # A workspace file object (docs/files.md), unlike "file" above, which is
    # a path in the workspace folder.
    "workspace_file": {
        "icon": "🗂️",
        "noun": "Workspace file",
        "path": lambda eid, meta: f"/artifacts?file={quote(eid)}",
        "label": _workspace_file_label,
    },
}

def _resolve_label(kind_spec: KindSpec, record: Dict[str, Any]) -> Optional[str]:
    label = (record.get("label") or "").strip()
    if label:
        return label
    resolver: Optional[Callable] = kind_spec.get("label")
    if resolver is None:
        return record.get("id")
    try:
        return resolver(record.get("id") or "", record.get("meta") or {})
    except Exception:  # noqa: BLE001 - one kind's label resolver must not break the whole link list
        log.debug("entity label lookup failed for %r", record, exc_info=True)
        return None


def entity_payloads(records) -> List[Dict[str, Any]]:
    """Records → ``[{kind, id, action, title, url, icon, noun}, …]``.

    URLs are site-relative: that is the form stored with the message and used
    by the web chat, which is already on the origin.

    Unknown kinds, entities that no longer exist and records with no page to
    point at are dropped. Order follows first touch, so the links read in the
    order the run did the work.
    """
    out: List[Dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        kind_spec = KINDS.get(record.get("kind") or "")
        if kind_spec is None:
            continue
        meta = record.get("meta") or {}
        entity_id = record.get("id") or ""
        if not entity_id:
            continue
        try:
            path = kind_spec["path"](entity_id, meta)
        except Exception:  # noqa: BLE001 - one record's path builder must not break the whole link list
            log.debug("entity path build failed for %r", record, exc_info=True)
            continue
        if not path:
            continue
        title = _resolve_label(kind_spec, record)
        if not title:
            continue
        out.append({
            "kind": record["kind"],
            "id": entity_id,
            "action": record.get("action") or "updated",
            "title": title,
            "url": path,
            "icon": kind_spec["icon"],
            "noun": kind_spec["noun"],
        })
    return out


__all__ = [
    "KINDS",
    "entity_payloads",
]
