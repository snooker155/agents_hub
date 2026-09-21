"""A compiled graph's shape, in the form the hub draws.

Observe mode has no way to ask: the hub never calls out to a connection, so a
graph that wants a picture has to send one. LangGraph knows its own topology and
will hand it over, which makes this a translation rather than an inspection.

The same normalisation as the pull-mode adapter
(``examples/imported-agents/langgraph-agenthub/adapter.py``), so a graph looks
the same whichever way it is attached.
"""
from __future__ import annotations

from typing import Any, Dict


def graph_topology(graph: Any) -> Dict[str, Any]:
    """Nodes and edges of a compiled LangGraph graph.

    Returns an empty topology rather than raising for anything that is not a
    compiled graph: reporting a shape is a nice-to-have, and a wrong argument
    here must not be the thing that stops a run from being monitored.
    """
    try:
        raw = graph.get_graph().to_json()
    except Exception:
        return {"framework": "langgraph", "nodes": [], "edges": []}

    nodes = []
    for node in raw.get("nodes") or []:
        node_id = str(node.get("id"))
        nodes.append({
            "id": node_id,
            "label": str(node.get("name") or node_id),
            # __start__/__end__ are LangGraph's own terminals; marking them lets
            # a renderer draw them as terminals rather than as work.
            "kind": "terminal" if node_id in ("__start__", "__end__") else "node",
        })

    edges = []
    for edge in raw.get("edges") or []:
        edges.append({
            "source": str(edge.get("source")),
            "target": str(edge.get("target")),
            "label": str(edge.get("data") or "") or None,
            # A conditional edge is a branch the run may or may not take, which
            # is what someone watching wants to see resolved live.
            "conditional": bool(edge.get("conditional")),
        })

    return {"framework": "langgraph", "nodes": nodes, "edges": edges}


__all__ = ["graph_topology"]
