"""
Sessions (Process Contexts) API routes.

A session is a high-level process context that groups one or more individual
agent run messages. For example:
- A single agent run creates one session (with one message).
- An agent factory/flow execution creates one session (with is_flow=True).
- Future multi-step processes can accumulate multiple messages in a session.

Individual agent run details live under /api/messages; the live copy of the
agent behind them lives under /api/instances.
Session contexts are stored in the ``sessions`` table (``common.db``).
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import asyncio
import json
import os
import re
import time
import yaml

from common.config import settings as _global_settings

from uuid import uuid4

from agents import registry
from providers.context_windows import get_model_context_window
from managers import run_manager
from tasks import service as tasks_service
from workspace import create_workspace_folder
from common.session_service import (
    query_contexts as _session_service_query,
    session_ids_without_runs as _session_ids_without_runs,
    delete_context as _delete_context,
    upsert_context as _upsert_context,
    get_context_by_id as _get_context_by_id,
    get_or_create_chat_session,
    add_run_to_session,
    add_event_to_session,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

FLOW_AGENT_IDS = {"flow-graph", "flow-custom-graph", "custom-graph"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------- Helpers --------------------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _resolve_session_model(run: dict) -> str:
    model = run.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()

    params = run.get("params")
    if isinstance(params, dict):
        for k in ("model", "chat_model"):
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    agent_id = run.get("agent_id")
    spec = registry.get_agent(agent_id) if agent_id else None
    if spec and spec.model and spec.model.strip():
        return spec.model.strip()

    if agent_id:
        root = Path(__file__).resolve().parents[4]
        yaml_path = root / "agents" / "definitions" / f"{agent_id}.yaml"
        if yaml_path.exists():
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    ymodel = data.get("model")
                    if isinstance(ymodel, str) and ymodel.strip():
                        return ymodel.strip()
            except Exception:
                pass

    # Workspace default model
    workspace = run.get("workspace") or run.get("task_workspace")
    if workspace:
        try:
            from workspace import get_workspace_metadata, get_workspace_default_model_config
            meta = get_workspace_metadata(workspace) or {}
            override = meta.get("model_override") or {}
            op = (override.get("provider") or "").strip()
            if op and op not in ("global", "workspace_default"):
                ws_dm = override
            elif op == "global":
                ws_dm = {}
            else:
                ws_dm = get_workspace_default_model_config(meta)
            if isinstance(ws_dm, dict):
                ws_m = ws_dm.get("model", "")
                if isinstance(ws_m, str) and ws_m.strip():
                    return ws_m.strip()
        except Exception:
            pass

    # Global settings fallback — use the actual configured model per provider
    provider = (run.get("provider") or "").lower().strip()
    if provider == "anthropic":
        return (os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-6").strip()
    if provider == "google":
        return (os.environ.get("GOOGLE_MODEL") or "gemini-2.0-flash").strip()
    if provider == "ollama":
        return (os.environ.get("OLLAMA_MODEL") or _global_settings.ollama_model or "llama3").strip()
    if provider == "lmstudio":
        return (os.environ.get("LMSTUDIO_MODEL") or _global_settings.lmstudio_model or "local-model").strip()

    # OpenAI or unknown — use global settings
    return (os.environ.get("OPENAI_MODEL") or _global_settings.model or "gpt-4o").strip()


def _resolve_session_provider(run: dict) -> str:
    """Provider that actually served the run — the key the model catalog is
    indexed by, so the context window resolves against the right entry."""
    provider = run.get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip().lower()

    params = run.get("params")
    if isinstance(params, dict):
        v = params.get("provider")
        if isinstance(v, str) and v.strip():
            return v.strip().lower()

    agent_id = run.get("agent_id")
    spec = registry.get_agent(agent_id) if agent_id else None
    if spec and getattr(spec, "provider", None) and str(spec.provider).strip():
        return str(spec.provider).strip().lower()

    return (_global_settings.default_provider or "openai").strip().lower()


def _get_context_window_tokens(model: str, provider: str = "") -> int:
    """Max input tokens for the model, or 0 when nothing knows it.

    Delegates to the single source of truth (``providers.context_windows``):
    the Models-page catalog first — user override, else what the backend
    reported during discovery — then the static per-provider fallback. A local
    table here would go stale and, worse, silently answer for models it has
    never heard of.
    """
    if not model:
        return 0
    return get_model_context_window(provider or "", model.strip())


def _build_context_window_metrics(model: str, input_tokens: int, provider: str = "") -> dict:
    """Context fill for one run.

    ``input_tokens`` must be the **largest single prompt** the run sent, not the
    sum of its prompts: an agent loop resends the conversation on every step, so
    the sum is a billing figure and can exceed the window many times over while
    the context was never close to full.
    """
    limit = _get_context_window_tokens(model, provider)
    used = max(0, int(input_tokens or 0))
    remaining = max(0, limit - used) if limit > 0 else 0
    pct = round((used / limit) * 100, 2) if limit > 0 else 0.0
    return {
        "model": model,
        "provider": provider or "",
        "context_window_tokens": limit,
        "input_tokens_used": used,
        "input_tokens_remaining": remaining,
        "input_fulfillment_pct": pct,
    }


def _new_unique_run_id() -> str:
    """Generate a run_id that is not already present in run state."""
    return run_manager.new_unique_run_id()


def _compute_session_status(runs: List[Dict[str, Any]]) -> str:
    """Derive overall session status from its message runs."""
    if not runs:
        return "pending"
    # Normalise transient statuses for display
    def _norm(s: str) -> str:
        if s == "stop":
            return "stopped"
        if s in ("awaiting_approval", "pending"):
            return "pending"
        return s
    statuses = [_norm(r.get("status", "")) for r in runs]
    if any(s == "running" for s in statuses):
        return "running"
    if all(s == "completed" for s in statuses):
        return "completed"
    if any(s in ("failed", "error") for s in statuses):
        return "failed"
    if all(s == "stopped" for s in statuses):
        return "stopped"
    return statuses[-1] if statuses else "unknown"


def _enrich_run(run: dict, tasks_by_id: dict) -> dict:
    """Add task title and workspace to a run record."""
    task_id = run.get("task_id")
    task = tasks_by_id.get(str(task_id)) if task_id else None
    run_title = run.get("title") or run.get("conversation_title")
    if not run_title and run.get("session_type") == "chat":
        log_file = run.get("log_file")
        if log_file and Path(log_file).exists():
            try:
                txt = Path(log_file).read_text(encoding="utf-8", errors="replace")
                m = re.search(r"^Title\s*:\s*(?P<v>.+)$", txt, flags=re.MULTILINE)
                if m:
                    run_title = (m.group("v") or "").strip()
            except Exception:
                pass
        if not run_title:
            run_title = f"Chat {str(task_id)[:8]}"
    return {
        **run,
        "task_title": (task.title if task else None) or run_title,
        "workspace": (task.workspace if task else None) or run.get("workspace"),
        "model": run.get("model"),
        "is_flow": run.get("is_flow") or (run.get("agent_id", "") in FLOW_AGENT_IDS),
    }


def _enrich_context(ctx: Dict[str, Any], stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Add dynamic status and message metadata to a session context.

    ``stats`` is the per-session tally from ``run_manager.session_run_stats``:
    one grouped query answers status, participants and counts for a whole page,
    so listing sessions no longer means reading every run record in the
    database.
    """
    tally = stats.get(str(ctx.get("session_id"))) or {}
    status = tally.get("status", "pending")

    finished_at = ctx.get("finished_at")
    if not finished_at and status not in ("running", "pending", "awaiting_approval"):
        finished_at = tally.get("finished_at") or finished_at

    return {
        **ctx,
        "status": status,
        "finished_at": finished_at,
        "agents": tally.get("agents", []),
        # Deliberately the number of *referenced* messages, as before — not the
        # tally's run count. Sessions in older databases carry message_ids whose
        # runs were since deleted, and quietly renumbering them here would be a
        # visible change to data the list has always reported this way.
        "message_count": len(ctx.get("message_ids") or []),
        "event_count": len(ctx.get("events") or []),
    }


# -------------------- Endpoints --------------------

@router.get("")
async def list_sessions(
    workspace: Optional[str] = None,
    status: Optional[str] = None,
    is_flow: Optional[bool] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
):
    """A page of session contexts, filtered and ordered in SQL.

    Returns ``{items, total, limit, offset}``. Only the returned page is
    enriched, and its status/participants come from one grouped query over the
    runs table — the list used to parse every session document and every run
    record in the database on each refresh.
    """
    # A session's status is derived from its runs, so it cannot be a column on
    # the sessions table. Rank the whole set first, then let SQL page the
    # surviving ids — the tally is one grouped query either way.
    session_ids = None
    if status:
        all_stats = run_manager.session_run_stats()
        session_ids = [sid for sid, tally in all_stats.items()
                       if tally.get("status") == status]
        if status == "pending":
            # Sessions with no runs at all read as pending and have no tally.
            session_ids += _session_ids_without_runs()

    page = _session_service_query(
        workspace=workspace,
        conversation_id=conversation_id,
        is_flow=is_flow,
        from_date=from_date,
        to_date=to_date,
        session_ids=session_ids,
        limit=max(1, min(int(limit), 500)),
        offset=max(0, int(offset)),
    )
    stats = run_manager.session_run_stats([c.get("session_id") for c in page["items"]])
    return {**page, "items": [_enrich_context(c, stats) for c in page["items"]]}


@router.get("/{session_id}")
async def get_session(session_id: str):
    """Get a session context by ID."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    stats = run_manager.session_run_stats([session_id])
    enriched = _enrich_context(ctx, stats)
    enriched["events"] = ctx.get("events") or []
    return enriched


@router.get("/{session_id}/messages")
async def get_session_messages(session_id: str):
    """List all messages (agent runs) belonging to this session."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    message_ids = ctx.get("message_ids") or []
    runs_by_id = run_manager.get_runs_by_ids(message_ids)

    task_ids = {str(r.get("task_id")) for r in runs_by_id.values() if r.get("task_id")}
    tasks_by_id = tasks_service.get_tasks(task_ids) if task_ids else {}

    result = [_enrich_run(runs_by_id[rid], tasks_by_id)
              for rid in message_ids if rid in runs_by_id]
    result.sort(key=lambda r: r.get("started_at") or "")
    return result


@router.post("/{session_id}/stop")
async def stop_session(session_id: str):
    """Stop all running messages in a session."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    message_ids = ctx.get("message_ids") or []
    attempted_count = 0
    stopped_count = 0
    failed_ids: List[str] = []
    for rid in message_ids:
        run = run_manager.get_run_by_id(rid)
        if not run:
            continue
        if run.get("status") not in {"running", "stop"}:
            continue
        attempted_count += 1
        if run_manager.stop_run_by_id(rid):
            stopped_count += 1
            # For in-process chat runs (no PID), the event_stream may not be active
            # to pick up the "stop" signal and finalize it.  Eagerly mark as "stopped"
            # so the status never gets permanently stuck.  If the event_stream IS still
            # running, its internal check fires first and it re-writes to "stopped" itself.
            updated = run_manager.get_run_by_id(rid) or {}
            if updated.get("status") == "stop" and not updated.get("pid") and not updated.get("container_name"):
                run_manager.update_run(rid, {
                    "status": "stopped",
                    "finished_at": _utc_now_iso(),
                    "error": "stopped by user",
                })
        else:
            failed_ids.append(str(rid))

    return {
        "stopped": stopped_count > 0,
        "attempted_count": attempted_count,
        "stopped_count": stopped_count,
        "failed_ids": failed_ids,
    }


@router.delete("/{session_id}")
async def delete_session(session_id: str, delete_messages: bool = False):
    """Delete a session context. Optionally also delete its message runs."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check no running messages
    message_ids = ctx.get("message_ids") or []
    for rid in message_ids:
        run = run_manager.get_run_by_id(rid)
        if run and run.get("status") == "running":
            raise HTTPException(status_code=400, detail="Stop all running messages before deleting the session")

    _delete_context(session_id)

    deleted_messages = 0
    if delete_messages:
        for rid in message_ids:
            run = run_manager.get_run_by_id(rid)
            if not run:
                continue
            log_file = run.get("log_file")
            if log_file:
                try:
                    p = Path(log_file)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            # delete_run also removes the structured payload row.
            if run_manager.delete_run(rid):
                deleted_messages += 1

    return {"deleted": True, "session_id": session_id, "deleted_messages": deleted_messages}


# -------------------- Session event publishing --------------------
#
# The persistent per-session SSE stream was removed: clients now receive a
# session's events on channel `<session_id>` of the single multiplexed
# `/api/stream` connection. Subprocess continuation runs still POST their events
# to the endpoint below, which fans them out through the broker.


@router.post("/{session_id}/events")
async def publish_session_event(session_id: str, request: Request):
    """Receive a single event from a subprocess and fan it out to SSE subscribers.

    Called by agent_run.py continuation subprocesses that cannot publish
    directly to the in-process broker.
    """
    from common.session_broker import broker
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Event must be a JSON object")
    # Keep a live tail of the run as well as fanning it out, so a page opened
    # mid-run can catch up on what it missed instead of joining a broadcast
    # already in progress (common.live_runs).
    from common import live_runs

    live_runs.record_run_event(event, session_id=session_id)
    await broker.apublish(session_id, event)
    return {"ok": True}


# -------------------- Legacy helpers re-exported for messages.py --------------------
# These are used by routes/messages.py via import

_REASONING_LINE_RE = re.compile(r"^\[reasoning\]\s+step=(?P<step>\d+)\s+content=(?P<content>.+)$")


def _parse_reasoning_line(line: str) -> Optional[dict]:
    """Decode a ``[reasoning] step=N content=<json>`` log line, or None.

    Older logs carried a chars-count summary instead of content; those fall
    back to the raw text so they still show up rather than being dropped.
    """
    m = _REASONING_LINE_RE.match((line or "").strip())
    if not m:
        return None
    raw = m.group("content")
    try:
        content = json.loads(raw)
    except Exception:
        content = raw
    return {"step": int(m.group("step")), "content": str(content), "native": True}


def _extract_messages(log_text: str) -> list:
    out = []
    for m in re.finditer(
        r"--- Conversation history ---\n(?P<hist>[\s\S]*?)\n--- User message ---",
        log_text, flags=re.MULTILINE,
    ):
        hist = m.group("hist") or ""
        for ln in hist.splitlines():
            line = ln.strip()
            if line.startswith("User: "):
                out.append({"role": "user", "content": line.replace("User: ", "", 1)})
            elif line.startswith("Assistant: "):
                out.append({"role": "assistant", "content": line.replace("Assistant: ", "", 1)})
    turn_pattern = re.compile(
        r"--- User message ---\n(?P<user>[\s\S]*?)\n--- Agent response \((?:pending|stream)\) ---\n(?P<assistant>[\s\S]*?)(?=\n=== Message at |\nFinished:|\Z)",
        flags=re.MULTILINE,
    )
    for m in turn_pattern.finditer(log_text):
        user_part = (m.group("user") or "").strip()
        if user_part:
            out.append({"role": "user", "content": user_part})
        raw_assistant = (m.group("assistant") or "").strip()
        filtered_lines = []
        for ln in raw_assistant.splitlines():
            txt = ln.strip()
            if not txt:
                continue
            if (txt.startswith("[llm_start]") or txt.startswith("[llm_usage]") or
                    txt.startswith("[tool_start]") or txt.startswith("[tool_end]") or
                    txt.startswith("[tool_call]") or txt.startswith("[tool_error]") or
                    txt.startswith("[llm_error]") or txt.startswith("[chain_error]") or
                    txt.startswith("[message_summary]") or txt.startswith("[reasoning]") or
                    txt.startswith("[artifact]")):
                continue
            filtered_lines.append(ln)
        assistant_part = "\n".join(filtered_lines).strip()
        if assistant_part:
            out.append({"role": "assistant", "content": assistant_part})
    deduped = []
    for msg in out:
        if deduped and deduped[-1]["role"] == msg["role"] and deduped[-1]["content"] == msg["content"]:
            continue
        deduped.append(msg)
    return deduped


def _extract_chat_message_runs(log_text: str) -> list:
    blocks = re.finditer(
        r"=== Message at (?P<ts>[^\n=]+?)(?: id=(?P<id>[a-zA-Z0-9_-]+))? ===\n(?P<body>[\s\S]*?)(?=\n=== Message at |\Z)",
        log_text, flags=re.MULTILINE,
    )
    runs = []
    for i, m in enumerate(blocks):
        ts = (m.group("ts") or "").strip()
        msg_id = (m.group("id") or str(i + 1)).strip()
        body = m.group("body") or ""
        agent_id = ""
        m_agent = re.search(r"^Agent:\s*(?P<v>[^\n]+)$", body, flags=re.MULTILINE)
        if m_agent:
            agent_id = (m_agent.group("v") or "").strip()
        user_input = ""
        m_user = re.search(r"--- User message ---\n(?P<v>[\s\S]*?)\n--- Agent response", body, flags=re.MULTILINE)
        if m_user:
            user_input = (m_user.group("v") or "").strip()
        output = ""
        m_out = re.search(
            r"--- Agent response \((?:pending|stream)\) ---\n(?P<v>[\s\S]*?)(?=\nFinished:|\nStatus\s*:|\Z)",
            body, flags=re.MULTILINE,
        )
        if m_out:
            lines = []
            for ln in (m_out.group("v") or "").splitlines():
                txt = ln.strip()
                if not txt:
                    continue
                if (txt.startswith("[llm_start]") or txt.startswith("[llm_usage]") or
                        txt.startswith("[tool_start]") or txt.startswith("[tool_end]") or
                        txt.startswith("[tool_call]") or txt.startswith("[tool_error]") or
                        txt.startswith("[llm_error]") or txt.startswith("[chain_error]") or
                        txt.startswith("[message_summary]") or txt.startswith("[reasoning]") or
                        txt.startswith("[artifact]")):
                    continue
                lines.append(ln)
            output = "\n".join(lines).strip()
        tools = []
        tool_start_re = re.compile(r"^\[tool_start\]\s+step=(?P<step>\d+)\s+tool=(?P<tool>[^\s]+)\s+input=(?P<input>.*)$")
        tool_end_re = re.compile(r"^\[tool_end\]\s+output=(?P<output>.*)$")
        for ln in body.splitlines():
            s = ln.strip()
            ms = tool_start_re.match(s)
            if ms:
                tools.append({"step": int(ms.group("step")), "tool": ms.group("tool"), "input": ms.group("input"), "output": None, "running": True})
                continue
            me = tool_end_re.match(s)
            if me and tools:
                for j in range(len(tools) - 1, -1, -1):
                    if tools[j].get("output") is None:
                        tools[j]["output"] = me.group("output")
                        tools[j]["running"] = False
                        break
        thinking = []
        reasoning = []
        for ln in body.splitlines():
            s = ln.strip()
            # Keep markers in chronological (log) order so the invocation steps
            # show when each tool was called relative to the LLM calls. Tool calls
            # surface as a single consolidated [tool_call] marker (step/name/
            # duration); the verbose [tool_start]/[tool_end] lines are skipped.
            if (s.startswith("[llm_start]") or s.startswith("[llm_usage]") or
                    s.startswith("[message_summary]") or s.startswith("[tool_call]") or
                    s.startswith("[reasoning]")):
                thinking.append(s)
            if s.startswith("[reasoning]"):
                parsed = _parse_reasoning_line(s)
                if parsed:
                    reasoning.append(parsed)
        inbound = outbound = total = tool_calls_count = duration_ms = 0
        tool_calls_count = len(tools)
        summary_re = re.search(
            r"\[message_summary\]\s+id=[^\s]+\s+inbound_tokens=(?P<in>\d+)\s+outbound_tokens=(?P<out>\d+)\s+total_tokens=(?P<tot>\d+)\s+tool_calls=(?P<tc>\d+)(?:\s+duration_ms=(?P<dur>\d+))?",
            body,
        )
        if summary_re:
            inbound = int(summary_re.group("in") or 0)
            outbound = int(summary_re.group("out") or 0)
            total = int(summary_re.group("tot") or 0)
            tool_calls_count = int(summary_re.group("tc") or tool_calls_count)
            duration_ms = int(summary_re.group("dur") or 0)
        else:
            for u in re.findall(r"\[llm_usage\]\s+prompt_tokens=(?P<in>\d+)\s+completion_tokens=(?P<out>\d+)\s+total_tokens=(?P<tot>\d+)", body):
                inbound += int(u[0] or 0)
                outbound += int(u[1] or 0)
                total += int(u[2] or 0)
        runs.append({
            "message_id": msg_id, "timestamp": ts, "agent_id": agent_id or None,
            "input": user_input, "output": output, "tools": tools, "thinking": thinking,
            "reasoning": reasoning,
            "inbound_tokens": inbound, "outbound_tokens": outbound,
            "total_tokens": total or (inbound + outbound),
            "tool_calls": tool_calls_count, "duration_ms": duration_ms,
        })
    return runs


def _extract_worker_io(log_text: str) -> tuple:
    input_text = output_text = ""
    # Capture multi-line instructions: everything from "Running agent with instruction:" until
    # the next recognised log boundary (Agent output/failed, worker separator, or tool/llm markers)
    m_in = re.search(
        r"Running agent with instruction:\s*(?P<v>[\s\S]+?)(?=\n(?:Agent output:|Agent failed:|---\s|\[(?:tool_start|tool_end|llm_start|llm_usage|message_summary)\]|Creating agent:)|\Z)",
        log_text,
    )
    if m_in:
        input_text = (m_in.group("v") or "").strip()
    m_out = re.search(r"Agent output:\n(?P<v>[\s\S]*?)(?:\nAgent failed:|\Z)", log_text)
    if m_out:
        output_text = (m_out.group("v") or "").strip()
    else:
        m_fail = re.search(r"Agent failed:\s*(?P<v>.+)", log_text)
        if m_fail:
            output_text = f"Error: {(m_fail.group('v') or '').strip()}"
    return input_text, output_text


def _related_runs_for_session(run: dict) -> list:
    base_task_id = str(run.get("task_id") or "")
    base_type = str(run.get("session_type") or "")
    if not base_task_id:
        return [run]
    if base_type == "chat":
        rel = run_manager.query_runs(task_id=base_task_id, session_type="chat",
                                     limit=500, ascending=True)["items"]
    else:
        rel = run_manager.query_runs(task_id=base_task_id, exclude_session_type="chat",
                                     limit=500, ascending=True)["items"]
        try:
            from uuid import UUID
            t = tasks_service.get_task(UUID(base_task_id))
            started_at = run.get("started_at") or ""
            cutoff = ""
            if t:
                for e in (tasks_service.get_task_activity_log(t.id) or []):
                    if not isinstance(e, dict):
                        continue
                    if str(e.get("type") or "") != "status_change":
                        continue
                    if str(e.get("to") or "") != "todo":
                        continue
                    ts = str(e.get("timestamp") or "")
                    if ts and ts <= started_at and ts > cutoff:
                        cutoff = ts
            if cutoff:
                rel = [r for r in rel if (r.get("started_at") or "") >= cutoff]
        except Exception:
            pass
    if not rel:
        rel = [run]
    rel.sort(key=lambda r: r.get("started_at") or "")
    return rel


def _extract_tools_from_progress(run: dict) -> list:
    ws = run.get("workspace")
    if not ws:
        return []
    try:
        ws_root = create_workspace_folder(str(ws))
        progress_file = ws_root / ".progress.json"
        if not progress_file.exists():
            return []
        payload = json.loads(progress_file.read_text(encoding="utf-8"))
        raw_steps = payload.get("steps", []) if isinstance(payload, dict) else []
    except Exception:
        return []
    started = _parse_iso(run.get("started_at"))
    finished = _parse_iso(run.get("finished_at")) or datetime.now(timezone.utc)
    if not started:
        return []
    result = []
    for s in raw_steps:
        if not isinstance(s, dict):
            continue
        st = _parse_iso(s.get("started_at"))
        if not st or st < started or st > finished:
            continue
        result.append({
            "step": s.get("step"), "tool": s.get("tool"), "input": s.get("input"),
            "output": s.get("output"), "started_at": s.get("started_at"),
            "finished_at": s.get("finished_at"), "running": not bool(s.get("finished_at")),
        })
    result.sort(key=lambda x: (x.get("step") or 0, x.get("started_at") or ""))
    return result


def _extract_tools_from_log(log_text: str) -> list:
    step_re = re.compile(r"^\[(?P<model>[^\]]+)\]\s+Step\s+(?P<step>\d+):\s+(?P<tool>.+?)\s+←\s+(?P<input>.*)$")
    result_re = re.compile(r"^\[(?P<model>[^\]]+)\]\s+Result:\s+(?P<output>.*)$")
    stream_tool_start_re = re.compile(r"^\[tool_start\]\s+step=(?P<step>\d+)\s+tool=(?P<tool>[^\s]+)\s+input=(?P<input>.*)$")
    stream_tool_end_re = re.compile(r"^\[tool_end\]\s+output=(?P<output>.*)$")
    steps = []
    for ln in log_text.splitlines():
        m_step = step_re.match(ln.strip())
        if m_step:
            steps.append({"step": int(m_step.group("step")), "tool": m_step.group("tool").strip(), "input": m_step.group("input").strip(), "output": None, "started_at": None, "finished_at": None, "running": True})
            continue
        m_stream_start = stream_tool_start_re.match(ln.strip())
        if m_stream_start:
            steps.append({"step": int(m_stream_start.group("step")), "tool": m_stream_start.group("tool").strip(), "input": m_stream_start.group("input").strip(), "output": None, "started_at": None, "finished_at": None, "running": True})
            continue
        m_result = result_re.match(ln.strip())
        if m_result and steps:
            for i in range(len(steps) - 1, -1, -1):
                if steps[i].get("output") is None:
                    steps[i]["output"] = m_result.group("output").strip()
                    steps[i]["running"] = False
                    break
        m_stream_end = stream_tool_end_re.match(ln.strip())
        if m_stream_end and steps:
            for i in range(len(steps) - 1, -1, -1):
                if steps[i].get("output") is None:
                    steps[i]["output"] = m_stream_end.group("output").strip()
                    steps[i]["running"] = False
                    break
    return steps


def _build_thinking_trace(run: dict, tools: list, log_text: str, messages: list) -> list:
    trace = []
    if run.get("session_type") == "chat":
        if messages:
            trace.append(f"Received {len(messages)} chat message event(s) in this session.")
        llm_calls = len(re.findall(r"^\[llm_start\]", log_text, flags=re.MULTILINE))
        if llm_calls:
            trace.append(f"LLM call count: {llm_calls}.")
        if tools:
            trace.append(f"Tool calls observed: {len(tools)}.")
        if run.get("status") == "running":
            trace.append("Model is generating the current response.")
        else:
            trace.append("Model finished generating a response.")
    else:
        for ln in log_text.splitlines():
            txt = ln.strip()
            if txt.startswith("Running agent with instruction:"):
                instruction = txt.replace("Running agent with instruction:", "", 1).strip()
                if instruction:
                    trace.append(f"Instruction: {instruction[:220]}")
                break
        for t in tools[:12]:
            step = t.get("step")
            tool = t.get("tool") or "tool"
            if step is not None:
                trace.append(f"Step {step}: called `{tool}`.")
            else:
                trace.append(f"Called `{tool}`.")
        if "Agent output:" in log_text:
            trace.append("Generated final agent output.")
        if run.get("status") == "running":
            trace.append("Session is still running.")
    err = run.get("error")
    if err:
        trace.append(f"Run error: {err}")
    return trace[:20]
