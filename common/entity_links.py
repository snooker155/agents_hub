"""
Entity links — turn the records a run's :class:`common.entity_sink.EntitySink`
collected into links back into the dashboard.

One place owns the mapping "entity kind → page URL + display label", because
every surface needs the same answer in a different shape:

* the web chat renders the records as clickable chips under the reply
  (:func:`entity_payloads`),
* Telegram and any other text-only surface append one markdown link per record
  to the reply text (:func:`append_entity_links`).

Label resolution is best-effort and lazy: a tool that just created a task
already holds its title and passes it as ``label``; anything without one is
looked up in its store here, and a record whose entity no longer exists (or
whose store errors) is dropped rather than linked into a 404.
"""
from __future__ import annotations

import logging
import os
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
    reply still shows the change in the file/diff panel.
    """
    workspace = (meta.get("workspace") or "").strip()
    if not workspace:
        return None
    return f"/workspaces/{quote(workspace)}?tab=files&file={quote(entity_id)}"


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
}

# Past-tense wording for the text surfaces, per action.
ACTION_WORDS = {
    "created": "created",
    "updated": "updated",
    "deleted": "deleted",
    "viewed": "opened",
    "scheduled": "scheduled",
    "cancelled": "cancelled",
    "started": "started",
    "stopped": "stopped",
    "assigned": "assigned",
    "blocked": "blocked",
}


def public_base() -> str:
    """Absolute-URL prefix when the hub knows its own address, else empty.

    The web chat wants site-relative links (it is already on the origin);
    Telegram needs absolute ones. ``AGENTS_HUB_PUBLIC_URL`` is the same knob the
    Telegram connector uses for its Studio deep-links.
    """
    return (os.environ.get("AGENTS_HUB_PUBLIC_URL") or "").rstrip("/")


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

    URLs are site-relative — that is the form stored with the message and used
    by the web chat, which is already on the origin. Text-only surfaces make
    them absolute through :func:`entity_link_lines`.

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


def entity_link_lines(items, *, seen_text: str = "") -> List[str]:
    """One markdown link line per entity payload, for surfaces that carry only text.

    ``items`` are :func:`entity_payloads` results. Their site-relative URLs are
    made absolute when ``AGENTS_HUB_PUBLIC_URL`` is set, so a Telegram link is
    clickable. Entities already linked in ``seen_text`` are skipped — when the
    agent wrote the link itself, repeating it is noise.
    """
    base = public_base()
    lines: List[str] = []
    seen: set = set()
    for item in items or []:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        entity_id = item.get("id") or ""
        if entity_id and entity_id in (seen_text or ""):
            continue
        # A flow reply concatenates several nodes' payloads; the same entity
        # touched by two nodes still gets one line.
        key = (item.get("kind") or "", entity_id)
        if key in seen:
            continue
        seen.add(key)
        word = ACTION_WORDS.get(item.get("action") or "", item.get("action") or "updated")
        url = f"{base}{item['url']}" if base and item["url"].startswith("/") else item["url"]
        noun = item.get("noun") or item.get("kind") or "Entity"
        lines.append(f"{item.get('icon') or '🔗'} {noun} {word}: [{item.get('title') or entity_id}]({url})")
    return lines


def append_entity_links(text: str, items, *, seen_text: str = "") -> str:
    """Return ``text`` with a link line appended for each entity the run touched."""
    lines = entity_link_lines(items, seen_text=seen_text or text or "")
    if not lines:
        return text
    body = (text or "").rstrip()
    return (body + "\n\n" if body else "") + "\n".join(lines)


__all__ = [
    "KINDS",
    "ACTION_WORDS",
    "public_base",
    "entity_payloads",
    "entity_link_lines",
    "append_entity_links",
]
