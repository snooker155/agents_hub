"""
Sessions (Agent Runs) API routes.

Sessions are agent runs tracked in agents/state/agent_runs.json.
Provides filtering by workspace, agent, status, and time interval,
plus the ability to start a new session directly against an agent.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import yaml

from agents import run_manager, registry
from common import tasks_service
from common.workspace import create_workspace_folder
from tasks import AgentState
from models import SessionCreate

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


MODEL_CONTEXT_WINDOWS = {
    # Common OpenAI context windows (input side)
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "gpt-4.1-nano": 1047576,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
}


def _enrich_run(run: dict, tasks_by_id: dict) -> dict:
    """Add task title and workspace to a run record.

    For chat sessions there is no task entry – fall back to fields stored
    directly on the run record (title, workspace, session_type).
    """
    task_id = run.get("task_id")
    task = tasks_by_id.get(str(task_id)) if task_id else None
    return {
        **run,
        "task_title": (task.title if task else None) or run.get("title"),
        "workspace": (task.workspace if task else None) or run.get("workspace"),
    }


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _resolve_session_model(run: dict) -> str:
    """Best-effort resolve model for a run from run data, registry defaults, or YAML definition."""
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
    if spec and isinstance(spec.default_params, dict):
        for k in ("model", "chat_model"):
            v = spec.default_params.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    if agent_id:
        root = Path(__file__).resolve().parents[3]
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

    return "gpt-4o"


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


def _extract_messages(log_text: str) -> list[dict]:
    """Extract chat messages from chat session logs."""
    out: list[dict] = []

    # Collect all history snapshots (best-effort; de-duplicated later)
    for m in re.finditer(
        r"--- Conversation history ---\n(?P<hist>[\s\S]*?)\n--- User message ---",
        log_text,
        flags=re.MULTILINE,
    ):
        hist = m.group("hist") or ""
        for ln in hist.splitlines():
            line = ln.strip()
            if line.startswith("User: "):
                out.append({"role": "user", "content": line.replace("User: ", "", 1)})
            elif line.startswith("Assistant: "):
                out.append({"role": "assistant", "content": line.replace("Assistant: ", "", 1)})

    # Collect all user/assistant turns
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
            if (
                txt.startswith("[llm_start]")
                or txt.startswith("[llm_usage]")
                or txt.startswith("[tool_start]")
                or txt.startswith("[tool_end]")
                or txt.startswith("[message_summary]")
            ):
                continue
            filtered_lines.append(ln)
        assistant_part = "\n".join(filtered_lines).strip()
        if assistant_part:
            out.append({"role": "assistant", "content": assistant_part})

    # De-duplicate sequential duplicates (history + turn content overlap)
    deduped: list[dict] = []
    for msg in out:
        if deduped and deduped[-1]["role"] == msg["role"] and deduped[-1]["content"] == msg["content"]:
            continue
        deduped.append(msg)

    return deduped


def _extract_chat_message_runs(log_text: str) -> list[dict]:
    """
    Parse chat log into per-message execution graph nodes.
    Each node contains input/output, tool calls, thinking events and token usage.
    """
    blocks = re.finditer(
        r"=== Message at (?P<ts>[^\n=]+?)(?: id=(?P<id>[a-zA-Z0-9_-]+))? ===\n(?P<body>[\s\S]*?)(?=\n=== Message at |\Z)",
        log_text,
        flags=re.MULTILINE,
    )
    runs: list[dict] = []

    for i, m in enumerate(blocks):
        ts = (m.group("ts") or "").strip()
        msg_id = (m.group("id") or str(i + 1)).strip()
        body = m.group("body") or ""

        user_input = ""
        m_user = re.search(r"--- User message ---\n(?P<v>[\s\S]*?)\n--- Agent response", body, flags=re.MULTILINE)
        if m_user:
            user_input = (m_user.group("v") or "").strip()

        output = ""
        m_out = re.search(
            r"--- Agent response \((?:pending|stream)\) ---\n(?P<v>[\s\S]*?)(?=\nFinished:|\nStatus\s*:|\Z)",
            body,
            flags=re.MULTILINE,
        )
        if m_out:
            lines = []
            for ln in (m_out.group("v") or "").splitlines():
                txt = ln.strip()
                if not txt:
                    continue
                if (
                    txt.startswith("[llm_start]")
                    or txt.startswith("[llm_usage]")
                    or txt.startswith("[tool_start]")
                    or txt.startswith("[tool_end]")
                    or txt.startswith("[message_summary]")
                ):
                    continue
                lines.append(ln)
            output = "\n".join(lines).strip()

        tools: list[dict] = []
        tool_start_re = re.compile(r"^\[tool_start\]\s+step=(?P<step>\d+)\s+tool=(?P<tool>[^\s]+)\s+input=(?P<input>.*)$")
        tool_end_re = re.compile(r"^\[tool_end\]\s+output=(?P<output>.*)$")
        for ln in body.splitlines():
            s = ln.strip()
            ms = tool_start_re.match(s)
            if ms:
                tools.append({
                    "step": int(ms.group("step")),
                    "tool": ms.group("tool"),
                    "input": ms.group("input"),
                    "output": None,
                    "running": True,
                })
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

        inbound = 0
        outbound = 0
        total = 0
        tool_calls = len(tools)

        summary_re = re.search(
            r"\[message_summary\]\s+id=[^\s]+\s+inbound_tokens=(?P<in>\d+)\s+outbound_tokens=(?P<out>\d+)\s+total_tokens=(?P<tot>\d+)\s+tool_calls=(?P<tc>\d+)(?:\s+duration_ms=(?P<dur>\d+))?",
            body,
        )
        duration_ms = 0
        if summary_re:
            inbound = int(summary_re.group("in") or 0)
            outbound = int(summary_re.group("out") or 0)
            total = int(summary_re.group("tot") or 0)
            tool_calls = int(summary_re.group("tc") or tool_calls)
            duration_ms = int(summary_re.group("dur") or 0)
        else:
            usage_re = re.findall(
                r"\[llm_usage\]\s+prompt_tokens=(?P<in>\d+)\s+completion_tokens=(?P<out>\d+)\s+total_tokens=(?P<tot>\d+)",
                body,
            )
            for u in usage_re:
                inbound += int(u[0] or 0)
                outbound += int(u[1] or 0)
                total += int(u[2] or 0)

        runs.append({
            "message_id": msg_id,
            "timestamp": ts,
            "input": user_input,
            "output": output,
            "tools": tools,
            "thinking": thinking,
            "inbound_tokens": inbound,
            "outbound_tokens": outbound,
            "total_tokens": total or (inbound + outbound),
            "tool_calls": tool_calls,
            "duration_ms": duration_ms,
        })

    return runs


def _extract_tools_from_progress(run: dict) -> list[dict]:
    """
    Extract per-run tool activity by slicing workspace .progress.json by run time window.
    Falls back to empty if workspace/progress is not available.
    """
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
        if not st:
            continue
        if st < started or st > finished:
            continue

        result.append({
            "step": s.get("step"),
            "tool": s.get("tool"),
            "input": s.get("input"),
            "output": s.get("output"),
            "started_at": s.get("started_at"),
            "finished_at": s.get("finished_at"),
            "running": not bool(s.get("finished_at")),
        })

    result.sort(key=lambda x: (x.get("step") or 0, x.get("started_at") or ""))
    return result


def _extract_tools_from_log(log_text: str) -> list[dict]:
    """Fallback tool extraction from worker logs when .progress.json is unavailable."""
    step_re = re.compile(r"^\[(?P<model>[^\]]+)\]\s+Step\s+(?P<step>\d+):\s+(?P<tool>.+?)\s+←\s+(?P<input>.*)$")
    result_re = re.compile(r"^\[(?P<model>[^\]]+)\]\s+Result:\s+(?P<output>.*)$")
    stream_tool_start_re = re.compile(r"^\[tool_start\]\s+step=(?P<step>\d+)\s+tool=(?P<tool>[^\s]+)\s+input=(?P<input>.*)$")
    stream_tool_end_re = re.compile(r"^\[tool_end\]\s+output=(?P<output>.*)$")

    steps: list[dict] = []
    for ln in log_text.splitlines():
        m_step = step_re.match(ln.strip())
        if m_step:
            steps.append({
                "step": int(m_step.group("step")),
                "tool": m_step.group("tool").strip(),
                "input": m_step.group("input").strip(),
                "output": None,
                "started_at": None,
                "finished_at": None,
                "running": True,
            })
            continue

        m_stream_start = stream_tool_start_re.match(ln.strip())
        if m_stream_start:
            steps.append({
                "step": int(m_stream_start.group("step")),
                "tool": m_stream_start.group("tool").strip(),
                "input": m_stream_start.group("input").strip(),
                "output": None,
                "started_at": None,
                "finished_at": None,
                "running": True,
            })
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


def _build_thinking_trace(run: dict, tools: list[dict], log_text: str, messages: list[dict]) -> list[str]:
    """
    Build a lightweight, safe 'thinking trace' from observable execution events.
    This is an execution summary, not hidden chain-of-thought.
    """
    trace: list[str] = []

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


@router.get("")
async def list_sessions(
    workspace: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """List all sessions with optional filters."""
    runs = run_manager._load_runs()

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}

    enriched = [_enrich_run(r, tasks_by_id) for r in runs]

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

    enriched.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return enriched


@router.post("")
async def create_session(data: SessionCreate):
    """Create a new session: creates a task and immediately runs the given agent on it."""
    spec = registry.get_agent(data.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{data.agent_id}' not found")

    ws_name = None
    if data.workspace:
        try:
            p = create_workspace_folder(data.workspace)
            ws_name = p.name
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to resolve workspace: {e}")

    task = tasks_service.create_task(
        title=data.title,
        description=data.description,
        workspace=ws_name,
    )

    try:
        run_id = run_manager.start_run(str(task.id), data.agent_id, data.params)
        tasks_service.assign_agent(task.id, data.agent_id, data.params, run_id=run_id)
        tasks_service.set_agent_state(task.id, AgentState.running, run_id=run_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"run_id": run_id, "task_id": str(task.id)}


@router.get("/{run_id}")
async def get_session(run_id: str):
    """Get details for a single session."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Session not found")

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}
    return _enrich_run(run, tasks_by_id)


@router.get("/{run_id}/logs")
async def get_session_logs(run_id: str):
    """Return the log file content for a session."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Session not found")

    log_file = run.get("log_file")
    if not log_file or not Path(log_file).exists():
        return {"logs": ""}

    try:
        content = Path(log_file).read_text(encoding="utf-8", errors="replace")
        return {"logs": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{run_id}/insights")
async def get_session_insights(run_id: str):
    """Return session card insights: messages, tools, and thinking trace."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Session not found")

    all_tasks = tasks_service.list_tasks()
    tasks_by_id = {str(t.id): t for t in all_tasks}
    run = _enrich_run(run, tasks_by_id)

    log_text = ""
    log_file = run.get("log_file")
    if log_file:
        p = Path(log_file)
        if p.exists():
            try:
                # Keep payload bounded for the UI/API.
                log_text = p.read_text(encoding="utf-8", errors="replace")[-120_000:]
            except Exception:
                log_text = ""

    message_runs = _extract_chat_message_runs(log_text) if run.get("session_type") == "chat" else []

    if message_runs:
        messages = []
        tools = []
        thinking = []
        inbound_total = 0
        outbound_total = 0
        for mr in message_runs:
            if mr.get("input"):
                messages.append({"role": "user", "content": mr.get("input")})
            if mr.get("output"):
                messages.append({"role": "assistant", "content": mr.get("output")})
            tools.extend(mr.get("tools") or [])
            thinking.extend(mr.get("thinking") or [])
            inbound_total += int(mr.get("inbound_tokens") or 0)
            outbound_total += int(mr.get("outbound_tokens") or 0)
        thinking = thinking or _build_thinking_trace(run, tools, log_text, messages)
        token_usage = {
            "inbound_tokens": inbound_total,
            "outbound_tokens": outbound_total,
            "total_tokens": inbound_total + outbound_total,
        }
    else:
        messages = _extract_messages(log_text)
        tools = _extract_tools_from_progress(run)
        if not tools:
            tools = _extract_tools_from_log(log_text)
        thinking = _build_thinking_trace(run, tools, log_text, messages)
        token_usage = {
            "inbound_tokens": 0,
            "outbound_tokens": 0,
            "total_tokens": 0,
        }

    model = _resolve_session_model(run)
    context_window = _build_context_window_metrics(model, int(token_usage.get("inbound_tokens") or 0))

    return {
        "run_id": run_id,
        "session_type": run.get("session_type"),
        "model": model,
        "messages": messages,
        "tools": tools,
        "thinking": thinking,
        "message_runs": message_runs,
        "token_usage": token_usage,
        "context_window": context_window,
    }


@router.post("/{run_id}/stop")
async def stop_session(run_id: str):
    """Stop a running session."""
    run = run_manager.get_run_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Session not found")

    task_id = run.get("task_id")
    if not task_id:
        raise HTTPException(status_code=400, detail="Session has no associated task")

    stopped = run_manager.stop_run(task_id)
    return {"stopped": stopped}


@router.delete("/{run_id}")
async def delete_session(run_id: str, delete_log: bool = True):
    """Delete a session record. Running sessions must be stopped first."""
    runs = run_manager._load_runs()
    idx = next((i for i, r in enumerate(runs) if r.get("run_id") == run_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Session not found")

    run = runs[idx]
    if run.get("status") == "running":
        raise HTTPException(status_code=400, detail="Stop the running session before deleting it")

    log_file = run.get("log_file")
    runs.pop(idx)
    run_manager._save_runs(runs)

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
