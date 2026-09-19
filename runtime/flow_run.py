"""
Flow subprocess entry point.

Executes an agent flow (DAG) with one run record per node, all linked to a
shared session. A flow is not itself an agent run, so it has no run record of
its own — ``run_id`` is only the stable group id tying the node runs and log
events to this execution (passed through as flow_run_id / run_group).

Each node execution:
  1. Opens its own run record (open_run) linked to the shared session
  2. Builds a prompt from shared context + predecessor outputs
  3. Runs the agent in-process so output is available to successor nodes
  4. Closes its run record (close_run)

When the DAG finishes, the task is finalized directly via finalize_flow_task,
which sets the task status to resolved (single mode) or in_progress (continuous).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

# This module is launched as a script (``python runtime/flow_run.py``) by flow.launcher,
# which puts ``runtime/`` — not the repo root — on sys.path[0]. Ensure the repo
# root is importable so first-party packages (flow, agents, common, run_agent)
# resolve whether this runs as a script or is imported as ``runtime.flow_run``.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# First-party imports. Kept at module top (after the sys.path shim above) rather
# than inside main(): this module already pulls the heavy agents.* / langchain
# graph transitively, so deferring these saves no startup cost.
from managers.run_manager import finalize_flow_task
from tasks.context import persist_task_result, build_task_instruction
from flow.launcher import _set_flow_running
from flow import run_store

# The DAG walk lives in flow.engine; the task-surface driver (node execution,
# run records, flow-log writes, stop check) lives in flow.task_driver. run.py is
# now just the subprocess entry point: preflight + drive the engine.
from flow.engine import run_flow_engine
from flow.task_driver import (
    log_flow,
    _resolve_agent_id,
    build_task_driver,
)
from tasks import service as _ts


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Preflight ───────────────────────────────────────────────────────────────

def _fail_preflight(flow_id: str, run_id: str, task_id: str, stage: str, detail: str) -> None:
    """Mark the flow run failed (flow-run record, task status, log) and exit."""
    msg = f"Flow preflight {stage} failed: {detail}"
    run_store.close_flow_run(run_id, status="failed", exit_code=1, error=msg)
    _set_flow_running(flow_id, False)
    finalize_flow_task(task_id, "failed", 1, error=msg)
    log_flow(flow_id, run_id, {
        "timestamp": _utc_now_iso(),
        "type": "flow_error",
        "content": msg,
        "status": "failed",
    })
    print(f"[flow_error] {msg}", file=sys.stderr)
    sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run an agent flow DAG")
    parser.add_argument("--flow-id", required=True, help="Flow ID (from .agents_hub/flows/)")
    parser.add_argument("--workspace", required=True, help="Absolute workspace path")
    parser.add_argument("--task-id", required=True, help="Task ID this flow run is for")
    parser.add_argument("--run-id", required=True, help="Meta run ID created by flow_launcher")
    parser.add_argument("--session-id", required=True, help="Shared session ID for all node runs")
    parser.add_argument("--desc", default="", help="Shared context / description override")
    parser.add_argument("--seed", default="", help="JSON seed state merged into the flow's initial state (scheduled/webhook triggers)")
    args = parser.parse_args()

    # No load_dotenv() here: like runtime/agent_run.py, this subprocess inherits
    # the launcher's already-populated environment (.env is loaded once by the
    # dashboard server), and the agent factory reads .env off disk directly for
    # provider/model resolution.
    # Publish the workspace name for call-time os.getenv consumers (task scoping,
    # filesystem tools). Workspaces always live under .agents_hub/workspaces/.
    os.environ.setdefault("AGENT_WORKSPACE", Path(args.workspace).name)

    flow_id = args.flow_id
    run_id = args.run_id or str(uuid4())

    from flow import store as flow_store
    flow = flow_store.get_flow(flow_id)
    if not flow:
        msg = f"Flow '{flow_id}' not found in .agents_hub/flows/"
        run_store.close_flow_run(run_id, status="failed", exit_code=1, error=msg)
        _set_flow_running(flow_id, False)
        finalize_flow_task(task_id, "failed", 1, error=msg)
        print(f"[flow_error] {msg}", file=sys.stderr)
        sys.exit(1)

    # A flow is not an agent run, so it has no run record of its own — the per-node
    # agent runs (open_run/close_run below) carry the actual runs in the session.
    # run_id is just the stable group id tying those node runs and log events to
    # this flow execution (passed through as flow_run_id / run_group).

    nodes: List[Dict] = flow.get("nodes", [])
    edges: List[Dict] = flow.get("edges", [])

    # ── Preflight pipeline (fail-fast before any node executes) ───────────────
    # 1. validate the loaded flow's structural consistency
    # 2. resolve & confirm every node's entity exists in the registry
    # (3. state init and 4. ordering happen just below, once preflight passes)
    from flow.validate import (
        validate_flow, resolve_entities, FlowValidationError,
    )

    try:
        validate_flow(flow)
        print(f"[preflight] validation OK ({len(nodes)} nodes, {len(edges)} edges)")
    except FlowValidationError as e:
        _fail_preflight(flow_id, run_id, task_id, "validation", "; ".join(e.errors))

    try:
        node_entities = resolve_entities(nodes)
        _cats = sorted({s.category for s in node_entities.values()})
        print(f"[preflight] resolved {len(node_entities)} entities from registry (categories: {_cats})")
    except FlowValidationError as e:
        _fail_preflight(flow_id, run_id, task_id, "entity resolution", "; ".join(e.errors))

    # Tracks per-node run ids for the final summary
    node_run_ids: Dict[str, str] = {}

    # Build the shared context the same way single-agent runs do (agent_run.py).
    # The base instruction merges the flow's own description with the per-run
    # --desc override (both kept when present, deduped if identical); then — when
    # a task_id is present — build_task_instruction layers the task title +
    # description, parent-task goal, sequence-sibling results, and prior run output.
    flow_desc = (flow.get("description") or "").strip()
    run_desc = (args.desc or "").strip()
    parts = [flow_desc] + ([run_desc] if run_desc and run_desc != flow_desc else [])
    shared_context = "\n\n".join(p for p in parts if p)
    task_id = args.task_id
    if task_id:
        shared_context = build_task_instruction(task_id, shared_context)

    _task = _ts.get_task(task_id) if task_id else None
    task_title = (getattr(_task, "title", "") or "").strip()

    session_id = args.session_id
    _port = int(os.environ.get("DASHBOARD_PORT", "8000"))

    print(f"[flow_start] flow_id={flow_id} nodes={len(nodes)} edges={len(edges)}")
    print(f"Running flow with context: {shared_context}")

    # The engine owns the DAG walk; the task driver supplies the subprocess sinks
    # (node execution, run records, flow-log writes, stop check). See flow.task_driver.
    driver = build_task_driver(
        args=args, flow=flow, flow_id=flow_id, run_id=run_id, task_id=task_id,
        session_id=session_id, task_title=task_title, node_run_ids=node_run_ids,
        port=_port,
    )

    # Seed state (scheduled/webhook triggers): a JSON object merged into the
    # flow's initial state so external callers can parameterize a run.
    seed_state = None
    if (args.seed or "").strip():
        try:
            import json as _json
            parsed = _json.loads(args.seed)
            if isinstance(parsed, dict):
                seed_state = parsed
            else:
                print(f"[flow_seed] ignoring non-object seed: {type(parsed).__name__}")
        except Exception as e:
            print(f"[flow_seed] failed to parse --seed JSON: {e}")

    async def _drive() -> dict:
        final: dict = {}
        async for ev in run_flow_engine(flow, flow_id=flow_id, shared_context=shared_context, driver=driver, seed_state=seed_state):
            if ev["type"] == "flow_finish":
                final = ev
        return final

    final = asyncio.run(_drive())

    # A user stop between nodes halts the engine with stopped=True; the stop
    # endpoint already logged flow_stopped + killed the pid, so just exit.
    if final.get("stopped"):
        print(f"[flow_stopped] flow_run={run_id} — stop requested, halting")
        sys.exit(1)

    any_node_failed = bool(final.get("any_failure"))
    combined_output = final.get("combined_output") or ""
    node_outputs = final.get("node_outputs") or {}

    log_flow(flow_id, run_id, {
        "timestamp": _utc_now_iso(),
        "type": "flow_finish",
        "content": f"Flow '{flow.get('name', args.flow_id)}' finished"
                   + (" (some nodes failed)" if any_node_failed else ""),
        "status": "completed",
    })

    # Save flow output as task result
    if combined_output:
        persist_task_result(
            args.task_id, args.run_id, combined_output, agent_id="flow-custom-graph",
        )

    # Close the flow-run record and finalize the task. The DAG always reaches the
    # end (a failed node doesn't abort the run), so the flow run itself completed;
    # node failures are reflected in exit_code and the task status.
    fr_status = "failed" if any_node_failed else "completed"
    fr_exit = 1 if any_node_failed else 0
    run_store.close_flow_run(args.run_id, status=fr_status, exit_code=fr_exit)
    _set_flow_running(args.flow_id, False)
    finalize_flow_task(args.task_id, "completed" if not any_node_failed else "failed", fr_exit)
    print(f"[flow_done] flow_id={args.flow_id} nodes_completed={len(node_outputs)}"
          f" nodes_failed={sum(1 for n in nodes if n.get('id') not in node_outputs and _resolve_agent_id(n))}")


if __name__ == "__main__":
    main()
