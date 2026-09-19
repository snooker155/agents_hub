"""
Graph-builder tools for the Architect Agent.

These let the agent construct a project's structure graph incrementally — each
call adds one node or edge to the live graph, which the chat/generate endpoint
persists and pushes to the canvas immediately (see :mod:`common.graph_sink`).
Building via tools (instead of emitting one final JSON blob) means the user sees
the graph take shape step by step and can steer it mid-build.

When no graph sink is installed (CLI / tests) the tools no-op with a hint.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from common import graph_sink

_KINDS = "frontend, backend, datastore, external, module, task, subtask, decision, actor, artifact"


class AddNodeInput(BaseModel):
    id: str = Field(..., description="Unique slug id for the node (lowercase, stable).")
    label: str = Field(..., description="Short display name.")
    kind: str = Field("module", description=f"One of: {_KINDS}.")
    subtitle: str = Field("", description="Optional one-line detail.")
    group: str = Field("", description=(
        "Optional cluster name (e.g. 'frontend', 'data', 'payments'). Nodes that "
        "share a group are laid out together as a band. Positions are computed "
        "automatically from the edges — never set coordinates."))


class AddEdgeInput(BaseModel):
    source: str = Field(..., description="Id of the source node (must already exist).")
    target: str = Field(..., description="Id of the target node (must already exist).")
    label: str = Field("", description="Relation label, e.g. 'then', 'calls', 'triggers'.")


class DeleteNodeInput(BaseModel):
    id: str = Field(..., description="Id of the node to delete. Its connected edges are removed too.")


class DeleteEdgeInput(BaseModel):
    source: str = Field(..., description="Id of the edge's source node.")
    target: str = Field(..., description="Id of the edge's target node.")


class ReadViewInput(BaseModel):
    view: str = Field("", description=(
        "Which view to read: 'architecture' (technical structure) or 'process' "
        "(business flow). Leave empty for the OTHER view — the one you are not "
        "currently building."))


@tool("add_graph_node", args_schema=AddNodeInput)
def add_graph_node(id: str, label: str, kind: str = "module", subtitle: str = "", group: str = "") -> str:
    """Add (or update) one node on the project graph. Applied to the live canvas immediately.

    Call this once per component/step as you identify it — do not batch them into
    a JSON blob. Reuse an existing id to update that node. Set ``group`` to cluster
    related nodes; positions are computed automatically from the edges.
    """
    node = {
        "id": str(id).strip(),
        "type": "default",
        "data": {"label": str(label), "kind": str(kind or "module"),
                 "subtitle": str(subtitle or ""), "group": str(group or "")},
    }
    stored = graph_sink.emit_node(node)
    if stored is None:
        return json.dumps({"ok": False, "error": "no active graph (call outside a build session)"})
    return json.dumps({"ok": True, "added_node": stored["id"]}, ensure_ascii=False)


@tool("add_graph_edge", args_schema=AddEdgeInput)
def add_graph_edge(source: str, target: str, label: str = "") -> str:
    """Add one directed edge between two existing nodes. Applied to the live canvas immediately.

    Both ``source`` and ``target`` must be ids of nodes you already added. Re-call
    with the same source/target to change an existing edge's label.
    """
    edge = {"source": str(source).strip(), "target": str(target).strip(), "label": str(label or "")}
    stored = graph_sink.emit_edge(edge)
    if stored is None:
        return json.dumps({"ok": False, "error": "no active graph (call outside a build session)"})
    if stored.get("rejected"):
        return json.dumps({"ok": False, "error": f"unknown node id in edge {source}->{target}"})
    return json.dumps({"ok": True, "added_edge": f"{source}->{target}"}, ensure_ascii=False)


@tool("delete_graph_node", args_schema=DeleteNodeInput)
def delete_graph_node(id: str) -> str:
    """Delete one node and its connected edges from the project graph. Applied to the live canvas immediately."""
    res = graph_sink.emit_delete_node(str(id).strip())
    if res is None:
        return json.dumps({"ok": False, "error": "no active graph (call outside a build session)"})
    if not res.get("deleted"):
        return json.dumps({"ok": False, "error": res.get("error") or "node not found"})
    return json.dumps({"ok": True, "deleted_node": res.get("id"),
                       "removed_edges": res.get("removed_edges", [])}, ensure_ascii=False)


@tool("delete_graph_edge", args_schema=DeleteEdgeInput)
def delete_graph_edge(source: str, target: str) -> str:
    """Delete one directed edge between two nodes from the project graph. Applied to the live canvas immediately."""
    res = graph_sink.emit_delete_edge(str(source).strip(), str(target).strip())
    if res is None:
        return json.dumps({"ok": False, "error": "no active graph (call outside a build session)"})
    if not res.get("deleted"):
        return json.dumps({"ok": False, "error": res.get("error") or "edge not found"})
    return json.dumps({"ok": True, "deleted_edge": res.get("id")}, ensure_ascii=False)


@tool("clear_graph")
def clear_graph() -> str:
    """Remove all nodes and edges to start the graph over. Applied to the live canvas immediately."""
    if graph_sink.clear_graph():
        return json.dumps({"ok": True, "cleared": True})
    return json.dumps({"ok": False, "error": "no active graph (call outside a build session)"})


@tool("read_graph_view", args_schema=ReadViewInput)
def read_graph_view(view: str = "") -> str:
    """Read another view's graph (architecture or process) for cross-reference.

    The project has two complementary views of the same system: an architecture
    view (services, data stores, dependencies) and a process view (actors, steps,
    handoffs). Use this to align the view you are building with the other one —
    e.g. to derive an architecture from an existing process flow, or to check the
    process against the real components. Leave ``view`` empty to read the OTHER
    view (the one you are not currently building). This is read-only — it does not
    change either graph. The two views are different lenses, so adapt rather than
    copy nodes verbatim.
    """
    result = graph_sink.read_view((view or "").strip() or None)
    if result is None:
        return json.dumps({"ok": False,
                           "error": "no active graph session — the other view is "
                                    "unavailable outside an interactive build"})
    nodes = [
        {"id": n.get("id"),
         "label": (n.get("data") or {}).get("label"),
         "kind": (n.get("data") or {}).get("kind"),
         "subtitle": (n.get("data") or {}).get("subtitle") or ""}
        for n in (result.get("nodes") or [])
    ]
    edges = [
        {"source": e.get("source"), "target": e.get("target"), "label": e.get("label") or ""}
        for e in (result.get("edges") or [])
    ]
    return json.dumps({"ok": True, "view": result.get("view"),
                       "nodes": nodes, "edges": edges}, ensure_ascii=False)


GRAPH_BUILDER_TOOLS = [add_graph_node, add_graph_edge, delete_graph_node,
                       delete_graph_edge, clear_graph, read_graph_view]

__all__ = ["add_graph_node", "add_graph_edge", "delete_graph_node",
           "delete_graph_edge", "clear_graph", "read_graph_view",
           "GRAPH_BUILDER_TOOLS"]
