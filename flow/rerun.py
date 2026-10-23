"""
Run a flow again from one of its nodes.

Two ways in, both built on what the engine already does:

* **From a past run** (:func:`rerun_checkpoint`). The run's checkpoint holds
  every finished node's output and the shared state. A new checkpoint keeps
  what came before the chosen node and drops what is to be done again, so the
  engine (``resume=``) replays the kept nodes and runs the rest: the chosen
  node gets the inputs it got in that run. ``single`` drops only that node,
  its successors keep their old results and do not run; ``rest`` drops the
  node and everything downstream of it, which then runs to the end.
* **With a new input** (:func:`start_at`). The flow's ``entry_point`` is set
  to the node, so the engine runs only what is reachable from it (see
  ``flow.validate.execution_order``) and the node, having no predecessor in
  that run, takes the run's input the way a root node does.

A chat conversation has no flow-run record, so its latest checkpoint is kept
next to its log (``flow.run_store.save_chat_checkpoint``); an older record
without one is rebuilt from its log (:func:`checkpoint_from_events`).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

#: The two ways to run a past run again from a node.
RERUN_MODES = ("single", "rest")


class RerunError(ValueError):
    """The run cannot be run again from that node (the message says why)."""


def _node_ids(flow: Dict[str, Any]) -> List[str]:
    return [str(n.get("id")) for n in flow.get("nodes") or [] if n.get("id")]


def _node(flow: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    return next((n for n in flow.get("nodes") or [] if str(n.get("id")) == node_id), None)


def node_label(flow: Dict[str, Any], node_id: str) -> str:
    """The name a person sees for a node: its label, else its id."""
    node = _node(flow, node_id) or {}
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(data.get("label") or node.get("label") or node_id)


def downstream(flow: Dict[str, Any], node_id: str) -> Set[str]:
    """Every node reachable from ``node_id`` along the flow's edges, without it."""
    succ: Dict[str, List[str]] = {}
    for e in flow.get("edges") or []:
        s, t = e.get("source"), e.get("target")
        if s and t:
            succ.setdefault(str(s), []).append(str(t))
    seen: Set[str] = set()
    stack = list(succ.get(node_id, []))
    while stack:
        cur = stack.pop()
        if cur in seen or cur == node_id:
            continue
        seen.add(cur)
        stack.extend(succ.get(cur, []))
    return seen


def predecessors(flow: Dict[str, Any], node_id: str) -> List[str]:
    return [str(e.get("source")) for e in flow.get("edges") or []
            if str(e.get("target")) == node_id and e.get("source")]


def _output_keys(node: Optional[Dict[str, Any]]) -> Set[str]:
    if not node:
        return set()
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    keys: Set[str] = set()
    for field in ("output", "outputs"):
        value = node.get(field, data.get(field))
        if isinstance(value, str):
            value = [value]
        for k in value or []:
            if isinstance(k, str) and k.strip():
                keys.add(k.strip())
    return keys


def rerun_checkpoint(
    flow: Dict[str, Any],
    source: Dict[str, Any],
    node_id: str,
    mode: str,
) -> Dict[str, Any]:
    """The checkpoint that runs ``source`` again from ``node_id``.

    Every node not run again is either replayed as done (with its output from
    ``source``) or skipped, so nothing outside the chosen part starts: a node
    a branch passed over in that run stays passed over. State keys only the
    nodes run again write are dropped, so they start without the old values.
    """
    if mode not in RERUN_MODES:
        raise RerunError(f"Unknown mode '{mode}', expected one of {', '.join(RERUN_MODES)}")
    if _node(flow, node_id) is None:
        raise RerunError(f"Node '{node_id}' is not in this flow")
    done_src = [d for d in source.get("done") or [] if isinstance(d, dict) and d.get("node_id")]
    if not done_src:
        raise RerunError("That run has no finished nodes to start from")

    again = {node_id} | (downstream(flow, node_id) if mode == "rest" else set())
    finished_ok = {str(d["node_id"]) for d in done_src if d.get("ok", True)}
    # The node runs when all its predecessors are done (the engine's rule);
    # one that never ran in that run has nothing to start from.
    missing = [p for p in predecessors(flow, node_id) if p not in finished_ok]
    if missing:
        raise RerunError(
            f"Node '{node_id}' cannot run from that run: "
            f"{', '.join(missing)} did not finish there"
        )

    done = [d for d in done_src if str(d["node_id"]) not in again]
    kept = {str(d["node_id"]) for d in done}
    skipped = [nid for nid in _node_ids(flow) if nid not in again and nid not in kept]

    state = dict(source.get("state") or {})
    keep_keys: Set[str] = set()
    for nid in kept:
        keep_keys |= _output_keys(_node(flow, nid))
    for nid in again:
        for key in _output_keys(_node(flow, nid)) - keep_keys:
            state.pop(key, None)

    return {"state": state, "done": done, "skipped": skipped}


def start_at(flow: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    """A copy of ``flow`` that starts at ``node_id`` and runs to the end."""
    if _node(flow, node_id) is None:
        raise RerunError(f"Node '{node_id}' is not in this flow")
    return {**flow, "entry_point": node_id}


def checkpoint_from_events(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """A checkpoint rebuilt from one run's log events, for a record that
    has none stored: each node's last finished output and the last state
    snapshot the log carries."""
    outputs: Dict[str, Dict[str, Any]] = {}
    state: Dict[str, Any] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if isinstance(ev.get("state"), dict):
            state = ev["state"]
        nid = ev.get("node_id")
        if not nid:
            continue
        if ev.get("type") == "agent_finish":
            outputs[str(nid)] = {"node_id": str(nid), "output": ev.get("output") or "", "ok": True}
        elif ev.get("type") == "agent_error":
            outputs[str(nid)] = {"node_id": str(nid), "output": ev.get("output") or "", "ok": False}
    return {"state": state, "done": list(outputs.values()), "skipped": []}
