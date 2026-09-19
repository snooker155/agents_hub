"""
Live graph sink handler for interactive build sessions.

``GraphStreamSink`` is the bridge between the Architect Agent's graph-builder
tools ([tools.graph_builder] via [common.graph_sink]) and a running SSE stream:
each ``add_node`` / ``add_edge`` / ``clear`` both **persists** to the graph store
and **pushes** an event onto the run's asyncio queue, so the canvas updates the
instant the agent acts — no waiting for the final reply.
"""
from __future__ import annotations

from typing import Any, Dict, List

# Grid spacing for incrementally-placed nodes (the agent supplies no
# coordinates; positions are assigned here as nodes arrive).
_COL_W = 220
_ROW_H = 130
_COLS = 5


class GraphStreamSink:
    """Persists live graph mutations and forwards them onto an SSE queue."""

    def __init__(self, loop, queue, store, project_id: str, view: str):
        self.loop = loop
        self.queue = queue
        self.store = store
        self.project_id = project_id
        self.view = view
        saved = store.get(project_id, view) or {}
        # Drop any legacy placeholder node so the agent extends a clean graph.
        from projects.graph import strip_placeholder_nodes
        _nodes, _edges = strip_placeholder_nodes(saved.get("nodes") or [], saved.get("edges") or [])
        self.nodes: List[Dict[str, Any]] = list(_nodes)
        self.edges: List[Dict[str, Any]] = list(_edges)
        self._node_ids = {n.get("id") for n in self.nodes}
        self.touched = False  # whether the agent changed anything this run
        self.added_nodes = 0
        self.added_edges = 0
        self.removed_nodes = 0
        self.removed_edges = 0
        self.cleared = False

    def summary(self) -> str:
        """One-line description of what changed this run (for an empty reply)."""
        parts = []
        if self.cleared:
            parts.append("cleared the graph")
        if self.added_nodes:
            parts.append(f"added {self.added_nodes} node" + ("s" if self.added_nodes != 1 else ""))
        if self.added_edges:
            parts.append(f"{self.added_edges} edge" + ("s" if self.added_edges != 1 else ""))
        if self.removed_nodes:
            parts.append(f"removed {self.removed_nodes} node" + ("s" if self.removed_nodes != 1 else ""))
        if self.removed_edges:
            parts.append(f"removed {self.removed_edges} edge" + ("s" if self.removed_edges != 1 else ""))
        if not parts:
            return ""
        return ("Done — " + ", ".join(parts) + ".").capitalize()

    # ── internals ──────────────────────────────────────────────────────────
    def _emit(self, payload: Dict[str, Any]) -> None:
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        except Exception:
            pass

    def _persist(self) -> None:
        # source="generated" — an agent-built graph (distinct from hand "manual").
        self.store.save(self.project_id, self.view, self.nodes, self.edges, source="generated")

    def _next_pos(self) -> Dict[str, int]:
        i = len(self.nodes)
        return {"x": (i % _COLS) * _COL_W, "y": (i // _COLS) * _ROW_H}

    def _relayout(self) -> None:
        """Re-run the kind-grouped layout (same as the "Arrange" action) and stream
        the new positions so the canvas keeps each type in its own row with the
        hubs on the left as the agent builds the graph."""
        try:
            from projects.graph import kind_grouped_layout
            kind_grouped_layout(self.nodes, self.edges)
        except Exception:
            return
        positions = {n["id"]: n.get("position") for n in self.nodes if n.get("position")}
        self._emit({"type": "graph_layout", "positions": positions})

    # ── handler protocol ───────────────────────────────────────────────────
    def add_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        nid = node.get("id")
        if not nid:
            return node
        self.touched = True
        if nid in self._node_ids:
            for i, n in enumerate(self.nodes):
                if n.get("id") == nid:
                    node["position"] = n.get("position") or self._next_pos()
                    self.nodes[i] = node
                    break
        else:
            node["position"] = self._next_pos()
            self.nodes.append(node)
            self._node_ids.add(nid)
            self.added_nodes += 1
        self._relayout()
        self._persist()
        self._emit({"type": "graph_node", "node": node})
        return node

    def add_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        src, tgt = edge.get("source"), edge.get("target")
        if src not in self._node_ids or tgt not in self._node_ids:
            return {"rejected": True}
        self.touched = True
        eid = f"{src}->{tgt}"
        label = edge.get("label", "")
        existing = next((e for e in self.edges if e.get("id") == eid), None)
        if existing is not None:
            existing["label"] = label  # re-adding an edge updates its label
            stored = existing
        else:
            stored = {
                "id": eid, "source": src, "target": tgt, "label": label,
                "animated": True, "markerEnd": {"type": "arrowclosed"},
            }
            self.edges.append(stored)
            self.added_edges += 1
        self._relayout()  # a new edge can change the whole layout
        self._persist()
        self._emit({"type": "graph_edge", "edge": stored})
        return stored

    def delete_node(self, node_id: str) -> Dict[str, Any]:
        nid = (node_id or "").strip()
        if nid not in self._node_ids:
            return {"deleted": False, "error": f"unknown node id '{nid}'"}
        self.touched = True
        self.nodes = [n for n in self.nodes if n.get("id") != nid]
        self._node_ids.discard(nid)
        removed_edges = [e.get("id") for e in self.edges
                         if e.get("source") == nid or e.get("target") == nid]
        self.edges = [e for e in self.edges
                      if e.get("source") != nid and e.get("target") != nid]
        self.removed_nodes += 1
        self._relayout()
        self._persist()
        self._emit({"type": "graph_node_delete", "id": nid, "edges": removed_edges})
        return {"deleted": True, "id": nid, "removed_edges": removed_edges}

    def delete_edge(self, source: str, target: str) -> Dict[str, Any]:
        eid = f"{(source or '').strip()}->{(target or '').strip()}"
        before = len(self.edges)
        self.edges = [e for e in self.edges if e.get("id") != eid]
        if len(self.edges) == before:
            return {"deleted": False, "error": f"unknown edge '{eid}'"}
        self.touched = True
        self.removed_edges += 1
        self._relayout()
        self._persist()
        self._emit({"type": "graph_edge_delete", "id": eid})
        return {"deleted": True, "id": eid}

    def clear(self) -> None:
        self.touched = True
        self.cleared = True
        self.nodes = []
        self.edges = []
        self._node_ids = set()
        self._persist()
        self._emit({"type": "graph_clear"})

    def read_view(self, view: str | None = None) -> Dict[str, Any]:
        """Return another view's graph for cross-reference (read-only).

        ``view`` is ``"architecture"`` / ``"process"``; ``None`` (or the view
        currently being built) returns the sibling view. The active view is
        served from live in-memory state; any other view from the store.
        """
        from projects.graph import other_view
        target = (view or "").strip() or other_view(self.view)
        if target == self.view:
            # The view being built — serve its live in-memory state.
            return {"view": target, "nodes": list(self.nodes), "edges": list(self.edges)}
        saved = self.store.get(self.project_id, target) or {}
        return {
            "view": target,
            "nodes": saved.get("nodes") or [],
            "edges": saved.get("edges") or [],
        }


__all__ = ["GraphStreamSink"]
