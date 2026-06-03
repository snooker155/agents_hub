"""
Flow subprocess entry point.

Executes an agent flow (DAG) with one run record per node, all linked to a
shared session. The overall flow lifecycle is tracked in a meta run record
(created by flow_runner.py before this process starts).

Each node execution:
  1. Opens its own run record (open_run) linked to the shared session
  2. Builds a prompt from shared context + predecessor outputs
  3. Runs the agent in-process so output is available to successor nodes
  4. Closes its run record (close_run)

The meta run is finalized at the end via finalize_task_from_run, which
updates the task status to resolved (single mode) or in_progress (continuous).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler


class _NodeFileCallback(BaseCallbackHandler):
    """Writes LLM/tool events for a single flow node to its own log file."""

    raise_error = False

    def __init__(self, log_path: Path) -> None:
        self._f = open(log_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    def _w(self, line: str) -> None:
        try:
            self._f.write(line + "\n")
        except Exception:
            pass

    def on_llm_start(self, serialized, prompts, **_):
        model = (serialized or {}).get("name", "unknown") if isinstance(serialized, dict) else "unknown"
        self._w(f"[llm_start] model={model}")

    def on_llm_end(self, response, **_):
        self._w("[llm_end]")

    def on_llm_error(self, error, **_):
        self._w(f"[llm_error] {type(error).__name__}: {error}")

    def on_tool_start(self, serialized, input_str, **_):
        name = (serialized or {}).get("name", "tool") if isinstance(serialized, dict) else "tool"
        preview = str(input_str or "")[:500]
        self._w(f"[tool_start] tool={name} input={preview}")

    def on_tool_end(self, output, **_):
        self._w(f"[tool_end] output={str(output or '')[:500]}")

    def on_tool_error(self, error, **_):
        self._w(f"[tool_error] {type(error).__name__}: {error}")

    def on_chain_error(self, error, **_):
        self._w(f"[chain_error] {type(error).__name__}: {error}")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── DAG helpers ───────────────────────────────────────────────────────────────

def _node_value(node: Dict[str, Any], key: str, default: Any = None) -> Any:
    data = node.get("data", {}) if isinstance(node.get("data"), dict) else {}
    return node[key] if key in node else data.get(key, default)


def _topological_order(nodes: List[Dict], edges: List[Dict]) -> List[str]:
    adjacency: Dict[str, List[str]] = {n["id"]: [] for n in nodes}
    indegree: Dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adjacency and t in indegree:
            adjacency[s].append(t)
            indegree[t] += 1
    queue = deque(nid for nid, deg in indegree.items() if deg == 0)
    ordered: List[str] = []
    while queue:
        cur = queue.popleft()
        ordered.append(cur)
        for nb in adjacency.get(cur, []):
            indegree[nb] -= 1
            if indegree[nb] == 0:
                queue.append(nb)
    # Fall back to original order if a cycle is detected
    return ordered if len(ordered) == len(nodes) else [n["id"] for n in nodes]


def _build_predecessors(edges: List[Dict]) -> Dict[str, List[str]]:
    preds: Dict[str, List[str]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            preds.setdefault(t, []).append(s)
    return preds


def _build_agent_input(
    shared_prompt: str,
    node_id: str,
    predecessors: Dict[str, List[str]],
    node_outputs: Dict[str, str],
    node_task: str = "",
) -> str:
    pred_ids = predecessors.get(node_id, [])
    pred_outputs = [(pid, node_outputs[pid]) for pid in pred_ids if pid in node_outputs]

    parts: List[str] = []
    if pred_outputs:
        # Downstream nodes work only from the previous agents' output. The
        # initial user input (shared_prompt) is intentionally not forwarded —
        # each node receives just what its predecessors produced.
        parts += [
            "=" * 60,
            "OUTPUT FROM PREVIOUS AGENTS IN THIS FLOW",
            "=" * 60,
        ]
        for pred_id, pred_out in pred_outputs:
            parts.append(f"\n[{pred_id}]:\n{pred_out}\n")
        parts += ["=" * 60, "\nContinue the work based on the above output."]
    else:
        # Root node(s) with no predecessors receive the initial user input.
        parts.append(shared_prompt)

    if node_task:
        parts += ["", "## Your specific task for this step:", node_task]

    return "\n".join(parts)


# Maps flow node agent_ids to YAML definition ids
_FACTORY_AGENT_MAP: Dict[str, str] = {
    "factory-pm": "pm_agent",
    "factory-ba": "ba_agent",
    "factory-sd": "sd_agent",
    "factory-tl": "tl_agent",
    "factory-be": "dev_agent",
    "factory-fe": "dev_agent",
    "factory-qa": "qa_agent",
    "factory-ops": "devops_agent",
}

_LABEL_ALIASES: Dict[str, str] = {
    "pm": "factory-pm", "product manager": "factory-pm",
    "ba": "factory-ba", "business analyst": "factory-ba",
    "sd": "factory-sd", "system designer": "factory-sd",
    "tl": "factory-tl", "team lead": "factory-tl",
    "be": "factory-be", "backend": "factory-be", "backend dev": "factory-be",
    "fe": "factory-fe", "frontend": "factory-fe", "frontend dev": "factory-fe",
    "qa": "factory-qa",
    "ops": "factory-ops", "devops": "factory-ops",
}


def _resolve_agent_id(node: Dict[str, Any]) -> Optional[str]:
    explicit = _node_value(node, "agent_id")
    if explicit:
        return explicit
    label = str(_node_value(node, "label", "")).lower()
    if label in _LABEL_ALIASES:
        return _LABEL_ALIASES[label]
    node_id = str(node.get("id", "")).lower()
    for alias, resolved in _LABEL_ALIASES.items():
        if node_id.startswith(alias.replace(" ", "-")):
            return resolved
    return None


# ── Flow loading ──────────────────────────────────────────────────────────────

def _load_flow(flow_id: str) -> Optional[Dict[str, Any]]:
    flows_file = Path(__file__).resolve().parent / "agents" / "state" / "flows.json"
    if not flows_file.exists():
        return None
    try:
        flows = json.loads(flows_file.read_text(encoding="utf-8"))
        return next((f for f in flows if f["id"] == flow_id), None)
    except Exception:
        return None


# ── Interaction log ───────────────────────────────────────────────────────────

def _log_event(log_path: Path, payload: Dict[str, Any]) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
        except Exception:
            existing = []
        existing.append(payload)
        log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Run an agent flow DAG")
    parser.add_argument("--flow-id", required=True, help="Flow ID from flows.json")
    parser.add_argument("--workspace", required=True, help="Absolute workspace path")
    parser.add_argument("--task-id", required=True, help="Task ID this flow run is for")
    parser.add_argument("--run-id", required=True, help="Meta run ID created by flow_runner")
    parser.add_argument("--session-id", required=True, help="Shared session ID for all node runs")
    parser.add_argument("--desc", default="", help="Shared context / description override")
    args = parser.parse_args()

    load_dotenv()
    os.environ["WORKSPACE_ROOT"] = args.workspace

    from agents.run_manager import open_run, close_run, finalize_task_from_run, STATE_DIR, update_run
    from agents.agent_factory import create_agent
    from common.session_service import add_run_to_session
    from common import tasks_service as _ts
    from uuid import UUID as _UUID
    from run_agent import RunStatsCallback, _SessionPublishCallback, _collect_changed_files
    from agents.flow_runner import _set_flow_running

    _sess_id = args.session_id
    _port = int(os.environ.get("DASHBOARD_PORT", "8000"))

    flow_log_path = STATE_DIR / "flow_logs" / f"{args.flow_id}.json"

    # Every event from this run is tagged with a stable run_group (the meta run id)
    # and kind="task" so the dashboard History tab can split the flat log stream
    # into one record per flow run.
    def log_flow(payload: Dict[str, Any]) -> None:
        payload.setdefault("run_group", args.run_id)
        payload.setdefault("kind", "task")
        _log_event(flow_log_path, payload)

    # Activate the meta run record (pre-created by flow_runner with status=pending)
    open_run(
        args.run_id,
        "flow-custom-graph",
        task_id=args.task_id,
        session_id=args.session_id,
        session_type="task",
        pid=os.getpid(),
        log_file=os.environ.get("AGENT_LOG_FILE"),
        is_flow=True,
        link_to_session=False,  # already linked by flow_runner.start_flow_run
    )

    flow = _load_flow(args.flow_id)
    if not flow:
        msg = f"Flow '{args.flow_id}' not found in flows.json"
        _set_flow_running(args.flow_id, False)
        close_run(args.run_id, status="failed", exit_code=1, error=msg)
        finalize_task_from_run(args.run_id, "failed", 1)
        print(f"[flow_error] {msg}", file=sys.stderr)
        sys.exit(1)

    nodes: List[Dict] = flow.get("nodes", [])
    edges: List[Dict] = flow.get("edges", [])

    task = _ts.get_task(args.task_id)
    task_title = (getattr(task, "title", "") or "").strip()
    task_desc = (getattr(task, "description", "") or "").strip()
    flow_desc = (args.desc or flow.get("description", "")).strip()

    # Build shared context in the same style as run_agent.py
    title_prefix = f"Task ID: {args.task_id}\nTask: {task_title}\n\n" if task_title else f"Task ID: {args.task_id}\n\n"
    body = "\n\n".join(filter(None, [task_desc, flow_desc]))
    shared_context = (title_prefix + body).strip() or f"Process task {args.task_id}"

    predecessors = _build_predecessors(edges)

    # Tracks text output of each completed node so successors can use it
    node_outputs: Dict[str, str] = {}
    # Tracks per-node run ids for the final summary
    node_run_ids: Dict[str, str] = {}

    # Store shared context on the meta run record so it's queryable from the dashboard
    try:
        update_run(args.run_id, {"input": shared_context})
    except Exception:
        pass

    print(f"[flow_start] flow_id={args.flow_id} nodes={len(nodes)} edges={len(edges)}")
    print(f"Running flow with context: {shared_context}")
    log_flow({
        "timestamp": _utc_now_iso(),
        "type": "flow_start",
        "content": f"Starting flow: {flow.get('name', args.flow_id)}",
        "status": "running",
        "title": task_title or f"Flow: {flow.get('name', args.flow_id)}",
        "task_id": args.task_id,
        "session_id": args.session_id,
    })

    agent_overrides: Dict[str, Any] = {}
    if os.environ.get("AGENT_PROVIDER"):
        agent_overrides["provider"] = os.environ["AGENT_PROVIDER"]
    if os.environ.get("AGENT_MODEL"):
        agent_overrides["model"] = os.environ["AGENT_MODEL"]
    if os.environ.get("AGENT_BASE_URL"):
        agent_overrides["base_url"] = os.environ["AGENT_BASE_URL"]

    any_node_failed = False

    for node_id in _topological_order(nodes, edges):
        node = next((n for n in nodes if n.get("id") == node_id), None)
        if not node:
            continue

        raw_agent_id = _resolve_agent_id(node)
        if not raw_agent_id:
            print(f"[node_skip] node={node_id} reason=no_agent_id")
            log_flow({
                "timestamp": _utc_now_iso(),
                "type": "node_skip",
                "node_id": node_id,
                "content": f"Skipping node '{node_id}': no agent assigned",
                "status": "skipped",
            })
            continue

        yaml_agent_id = _FACTORY_AGENT_MAP.get(raw_agent_id, raw_agent_id)
        agent_label = _node_value(node, "label") or raw_agent_id
        node_task = _node_value(node, "nodeTask") or ""
        domain = _node_value(node, "domain") or "flow"

        # Open a dedicated run record for this node, linked to the shared session
        node_run_id = str(uuid4())
        node_run_ids[node_id] = node_run_id

        node_log_path = STATE_DIR / "run_logs" / f"node_{node_run_id}.log"
        node_log_path.parent.mkdir(parents=True, exist_ok=True)

        open_run(
            node_run_id,
            yaml_agent_id,
            task_id=args.task_id,
            session_id=args.session_id,
            session_type="task",
            pid=os.getpid(),
            log_file=str(node_log_path),
            flow_id=args.flow_id,
            flow_run_id=args.run_id,
            flow_node_id=node_id,
            flow_node_label=agent_label,
            link_to_session=True,
        )

        agent_input = _build_agent_input(
            shared_context, node_id, predecessors, node_outputs, node_task
        )
        try:
            update_run(node_run_id, {"input": agent_input})
        except Exception:
            pass

        print(f"[node_start] node={node_id} agent={yaml_agent_id} label={agent_label}")
        log_flow({
            "timestamp": _utc_now_iso(),
            "type": "agent_start",
            "node_id": node_id,
            "agent_id": yaml_agent_id,
            "agent_name": agent_label,
            "tag": domain,
            "content": f"Running {agent_label}",
            "status": "running",
            "input": agent_input,
        })

        with open(node_log_path, "w", encoding="utf-8") as _nlf:
            _nlf.write(
                f"--- Node run started at {_utc_now_iso()} ---\n"
                f"Node   : {node_id}\n"
                f"Agent  : {yaml_agent_id} ({agent_label})\n\n"
                f"=== INPUT ===\n{agent_input}\n\n"
                f"=== EXECUTION ===\n"
            )

        t0 = time.perf_counter()
        file_cb = _NodeFileCallback(node_log_path)
        try:
            agent = create_agent(yaml_agent_id, workspace=args.workspace, **agent_overrides)
            print(
                f"[agent_init] agent={yaml_agent_id}"
                f" provider={agent.provider or 'unknown'}"
                f" model={agent.model or 'unknown'}"
                f" workspace={args.workspace}"
            )
            _sp_preview = " | ".join((agent.system_prompt or "").splitlines()[:3])
            print(f"[agent_prompt] {_sp_preview[:300]}")

            stats_cb = RunStatsCallback()
            callbacks = [stats_cb, file_cb]
            pub_cb = _SessionPublishCallback(_sess_id, node_run_id, yaml_agent_id, _port)
            callbacks.append(pub_cb)
            pub_cb._post({
                "type": "meta",
                "run_id": node_run_id,
                "session_id": _sess_id,
                "agent_id": yaml_agent_id,
                "continuation": True,
            })

            result = agent.run(agent_input, callbacks=callbacks)
            duration_ms = int((time.perf_counter() - t0) * 1000)

            if result.ok:
                output = result.agent_output or ""
                node_outputs[node_id] = output
                finished_at = _utc_now_iso()
                process = stats_cb.build_process(duration_ms)
                file_cb._w(f"\n=== OUTPUT ===\n{output}\n--- Completed at {finished_at} duration_ms={duration_ms} ---")
                file_cb.close()
                close_run(node_run_id, status="completed", exit_code=0, output=output, process=process)
                try:
                    _ts.upsert_task_execution_log_entry(
                        _UUID(args.task_id), node_run_id,
                        agent_id=yaml_agent_id, status="completed",
                        finished_at=finished_at, exit_code=0,
                    )
                except Exception:
                    pass
                print(f"[node_done] node={node_id} agent={yaml_agent_id} duration_ms={duration_ms}")
                log_flow({
                    "timestamp": finished_at,
                    "type": "agent_finish",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_name": agent_label,
                    "tag": domain,
                    "content": f"Completed {agent_label}",
                    "status": "completed",
                    "input": agent_input,
                    "output": output,
                })
            else:
                error_msg = str(result.error or "unknown error")
                any_node_failed = True
                finished_at = _utc_now_iso()
                process = stats_cb.build_process(duration_ms)
                file_cb._w(f"\n=== ERROR ===\n{error_msg}\n--- Failed at {finished_at} duration_ms={duration_ms} ---")
                file_cb.close()
                close_run(node_run_id, status="failed", exit_code=1, error=error_msg, process=process)
                try:
                    _ts.upsert_task_execution_log_entry(
                        _UUID(args.task_id), node_run_id,
                        agent_id=yaml_agent_id, status="failed",
                        finished_at=finished_at, exit_code=1, error=error_msg,
                    )
                except Exception:
                    pass
                print(f"[node_error] node={node_id} agent={yaml_agent_id} error={error_msg}")
                log_flow({
                    "timestamp": finished_at,
                    "type": "agent_error",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_name": agent_label,
                    "tag": domain,
                    "content": f"{agent_label} failed: {error_msg}",
                    "status": "failed",
                    "input": agent_input,
                    "output": error_msg,
                })

        except Exception as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            error_msg = f"{type(exc).__name__}: {exc}"
            any_node_failed = True
            finished_at = _utc_now_iso()
            file_cb._w(f"\n=== EXCEPTION ===\n{error_msg}\n--- Failed at {finished_at} ---")
            file_cb.close()
            close_run(node_run_id, status="failed", exit_code=1, error=error_msg)
            try:
                _ts.upsert_task_execution_log_entry(
                    _UUID(args.task_id), node_run_id,
                    agent_id=yaml_agent_id, status="failed",
                    finished_at=finished_at, exit_code=1, error=error_msg,
                )
            except Exception:
                pass
            print(f"[node_exception] node={node_id} agent={yaml_agent_id} error={error_msg}")
            log_flow({
                "timestamp": finished_at,
                "type": "agent_error",
                "node_id": node_id,
                "agent_id": yaml_agent_id,
                "agent_name": agent_label,
                "tag": domain,
                "content": f"{agent_label} failed: {error_msg}",
                "status": "failed",
                "input": agent_input,
                "output": error_msg,
            })

    # Aggregate all node outputs for the task result
    combined_output = "\n\n".join(
        f"### [{nid}]\n{out}" for nid, out in node_outputs.items() if out
    )

    log_flow({
        "timestamp": _utc_now_iso(),
        "type": "flow_finish",
        "content": f"Flow '{flow.get('name', args.flow_id)}' finished"
                   + (" (some nodes failed)" if any_node_failed else ""),
        "status": "completed",
    })

    # Save flow output as task result
    if combined_output:
        try:
            from uuid import UUID
            from common import tasks_service
            from agents.run_manager import get_run_by_id
            run_rec = get_run_by_id(args.run_id)
            started_at = (run_rec or {}).get("started_at")
            changed_files = _collect_changed_files(args.task_id, started_at)
            tasks_service.set_task_result(
                UUID(args.task_id),
                combined_output,
                files=changed_files,
                run_id=args.run_id,
                agent_id="flow-custom-graph",
            )
        except Exception:
            pass

    # Close and finalize the meta run — this updates the task status
    close_run(
        args.run_id,
        status="completed",
        exit_code=0,
        output=combined_output,
        node_run_ids=node_run_ids,
    )
    _set_flow_running(args.flow_id, False)
    finalize_task_from_run(args.run_id, "completed", 0)
    print(f"[flow_done] flow_id={args.flow_id} nodes_completed={len(node_outputs)}"
          f" nodes_failed={sum(1 for n in nodes if n.get('id') not in node_outputs and _resolve_agent_id(n))}")


if __name__ == "__main__":
    main()
