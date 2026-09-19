"""
Flow execution preflight: validate → resolve → init state.

The flow engine runs an explicit, fail-fast pipeline before executing any node:

1. ``validate_flow(flow)``       — structural consistency of the loaded YAML/flow
   dict (ids, edges, entry_point, acyclicity, branch targets).
2. ``resolve_entities(flow)``    — collect every node's entity from the registry
   and confirm it exists; returns {node_id: FlowEntitySpec}.
3. ``FlowState.from_flow(...)``  — initialize shared state (done by the caller;
   validated here only insofar as declared keys are well-formed).
4. ``execution_order(flow)``     — ordered node ids to execute, starting from the
   declared ``entry_point`` (or the graph roots when unset).

Each stage raises ``FlowValidationError`` with an aggregated, human-readable
list of problems so the run fails before side effects, rather than skipping
nodes silently mid-flight.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Dict, List, Optional

from flow import registry as flow_registry
from flow import dispatch as flow_dispatch


class FlowValidationError(Exception):
    """Raised when a flow fails preflight validation. ``errors`` holds details."""

    def __init__(self, stage: str, errors: List[str]):
        self.stage = stage
        self.errors = errors
        super().__init__(f"Flow {stage} failed:\n  - " + "\n  - ".join(errors))


# ── stage 1: structural consistency ──────────────────────────────────────────

def validate_flow(flow: Dict[str, Any]) -> None:
    """Check the flow dict is structurally consistent. Raises on any problem."""
    errors: List[str] = []

    if not isinstance(flow, dict):
        raise FlowValidationError("validation", ["flow is not an object"])

    nodes = flow.get("nodes")
    edges = flow.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise FlowValidationError("validation", ["flow has no nodes"])
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []

    # Node ids present and unique.
    ids: List[str] = []
    for i, n in enumerate(nodes):
        nid = n.get("id") if isinstance(n, dict) else None
        if not nid:
            errors.append(f"node at index {i} has no id")
        else:
            ids.append(nid)
    id_set = set(ids)
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        errors.append(f"duplicate node ids: {sorted(dupes)}")

    # Edges reference existing nodes.
    for e in edges:
        if not isinstance(e, dict):
            errors.append(f"edge is not an object: {e!r}")
            continue
        s, t = e.get("source"), e.get("target")
        if s not in id_set:
            errors.append(f"edge source '{s}' is not a known node")
        if t not in id_set:
            errors.append(f"edge target '{t}' is not a known node")

    # entry_point, when set, must name a node.
    entry = flow.get("entry_point")
    if entry and entry not in id_set:
        errors.append(f"entry_point '{entry}' is not a known node")

    # state, when set, must be an object.
    state = flow.get("state")
    if state is not None and not isinstance(state, dict):
        errors.append("state must be an object (key -> default value)")

    # Acyclicity (the engine executes in topological order).
    if not errors and _has_cycle(ids, edges):
        errors.append("flow graph contains a cycle (must be a DAG)")

    if errors:
        raise FlowValidationError("validation", errors)


def _has_cycle(ids: List[str], edges: List[Dict]) -> bool:
    adjacency: Dict[str, List[str]] = {i: [] for i in ids}
    indegree: Dict[str, int] = {i: 0 for i in ids}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adjacency and t in indegree:
            adjacency[s].append(t)
            indegree[t] += 1
    queue = deque(i for i, d in indegree.items() if d == 0)
    visited = 0
    while queue:
        cur = queue.popleft()
        visited += 1
        for nb in adjacency[cur]:
            indegree[nb] -= 1
            if indegree[nb] == 0:
                queue.append(nb)
    return visited != len(ids)


# ── stage 2: resolve & verify entities ────────────────────────────────────────

def resolve_entities(nodes: List[Dict[str, Any]]) -> Dict[str, flow_registry.FlowEntitySpec]:
    """Resolve every node to a registry entity, confirming each exists.

    Returns {node_id: FlowEntitySpec}. Raises ``FlowValidationError`` listing
    every node whose entity_id/agent_id is not in the registry — so a missing
    class is caught up front, not as a mid-run skip.
    """
    resolved: Dict[str, flow_registry.FlowEntitySpec] = {}
    errors: List[str] = []
    for node in nodes:
        nid = node.get("id")
        spec = flow_dispatch.resolve_entity(node)
        if spec is None:
            ref = (
                flow_dispatch.node_field(node, "entity_id")
                or flow_dispatch.node_field(node, "agent_id")
                or "(none)"
            )
            label = flow_dispatch.node_field(node, "label") or nid
            errors.append(f"node '{label}' references unknown entity '{ref}'")
        else:
            resolved[nid] = spec
    if errors:
        raise FlowValidationError("entity resolution", errors)
    return resolved


# ── stage 4: execution order from entry_point ─────────────────────────────────

def execution_order(flow: Dict[str, Any]) -> List[str]:
    """Topological order of node ids, started from ``entry_point`` when set.

    With an entry_point, only nodes reachable from it are returned (in topo
    order); unreachable nodes are excluded from execution. Without one, all
    nodes are returned in topological order (graph roots first).
    """
    nodes = flow.get("nodes", [])
    edges = flow.get("edges", [])
    ids = [n["id"] for n in nodes]
    adjacency: Dict[str, List[str]] = {i: [] for i in ids}
    indegree: Dict[str, int] = {i: 0 for i in ids}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adjacency and t in indegree:
            adjacency[s].append(t)
            indegree[t] += 1

    entry = flow.get("entry_point")
    if entry and entry in adjacency:
        # Restrict to the subgraph reachable from the entry point, then topo-sort it.
        reachable: set = set()
        stack = [entry]
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(adjacency.get(cur, []))
        sub_indeg = {i: 0 for i in reachable}
        for s in reachable:
            for t in adjacency.get(s, []):
                if t in reachable:
                    sub_indeg[t] += 1
        # Entry point starts first regardless of computed indegree.
        queue = deque([entry] + [i for i in reachable if i != entry and sub_indeg[i] == 0])
        ordered: List[str] = []
        seen: set = set()
        while queue:
            cur = queue.popleft()
            if cur in seen:
                continue
            seen.add(cur)
            ordered.append(cur)
            for nb in adjacency.get(cur, []):
                if nb in reachable:
                    sub_indeg[nb] -= 1
                    if sub_indeg[nb] == 0:
                        queue.append(nb)
        return ordered

    # No entry point: full-graph topological order (roots first).
    queue = deque(i for i, d in indegree.items() if d == 0)
    ordered = []
    while queue:
        cur = queue.popleft()
        ordered.append(cur)
        for nb in adjacency[cur]:
            indegree[nb] -= 1
            if indegree[nb] == 0:
                queue.append(nb)
    return ordered if len(ordered) == len(ids) else ids
