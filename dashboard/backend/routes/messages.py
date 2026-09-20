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

from common import live_runs
from managers import run_manager
from managers.run_manager import update_run as update_message_run
from tasks import service as tasks_service
from workspace import create_workspace_folder
from models import SessionCreate

# Re-use all helper logic from sessions module
from routes.sessions import (
    _enrich_run,
    _resolve_session_model,
    _resolve_session_provider,
    _build_context_window_metrics,
    _extract_chat_message_runs,
    _extract_worker_io,

    _extract_tools_from_progress,
    _extract_tools_from_log,
    _build_thinking_trace,
    _parse_reasoning_line,
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


def _enrich_page(runs: list) -> list:
    """Attach task titles to a page of runs, fetching only the tasks it needs."""
    task_ids = {str(r.get("task_id")) for r in runs if r.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}
    return [_enrich_message(r, tasks_by_id) for r in runs]


@router.get("")
async def list_messages(
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    session_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    is_flow: Optional[bool] = None,
    channel: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    """A page of agent run messages, filtered and ordered in SQL.

    Returns ``{items, total, limit, offset}``. Filtering used to happen in
    Python over every run ever recorded, which a workspace running a thousand
    agents in parallel turns into a full-table scan on every refresh.
    """
    page = run_manager.query_runs(
        workspace=workspace,
        agent_id=agent_id,
        status=status,
        session_id=session_id,
        instance_id=instance_id,
        channel=channel,
        is_flow=is_flow,
        flow_agent_ids=sorted(FLOW_AGENT_IDS),
        from_date=from_date,
        to_date=to_date,
        q=q,
        limit=max(1, min(int(limit), 500)),
        offset=max(0, int(offset)),
    )
    return {**page, "items": _enrich_page(page["items"])}


@router.get("/{run_id}")
async def get_message(run_id: str):
    """Get details for a single message (agent run)."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Message not found")

    task_id = run.get("task_id")
    tasks_by_id = tasks_service.get_tasks([task_id]) if task_id else {}
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


@router.get("/{run_id}/live")
async def get_message_live(run_id: str):
    """What this run has produced so far, for a run that is still going.

    A finished run answers from its record: the log, the payloads, the process
    graph. A running one has none of that yet, and its events are a broadcast
    nobody kept — so opening the page of a run in progress used to show a status
    badge and an empty log until it ended. This returns the live tail held in
    :mod:`common.live_runs`, which the page then continues from the session
    channel.

    ``{"turn": null}`` when nothing live is known: the run is over, or it was
    never one of the kinds that report (see the module docstring there).
    """
    return {"turn": live_runs.by_run(run_id)}


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

    # Heavy process payload lives in a per-run sidecar (see run_manager); the slim
    # value on the index record only carries token totals.
    rr_process = run_manager.get_run_process(run_id) or {}

    # File-change artifacts (diffs) captured during chat/flow runs, tagged with
    # the run_id so the UI can rebuild the Artifacts panel on conversation reload.
    for art in (rr_process.get("artifacts") or []):
        if isinstance(art, dict):
            artifacts.append({**art, "run_id": run.get("run_id"), "agent_id": run.get("agent_id")})
    rr_llm = rr_process.get("llm_invoke_responses") or []
    rr_input_context = rr_process.get("llm_input_context")
    if rr_input_context:
        # New runs store a structured object {system_prompt, history,
        # user_message, response, llm_invocations}; older runs stored a plain
        # string. Pass the object through and keep the string under "context"
        # for backward compatibility with the existing UI fallback.
        entry = {
            "run_id": run.get("run_id"),
            "agent_id": run.get("agent_id"),
        }
        if isinstance(rr_input_context, dict):
            entry["structured"] = rr_input_context
            entry["context"] = rr_input_context.get("user_message") or ""
        else:
            entry["context"] = str(rr_input_context)
        input_contexts.append(entry)
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
        # Native model thoughts captured during the run ([reasoning] lines)
        stored_reasoning = [
            r for r in (_parse_reasoning_line(line) for line in stored_thinking) if r
        ]
        if not stored_reasoning and log_text:
            stored_reasoning = [
                r for r in (_parse_reasoning_line(line) for line in log_text.splitlines()) if r
            ]

        pseudo = {
            "message_id": run.get("run_id"),
            "timestamp": run.get("started_at"),
            "agent_id": run.get("agent_id"),
            "run_id": run.get("run_id"),
            "input": in_text,
            "output": out_text,
            "tools": run_tools,
            "thinking": stored_thinking,
            "reasoning": stored_reasoning,
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
    # Context fill is measured by the LARGEST single prompt the run sent, not by
    # the sum of its prompts. An agent loop resends the whole conversation on
    # every step, so `inbound_tokens` (30 steps x ~5k here) is a billing figure
    # and would report a window many times "overfull" while the context never
    # came close. Prefer the per-call records, fall back to the log's usage
    # lines, and only then to the total (single-call runs, where they agree).
    peak_prompt = 0
    for inv in (rr_process.get("llm_invocations") or []):
        if isinstance(inv, dict):
            peak_prompt = max(peak_prompt, int((inv.get("token_usage") or {}).get("inbound_tokens") or 0))
    if not peak_prompt and log_text:
        for m in re.findall(r"\[llm_usage\]\s+prompt_tokens=(\d+)", log_text):
            peak_prompt = max(peak_prompt, int(m))
    if not peak_prompt:
        peak_prompt = int(token_usage.get("inbound_tokens") or 0)
    context_window = _build_context_window_metrics(
        model, peak_prompt, _resolve_session_provider(run)
    )

    # Structured response (buttons / keyboard) recorded on the unified payload,
    # if the agent emitted one — the response JSON connected to this run.
    response_block = rr_process.get("response") if isinstance(rr_process.get("response"), dict) else None
    structured_response = (response_block or {}).get("structured")

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
        # Links to the service entities this run touched (tasks, views, files, …),
        # resolved when the run finished — see common/entity_links.py.
        "entities": list(rr_process.get("entities") or []),
        "aggregated_logs": log_text,
        "llm_invoke_responses": llm_invoke_responses,
        "input_contexts": input_contexts,
        "structured_response": structured_response,
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
    run = run_manager.get_run_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Message not found")

    if run.get("status") == "running":
        raise HTTPException(status_code=400, detail="Stop the running message before deleting it")

    log_file = run.get("log_file")
    task_id = run.get("task_id")
    # delete_run removes both the run record and its structured payload row.
    run_manager.delete_run(run_id)

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
