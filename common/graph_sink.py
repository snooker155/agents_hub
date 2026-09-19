"""
Graph sink — a context-var channel for live project-graph mutations.

The Architect Agent builds a project's structure graph by calling graph-builder
tools (``add_graph_node`` / ``add_graph_edge`` / ``clear_graph``). Rather than
coupling those low-level tools to the web layer, the chat/generate endpoint
installs a handler on a ContextVar for the duration of a run; the tools call
:func:`emit_node` / :func:`emit_edge` / :func:`clear_graph`, which forward to the
handler if (and only if) one is present.

The handler (see the projects route) persists each change to the graph store and
pushes an SSE event onto the run's queue, so a new node appears on the canvas the
instant the agent adds it — no waiting for the final output. Outside a run the
ContextVar is empty and the tools no-op (returning a hint), so the same tools are
safe to call from a CLI or test.
"""
from __future__ import annotations

import contextvars
from typing import Any, Dict, Optional, Protocol


class GraphHandler(Protocol):
    def add_node(self, node: Dict[str, Any]) -> Dict[str, Any]: ...
    def add_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]: ...
    def delete_node(self, node_id: str) -> Dict[str, Any]: ...
    def delete_edge(self, source: str, target: str) -> Dict[str, Any]: ...
    def clear(self) -> None: ...
    def read_view(self, view: Optional[str]) -> Dict[str, Any]: ...


_graph_handler: contextvars.ContextVar[Optional[GraphHandler]] = contextvars.ContextVar(
    "graph_handler", default=None
)


def set_handler(handler: Optional[GraphHandler]):
    """Install a handler for the current context. Returns the reset token."""
    return _graph_handler.set(handler)


def reset_handler(token) -> None:
    """Restore the previous handler using a token from :func:`set_handler`."""
    try:
        _graph_handler.reset(token)
    except Exception:
        pass


def active() -> bool:
    """Whether a graph handler is installed in the current context."""
    return _graph_handler.get() is not None


def emit_node(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Forward a node to the active handler; returns the stored node or None."""
    handler = _graph_handler.get()
    if handler is None:
        return None
    return handler.add_node(node)


def emit_edge(edge: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Forward an edge to the active handler; returns the stored edge or None."""
    handler = _graph_handler.get()
    if handler is None:
        return None
    return handler.add_edge(edge)


def emit_delete_node(node_id: str) -> Optional[Dict[str, Any]]:
    """Delete a node (and its edges) via the active handler; None if no handler."""
    handler = _graph_handler.get()
    if handler is None:
        return None
    return handler.delete_node(node_id)


def emit_delete_edge(source: str, target: str) -> Optional[Dict[str, Any]]:
    """Delete one edge via the active handler; None if no handler is installed."""
    handler = _graph_handler.get()
    if handler is None:
        return None
    return handler.delete_edge(source, target)


def clear_graph() -> bool:
    """Clear the live graph via the active handler. Returns True if applied."""
    handler = _graph_handler.get()
    if handler is None:
        return False
    handler.clear()
    return True


def read_view(view: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read another view's graph via the active handler.

    ``view`` is ``"architecture"`` / ``"process"``; ``None`` means the sibling of
    the view currently being built. Returns ``{"view", "nodes", "edges"}`` or
    ``None`` when no handler is installed (CLI / one-shot generation).
    """
    handler = _graph_handler.get()
    if handler is None:
        return None
    return handler.read_view(view)


__all__ = [
    "GraphHandler", "set_handler", "reset_handler", "active",
    "emit_node", "emit_edge", "emit_delete_node", "emit_delete_edge",
    "clear_graph", "read_view",
]
