"""
Shared flow-execution engine.

A single async-generator engine drives a flow DAG once, for both surfaces that
run flows in-process:

  * ``runtime/flow_run.py`` — the subprocess "task" path (drives this with ``asyncio.run``)
  * ``dashboard/backend/routes/chat.py`` — the in-process "chat" SSE path

The engine owns everything that is identical between the two: preflight
(validate + entity resolution), execution ordering, predecessor/successor
wiring, condition-node branch pruning, ``FlowState``, and the node loop with
agent/entity dispatch. Everything that genuinely differs — how an agent node is
executed (sync lifecycle vs streaming ``arun``), how run records and logs are
written, where events are published, how a stop is detected — is supplied by a
caller-provided :class:`FlowEngineDriver`.

The engine yields *neutral* lifecycle events (``flow_start`` / ``node_skip`` /
``node_start`` / ``node_event`` / ``node_done`` / ``flow_finish``). Each adapter
translates those into its own event shapes (SSE for chat, flow-log/run-records
for task) — so external formats consumed by the frontend and the dashboard are
unchanged; only the control flow that produces them is centralized here.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional


# ── DAG helpers (moved here from runtime/flow_run.py so both adapters share them) ───────

def _node_value(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    return node[key] if key in node else data.get(key, default)


def _build_predecessors(edges: List[Dict]) -> Dict[str, List[str]]:
    preds: Dict[str, List[str]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            preds.setdefault(t, []).append(s)
    return preds


def _build_successors(edges: List[Dict]) -> Dict[str, List[str]]:
    succ: Dict[str, List[str]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            succ.setdefault(s, []).append(t)
    return succ


def _prune_unselected_branches(
    cond_node_id: str,
    selected: List[str],
    successors: Dict[str, List[str]],
    predecessors: Dict[str, List[str]],
    skipped: set,
    executed: set,
) -> None:
    """After a condition node picks ``selected`` targets, mark the subtrees of
    its *unselected* successors as skipped — but only nodes that are no longer
    reachable from any non-skipped, non-condition path.

    A pruned node is rescued if it still has a predecessor that is either
    already executed or not skipped (i.e. another live branch reaches it).
    """
    selected_set = {s for s in selected if s}
    unselected = [t for t in successors.get(cond_node_id, []) if t not in selected_set]

    frontier = deque(unselected)
    seen: set = set()
    while frontier:
        nid = frontier.popleft()
        if nid in seen or nid in executed or nid in selected_set:
            continue
        seen.add(nid)
        live_parent = any(
            (p not in skipped and p != cond_node_id) or p in executed
            for p in predecessors.get(nid, [])
            if p != cond_node_id
        )
        if live_parent:
            continue
        skipped.add(nid)
        for child in successors.get(nid, []):
            frontier.append(child)


def build_agent_input(
    shared_prompt: str,
    node: Dict[str, Any],
    node_id: str,
    predecessors: Dict[str, List[str]],
    node_outputs: Dict[str, str],
    flow_state: Any,
    node_task: str = "",
) -> str:
    """Build a node's prompt: declared input-state slice + predecessor outputs +
    node task. Moved from ``runtime/flow_run.py`` so both adapters share one builder."""
    pred_ids = predecessors.get(node_id, [])
    pred_outputs = [(pid, node_outputs[pid]) for pid in pred_ids if pid in node_outputs]

    parts: List[str] = []

    in_keys = _node_value(node, "input") or _node_value(node, "inputs") or []
    state_slice = flow_state.slice(in_keys) if in_keys else None
    if state_slice:
        block = "\n".join(f"[{k}]:\n{v}" for k, v in state_slice.items())
        parts += ["=" * 60, "FLOW STATE", "=" * 60, block, "=" * 60, ""]

    if pred_outputs:
        parts += [
            "=" * 60,
            "OUTPUT FROM PREVIOUS AGENTS IN THIS FLOW",
            "=" * 60,
        ]
        for pred_id, pred_out in pred_outputs:
            parts.append(f"\n[{pred_id}]:\n{pred_out}\n")
        parts += ["=" * 60, "\nContinue the work based on the above output."]
    else:
        parts.append(shared_prompt)

    if node_task:
        parts += ["", "## Your specific task for this step:", node_task]

    return "\n".join(parts)


# ── Driver contract ─────────────────────────────────────────────────────────

@dataclass
class AgentNodeOutcome:
    """Filled by :attr:`FlowEngineDriver.run_agent_node` once a node finishes.

    Mirrors the fields of ``flow.dispatch.DispatchResult`` that the engine needs,
    plus ``stopped`` so the loop can short-circuit on a user stop. The driver
    owns building the per-node ``DispatchResult``-equivalent (state.apply,
    run-record close, logging); the engine only reads these fields.
    """
    ok: bool = False
    output: str = ""
    text: str = ""
    written: Dict[str, Any] = field(default_factory=dict)
    goto: Optional[List[str]] = None
    error: str = ""
    run_id: str = ""
    duration_ms: int = 0
    stopped: bool = False


@dataclass
class FlowEngineDriver:
    """Caller-supplied behaviour the engine delegates the divergent edges to.

    The chat and task adapters each build one of these. Everything here is the
    part that genuinely differs between the two surfaces; the shared DAG walk
    lives in :func:`run_flow_engine`.
    """
    #: Build the per-node prompt. Receives the engine's shared context, the node,
    #: predecessors map, accumulated node outputs, the live FlowState, and the
    #: node's declared task string. Chat wraps this to add history/attachments.
    build_agent_prompt: Callable[
        [str, Dict[str, Any], str, Dict[str, List[str]], Dict[str, str], Any, str], str
    ]
    #: Execute one agent node. An async generator that yields raw agent events
    #: (chat: callback-stream dicts; task: nothing) and fills ``outcome`` before
    #: it returns. Receives (node, node_id, label, prompt, outcome, flow_state) —
    #: ``flow_state`` is the engine's authoritative FlowState so a node that
    #: declares output keys writes them where successors read them.
    run_agent_node: Callable[..., AsyncGenerator[dict, None]]
    #: Build a ``flow.state.RunContext`` for an entity (non-agent) node.
    make_run_context: Callable[[str], Any]
    #: Cooperative between-node stop check (task reads its flow-run record;
    #: chat returns False and relies on per-node polling inside run_agent_node).
    should_stop: Callable[[], bool] = lambda: False
    #: Sync, cheap lifecycle hooks for logging / run records / flow-log store.
    on_flow_start: Callable[[dict], None] = lambda ev: None
    on_node_start: Callable[[dict], None] = lambda ev: None
    on_node_done: Callable[[dict], None] = lambda ev: None
    on_node_skip: Callable[[dict], None] = lambda ev: None
    on_flow_finish: Callable[[dict], None] = lambda ev: None


# ── Engine ──────────────────────────────────────────────────────────────────

async def run_flow_engine(
    flow: Dict[str, Any],
    *,
    flow_id: str,
    shared_context: str,
    driver: FlowEngineDriver,
    seed_state: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[dict, None]:
    """Drive a flow DAG once, yielding neutral lifecycle events.

    Preflight (``validate_flow`` + ``resolve_entities``) runs first and raises
    ``flow.validate.FlowValidationError`` on failure — the caller catches it and
    translates to its own error surface. Then nodes run in ``execution_order``
    (entry_point aware), with condition-node branch pruning, FlowState, and
    agent/entity dispatch. Agent nodes are executed via ``driver.run_agent_node``
    (whose yielded events are re-emitted as ``node_event``); entity nodes run
    synchronously via ``FlowEntity.run`` (fast CPU-bound callables).

    ``flow_id`` is the canonical id the caller loaded the flow under and is the
    single source of truth for the ``flow_start`` event — the engine does not
    read identity from the (possibly partial) ``flow`` dict.
    """
    # Local imports: keep engine import-light and avoid load-time circulars.
    from flow.validate import validate_flow, resolve_entities, execution_order
    from flow.state import FlowState, StateMutationError
    from flow import dispatch as flow_dispatch
    from flow.dispatch import DispatchResult

    nodes: List[Dict] = flow.get("nodes", []) or []
    edges: List[Dict] = flow.get("edges", []) or []

    # Preflight (raises FlowValidationError; caller translates).
    validate_flow(flow)
    resolve_entities(nodes)

    predecessors = _build_predecessors(edges)
    successors = _build_successors(edges)

    seed = {"user_request": shared_context}
    if seed_state:
        seed.update(seed_state)
    flow_state = FlowState.from_flow(flow, seed=seed)

    order = execution_order(flow)
    entry_point = flow.get("entry_point") or (order[0] if order else None)

    # Scaffold the node list once so adapters can build their flow-meta.
    meta_nodes: List[dict] = []
    for nid in order:
        node = next((n for n in nodes if n.get("id") == nid), None)
        if not node:
            continue
        entity = flow_dispatch.FlowEntity.for_node(node)
        spec = entity.spec if entity else None
        label = _node_value(node, "label") or (spec.name if spec else nid)
        category = (spec.category if spec else None) or (_node_value(node, "domain") or "flow")
        meta_nodes.append({
            "node_id": nid,
            "entity_id": (spec.id if spec else _node_value(node, "agent_id")),
            "label": label,
            "category": category,
            "is_agent": bool(entity and entity.runs_in_process),
        })

    flow_start_ev = {
        "type": "flow_start",
        "flow_id": flow_id,
        "flow_name": flow.get("name"),
        "entry_point": entry_point,
        "nodes": meta_nodes,
        # Initial shared state (declared defaults + seed) so the dashboard can
        # show the flow's starting state before any node has run.
        "state": flow_state.snapshot(),
    }
    driver.on_flow_start(flow_start_ev)
    yield flow_start_ev

    node_outputs: Dict[str, str] = {}
    skipped_nodes: set = set()
    executed_nodes: set = set()
    any_failure = False
    stopped = False

    for node_id in order:
        # Cooperative stop between nodes.
        if driver.should_stop():
            stopped = True
            break

        node = next((n for n in nodes if n.get("id") == node_id), None)
        if not node:
            continue

        if node_id in skipped_nodes:
            skip_ev = {
                "type": "node_skip",
                "node_id": node_id,
                "reason": "branch_not_selected",
            }
            driver.on_node_skip(skip_ev)
            yield skip_ev
            continue

        entity = flow_dispatch.FlowEntity.for_node(node)
        spec = entity.spec if entity else None
        is_agent = bool(entity and entity.runs_in_process)
        label = _node_value(node, "label") or (spec.name if spec else node_id)
        category = (spec.category if spec else None) or (_node_value(node, "domain") or "flow")

        if is_agent:
            node_task = _node_value(node, "nodeTask") or ""
            prompt = driver.build_agent_prompt(
                shared_context, node, node_id, predecessors, node_outputs,
                flow_state, node_task,
            )
            start_ev = {
                "type": "node_start",
                "node_id": node_id,
                "entity_id": (spec.id if spec else _node_value(node, "agent_id")),
                "label": label,
                "category": category,
                "is_agent": True,
                "prompt": prompt,
            }
            driver.on_node_start(start_ev)
            yield start_ev

            outcome = AgentNodeOutcome()
            async for raw in driver.run_agent_node(node, node_id, label, prompt, outcome, flow_state):
                yield {"type": "node_event", "node_id": node_id, "event": raw}

            # Agent node may declare output state keys; write them now so the
            # successor's prompt (build_agent_input) can read the slice.
            written: Dict[str, Any] = dict(outcome.written or {})
            if outcome.ok:
                out_keys = _node_value(node, "output") or _node_value(node, "outputs") or []
                if out_keys and not written:
                    try:
                        written = flow_state.apply(list(out_keys), outcome.output)
                    except StateMutationError as sme:
                        outcome.ok = False
                        outcome.error = str(sme)
            dr = DispatchResult(
                ok=outcome.ok, text=outcome.text or (f"Completed {label}" if outcome.ok else ""),
                written=written, goto=outcome.goto, error=outcome.error,
                output=outcome.output, run_id=outcome.run_id, duration_ms=outcome.duration_ms,
            )
            node_stopped = outcome.stopped
        else:
            # Entity (non-agent) node: emit start, run synchronously, no stream.
            start_ev = {
                "type": "node_start",
                "node_id": node_id,
                "entity_id": (spec.id if spec else None),
                "label": label,
                "category": category,
                "is_agent": False,
                "prompt": None,
            }
            driver.on_node_start(start_ev)
            yield start_ev
            if entity is None:
                dr = DispatchResult(ok=False, error="node references no known entity or agent")
            else:
                ctx = driver.make_run_context(node_id)
                dr = entity.run(node, flow_state, ctx)
            node_stopped = False

        if dr.run_id:
            node_outputs.setdefault(node_id, "")  # ensure key order is stable

        done_ev = {
            "type": "node_done",
            "node_id": node_id,
            "entity_id": (spec.id if spec else _node_value(node, "agent_id")),
            "label": label,
            "category": category,
            "is_agent": is_agent,
            "ok": dr.ok,
            "output": dr.output,
            "text": dr.text,
            "written": list(dr.written or {}),
            "goto": dr.goto,
            "error": dr.error,
            "run_id": dr.run_id,
            "duration_ms": dr.duration_ms,
            "stopped": node_stopped,
            # Full shared-state snapshot after this node's writes were applied,
            # so the dashboard can show runtime state values as the run unfolds.
            "state": flow_state.snapshot(),
        }

        if node_stopped:
            any_failure = True
            stopped = True
            driver.on_node_done(done_ev)
            yield done_ev
            break

        if dr.ok:
            executed_nodes.add(node_id)
            node_outputs[node_id] = dr.output or dr.text
            if dr.goto is not None:
                _prune_unselected_branches(
                    node_id, dr.goto, successors, predecessors,
                    skipped_nodes, executed_nodes,
                )
        else:
            any_failure = True

        driver.on_node_done(done_ev)
        yield done_ev

    combined_output = "\n\n".join(
        f"### [{nid}]\n{out}" for nid, out in node_outputs.items() if out
    )
    finish_ev = {
        "type": "flow_finish",
        "ok": not any_failure,
        "any_failure": any_failure,
        "stopped": stopped,
        "combined_output": combined_output,
        "node_outputs": node_outputs,
    }
    driver.on_flow_finish(finish_ev)
    yield finish_ev
