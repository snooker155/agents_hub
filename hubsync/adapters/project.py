"""
Projects for the sync engine: the project record and the graphs a person kept.

Synced: the project document (type, repository link, tracker link, the
frontend and backend settings, tags) and its saved structure graphs that have
content, one per view. A graph's chat transcript and trace are conversation
history of the architect chat on one hub and stay there; so does the session
epoch. A saved graph over 256 KB is left out rather than making the record
unwritable.

Not synced: deployments (``project_deployments``) are live infrastructure on
the hub that runs them, and the files in the repository checkout, which the
workspace files kind or git carry. A repository URL with credentials in it
(``https://user:token@host/...``) is written without the user part, so a token
pasted into a clone URL never leaves the hub.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from hubsync.adapters.base import Adapter, AdapterError, register

_MAX_GRAPH_BYTES = 256_000


def _without_credentials(url: Optional[str]) -> Optional[str]:
    if not url or "@" not in url or "://" not in url:
        return url
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))


def _graphs(project_id: str) -> Dict[str, Dict[str, Any]]:
    import json

    from projects.graph_store import ProjectGraphStore
    docs = ProjectGraphStore().docs
    out: Dict[str, Dict[str, Any]] = {}
    for key in sorted(docs.keys()):
        if not key.startswith(f"{project_id}:"):
            continue
        entry = docs.get(key) or {}
        if not entry.get("nodes"):
            continue
        graph = {"nodes": entry.get("nodes") or [], "edges": entry.get("edges") or [],
                 "source": entry.get("source") or "manual"}
        if len(json.dumps(graph, default=str)) <= _MAX_GRAPH_BYTES:
            out[key.split(":", 1)[1]] = graph
    return out


class ProjectAdapter(Adapter):
    kind = "project"
    order = 44
    label = "Projects"

    def list_ids(self, workspace: str, owner: str) -> List[str]:
        from projects.storage import ProjectStore
        return sorted(p.id for p in ProjectStore().list() if p.workspace == workspace)

    def read(self, workspace: str, record_id: str) -> Optional[Dict[str, Any]]:
        from projects.storage import ProjectStore
        project = ProjectStore().get(record_id)
        if project is None:
            return None
        doc = project.model_dump(mode="json")
        for key in ("id", "workspace"):
            doc.pop(key, None)
        doc["repo"]["url"] = _without_credentials(doc["repo"].get("url"))
        doc["graphs"] = _graphs(record_id)
        return doc

    def apply(self, workspace: str, record_id: str, doc: Dict[str, Any], *, owner: str) -> str:
        from projects.graph_store import ProjectGraphStore
        from projects.models import Project
        from projects.storage import ProjectStore
        store = ProjectStore()
        prev = store.get(record_id)
        if prev is not None and prev.workspace != workspace:
            raise AdapterError(f"project {record_id} lives in another workspace here")
        data = {k: v for k, v in doc.items() if k != "graphs"}
        data.update(id=record_id, workspace=workspace)
        if prev is not None and isinstance(data.get("repo"), dict):
            # A URL stripped of credentials on the way out must not wipe the
            # one that works here.
            if _without_credentials(prev.repo.url) == data["repo"].get("url"):
                data["repo"] = {**data["repo"], "url": prev.repo.url}
        try:
            store.add(Project.model_validate(data))
        except ValueError as exc:
            raise AdapterError(f"unreadable project: {exc}") from exc
        graphs = ProjectGraphStore()
        incoming = doc.get("graphs") or {}
        for view, graph in incoming.items():
            graphs.save(record_id, view, graph.get("nodes") or [], graph.get("edges") or [],
                        source=graph.get("source") or "manual")
        for view in _graphs(record_id):
            if view not in incoming:
                graphs.delete(record_id, view)
        return record_id

    def delete(self, workspace: str, record_id: str) -> None:
        from projects.graph_store import ProjectGraphStore
        from projects.storage import ProjectStore
        ProjectStore().delete(record_id)
        ProjectGraphStore().delete_project(record_id)


register(ProjectAdapter())
