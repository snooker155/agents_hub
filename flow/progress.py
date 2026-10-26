"""
How far a flow run has got: nodes passed, the node(s) working, and how many
are left on the longest way to the end.

A flow with branches has no fixed length: which nodes run depends on the
switches it meets. So the total is what the run has done plus the longest
path still open from where it stands, counted from the node working now (or,
between nodes, from the ones the finished nodes lead to). A branch the run
did not take drops out as soon as it is passed over, so the total only
shrinks or holds as the run goes on, and equals the done count at the end.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

#: Log lines that end a node.
_DONE_TYPES = ("agent_finish", "agent_error", "agent_stopped")


def _graph(flow: Dict[str, Any]) -> Dict[str, List[str]]:
    succ: Dict[str, List[str]] = {str(n.get("id")): [] for n in flow.get("nodes") or [] if n.get("id")}
    for e in flow.get("edges") or []:
        source, target = str(e.get("source") or ""), str(e.get("target") or "")
        if source in succ and target in succ and target not in succ[source]:
            succ[source].append(target)
    return succ


def longest_path(succ: Dict[str, List[str]], start: str, skip: Set[str]) -> int:
    """Nodes on the longest path from ``start`` (counted) to an end, through
    nodes not in ``skip``. A cycle (a loop-back edge) is walked once."""
    memo: Dict[str, int] = {}
    on_path: Set[str] = set()

    def walk(node: str) -> int:
        if node in memo:
            return memo[node]
        on_path.add(node)
        best = 0
        for nxt in succ.get(node, []):
            if nxt in skip or nxt in on_path:
                continue
            best = max(best, walk(nxt))
        on_path.discard(node)
        memo[node] = best + 1
        return memo[node]

    return walk(start) if start not in skip else 0


def flow_progress(flow: Dict[str, Any], events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """``{done, total, current: [{node_id, label, agent_id}]}`` from a run's
    log events (flow/run_store.read_run_group_events)."""
    succ = _graph(flow)
    labels = {str(n.get("id")): (n.get("data") or {}).get("label") or str(n.get("id"))
              for n in flow.get("nodes") or [] if n.get("id")}
    done: List[str] = []
    skipped: Set[str] = set()
    started: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        node_id = str(ev.get("node_id") or "")
        if not node_id:
            continue
        kind = ev.get("type")
        if kind == "agent_start":
            started[node_id] = ev
        elif kind in _DONE_TYPES:
            started.pop(node_id, None)
            if node_id not in done:
                done.append(node_id)
        elif kind == "node_skip":
            skipped.add(node_id)
    passed = set(done) | skipped
    current = [n for n in started if n not in passed]
    frontier = current or sorted({nxt for n in done for nxt in succ.get(n, []) if nxt not in passed})
    if not done and not current and not frontier:
        # Nothing has run yet: the way from the flow's start.
        targets = {t for outs in succ.values() for t in outs}
        frontier = [n for n in succ if n not in targets]
    remaining = max((longest_path(succ, n, passed) for n in frontier), default=0)
    return {
        "done": len(done),
        "total": len(done) + remaining,
        "current": [{"node_id": n, "label": labels.get(n, n),
                     "agent_id": started[n].get("agent_id") or started[n].get("agent_name")}
                    for n in current],
    }


def run_progress(flow_id: str, flow_run_id: str, flow: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """:func:`flow_progress` of a stored run, or None when the flow is gone."""
    from flow import run_store
    if flow is None:
        from flow import store as flow_store
        try:
            flow = flow_store.get_flow(flow_id)
        except Exception:  # noqa: BLE001 - a broken flow has no progress to show
            flow = None
    if not flow:
        return None
    return flow_progress(flow, run_store.read_run_group_events(flow_id, flow_run_id))


__all__ = ["flow_progress", "longest_path", "run_progress"]
