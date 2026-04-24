"""
Sessions (Process Contexts) API routes.

A session is a high-level process context that groups one or more individual
agent run messages. For example:
- A single agent run creates one session (with one message).
- An agent factory/flow execution creates one session (with is_flow=True).
- Future multi-step processes can accumulate multiple messages in a session.

Individual agent run details live under /api/messages.
Session contexts are stored in agents/state/session_contexts.json.
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
from langchain_core.callbacks import BaseCallbackHandler

from uuid import uuid4

from agents import run_manager, registry
from common import tasks_service
from workspace import create_workspace_folder
from common.session_service import (
    load_contexts as _load_contexts,
    save_contexts as _save_contexts,
    upsert_context as _upsert_context,
    get_context_by_id as _get_context_by_id,
    get_or_create_chat_session,
    add_run_to_session,
    add_event_to_session,
)
from models import SessionCreate, SessionContextCreate

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

FLOW_AGENT_IDS = {"flow-graph", "flow-custom-graph", "custom-graph"}

MODEL_CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1-nano": 1047576,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------------------- Helpers --------------------

def _to_json_safe(value, *, depth: int = 0, max_depth: int = 5):
    if depth >= max_depth:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:200]:
            out[str(k)] = _to_json_safe(v, depth=depth + 1, max_depth=max_depth)
        return out
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v, depth=depth + 1, max_depth=max_depth) for v in list(value)[:200]]
    for meth in ("model_dump", "dict"):
        fn = getattr(value, meth, None)
        if callable(fn):
            try:
                return _to_json_safe(fn(), depth=depth + 1, max_depth=max_depth)
            except Exception:
                pass
    if hasattr(value, "__dict__"):
        try:
            return _to_json_safe(vars(value), depth=depth + 1, max_depth=max_depth)
        except Exception:
            pass
    return str(value)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int((len(text) + 3) / 4))


class _SessionChatCallback(BaseCallbackHandler):
    def __init__(self, prompt_text: str = ""):
        self.prompt_text = str(prompt_text or "")
        self.output_parts: List[str] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_calls = 0
        self.tool_history: List[Dict[str, Any]] = []
        self.thinking_history: List[str] = []
        self.llm_invoke_responses: List[Dict[str, Any]] = []
        self._pending_tool: Optional[Dict[str, Any]] = None
        self.cancelled: bool = False  # set True to interrupt LLM streaming mid-generation

    def on_llm_start(self, serialized, prompts, **kwargs):
        model_name = serialized.get("name") if isinstance(serialized, dict) else "unknown"
        line = f"[llm_start] model={model_name or 'unknown'}"
        self.thinking_history.append(line)
        try:
            if isinstance(prompts, list):
                self.prompt_text = "\n".join(str(p) for p in prompts if p is not None)
        except Exception:
            pass

    def on_llm_new_token(self, token, **kwargs):
        if self.cancelled:
            raise InterruptedError("Generation stopped by user")
        if token:
            self.output_parts.append(str(token))

    def on_llm_end(self, response, **kwargs):
        try:
            self.llm_invoke_responses.append({
                "response_type": response.__class__.__name__,
                "llm_output": _to_json_safe(getattr(response, "llm_output", None)),
                "generations": _to_json_safe(getattr(response, "generations", None)),
            })
        except Exception:
            pass

        usage = {}
        try:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {}) or {}
        except Exception:
            usage = {}
        if not usage:
            try:
                gens = getattr(response, "generations", []) or []
                for grp in gens:
                    for g in grp:
                        md = getattr(getattr(g, "message", None), "response_metadata", None) or {}
                        tu = md.get("token_usage") or md.get("usage") or {}
                        if tu:
                            usage = tu
                            break
                    if usage:
                        break
            except Exception:
                usage = {}
        if not usage:
            try:
                gens = getattr(response, "generations", []) or []
                for grp in gens:
                    for g in grp:
                        um = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                        if um:
                            usage = {
                                "prompt_tokens": um.get("input_tokens"),
                                "completion_tokens": um.get("output_tokens"),
                                "total_tokens": um.get("total_tokens"),
                            }
                            break
                    if usage:
                        break
            except Exception:
                usage = {}

        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        t = int(usage.get("total_tokens") or (p + c))
        estimated = False
        if p == 0 and c == 0 and t == 0:
            p = _estimate_tokens(self.prompt_text)
            c = _estimate_tokens("".join(self.output_parts))
            t = p + c
            estimated = True

        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.thinking_history.append(
            f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}"
            + (" estimated=true" if estimated else "")
        )

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.tool_calls += 1
        name = serialized.get("name") if isinstance(serialized, dict) else "tool"
        entry = {
            "step": self.tool_calls,
            "tool": str(name or "tool"),
            "input": str(input_str),
        }
        self._pending_tool = entry
        self.thinking_history.append(f"[tool_start] step={entry['step']} tool={entry['tool']} input={entry['input'][:240]}")

    def on_tool_end(self, output, **kwargs):
        out = str(output)
        self.thinking_history.append(f"[tool_end] output={out[:240]}")
        if self._pending_tool is not None:
            entry = dict(self._pending_tool)
            entry["output"] = out
            self.tool_history.append(entry)
            self._pending_tool = None

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


def _get_context_window_tokens(model: str) -> int:
    if not model:
        return 128000
    m = model.strip().lower()
    if m in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[m]
    for k, v in MODEL_CONTEXT_WINDOWS.items():
        if m.startswith(f"{k}-"):
            return v
    return 128000


def _build_context_window_metrics(model: str, input_tokens: int) -> dict:
    limit = _get_context_window_tokens(model)
    used = max(0, int(input_tokens or 0))
    remaining = max(0, limit - used)
    pct = round((used / limit) * 100, 2) if limit > 0 else 0.0
    return {
        "model": model,
        "context_window_tokens": limit,
        "input_tokens_used": used,
        "input_tokens_remaining": remaining,
        "input_fulfillment_pct": pct,
    }


def _new_unique_run_id() -> str:
    """Generate a run_id that is not already present in agent_runs state."""
    try:
        existing = {str(r.get("run_id")) for r in (run_manager.load_runs() or []) if r.get("run_id")}
    except Exception:
        existing = set()
    rid = str(uuid4())
    while rid in existing:
        rid = str(uuid4())
    return rid


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


def _enrich_context(ctx: Dict[str, Any], runs_by_id: Dict[str, Dict]) -> Dict[str, Any]:
    """Add dynamic status and message metadata to a session context."""
    message_ids = ctx.get("message_ids") or []
    runs = [runs_by_id[rid] for rid in message_ids if rid in runs_by_id]

    status = _compute_session_status(runs)

    # Derive finished_at: max finished_at of completed messages if all done
    finished_at = ctx.get("finished_at")
    if not finished_at and runs and status not in ("running", "pending", "awaiting_approval"):
        times = [r.get("finished_at") for r in runs if r.get("finished_at")]
        if times:
            finished_at = max(times)

    # Collect agent participants
    agents = list({r.get("agent_id") for r in runs if r.get("agent_id")})

    return {
        **ctx,
        "status": status,
        "finished_at": finished_at,
        "agents": agents,
        "message_count": len(message_ids),
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
):
    """List all session contexts."""
    contexts = _load_contexts()
    runs = run_manager.load_runs()
    runs_by_id = {r["run_id"]: r for r in runs}

    enriched = [_enrich_context(c, runs_by_id) for c in contexts]

    if workspace:
        enriched = [c for c in enriched if c.get("workspace") == workspace]
    if status:
        enriched = [c for c in enriched if c.get("status") == status]
    if is_flow is not None:
        enriched = [c for c in enriched if bool(c.get("is_flow")) == is_flow]
    if from_date:
        enriched = [c for c in enriched if (c.get("created_at") or "") >= from_date]
    if to_date:
        enriched = [c for c in enriched if (c.get("created_at") or "") <= to_date]

    enriched.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return enriched

@router.get("/{session_id}")
async def get_session(session_id: str):
    """Get a session context by ID."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    runs = run_manager.load_runs()
    runs_by_id = {r["run_id"]: r for r in runs}
    enriched = _enrich_context(ctx, runs_by_id)
    enriched["events"] = ctx.get("events") or []
    return enriched


@router.get("/{session_id}/messages")
async def get_session_messages(session_id: str):
    """List all messages (agent runs) belonging to this session."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    message_ids = ctx.get("message_ids") or []
    runs = run_manager.load_runs()
    runs_by_id = {r["run_id"]: r for r in runs}

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}

    result = []
    for rid in message_ids:
        r = runs_by_id.get(rid)
        if r:
            result.append(_enrich_run(r, tasks_by_id))

    result.sort(key=lambda r: r.get("started_at") or "")
    return result


@router.post("/{session_id}/messages")
async def add_message_to_session(session_id: str, data: SessionCreate):
    """Start a new agent run and add it to an existing session."""
    ctx = _get_context_by_id(session_id)
    if not ctx:
        raise HTTPException(status_code=404, detail="Session not found")

    spec = registry.get_agent(data.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{data.agent_id}' not found")

    ws_name = ctx.get("workspace")
    if data.workspace:
        try:
            p = create_workspace_folder(data.workspace)
            ws_name = p.name
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve workspace: {e}")

    conv_id = str(ctx.get("conversation_id") or session_id)
    run_id = _new_unique_run_id()
    started = _utc_now_iso()
    message_text = (data.description or data.title or "").strip()
    title = (data.title or (message_text[:60] + ("…" if len(message_text) > 60 else "")) or "Chat message").strip()

    # Rebuild bounded session history from prior chat message runs so the
    # model can see previous turns in this session.
    history_items: List[Dict[str, str]] = []
    try:
        all_runs = run_manager.load_runs()
        runs_by_id = {str(r.get("run_id")): r for r in all_runs}
        for prev_rid in (ctx.get("message_ids") or []):
            rr = runs_by_id.get(str(prev_rid))
            if not rr:
                continue
            lp = rr.get("log_file")
            if not lp:
                continue
            p = Path(lp)
            if not p.exists():
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")[-200_000:]
                for mr in _extract_chat_message_runs(txt):
                    u = str(mr.get("input") or "").strip()
                    a = str(mr.get("output") or "").strip()
                    if u:
                        history_items.append({"role": "user", "content": u})
                    if a:
                        history_items.append({"role": "assistant", "content": a})
            except Exception:
                continue
    except Exception:
        history_items = []

    bounded_history = history_items[-40:]
    budget = 60_000
    history_lines: List[str] = []
    for item in reversed(bounded_history):
        role = "User" if item.get("role") == "user" else "Assistant"
        content = str(item.get("content") or "")
        if len(content) > 4000:
            content = content[:4000] + "\n...[truncated]"
        line = f"{role}: {content}"
        if budget - len(line) < 0:
            break
        budget -= len(line)
        history_lines.insert(0, line)

    full_prompt = message_text
    if history_lines:
        full_prompt = "\n".join([
            "Use the conversation history for context when answering the latest user message.",
            "",
            "Conversation history:",
            *history_lines,
            "",
            "Latest user message:",
            message_text,
        ])
    chat_logs_dir = run_manager.STATE_DIR / "chat_logs"
    chat_logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = chat_logs_dir / f"chat_{run_id}.log"
    msg_id = str(uuid4())[:8]

    log_lines = [
        f"=== Chat message  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {data.agent_id}",
        f"Workspace : {ws_name or '—'}",
        f"Conv ID   : {conv_id}",
        f"Session ID: {session_id}",
        f"Title     : {title}",
        "",
        f"=== Message at {started} id={msg_id} ===",
    ]
    if history_lines:
        log_lines.extend([
            "--- Conversation history ---",
            *history_lines,
            "",
        ])
    log_lines.extend([
        "--- User message ---",
        message_text,
        "",
        "--- Agent response (stream) ---",
    ])
    try:
        log_file.write_text("\n".join(log_lines), encoding="utf-8")
    except Exception:
        pass

    run_manager.open_run(
        run_id,
        data.agent_id,
        task_id=conv_id,
        session_id=session_id,
        session_type="chat",
        message_origin="session_direct",
        workspace=ws_name,
        title=title,
        log_file=str(log_file),
        link_to_session=False,
    )

    started_perf = time.perf_counter()
    callback = _SessionChatCallback(prompt_text=full_prompt)
    final_status = "failed"
    final_error = None
    final_output = ""
    try:
        from agents.agent_factory import create_agent

        workspace_abs = None
        if ws_name:
            try:
                workspace_abs = str(create_workspace_folder(ws_name))
            except Exception:
                workspace_abs = None

        # Propagate session_id into tool calls that run inside the agent thread
        from common.agent_context import current_session_id as _session_ctx
        _session_ctx.set(session_id)

        def _run_agent():
            agent = create_agent(data.agent_id, workspace=workspace_abs, streaming=True)
            return agent.run(full_prompt, run_id, callbacks=[callback])

        agent_task = asyncio.create_task(asyncio.to_thread(_run_agent))

        # Poll for external stop signal every 0.2s while the agent runs
        while not agent_task.done():
            await asyncio.sleep(0.2)
            current_check = run_manager.get_run_by_id(run_id) or {}
            if current_check.get("status") == "stop":
                callback.cancelled = True  # interrupt LLM token streaming in the thread
                agent_task.cancel()
                break

        try:
            result = await agent_task
            if result.ok:
                final_output = str(result.agent_output or "").strip()
                final_status = "completed"
            else:
                final_error = str(result.error or "Agent returned no output")
                final_output = f"Error: {final_error}"
                final_status = "failed"
        except (asyncio.CancelledError, InterruptedError):
            callback.cancelled = True
            final_status = "stopped"
            final_error = "stopped by user"
            final_output = "Stopped by user"
    except Exception as e:
        final_error = str(e)
        final_output = f"Error: {final_error}"
        final_status = "failed"

    finished = _utc_now_iso()
    duration_ms = int((time.perf_counter() - started_perf) * 1000)
    current = run_manager.get_run_by_id(run_id) or {}
    if current.get("status") == "stop":
        final_status = "stopped"
        final_error = "stopped by user"
        final_output = "Stopped by user"
    summary_line = (
        f"[message_summary] id={msg_id} "
        f"inbound_tokens={callback.prompt_tokens} "
        f"outbound_tokens={callback.completion_tokens} "
        f"total_tokens={callback.total_tokens} "
        f"tool_calls={callback.tool_calls} "
        f"duration_ms={duration_ms}"
    )
    try:
        merged_lines = list(log_lines)
        merged_lines.extend(callback.thinking_history)
        if final_output:
            merged_lines.append(final_output)
        merged_lines.append(summary_line)
        merged_lines.extend(["", f"Finished: {finished}", f"Status  : {final_status}"])
        log_file.write_text("\n".join(merged_lines), encoding="utf-8")
    except Exception:
        pass

    run_manager.update_run(run_id, {
        "status": final_status,
        "finished_at": finished,
        "exit_code": 0 if final_status == "completed" else 1,
        "error": None if final_status == "completed" else (final_error or "agent error"),
        "process": {
            "llm_input_context": callback.prompt_text or full_prompt,
            "tool_calls": callback.tool_history,
            "thinking": callback.thinking_history + [summary_line],
            "llm_invoke_responses": callback.llm_invoke_responses,
            "token_usage": {
                "inbound_tokens": callback.prompt_tokens,
                "outbound_tokens": callback.completion_tokens,
                "total_tokens": callback.total_tokens,
            },
            "duration_ms": duration_ms,
        },
    })

    message_ids = list(ctx.get("message_ids") or [])
    if run_id not in message_ids:
        message_ids.append(run_id)
    _upsert_context({**ctx, "message_ids": message_ids, "updated_at": _utc_now_iso()})

    return {"session_id": session_id, "run_id": run_id, "task_id": conv_id}


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

    contexts = _load_contexts()
    contexts = [c for c in contexts if c.get("session_id") != session_id]
    _save_contexts(contexts)

    deleted_messages = 0
    if delete_messages:
        runs = run_manager.load_runs()
        remaining = []
        for r in runs:
            if r.get("run_id") in set(message_ids):
                log_file = r.get("log_file")
                if log_file:
                    try:
                        p = Path(log_file)
                        if p.exists():
                            p.unlink()
                    except Exception:
                        pass
                deleted_messages += 1
            else:
                remaining.append(r)
        run_manager.save_runs(remaining)

    return {"deleted": True, "session_id": session_id, "deleted_messages": deleted_messages}


# -------------------- Session SSE stream --------------------

@router.get("/{session_id}/stream")
async def session_stream(session_id: str, request: Request):
    """Persistent SSE stream for a session.

    Clients subscribe here to receive events from ALL runs that belong to
    this session — including continuation runs spawned as subprocesses after
    the original HTTP request has already closed.

    Event types: meta, token, tool_start, tool_end, usage, done, heartbeat, session_done
    """
    from common.session_broker import broker

    async def _generate():
        async for event in broker.subscribe(session_id):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{session_id}/events")
async def publish_session_event(session_id: str, request: Request):
    """Receive a single event from a subprocess and fan it out to SSE subscribers.

    Called by run_agent.py continuation subprocesses that cannot publish
    directly to the in-process broker.
    """
    from common.session_broker import broker
    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Event must be a JSON object")
    await broker.apublish(session_id, event)
    return {"ok": True}


# -------------------- Legacy helpers re-exported for messages.py --------------------
# These are used by routes/messages.py via import

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
                    txt.startswith("[message_summary]")):
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
                        txt.startswith("[message_summary]")):
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
        for ln in body.splitlines():
            s = ln.strip()
            if s.startswith("[llm_start]"):
                thinking.append(s)
            elif s.startswith("[llm_usage]"):
                thinking.append(s)
            elif s.startswith("[message_summary]"):
                thinking.append(s)
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
    all_runs = run_manager.load_runs()
    if base_type == "chat":
        rel = [r for r in all_runs if str(r.get("session_type") or "") == "chat" and str(r.get("task_id") or "") == base_task_id]
    else:
        rel = [r for r in all_runs if str(r.get("session_type") or "") != "chat" and str(r.get("task_id") or "") == base_task_id]
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
