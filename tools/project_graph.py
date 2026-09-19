"""
Read-only access to a project's saved structure graph (process / architecture view).

This is the consumer side of the project graph that the Architect Agent builds and
the canvas persists (see :mod:`projects.graph_store`). A general agent gets this tool
only when its run is scoped to a project — :meth:`AgentFactory.create_agent` injects it
(plus a short usage note) based on ``resolve_active_project()`` so it is absent in
unscoped chats. With it, an agent can consult the structure a user laid out on the
canvas instead of re-deriving it from a raw file scan.

The active project is read from the run context, never passed by the model — the agent
only chooses which *view* to read.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from common.entity_sink import record_entity
from common.workspace_context import resolve_active_project


class GetProjectGraphInput(BaseModel):
    view: str = Field(
        "process",
        description=(
            "Which view to read: 'process' (the business/process flow, default) or "
            "'architecture' (the technical structure: client → service → data/deps)."
        ),
    )


def _resolve_root(project) -> Optional[Any]:
    from workspace import get_workspace_folder, project_folder_name
    try:
        ws = get_workspace_folder(project.workspace)
        if ws is None:
            return None
        cand = (ws / project_folder_name(project.name)).resolve()
        return cand if cand.exists() else None
    except Exception:
        return None


def _load_graph(project_id: str, view: str) -> Optional[Dict[str, Any]]:
    """The saved graph for ``(project_id, view)`` if present, else the deterministic
    build (the same auto path the dashboard serves). ``None`` if the project is gone."""
    from projects.storage import ProjectStore
    from projects.graph_store import ProjectGraphStore
    from common.paths import PROJECTS_FILE

    project = ProjectStore(path=PROJECTS_FILE).get(project_id)
    if project is None:
        return None

    saved = ProjectGraphStore().get(project_id, view)
    if saved:
        from projects.graph import strip_placeholder_nodes
        saved["nodes"], saved["edges"] = strip_placeholder_nodes(
            saved.get("nodes") or [], saved.get("edges") or [])
        return saved

    from projects.graph import build_project_graph

    tasks = None
    if view == "process":
        try:
            from tasks import service as tasks_service
            tasks = [t for t in tasks_service.list_tasks() if t.project_id == project_id]
        except Exception:
            tasks = None
    try:
        graph = build_project_graph(project, view, _resolve_root(project), tasks)
        graph["source"] = "auto"
        return graph
    except Exception:
        return None


def _compact(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Drop canvas-only fields (coordinates, react-flow type) the model doesn't need."""
    nodes: List[dict] = []
    for n in graph.get("nodes") or []:
        data = n.get("data") or {}
        node = {"id": n.get("id"), "label": data.get("label", "")}
        for k in ("kind", "subtitle", "group"):
            if data.get(k):
                node[k] = data[k]
        nodes.append(node)
    edges: List[dict] = []
    for e in graph.get("edges") or []:
        edge = {"source": e.get("source"), "target": e.get("target")}
        if e.get("label"):
            edge["label"] = e["label"]
        edges.append(edge)
    return {
        "view": graph.get("view"),
        "source": graph.get("source"),
        "nodes": nodes,
        "edges": edges,
    }


@tool("get_project_graph", args_schema=GetProjectGraphInput)
def get_project_graph(view: str = "process") -> str:
    """Read the current project's structure graph (process or architecture view).

    Returns the nodes and edges a user built on the project canvas, as JSON. The
    'process' view (default) is the business/process flow; the 'architecture' view is
    the technical structure (client → service → data/dependencies). Use this to ground
    your work in the project's intended structure before scanning files.
    """
    view = (view or "process").strip().lower()
    if view not in ("process", "architecture"):
        return json.dumps({"ok": False, "error": "view must be 'process' or 'architecture'"})

    project_id = resolve_active_project()
    if not project_id:
        return json.dumps({"ok": False, "error": "no active project for this run"})

    graph = _load_graph(project_id, view)
    if graph is None:
        return json.dumps({"ok": False, "error": "project not found or graph unavailable"})

    record_entity("project", project_id, "viewed")

    out = _compact(graph)
    if not out["nodes"]:
        return json.dumps(
            {"ok": True, "view": view, "empty": True,
             "note": f"No {view} graph has been built for this project yet.",
             "nodes": [], "edges": []},
            ensure_ascii=False,
        )
    return json.dumps({"ok": True, **out}, ensure_ascii=False)


PROJECT_GRAPH_TOOLS = [get_project_graph]

__all__ = ["get_project_graph", "PROJECT_GRAPH_TOOLS"]
