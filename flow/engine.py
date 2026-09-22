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

Independent nodes run concurrently: the engine starts every node whose
predecessors have finished, bounded by the flow's ``max_parallel``, and merges
their events into one ordered stream. What a failed node does to the rest of
the run is the flow's ``on_error`` policy; each node may declare its own
``retry`` and ``timeout_seconds``. After every node the engine offers the
driver a checkpoint, which is all a later run needs to resume where this one
stopped.

The engine yields *neutral* lifecycle events (``flow_start`` / ``node_skip`` /
``node_start`` / ``node_event`` / ``node_retry`` / ``node_done`` /
``flow_finish``). Each adapter
translates those into its own event shapes (SSE for chat, flow-log/run-records
for task) — so external formats consumed by the frontend and the dashboard are
unchanged; only the control flow that produces them is centralized here.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ── Flow-level and per-node policy ───────────────────────────────────────────

#: How many nodes the engine may run at once when the flow does not say.
DEFAULT_MAX_PARALLEL = 4

#: What a failing node does to the rest of the run.
#:   ``fail_fast``      — the default. Nodes already running are left to finish;
#:                        nothing else starts, and the run fails.
#:   ``continue``       — every node with at least one successful predecessor
#:                        still runs (a join tolerates one failed input); the
#:                        run is marked failed at the end.
#:   ``isolate_branch`` — every descendant of the failed node is skipped, even
#:                        one another branch also reaches; unrelated branches
#:                        run to completion.
ON_ERROR_POLICIES = ("fail_fast", "continue", "isolate_branch")
DEFAULT_ON_ERROR = "fail_fast"

#: How often the engine calls ``driver.on_heartbeat`` while anything is running.
#: Well under the watchdog's staleness threshold, so a long agent node keeps
#: proving it is alive without waiting for its node to finish.
HEARTBEAT_SECONDS = 15.0


def flow_max_parallel(flow: Dict[str, Any]) -> int:
    """The flow's concurrency bound (``max_parallel``), clamped to >= 1."""
    try:
        value = int(flow.get("max_parallel") or DEFAULT_MAX_PARALLEL)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_PARALLEL
    return max(1, value)


def flow_on_error(flow: Dict[str, Any]) -> str:
    """The flow's error policy (``on_error``), defaulting to fail_fast."""
    value = str(flow.get("on_error") or DEFAULT_ON_ERROR).strip()
    return value if value in ON_ERROR_POLICIES else DEFAULT_ON_ERROR


def node_retry(node: Dict[str, Any]) -> tuple[int, float]:
    """``retry: {max, backoff_seconds}`` for one node, as (max, backoff)."""
    raw = _node_value(node, "retry") or {}
    if not isinstance(raw, dict):
        return 0, 0.0
    try:
        max_retries = max(0, int(raw.get("max") or 0))
    except (TypeError, ValueError):
        max_retries = 0
    try:
        backoff = max(0.0, float(raw.get("backoff_seconds") or 0.0))
    except (TypeError, ValueError):
        backoff = 0.0
    return max_retries, backoff


def node_timeout(node: Dict[str, Any]) -> Optional[float]:
    """``timeout_seconds`` for one node, or None when it may take as long as it
    takes. Zero and negative values mean no timeout."""
    raw = _node_value(node, "timeout_seconds")
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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
    # ── optional callbacks (added with parallel execution; all default to
    # no-ops so an adapter that predates them keeps working unchanged) ──
    #: Persist the run's resume point. Called after every node finishes with
    #: the checkpoint dict described in :func:`run_flow_engine`.
    on_checkpoint: Callable[[dict], None] = lambda cp: None
    #: "Still alive" tick, called every :data:`HEARTBEAT_SECONDS` while nodes
    #: are running, so a run whose process dies is distinguishable from one
    #: sitting inside a long agent call.
    on_heartbeat: Callable[[], None] = lambda: None
    #: Ask the driver to stop an agent node that has hit its ``timeout_seconds``
    #: (kill the run record, cancel the invocation — whatever it can offer).
    #: Receives (node, node_id, outcome).
    stop_agent_node: Callable[..., None] = lambda node, node_id, outcome: None
    #: True when this surface can park the run and come back later, which is
    #: what a ``human_interrupt`` node needs. The task surface sets it; chat
    #: leaves it False and the interrupt node simply answers with its question.
    supports_interrupt: bool = False


# ── Engine ──────────────────────────────────────────────────────────────────

async def run_flow_engine(
    flow: Dict[str, Any],
    *,
    flow_id: str,
    shared_context: str,
    driver: FlowEngineDriver,
    seed_state: Optional[Dict[str, Any]] = None,
    resume: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[dict, None]:
    """Drive a flow DAG once, yielding neutral lifecycle events.

    Preflight (``validate_flow`` + ``resolve_entities``) runs first and raises
    ``flow.validate.FlowValidationError`` on failure — the caller catches it and
    translates to its own error surface.

    **Scheduling.** Nodes are not walked one at a time: a node becomes *ready*
    once every predecessor has finished (done, failed or skipped), and every
    ready node is started at once, up to the flow's ``max_parallel`` (default
    :data:`DEFAULT_MAX_PARALLEL`). Each running node is its own ``asyncio``
    task; agent nodes stream through ``driver.run_agent_node`` and entity nodes
    run on a worker thread (``asyncio.to_thread``), and both push their events
    into one queue. The engine yields that queue, so callers still see a single
    ordered event stream — ordering *within* a node is preserved, while two
    parallel nodes' events interleave, which is what actually happened.

    **Failure.** ``on_error`` decides what a failed node does to the rest of the
    run; see :data:`ON_ERROR_POLICIES`. Per node, ``retry: {max,
    backoff_seconds}`` re-runs a failed attempt and ``timeout_seconds`` bounds
    each attempt (a timed-out agent node is stopped through
    ``driver.stop_agent_node`` and counts as failed). The outcome contract is
    unchanged: ``any_failure`` is true when any node ended not-ok, and the
    ``flow_finish`` event reports ``ok = not any_failure``.

    **Checkpoint / resume.** After every node finishes, ``driver.on_checkpoint``
    receives ``{"state", "done", "skipped", "updated_at"}`` — enough to restart
    the run where it stopped. Passing that dict back as ``resume`` replays the
    finished nodes (their outputs into ``node_outputs`` and their values into
    ``FlowState``) and continues with what is left.

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
    order_set = set(order)
    nodes_by_id: Dict[str, Dict[str, Any]] = {
        n.get("id"): n for n in nodes if n.get("id")
    }
    entry_point = flow.get("entry_point") or (order[0] if order else None)
    max_parallel = flow_max_parallel(flow)
    on_error = flow_on_error(flow)

    # Scaffold the node list once so adapters can build their flow-meta.
    meta_nodes: List[dict] = []
    for nid in order:
        node = nodes_by_id.get(nid)
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
        "max_parallel": max_parallel,
        "on_error": on_error,
        # Initial shared state (declared defaults + seed) so the dashboard can
        # show the flow's starting state before any node has run.
        "state": flow_state.snapshot(),
    }
    driver.on_flow_start(flow_start_ev)
    yield flow_start_ev

    node_outputs: Dict[str, str] = {}
    done_nodes: set = set()          # finished ok
    failed_nodes: set = set()        # finished not-ok
    skipped_nodes: set = set()       # never executed
    marked_skip: Dict[str, str] = {}  # node_id -> reason, decided before it was ready
    any_failure = False
    stopped = False
    aborting = False                 # no new node may start
    interrupt: Optional[Dict[str, Any]] = None
    checkpoint: Dict[str, Any] = {}

    def _finished(nid: str) -> bool:
        return nid in done_nodes or nid in failed_nodes or nid in skipped_nodes

    def _live_preds(nid: str) -> List[str]:
        return [p for p in predecessors.get(nid, []) if p in order_set]

    def _snapshot_checkpoint() -> Dict[str, Any]:
        """The run's resume point, as of right now."""
        return {
            "state": flow_state.snapshot(),
            "done": [
                {"node_id": nid, "output": node_outputs.get(nid, ""),
                 "ok": nid not in failed_nodes}
                for nid in order
                if nid in done_nodes or nid in failed_nodes
            ],
            "skipped": [nid for nid in order if nid in skipped_nodes],
            "updated_at": _utc_now_iso(),
        }

    def _emit_skip(nid: str, reason: str) -> dict:
        skipped_nodes.add(nid)
        marked_skip.pop(nid, None)
        ev = {"type": "node_skip", "node_id": nid, "reason": reason}
        driver.on_node_skip(ev)
        return ev

    # ── Replay a checkpoint ─────────────────────────────────────────────────
    replay_events: List[dict] = []
    if resume:
        flow_state.merge(resume.get("state"))
        for rec in resume.get("done") or []:
            nid = str((rec or {}).get("node_id") or "")
            if nid not in order_set:
                continue
            if bool(rec.get("ok", True)):
                done_nodes.add(nid)
                node_outputs[nid] = rec.get("output") or ""
            else:
                failed_nodes.add(nid)
                any_failure = True
            ev = {"type": "node_skip", "node_id": nid, "reason": "already_done"}
            driver.on_node_skip(ev)
            replay_events.append(ev)
        for nid in resume.get("skipped") or []:
            if nid in order_set and not _finished(str(nid)):
                replay_events.append(_emit_skip(str(nid), "already_skipped"))
        checkpoint = _snapshot_checkpoint()
    for ev in replay_events:
        yield ev

    # ── Node execution ──────────────────────────────────────────────────────
    queue: "asyncio.Queue[tuple]" = asyncio.Queue()
    running: Dict[str, asyncio.Task] = {}

    async def _run_agent_attempt(node, node_id, label, prompt, outcome) -> None:
        """One attempt at an agent node: forward its events, fill ``outcome``."""
        async for raw in driver.run_agent_node(node, node_id, label, prompt, outcome, flow_state):
            await queue.put(("event", {"type": "node_event", "node_id": node_id, "event": raw}))

    async def _worker(node_id: str, node: Dict[str, Any]) -> None:
        """Run one node to its conclusion and post its result to the queue.

        Everything a node decides — its prompt, its retries, its state writes —
        happens here, so two nodes running at once never interleave inside a
        single node's own sequence.
        """
        entity = flow_dispatch.FlowEntity.for_node(node)
        spec = entity.spec if entity else None
        is_agent = bool(entity and entity.runs_in_process)
        label = _node_value(node, "label") or (spec.name if spec else node_id)
        category = (spec.category if spec else None) or (_node_value(node, "domain") or "flow")
        max_retries, backoff = node_retry(node)
        timeout = node_timeout(node)

        prompt = None
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
            "is_agent": is_agent,
            "prompt": prompt,
        }
        driver.on_node_start(start_ev)
        await queue.put(("event", start_ev))

        dr: Any = None
        node_stopped = False
        for attempt in range(max_retries + 1):
            if is_agent:
                outcome = AgentNodeOutcome()
                try:
                    coro = _run_agent_attempt(node, node_id, label, prompt, outcome)
                    if timeout:
                        await asyncio.wait_for(coro, timeout)
                    else:
                        await coro
                except asyncio.TimeoutError:
                    # The driver owns whatever "stop" means for its surface
                    # (close the run record, kill the invocation); the engine
                    # only insists that the node counts as failed.
                    try:
                        driver.stop_agent_node(node, node_id, outcome)
                    except Exception as e:  # noqa: BLE001
                        print(f"[flow_engine] stop_agent_node failed for {node_id}: {e}")
                    outcome.ok = False
                    outcome.error = f"node timed out after {timeout:g}s"
                except Exception as e:  # noqa: BLE001 - a node body error fails the node
                    outcome.ok = False
                    outcome.error = f"{type(e).__name__}: {e}"

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
                    output=outcome.output, run_id=outcome.run_id,
                    duration_ms=outcome.duration_ms,
                )
                node_stopped = outcome.stopped
            elif entity is None:
                dr = DispatchResult(ok=False, error="node references no known entity or agent")
            else:
                ctx = driver.make_run_context(node_id)
                try:
                    # Entity callables are sync and CPU-bound; a thread keeps a
                    # slow one from stalling the branches running beside it.
                    call = asyncio.to_thread(entity.run, node, flow_state, ctx)
                    dr = await (asyncio.wait_for(call, timeout) if timeout else call)
                except asyncio.TimeoutError:
                    # to_thread cannot be interrupted: the thread runs on to its
                    # own end, but the node is failed here and now.
                    dr = DispatchResult(ok=False, error=f"node timed out after {timeout:g}s")
                except Exception as e:  # noqa: BLE001
                    dr = DispatchResult(ok=False, error=f"{type(e).__name__}: {e}")

            if dr.ok or node_stopped or attempt >= max_retries:
                break
            if backoff:
                await asyncio.sleep(backoff)
            await queue.put(("event", {
                "type": "node_retry", "node_id": node_id, "label": label,
                "attempt": attempt + 2, "of": max_retries + 1, "error": dr.error,
            }))

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
            "attempts": attempt + 1,
            # Full shared-state snapshot after this node's writes were applied,
            # so the dashboard can show runtime state values as the run unfolds.
            "state": flow_state.snapshot(),
        }
        await queue.put(("finished", node_id, dr, node_stopped, done_ev))

    async def _guarded_worker(node_id: str, node: Dict[str, Any]) -> None:
        """Run a node's worker, and make sure it always reports back.

        A worker that died without posting its result would leave the engine
        waiting on a queue nothing will ever fill, so the whole run would hang
        on one unexpected exception. It fails the node instead.
        """
        try:
            await _worker(node_id, node)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            dr = DispatchResult(ok=False, error=f"{type(e).__name__}: {e}")
            await queue.put(("finished", node_id, dr, False, {
                "type": "node_done", "node_id": node_id, "label": node_id,
                "is_agent": False, "ok": False, "output": "", "text": "",
                "written": [], "goto": None, "error": dr.error, "run_id": "",
                "duration_ms": 0, "stopped": False, "attempts": 1,
                "state": flow_state.snapshot(),
            }))

    def _spawn(node_id: str, node: Dict[str, Any]) -> None:
        running[node_id] = asyncio.create_task(
            _guarded_worker(node_id, node), name=f"flow-node-{node_id}"
        )

    async def _heartbeat() -> None:
        """Prove the run is alive while a single node takes a long time."""
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                driver.on_heartbeat()
            except Exception as e:  # noqa: BLE001 - a heartbeat never fails a run
                print(f"[flow_engine] heartbeat failed: {e}")

    heartbeat_task = asyncio.create_task(_heartbeat(), name="flow-heartbeat")

    try:
        while True:
            # ── schedule every ready node, up to max_parallel ───────────────
            pending: List[dict] = []
            if not aborting and driver.should_stop():
                # Cooperative stop, checked at the same boundary the sequential
                # engine checked it: before any further node starts.
                stopped = True
                aborting = True
            if not aborting:
                for nid in order:
                    if len(running) >= max_parallel:
                        break
                    if _finished(nid) or nid in running:
                        continue
                    if any(not _finished(p) for p in _live_preds(nid)):
                        continue
                    node = nodes_by_id.get(nid)
                    if node is None:
                        skipped_nodes.add(nid)
                        continue
                    reason = marked_skip.get(nid)
                    if reason is None:
                        preds = _live_preds(nid)
                        if preds and not any(p in done_nodes for p in preds):
                            reason = ("predecessor_failed"
                                      if any(p in failed_nodes for p in preds)
                                      else "branch_not_selected")
                    if reason is not None:
                        pending.append(_emit_skip(nid, reason))
                        continue
                    _spawn(nid, node)
            for ev in pending:
                yield ev

            if not running:
                if aborting or all(_finished(nid) for nid in order):
                    break
                # Nothing running and nothing ready: the rest is unreachable.
                if not any(
                    not _finished(nid) and all(_finished(p) for p in _live_preds(nid))
                    for nid in order
                ):
                    break
                continue

            kind, *payload = await queue.get()
            if kind == "event":
                yield payload[0]
                continue

            node_id, dr, node_stopped, done_ev = payload
            task = running.pop(node_id, None)
            if task is not None:
                # Surface a worker crash rather than swallowing it silently.
                exc = task.exception() if task.done() else None
                if exc:
                    print(f"[flow_engine] node {node_id} worker error: {exc}")

            if dr.run_id:
                node_outputs.setdefault(node_id, "")  # keep key order stable

            if node_stopped:
                failed_nodes.add(node_id)
                any_failure = True
                stopped = True
                aborting = True
            elif dr.ok:
                done_nodes.add(node_id)
                node_outputs[node_id] = dr.output or dr.text
                if dr.goto is not None:
                    # Pruning decides which nodes are dead, but a node is only
                    # *announced* as skipped when the scheduler reaches it, so
                    # the decision is parked in marked_skip and the node_skip
                    # event is emitted in graph order like every other one.
                    pruned = set(skipped_nodes) | set(marked_skip)
                    before = set(pruned)
                    _prune_unselected_branches(
                        node_id, dr.goto, successors, predecessors,
                        pruned, done_nodes,
                    )
                    for nid in pruned - before:
                        marked_skip.setdefault(nid, "branch_not_selected")
                if getattr(dr, "interrupt", None) and driver.supports_interrupt:
                    # A human_interrupt node parks the whole run: nothing new
                    # starts, and this node is NOT recorded as done, so the
                    # resume re-runs it with the answer already in state.
                    interrupt = {**dr.interrupt, "node_id": node_id}
                    done_nodes.discard(node_id)
                    node_outputs.pop(node_id, None)
                    aborting = True
            else:
                failed_nodes.add(node_id)
                any_failure = True
                if on_error == "fail_fast":
                    aborting = True
                elif on_error == "isolate_branch":
                    frontier = deque(successors.get(node_id, []))
                    seen: set = set()
                    while frontier:
                        child = frontier.popleft()
                        if child in seen or child in order_set and _finished(child):
                            continue
                        seen.add(child)
                        marked_skip.setdefault(child, "branch_isolated")
                        frontier.extend(successors.get(child, []))

            checkpoint = _snapshot_checkpoint()
            try:
                driver.on_checkpoint(checkpoint)
            except Exception as e:  # noqa: BLE001 - a checkpoint never fails a run
                print(f"[flow_engine] checkpoint failed: {e}")

            driver.on_node_done(done_ev)
            yield done_ev

        # ── drain: nodes fail_fast cancelled before they ever started ───────
        if aborting and not stopped and interrupt is None:
            for nid in order:
                if _finished(nid) or nid in running:
                    continue
                yield _emit_skip(nid, "cancelled_after_failure")
            checkpoint = _snapshot_checkpoint()
    finally:
        heartbeat_task.cancel()
        for task in running.values():
            task.cancel()

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
        # Added with parallel execution / checkpointing; every pre-existing key
        # above keeps its meaning, so callers that ignore these are unaffected.
        "failed_nodes": [nid for nid in order if nid in failed_nodes],
        "skipped_nodes": [nid for nid in order if nid in skipped_nodes],
        "interrupt": interrupt,
        "checkpoint": checkpoint or _snapshot_checkpoint(),
        "on_error": on_error,
    }
    driver.on_flow_finish(finish_ev)
    yield finish_ev
