"""
Task-surface driver for the shared flow engine.

Builds the :class:`flow.engine.FlowEngineDriver` used by ``runtime/flow_run.py``'s
subprocess flow runs: per-node run records, node log files, ``SessionPublishCallback``
HTTP streaming, task execution-log entries, ``kind="task"`` flow-log events, and
the between-node stop check against the flow-run record.

The engine owns the DAG walk; everything here is the task-specific I/O it
delegates to. ``build_task_driver`` captures the per-run context (argparse args,
ids, the loaded flow) in closures and returns a configured driver.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.callbacks import (
    NodeFileCallback as _NodeFileCallback,
    SessionPublishCallback as _SessionPublishCallback,
)
from agents.agent_lifecycle import run_agent_lifecycle
from managers.run_manager import open_run, close_run, update_run, run_log_path
from flow import run_store
from flow.engine import (
    _node_value,
    build_agent_input as _build_agent_input,
    FlowEngineDriver,
)
from flow.state import RunContext, StateMutationError
from flow.dispatch import DispatchResult


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_flow(flow_id, run_id, payload: Dict[str, Any]) -> None:
    """Append a ``kind="task"`` flow-log event. Each flow run writes to its own
    file (flow_logs/<flow_id>/<run_id>.json), tagged with run_group=run_id so the
    dashboard History tab groups events by run. Shared tagging lives in run_store."""
    run_store.log_flow_event(flow_id, run_id, payload, kind="task")


def _resolve_agent_id(node: Dict[str, Any]) -> Optional[str]:
    """The agent a node runs, taken from its explicit ``agent_id``.

    Nodes must carry a real agent id; there is no label/alias translation.
    """
    return _node_value(node, "agent_id") or None


def _close_node_run(
    task_id: str,
    run_id: str,
    agent_id: str,
    *,
    status: str,
    exit_code: int,
    error: str = "",
    output: Optional[str] = None,
    process: Any = None,
) -> None:
    """Close a node's run record (status/exit_code/output/process/error).

    The task's run history is derived from the session (message_ids → runs), so
    the run record is the single source of truth — there is no separate sidecar.
    """
    close_kw: Dict[str, Any] = {"status": status, "exit_code": exit_code}
    if error:
        close_kw["error"] = error
    if output is not None:
        close_kw["output"] = output
    if process is not None:
        close_kw["process"] = process
    close_run(run_id, **close_kw)


def _run_agent_node(
    node: Dict[str, Any],
    node_id: str,
    prompt: str,
    *,
    args: argparse.Namespace,
    flow_state: Any,
    agent_overrides: Dict[str, Any],
    port: int,
    outcome: Any = None,
) -> Any:
    """Execute an agent node in-process: open a run record, run the LLM agent
    with callbacks against the prebuilt ``prompt``, write its output to flow
    state, and close the record. Returns the same DispatchResult shape the
    dispatcher produced, so the caller handles agent and entity nodes uniformly.
    The prompt is built by the engine driver (flow.engine.build_agent_input)."""
    from uuid import uuid4

    agent_id = _resolve_agent_id(node)
    if not agent_id:
        return DispatchResult(ok=False, error="node has no agent assigned")
    label = _node_value(node, "label") or agent_id

    run_id = str(uuid4())
    # Publish the run id on the outcome as soon as it exists: a node that hits
    # its timeout is stopped from the engine's thread, and closing its run
    # record is the only stop this surface can offer (the LLM call itself runs
    # in a worker thread that cannot be interrupted).
    if outcome is not None:
        outcome.run_id = run_id
    log_path = run_log_path(run_id)

    # One instance per node of this flow run — re-executions of the same node
    # (retry, loop-back) land on the same copy rather than multiplying rows.
    instance_id = None
    try:
        from instances import registry as instance_registry
        instance_id = instance_registry.ensure_instance(
            agent_id,
            instance_id=instance_registry.deterministic_id(args.run_id, node_id),
            kind="flow_node",
            session_id=args.session_id,
            task_id=args.task_id,
            label=f"{agent_id} · {label}",
            state="active",
            pid=os.getpid(),
        )["instance_id"]
    except Exception:
        pass

    open_run(
        run_id, agent_id, pid=os.getpid(),
        task_id=args.task_id, session_id=args.session_id, session_type="task",
        channel="flow",
        log_file=str(log_path), flow_id=args.flow_id, flow_run_id=args.run_id,
        flow_node_id=node_id, flow_node_label=label, link_to_session=True,
        instance_id=instance_id,
    )

    try:
        update_run(run_id, {"input": prompt})
    except Exception:
        pass

    with open(log_path, "w", encoding="utf-8") as _f:
        _f.write(f"--- Node run started at {_utc_now_iso()} ---\nAgent: {agent_id}\n\n=== EXECUTION ===\n")
    file_cb = _NodeFileCallback(log_path)
    pub_cb = _SessionPublishCallback(args.session_id, run_id, agent_id, port)
    pub_cb._post({"type": "meta", "run_id": run_id, "session_id": args.session_id,
                  "agent_id": agent_id, "continuation": True})

    def _finalize(duration_ms: int, *, output: str = "", error: str = ""):
        if error:
            file_cb._w(f"\n=== ERROR ===\n{error}\n--- duration_ms={duration_ms} ---")
        else:
            file_cb._w(f"\n=== OUTPUT ===\n{output}\n--- duration_ms={duration_ms} ---")
        file_cb.close()

    def _on_build_error(error_msg: str) -> Any:
        # create_agent never started running, so there is no duration/process.
        _finalize(0, error=error_msg)
        _close_node_run(args.task_id, run_id, agent_id, status="failed", exit_code=1, error=error_msg)
        return DispatchResult(ok=False, error=error_msg, run_id=run_id, duration_ms=0)

    def _on_failure(error_msg: str, inv: Any) -> Any:
        pub_cb.publish_done(inv.result, inv)
        _finalize(inv.duration_ms, error=error_msg)
        _close_node_run(args.task_id, run_id, agent_id, status="failed", exit_code=1,
                        error=error_msg, process=inv.process)
        return DispatchResult(ok=False, error=error_msg, run_id=run_id, duration_ms=inv.duration_ms)

    def _on_success(result: Any, inv: Any) -> Any:
        output = result.agent_output or ""
        # Write to declared output state keys (mutability enforced).
        out_keys = _node_value(node, "output") or _node_value(node, "outputs") or []
        written: Dict[str, Any] = {}
        if out_keys:
            try:
                written = flow_state.apply(list(out_keys), output)
            except StateMutationError as sme:
                return _on_failure(str(sme), inv)
        # Close the node's live block only once the state writes succeeded — a
        # mutation error routes to _on_failure, which publishes its own `done`.
        pub_cb.publish_done(result, inv)
        _finalize(inv.duration_ms, output=output)
        _close_node_run(args.task_id, run_id, agent_id, status="completed", exit_code=0,
                        output=output, process=inv.process)
        return DispatchResult(
            ok=True, text=f"Completed {label}", output=output,
            written=written, run_id=run_id, duration_ms=inv.duration_ms,
        )

    return run_agent_lifecycle(
        agent_id, args.workspace, prompt,
        overrides=agent_overrides,
        extra_callbacks=[file_cb, pub_cb], catch_invoke_exceptions=True,
        on_build_error=_on_build_error, on_success=_on_success, on_failure=_on_failure,
    )


def build_task_driver(
    *,
    args: argparse.Namespace,
    flow: Dict[str, Any],
    flow_id: str,
    run_id: str,
    task_id: str,
    session_id: str,
    task_title: str,
    node_run_ids: Dict[str, str],
    port: int,
    agent_overrides: Optional[Dict[str, Any]] = None,
) -> FlowEngineDriver:
    """Build the task-surface :class:`FlowEngineDriver`.

    Captures the per-run context in closures: agent nodes run through the sync
    ``_run_agent_node`` (off the event loop via ``asyncio.to_thread``), node
    outcomes are mirrored into flow-log events + the ``node_run_ids`` map, and
    the stop check reads the live flow-run record. The checkpoint and heartbeat
    hooks write to the same record, which is what lets a killed run be resumed
    instead of failed.
    """
    agent_overrides = agent_overrides or {}

    async def _run_agent_node_async(node, node_id, label, prompt, outcome, flow_state):
        # _run_agent_node is synchronous (sync invoke_agent); run it off the loop.
        # It yields no streaming events — agent output reaches the UI via the
        # SessionPublishCallback HTTP posts inside _run_agent_node itself.
        # flow_state is the engine's instance, so output-key writes land where
        # successor nodes read them.
        dr = await asyncio.to_thread(
            _run_agent_node, node, node_id, prompt,
            args=args, flow_state=flow_state, agent_overrides=agent_overrides,
            port=port, outcome=outcome,
        )
        outcome.ok = dr.ok
        outcome.output = dr.output
        outcome.text = dr.text
        outcome.written = dr.written or {}
        outcome.goto = dr.goto
        outcome.error = dr.error
        outcome.run_id = dr.run_id
        outcome.duration_ms = dr.duration_ms
        return
        yield  # make this an async generator (never reached)

    def _stop_agent_node(node: Dict[str, Any], node_id: str, outcome: Any) -> None:
        """Stop a node that ran past its ``timeout_seconds``.

        The invocation itself is a synchronous call on a worker thread and
        cannot be interrupted, so what is stopped is the record of it: the
        node's run is closed as failed immediately, the engine fails the node,
        and the abandoned thread's later close is a no-op on an already closed
        record. Saying so here is better than pretending the model stopped.
        """
        rid = getattr(outcome, "run_id", "") or ""
        if not rid:
            return
        try:
            close_run(rid, status="failed", exit_code=1,
                      error="node exceeded its timeout_seconds and was abandoned")
        except Exception as e:  # noqa: BLE001
            print(f"[node_timeout] could not close run {rid[:8]}: {e}")

    def _on_checkpoint(cp: Dict[str, Any]) -> None:
        """Persist the resume point on the flow-run record after every node.

        Arbitrary keys survive on a flow-run record, so the checkpoint rides on
        the record the watchdog and the resume endpoint already read, rather
        than in a file of its own that could disagree with it.
        """
        try:
            run_store.update_flow_run(run_id, {
                "checkpoint": cp, "heartbeat_at": _utc_now_iso(),
            })
        except Exception as e:  # noqa: BLE001 - a checkpoint never fails a run
            print(f"[checkpoint] could not write checkpoint for {run_id[:8]}: {e}")

    def _on_heartbeat() -> None:
        """Prove the run is alive while one node takes a long time. The watchdog
        treats a stale heartbeat, not a missing pid, as death."""
        try:
            run_store.update_flow_run(run_id, {"heartbeat_at": _utc_now_iso()})
        except Exception:
            pass

    def _make_run_context(node_id: str) -> RunContext:
        return RunContext(
            flow_id=args.flow_id, run_id=args.run_id, task_id=args.task_id,
            session_id=args.session_id, workspace=args.workspace, node_id=node_id,
        )

    def _should_stop() -> bool:
        _fr = run_store.get_flow_run(run_id)
        return bool(_fr and _fr.get("status") in ("stop", "stopped"))

    def _on_node_start(ev: dict) -> None:
        if ev.get("is_agent"):
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_start",
                "node_id": ev["node_id"], "agent_name": ev.get("label"),
                "tag": ev.get("category"), "content": f"Running {ev.get('label')}",
                "status": "running",
            })

    #: Why a node was not executed, in the words the history tab shows.
    _SKIP_REASONS = {
        "branch_not_selected": "not on the selected branch",
        "predecessor_failed": "a node it depends on failed",
        "branch_isolated": "its branch was isolated after a failure",
        "cancelled_after_failure": "the run stopped at the first failure",
        "already_done": "already completed before this run resumed",
        "already_skipped": "already skipped before this run resumed",
    }

    def _on_node_skip(ev: dict) -> None:
        reason = ev.get("reason") or ""
        print(f"[node_skip] node={ev['node_id']} reason={reason}")
        log_flow(flow_id, run_id, {
            "timestamp": _utc_now_iso(), "type": "node_skip", "node_id": ev["node_id"],
            "content": f"Skipping node '{ev['node_id']}': "
                       + _SKIP_REASONS.get(reason, reason or "not on the selected branch"),
            "status": "skipped",
        })

    def _on_node_done(ev: dict) -> None:
        node_id = ev["node_id"]
        if ev.get("run_id"):
            node_run_ids[node_id] = ev["run_id"]
        is_agent = ev.get("is_agent")
        if ev.get("ok"):
            if ev.get("goto") is not None:
                print(f"[branch] node={node_id} selected={ev['goto']}")
            print(f"[node_done] node={node_id} label={ev.get('label')} duration_ms={ev.get('duration_ms')}")
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_finish",
                "node_id": node_id, "agent_id": ev.get("entity_id"),
                "agent_name": ev.get("label"), "tag": ev.get("category"),
                "content": f"Completed {ev.get('label')}"
                           + ("" if is_agent else
                              f": wrote {', '.join(ev.get('written') or []) or '(no state keys)'}")
                           + (f" → branch {ev['goto']}" if ev.get("goto") else ""),
                "status": "completed",
                "output": ev.get("output") or ev.get("text"),
                "state": ev.get("state"),
            })
        else:
            print(f"[node_error] node={node_id} label={ev.get('label')} error={ev.get('error')}")
            log_flow(flow_id, run_id, {
                "timestamp": _utc_now_iso(), "type": "agent_error",
                "node_id": node_id, "agent_id": ev.get("entity_id"),
                "agent_name": ev.get("label"), "tag": ev.get("category"),
                "content": f"{ev.get('label')} failed: {ev.get('error')}",
                "status": "failed", "output": ev.get("error"),
            })

    def _on_flow_start(ev: dict) -> None:
        log_flow(flow_id, run_id, {
            "timestamp": _utc_now_iso(), "type": "flow_start",
            "content": f"Starting flow: {flow.get('name', flow_id)}", "status": "running",
            "title": task_title or f"Flow: {flow.get('name', flow_id)}",
            "task_id": task_id, "session_id": session_id,
            "state": ev.get("state"),
        })

    return FlowEngineDriver(
        build_agent_prompt=_build_agent_input,
        run_agent_node=_run_agent_node_async,
        make_run_context=_make_run_context,
        should_stop=_should_stop,
        on_flow_start=_on_flow_start,
        on_node_start=_on_node_start,
        on_node_done=_on_node_done,
        on_node_skip=_on_node_skip,
        on_checkpoint=_on_checkpoint,
        on_heartbeat=_on_heartbeat,
        stop_agent_node=_stop_agent_node,
        # The task surface owns a task and a flow-run record, so it can park a
        # run in awaiting_input and come back to it. That is what makes a
        # human_interrupt node work here and not in flow chat.
        supports_interrupt=True,
    )
