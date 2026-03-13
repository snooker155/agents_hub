"""
Chat API – direct in-process conversation with an agent.

Each call receives the full conversation history so the agent has context.
The agent is created fresh per message using the factory (YAML-based agents only).
Remote agents are not supported for chat.

Requires at least one running node for the selected agent — enforced server-side.
Each message exchange is recorded as a run in agent_runs.json so it appears
in the Sessions list, complete with a log file containing the full exchange.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json
import math
import re
import time

from langchain_core.callbacks import BaseCallbackHandler

from agents.factory import create_agent
from agents import registry
from agents.node_manager import get_running_nodes_for_agent
from agents.run_manager import _upsert_run, _update_run, STATE_DIR, _load_runs
from models import ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _agent_overrides(agent_id: str) -> dict:
    """Read per-agent model overrides from registry default_params."""
    try:
        spec = registry.get_agent(agent_id)
        if not spec:
            return {}
        dp = dict(spec.default_params or {})
        overrides = {}
        provider = dp.get("provider")
        if provider and provider != "inherit":
            overrides["provider"] = provider
        for key in ("model", "base_url", "api_key", "temperature", "max_tokens"):
            if dp.get(key) is not None:
                overrides[key] = dp[key]
        return overrides
    except Exception:
        return {}

CHAT_LOGS_DIR = STATE_DIR / "chat_logs"
CHAT_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(log_file: Path, lines: list[str]) -> None:
    try:
        log_file.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def _append_log(log_lines: list[str], line: str, log_file: Path) -> None:
    log_lines.append(line)
    _write_log(log_file, log_lines)


def _validate_chat_request(request: ChatRequest):
    spec = registry.get_agent(request.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_id}' not found")

    if getattr(spec, "is_remote", False):
        raise HTTPException(status_code=400, detail="Remote agents do not support direct chat")

    running_nodes = get_running_nodes_for_agent(request.agent_id)
    if not running_nodes:
        raise HTTPException(
            status_code=400,
            detail=f"No running nodes for agent '{request.agent_id}'. Start a node first.",
        )
    return spec


def _build_chat_context(request: ChatRequest) -> tuple[str, str | None]:
    # Resolve workspace to absolute path
    workspace_abs: str | None = None
    if request.workspace:
        try:
            from common.workspace import create_workspace_folder
            workspace_abs = str(create_workspace_folder(request.workspace))
        except Exception:
            workspace_abs = None

    lines = []
    if request.history:
        lines.append("=== Conversation so far ===")
        for msg in request.history[-20:]:
            role = "User" if msg.role == "user" else "Assistant"
            lines.append(f"{role}: {msg.content}")
        lines.append("")
        lines.append("=== New message ===")
    lines.append(request.message)
    if request.attachments:
        lines.extend(["", "=== Attached files ==="])
        for idx, att in enumerate(request.attachments, start=1):
            content = (att.content or "")
            if len(content) > 40000:
                content = content[:40000] + "\n...[truncated]"
            stored_note = ""
            if getattr(att, "stored_workspace_path", None):
                stored_note = f" (stored at workspace path: {att.stored_workspace_path})"
            lines.append(f"[Attachment {idx}] {att.filename}{stored_note}")
            lines.append("```")
            lines.append(content)
            lines.append("```")
            lines.append("")
    full_prompt = "\n".join(lines)
    return full_prompt, workspace_abs


def _find_existing_chat_run(conversation_id: str):
    """Find latest chat run for a conversation id."""
    runs = _load_runs()
    candidates = [
        r for r in runs
        if r.get("session_type") == "chat" and str(r.get("task_id")) == str(conversation_id)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return candidates[0]


def _create_chat_run(request: ChatRequest):
    conv_id = request.conversation_id or str(uuid4())
    existing = _find_existing_chat_run(conv_id)
    run_id = existing.get("run_id") if existing else str(uuid4())
    title = request.conversation_title or (
        request.message[:60] + ("…" if len(request.message) > 60 else "")
    )
    existing_log = existing.get("log_file") if existing else None
    log_file = Path(existing_log) if existing_log else (CHAT_LOGS_DIR / f"chat_{run_id}.log")
    started = _utc_iso()

    if log_file.exists():
        try:
            log_lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            log_lines = []
    else:
        log_lines = [
            f"=== Chat session  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {request.agent_id}",
            f"Workspace : {request.workspace or '—'}",
            f"Conv ID   : {conv_id}",
            f"Title     : {title}",
            "",
        ]

    msg_id = str(uuid4())[:8]
    log_lines.extend([f"=== Message at {started} id={msg_id} ==="])
    if request.history:
        log_lines.append("--- Conversation history ---")
        for msg in request.history[-20:]:
            role = "User" if msg.role == "user" else "Assistant"
            log_lines.append(f"{role}: {msg.content}")
        log_lines.append("")
    log_lines.extend([
        "--- User message ---",
        request.message,
        "",
    ])
    if request.attachments:
        log_lines.append("--- Attachments ---")
        for idx, att in enumerate(request.attachments, start=1):
            bytes_len = len((att.content or "").encode("utf-8", errors="ignore"))
            stored = getattr(att, "stored_workspace_path", None)
            store_text = f"stored={stored}" if stored else f"store_requested={bool(att.store_to_workspace)}"
            log_lines.append(f"[{idx}] file={att.filename} bytes={bytes_len} {store_text}")
        log_lines.append("")
    log_lines.extend([
        "--- Agent response (stream) ---",
    ])
    _write_log(log_file, log_lines)

    run_rec = {
        "run_id": run_id,
        "task_id": conv_id,
        "agent_id": request.agent_id,
        "title": title,
        "session_type": "chat",
        "workspace": request.workspace,
        "pid": None,
        "status": "running",
        "started_at": existing.get("started_at") if existing else started,
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "log_file": str(log_file),
    }
    _upsert_run(run_rec)
    return run_id, msg_id, log_file, log_lines


class ChatStreamCallback(BaseCallbackHandler):
    """Callback handler that forwards LLM/tool execution events to an asyncio queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, log_lines: list[str], log_file: Path):
        self.loop = loop
        self.queue = queue
        self.log_lines = log_lines
        self.log_file = log_file
        self._step = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_calls = 0
        self._last_prompt_text = ""
        self._output_text_parts: list[str] = []

    def _emit(self, payload: dict):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)

    def on_llm_start(self, serialized, prompts, **kwargs):
        model_name = None
        if isinstance(serialized, dict):
            model_name = serialized.get("name")
        try:
            if isinstance(prompts, list):
                self._last_prompt_text = "\n".join(str(p) for p in prompts if p is not None)
            else:
                self._last_prompt_text = str(prompts or "")
        except Exception:
            self._last_prompt_text = ""
        line = f"[llm_start] model={model_name or 'unknown'}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "thinking", "message": line})

    def on_llm_new_token(self, token, **kwargs):
        if token:
            self._output_text_parts.append(str(token))
            self._emit({"type": "token", "token": token})

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        # Heuristic fallback when provider usage metadata is unavailable.
        if not text:
            return 0
        return max(1, int(math.ceil(len(text) / 4)))

    def on_llm_end(self, response, **kwargs):
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
            # More providers expose usage on AIMessage.usage_metadata.
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
            p = self._estimate_tokens(self._last_prompt_text)
            c = self._estimate_tokens("".join(self._output_text_parts))
            t = p + c
            estimated = True

        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t

        line = (
            f"[llm_usage] prompt_tokens={p} completion_tokens={c} total_tokens={t}"
            + (" estimated=true" if estimated else "")
        )
        _append_log(self.log_lines, line, self.log_file)
        self._emit({
            "type": "usage",
            "prompt_tokens": p,
            "completion_tokens": c,
            "total_tokens": t,
            "estimated": estimated,
        })

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._step += 1
        self.tool_calls += 1
        name = serialized.get("name") if isinstance(serialized, dict) else "tool"
        preview = str(input_str)
        if len(preview) > 240:
            preview = preview[:240] + "..."
        line = f"[tool_start] step={self._step} tool={name} input={preview}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "tool_start", "step": self._step, "tool": name, "input": str(input_str)})

    def on_tool_end(self, output, **kwargs):
        preview = str(output)
        if len(preview) > 240:
            preview = preview[:240] + "..."
        line = f"[tool_end] output={preview}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "tool_end", "output": str(output)})


def _safe_attachment_filename(filename: str, idx: int) -> str:
    base = Path(filename or "").name.strip()
    if not base:
        base = f"attachment_{idx}.txt"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not safe:
        safe = f"attachment_{idx}.txt"
    return safe[:120]


def _materialize_attachments(request: ChatRequest) -> None:
    if not request.attachments:
        return

    max_file_bytes = 200_000
    max_total_bytes = 700_000
    total = 0
    workspace_root = None

    for idx, att in enumerate(request.attachments, start=1):
        att.filename = _safe_attachment_filename(att.filename, idx)
        content_bytes = len((att.content or "").encode("utf-8", errors="ignore"))
        if content_bytes > max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment '{att.filename}' is too large ({content_bytes} bytes). Limit is {max_file_bytes} bytes.",
            )
        total += content_bytes
        if total > max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Total attachment size exceeds limit ({max_total_bytes} bytes).",
            )

        if att.store_to_workspace:
            if not request.workspace:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attachment '{att.filename}' is marked to store in workspace, but no workspace is selected.",
                )
            if workspace_root is None:
                from common.workspace import create_workspace_folder
                workspace_root = create_workspace_folder(request.workspace)

            uploads_dir = workspace_root / "chat_uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target = uploads_dir / f"{stamp}_{uuid4().hex[:8]}_{att.filename}"
            try:
                target.write_text(att.content or "", encoding="utf-8")
                att.stored_workspace_path = target.relative_to(workspace_root).as_posix()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to store attachment '{att.filename}': {e}")


@router.post("/message")
async def send_message(request: ChatRequest):
    """Send a message to an agent and get a response."""
    _validate_chat_request(request)
    _materialize_attachments(request)
    full_prompt, workspace_abs = _build_chat_context(request)
    run_id, _, log_file, log_lines = _create_chat_run(request)

    def _run_agent():
        overrides = _agent_overrides(request.agent_id)
        agent = create_agent(request.agent_id, workspace=workspace_abs, **overrides)
        return agent.run(full_prompt)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_agent),
            timeout=300,
        )
    except asyncio.TimeoutError:
        finished = _utc_iso()
        _write_log(log_file, log_lines + ["(timed out after 5 minutes)", "", f"Finished: {finished}"])
        _update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": "timed out"})
        raise HTTPException(status_code=504, detail="Agent timed out after 5 minutes")
    except FileNotFoundError:
        finished = _utc_iso()
        _write_log(log_file, log_lines + ["(no YAML definition)", "", f"Finished: {finished}"])
        _update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": "no YAML definition"})
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{request.agent_id}' has no YAML definition and cannot run in chat mode",
        )
    except Exception as e:
        finished = _utc_iso()
        _write_log(log_file, log_lines + [f"(error: {e})", "", f"Finished: {finished}"])
        _update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

    finished = _utc_iso()

    if result.ok:
        response_text = str(result.agent_output)
        _write_log(log_file, log_lines + [response_text, "", f"Finished: {finished}", "Status  : completed"])
        _update_run(run_id, {"status": "completed", "finished_at": finished, "exit_code": 0})
        return {"response": response_text, "ok": True, "run_id": run_id}

    error_text = result.error or "Agent returned no output"
    _write_log(log_file, log_lines + [f"(error: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
    _update_run(run_id, {"status": "failed", "finished_at": finished,
                         "exit_code": 1, "error": error_text})
    return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id}


@router.post("/stream")
async def stream_message(request: ChatRequest):
    """Stream chat response tokens and execution events (SSE)."""
    _validate_chat_request(request)
    _materialize_attachments(request)
    full_prompt, workspace_abs = _build_chat_context(request)
    run_id, msg_id, log_file, log_lines = _create_chat_run(request)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        callback = ChatStreamCallback(loop, queue, log_lines, log_file)
        message_started = time.perf_counter()

        token_parts: list[str] = []
        final_response: str = ""
        final_ok: bool = False
        final_error: str | None = None

        def _run_agent():
            overrides = _agent_overrides(request.agent_id)
            agent = create_agent(request.agent_id, workspace=workspace_abs, streaming=True, **overrides)
            return agent.run(full_prompt, callbacks=[callback])

        task = asyncio.create_task(asyncio.to_thread(_run_agent))

        try:
            yield f"data: {json.dumps({'type': 'meta', 'run_id': run_id})}\n\n"

            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if event.get("type") == "token":
                        tok = event.get("token") or ""
                        token_parts.append(tok)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Flush any queued events
            while not queue.empty():
                event = await queue.get()
                if event.get("type") == "token":
                    tok = event.get("token") or ""
                    token_parts.append(tok)
                yield f"data: {json.dumps(event)}\n\n"

            try:
                result = await asyncio.wait_for(task, timeout=1)
                if result.ok:
                    final_response = str(result.agent_output)
                    final_ok = True
                else:
                    final_error = result.error or "Agent returned no output"
            except Exception as e:
                final_error = str(e)

            if not final_response and token_parts:
                final_response = "".join(token_parts)
            if not final_response:
                final_response = "(no textual output)"

            finished = _utc_iso()
            duration_ms = int((time.perf_counter() - message_started) * 1000)
            if final_ok:
                _append_log(log_lines, final_response, log_file)
                _append_log(
                    log_lines,
                    (
                        f"[message_summary] id={msg_id} "
                        f"inbound_tokens={callback.prompt_tokens} "
                        f"outbound_tokens={callback.completion_tokens} "
                        f"total_tokens={callback.total_tokens} "
                        f"tool_calls={callback.tool_calls} "
                        f"duration_ms={duration_ms}"
                    ),
                    log_file,
                )
                _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : completed"])
                _update_run(run_id, {"status": "completed", "finished_at": finished, "exit_code": 0})
                yield (
                    "data: "
                    + json.dumps({
                        "type": "done",
                        "ok": True,
                        "response": final_response,
                        "run_id": run_id,
                        "usage": {
                            "inbound_tokens": callback.prompt_tokens,
                            "outbound_tokens": callback.completion_tokens,
                            "total_tokens": callback.total_tokens,
                        },
                        "tool_calls": callback.tool_calls,
                        "duration_ms": duration_ms,
                    })
                    + "\n\n"
                )
            else:
                err = final_error or "unknown error"
                _append_log(log_lines, f"(error: {err})", log_file)
                _append_log(
                    log_lines,
                    (
                        f"[message_summary] id={msg_id} "
                        f"inbound_tokens={callback.prompt_tokens} "
                        f"outbound_tokens={callback.completion_tokens} "
                        f"total_tokens={callback.total_tokens} "
                        f"tool_calls={callback.tool_calls} "
                        f"duration_ms={duration_ms}"
                    ),
                    log_file,
                )
                _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : failed"])
                _update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1, "error": err})
                yield (
                    "data: "
                    + json.dumps({
                        "type": "done",
                        "ok": False,
                        "response": f"Error: {err}",
                        "error": err,
                        "run_id": run_id,
                        "usage": {
                            "inbound_tokens": callback.prompt_tokens,
                            "outbound_tokens": callback.completion_tokens,
                            "total_tokens": callback.total_tokens,
                        },
                        "tool_calls": callback.tool_calls,
                        "duration_ms": duration_ms,
                    })
                    + "\n\n"
                )
        except asyncio.CancelledError:
            finished = _utc_iso()
            _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
            _update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
            raise
        except Exception as e:
            finished = _utc_iso()
            _write_log(log_file, log_lines + [f"(stream error: {e})", "", f"Finished: {finished}", "Status  : failed"])
            _update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1, "error": str(e)})
            yield f"data: {json.dumps({'type': 'done', 'ok': False, 'response': f'Error: {e}', 'error': str(e), 'run_id': run_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
