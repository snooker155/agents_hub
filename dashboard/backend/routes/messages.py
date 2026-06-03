"""
Messages (Individual Agent Runs) API routes.

A message represents a single agent run - its log, tools, token usage, etc.
This is the granular view of execution, as opposed to /api/sessions which
groups multiple messages into a process-level context.
"""
import re

from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path

from agents import run_manager
from agents.run_manager import update_run as update_message_run, load_runs as load_all_runs, save_runs as save_all_runs
from common import tasks_service
from workspace import create_workspace_folder
from models import SessionCreate

# Re-use all helper logic from sessions module
from routes.sessions import (
    _enrich_run,
    _resolve_session_model,
    _build_context_window_metrics,
    _extract_chat_message_runs,
    _extract_worker_io,

    _extract_tools_from_progress,
    _extract_tools_from_log,
    _build_thinking_trace,
)

router = APIRouter(prefix="/api/messages", tags=["messages"])

FLOW_AGENT_IDS = {"flow-graph", "flow-custom-graph", "custom-graph"}


def _compute_is_flow(run: dict) -> bool:
    """Detect if a run is part of a factory/flow execution."""
    if run.get("is_flow") is not None:
        return bool(run["is_flow"])
    return run.get("agent_id", "") in FLOW_AGENT_IDS


def _enrich_message(run: dict, tasks_by_id: dict) -> dict:
    enriched = _enrich_run(run, tasks_by_id)
    enriched["is_flow"] = _compute_is_flow(run)
    enriched["session_id"] = run.get("session_id")
    return enriched


@router.get("")
async def list_messages(
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    session_id: Optional[str] = None,
    is_flow: Optional[bool] = None,
):
    """List all individual agent run messages with optional filters."""
    runs = load_all_runs()

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}

    enriched = [_enrich_message(r, tasks_by_id) for r in runs]

    if workspace:
        enriched = [r for r in enriched if r.get("workspace") == workspace]
    if agent_id:
        enriched = [r for r in enriched if r.get("agent_id") == agent_id]
    if status:
        enriched = [r for r in enriched if r.get("status") == status]
    if from_date:
        enriched = [r for r in enriched if (r.get("started_at") or "") >= from_date]
    if to_date:
        enriched = [r for r in enriched if (r.get("started_at") or "") <= to_date]
    if session_id:
        enriched = [r for r in enriched if r.get("session_id") == session_id]
    if is_flow is not None:
        enriched = [r for r in enriched if r.get("is_flow") == is_flow]

    enriched.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return enriched


@router.get("/{run_id}")
async def get_message(run_id: str):
    """Get details for a single message (agent run)."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}
    enriched = _enrich_message(run, tasks_by_id)
    if not enriched.get("model"):
        enriched["model"] = _resolve_session_model(enriched)
    return enriched


@router.get("/{run_id}/logs")
async def get_message_logs(run_id: str):
    """Return the log file content for a message."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")

    log_file = run.get("log_file")
    if not log_file or not Path(log_file).exists():
        return {"logs": ""}

    try:
        content = Path(log_file).read_text(encoding="utf-8", errors="replace")
        return {"logs": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{run_id}/insights")
async def get_message_insights(run_id: str):
    """Return message insights: process graph, tools, thinking trace, token usage."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")

    messages = []
    tools = []
    thinking = []
    message_runs = []
    artifacts = []
    inbound_total = 0
    outbound_total = 0
    llm_invoke_responses = []
    input_contexts = []

    rr_process = run.get("process") or {}

    # File-change artifacts (diffs) captured during chat/flow runs, tagged with
    # the run_id so the UI can rebuild the Artifacts panel on conversation reload.
    for art in (rr_process.get("artifacts") or []):
        if isinstance(art, dict):
            artifacts.append({**art, "run_id": run.get("run_id"), "agent_id": run.get("agent_id")})
    rr_llm = rr_process.get("llm_invoke_responses") or []
    rr_input_context = rr_process.get("llm_input_context")
    if rr_input_context:
        input_contexts.append({
            "run_id": run.get("run_id"),
            "agent_id": run.get("agent_id"),
            "context": str(rr_input_context),
        })
    if isinstance(rr_llm, list):
        for entry in rr_llm:
            llm_invoke_responses.append({
                "run_id": run.get("run_id"),
                "agent_id": run.get("agent_id"),
                "payload": entry,
            })

    log_text = ""
    log_file = run.get("log_file")
    if log_file:
        p = Path(log_file)
        if p.exists():
            try:
                log_text = p.read_text(encoding="utf-8", errors="replace")[-120_000:]
            except Exception:
                log_text = ""

    if run.get("session_type") == "chat":
        chat_runs = _extract_chat_message_runs(log_text)
        for mr in chat_runs:
            msg_agent_id = mr.get("agent_id") or run.get("agent_id")
            mr["agent_id"] = msg_agent_id
            mr["run_id"] = run.get("run_id")
            message_runs.append(mr)
            if mr.get("input"):
                messages.append({"role": "user", "content": mr.get("input"), "agent_id": msg_agent_id})
            if mr.get("output"):
                messages.append({"role": "assistant", "content": mr.get("output"), "agent_id": msg_agent_id})
            inbound_total += int(mr.get("inbound_tokens") or 0)
            outbound_total += int(mr.get("outbound_tokens") or 0)
        for mr in chat_runs:
            msg_agent_id = mr.get("agent_id") or run.get("agent_id")
            for t in (mr.get("tools") or []):
                t["agent_id"] = t.get("agent_id") or msg_agent_id
                t["run_id"] = run.get("run_id")
                tools.append(t)
        thinking.extend(sum([(mr.get("thinking") or []) for mr in chat_runs], []))
    else:
        # Prefer stored fields; fall back to log parsing for older runs
        stored_input = run.get("input") or ""
        stored_output = run.get("output") or ""
        if stored_input or stored_output:
            in_text, out_text = stored_input, stored_output
        else:
            in_text, out_text = _extract_worker_io(log_text)
        # If output is still empty but log exists, try extracting from log
        # (node runs store input but may not store output in the run record)
        if not out_text and log_text:
            _, log_out = _extract_worker_io(log_text)
            out_text = log_out

        run_tools = _extract_tools_from_progress(run)
        if not run_tools:
            run_tools = _extract_tools_from_log(log_text)
        # Fall back to stored process data (node runs — print() goes to node stdout, not per-run log)
        if not run_tools and rr_process.get("tool_calls"):
            for i, tc in enumerate(rr_process["tool_calls"]):
                run_tools.append({
                    "step": i + 1,
                    "tool": tc.get("tool", "tool"),
                    "input": tc.get("input", ""),
                    "output": tc.get("output", ""),
                    "started_at": None,
                    "finished_at": None,
                    "running": False,
                })
        for t in run_tools:
            t["agent_id"] = run.get("agent_id")
            t["run_id"] = run.get("run_id")
        tools.extend(run_tools)

        run_inbound = run_outbound = run_total = 0
        for u in re.findall(
            r"\[llm_usage\]\s+prompt_tokens=(\d+)\s+completion_tokens=(\d+)\s+total_tokens=(\d+)",
            log_text,
        ):
            run_inbound += int(u[0])
            run_outbound += int(u[1])
            run_total += int(u[2])
        # Fall back to stored token_usage for node runs (log file missing print() output)
        if run_inbound == 0 and run_outbound == 0:
            stored_tu = rr_process.get("token_usage") or {}
            run_inbound = int(stored_tu.get("inbound_tokens") or 0)
            run_outbound = int(stored_tu.get("outbound_tokens") or 0)
            run_total = int(stored_tu.get("total_tokens") or (run_inbound + run_outbound))
        inbound_total += run_inbound
        outbound_total += run_outbound

        # Use stored process thinking for node runs (where print() doesn't reach the log file)
        stored_thinking = list(rr_process.get("thinking") or [])

        pseudo = {
            "message_id": run.get("run_id"),
            "timestamp": run.get("started_at"),
            "agent_id": run.get("agent_id"),
            "run_id": run.get("run_id"),
            "input": in_text,
            "output": out_text,
            "tools": run_tools,
            "thinking": stored_thinking,
            "inbound_tokens": run_inbound,
            "outbound_tokens": run_outbound,
            "total_tokens": run_total or (run_inbound + run_outbound),
            "tool_calls": len(run_tools),
            "duration_ms": rr_process.get("duration_ms") or 0,
        }
        message_runs.append(pseudo)
        if in_text:
            messages.append({"role": "user", "content": in_text, "agent_id": run.get("agent_id")})
        if out_text:
            messages.append({"role": "assistant", "content": out_text, "agent_id": run.get("agent_id")})

        trace = _build_thinking_trace(run, run_tools, log_text, messages)
        # Merge stored thinking lines not already covered by log-based trace
        if stored_thinking and not any("[llm_start]" in line for line in trace):
            trace = stored_thinking + trace
        thinking.extend(trace)

    if not thinking:
        thinking = ["No process trace captured."]

    token_usage = {
        "inbound_tokens": inbound_total,
        "outbound_tokens": outbound_total,
        "total_tokens": inbound_total + outbound_total,
    }

    model = _resolve_session_model(run)
    context_window = _build_context_window_metrics(model, int(token_usage.get("inbound_tokens") or 0))

    return {
        "run_id": run_id,
        "session_type": run.get("session_type"),
        "session_id": run.get("session_id"),
        "is_flow": run.get("is_flow", False),
        "model": model,
        "messages": messages,
        "tools": tools,
        "thinking": thinking,
        "message_runs": message_runs,
        "artifacts": artifacts,
        "aggregated_logs": log_text,
        "llm_invoke_responses": llm_invoke_responses,
        "input_contexts": input_contexts,
        "token_usage": token_usage,
        "context_window": context_window,
    }


@router.post("/{run_id}/stop")
async def stop_message(run_id: str):
    """Stop a running message (agent run)."""
    from datetime import datetime, timezone
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")

    stopped = run_manager.stop_run_by_id(run_id)
    if stopped:
        # For in-process chat runs (no PID), eagerly finalize as "stopped" so the
        # status is never permanently stuck in the transient "stop" signal state.
        updated = run_manager.get_run_by_id(run_id) or {}
        if updated.get("status") == "stop" and not updated.get("pid") and not updated.get("container_name"):
            update_message_run(run_id, {
                "status": "stopped",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "stopped by user",
            })
    return {"stopped": stopped}


@router.delete("/{run_id}")
async def delete_message(run_id: str, delete_log: bool = True):
    """Delete a message record. Running messages must be stopped first."""
    runs = load_all_runs()
    idx = next((i for i, r in enumerate(runs) if r.get("run_id") == run_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Message not found")

    run = runs[idx]
    if run.get("status") == "running":
        raise HTTPException(status_code=400, detail="Stop the running message before deleting it")

    log_file = run.get("log_file")
    task_id = run.get("task_id")
    runs.pop(idx)
    save_all_runs(runs)

    if task_id:
        try:
            from uuid import UUID
            task = tasks_service.get_task(UUID(str(task_id)))
            if task and str(task.assigned_agent_run_id) == run_id:
                tasks_service.update_task(
                    task.id,
                    assigned_agent_run_id=None,
                    assigned_agent_type=None,
                )
        except Exception:
            pass

    log_deleted = False
    if delete_log and log_file:
        try:
            p = Path(log_file)
            if p.exists():
                p.unlink()
                log_deleted = True
        except Exception:
            log_deleted = False

    return {"deleted": True, "run_id": run_id, "log_deleted": log_deleted}
