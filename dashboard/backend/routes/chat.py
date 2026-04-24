"""
Chat API – direct in-process conversation with an agent.

Each call receives the full conversation history so the agent has context.
The agent is created fresh per message using the factory (YAML-based agents only).
Remote agents are not supported for chat.

No running node is required — each message runs the agent on-request inside the
server process.  Each message exchange is recorded as a run in agent_runs.json
so it appears in the Sessions list, complete with a log file.
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

from agents.agent_factory import create_agent
from agents import registry
from agents.run_manager import (
    CHAT_LOGS_DIR,
    new_unique_run_id,
    open_run as register_run,
    update_run,
    get_run_by_id as get_run,
)
from models import ChatRequest
from common.session_service import get_or_create_chat_session

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _agent_overrides(agent_id: str) -> dict:
    """Read per-agent model overrides from the registry AgentSpec fields."""
    try:
        spec = registry.get_agent(agent_id)
        if not spec:
            return {}
        overrides = {}
        # spec.provider / model / base_url are set via the Agent > Model tab in the UI.
        # default_params is always {} — the real overrides live as top-level spec fields.
        if spec.provider and spec.provider != "inherit":
            overrides["provider"] = spec.provider
        if spec.model:
            overrides["model"] = spec.model
        if spec.base_url:
            overrides["base_url"] = spec.base_url
        if spec.api_key:
            overrides["api_key"] = spec.api_key
        if spec.temperature is not None:
            overrides["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            overrides["max_tokens"] = spec.max_tokens
        return overrides
    except Exception:
        return {}


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


def _to_json_safe(value, *, depth: int = 0, max_depth: int = 5):
    """Best-effort conversion of callback payloads to JSON-safe structures."""
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
    # Pydantic models and similar objects
    for meth in ("model_dump", "dict"):
        fn = getattr(value, meth, None)
        if callable(fn):
            try:
                return _to_json_safe(fn(), depth=depth + 1, max_depth=max_depth)
            except Exception:
                pass
    # Generic object fallback
    if hasattr(value, "__dict__"):
        try:
            return _to_json_safe(vars(value), depth=depth + 1, max_depth=max_depth)
        except Exception:
            pass
    return str(value)


def _validate_chat_request(request: ChatRequest):
    spec = registry.get_agent(request.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_id}' not found")

    if getattr(spec, "is_remote", False):
        raise HTTPException(status_code=400, detail="Remote agents do not support direct chat")

    return spec


def _build_chat_context(request: ChatRequest) -> tuple[str, str | None]:
    # Resolve workspace to absolute path
    workspace_abs: str | None = None
    if request.workspace:
        try:
            from workspace import create_workspace_folder
            workspace_abs = str(create_workspace_folder(request.workspace))
        except Exception:
            workspace_abs = None

    # Include bounded conversation history, then latest user message.
    history_lines: list[str] = []
    budget = 60_000
    for msg in reversed(request.history[-40:]):
        role = "User" if str(msg.role) == "user" else "Assistant"
        content = str(msg.content or "")
        if len(content) > 4000:
            content = content[:4000] + "\n...[truncated]"
        line = f"{role}: {content}"
        if budget - len(line) < 0:
            break
        budget -= len(line)
        history_lines.insert(0, line)

    lines = []
    if history_lines:
        lines.extend([
            "Use the conversation history for context when answering the latest user message.",
            "",
            "Conversation history:",
            *history_lines,
            "",
            "Latest user message:",
        ])
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


def _create_chat_run(request: ChatRequest):
    """Create a new run record for each chat message exchange."""
    conv_id = request.conversation_id or str(uuid4())
    run_id = new_unique_run_id()
    run_title = request.message[:60] + ("…" if len(request.message) > 60 else "")
    session_title = request.conversation_title or run_title
    log_file = CHAT_LOGS_DIR / f"chat_{run_id}.log"
    started = _utc_iso()

    # Ensure/create a session context for this conversation
    try:
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=session_title,
            workspace=request.workspace,
            agent_id=request.agent_id,
        )
    except Exception:
        session_id = None

    msg_id = str(uuid4())[:8]
    log_lines = [
        f"=== Chat message  run_id={run_id} ===",
        f"Started   : {started}",
        f"Agent     : {request.agent_id}",
        f"Workspace : {request.workspace or '—'}",
        f"Conv ID   : {conv_id}",
        f"Session ID: {session_id or '—'}",
        f"Title     : {run_title}",
        "",
        f"=== Message at {started} id={msg_id} ===",
    ]
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

    register_run(
        run_id,
        request.agent_id,
        task_id=conv_id,
        session_id=session_id,
        session_type="chat",
        message_origin="chat",
        workspace=request.workspace,
        title=run_title,
        log_file=str(log_file),
    )

    return run_id, msg_id, log_file, log_lines, session_id


class ChatStreamCallback(BaseCallbackHandler):
    """Callback handler that forwards LLM/tool execution events to an asyncio queue."""

    # Tell LangChain to propagate exceptions raised in callbacks instead of swallowing them.
    raise_error: bool = True

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue,
        log_lines: list[str],
        log_file: Path,
        session_id: str | None = None,
    ):
        self.loop = loop
        self.queue = queue
        self.log_lines = log_lines
        self.log_file = log_file
        self.session_id = session_id
        self._step = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.tool_calls = 0
        self._last_prompt_text = ""
        self._output_text_parts: list[str] = []
        # Full process graph data collected during the run
        self.tool_history: list[dict] = []
        self.thinking_history: list[str] = []
        self.llm_invoke_responses: list[dict] = []
        self._pending_tool: dict | None = None
        self.cancelled: bool = False  # set True to interrupt LLM streaming mid-generation

    def _emit(self, payload: dict):
        # Forward to the local SSE queue for the active HTTP response.
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        # Also publish to the session broker so /api/sessions/{id}/stream
        # subscribers (e.g. continuation SSE connections) receive the same events.
        if self.session_id:
            try:
                from common.session_broker import broker
                broker.publish_threadsafe(self.session_id, payload)
            except Exception:
                pass

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
        self.thinking_history.append(line)
        self._emit({"type": "thinking", "message": line})

    def on_llm_new_token(self, token, **kwargs):
        if self.cancelled:
            return
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
        try:
            raw_payload = {
                "response_type": response.__class__.__name__,
                "llm_output": _to_json_safe(getattr(response, "llm_output", None)),
                "generations": _to_json_safe(getattr(response, "generations", None)),
            }
            self.llm_invoke_responses.append(raw_payload)
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
        input_full = str(input_str)
        preview = input_full
        if len(preview) > 240:
            preview = preview[:240] + "..."
        line = f"[tool_start] step={self._step} tool={name} input={preview}"
        _append_log(self.log_lines, line, self.log_file)
        self._pending_tool = {"step": self._step, "tool": name, "input": input_full}
        self._emit({"type": "tool_start", "step": self._step, "tool": name, "input": input_full})

    def on_tool_end(self, output, **kwargs):
        if self.cancelled:
            return
        output_full = str(output)
        preview = output_full
        if len(preview) > 240:
            preview = preview[:240] + "..."
        line = f"[tool_end] output={preview}"
        _append_log(self.log_lines, line, self.log_file)
        if self._pending_tool is not None:
            entry = dict(self._pending_tool)
            entry["output"] = output_full
            self.tool_history.append(entry)
            self._pending_tool = None
        self._emit({"type": "tool_end", "output": output_full})

    def on_llm_error(self, error, **kwargs):
        line = f"[llm_error] {type(error).__name__}: {error}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "error", "source": "llm", "error": str(error)})

    def on_tool_error(self, error, **kwargs):
        tool_name = (self._pending_tool or {}).get("tool", "unknown")
        line = f"[tool_error] tool={tool_name} {type(error).__name__}: {error}"
        _append_log(self.log_lines, line, self.log_file)
        if self._pending_tool is not None:
            entry = dict(self._pending_tool)
            entry["output"] = f"ERROR: {error}"
            self.tool_history.append(entry)
            self._pending_tool = None
        self._emit({"type": "tool_error", "tool": tool_name, "error": str(error)})

    def on_chain_error(self, error, **kwargs):
        line = f"[chain_error] {type(error).__name__}: {error}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "error", "source": "chain", "error": str(error)})


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
                from workspace import create_workspace_folder
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
    run_id, _, log_file, log_lines, __ = _create_chat_run(request)

    def _run_agent():
        overrides = _agent_overrides(request.agent_id)
        agent = create_agent(request.agent_id, workspace=workspace_abs, **overrides)
        return agent.run(full_prompt, run_id)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_agent),
            timeout=300,
        )
    except asyncio.TimeoutError:
        finished = _utc_iso()
        _write_log(log_file, log_lines + ["(timed out after 5 minutes)", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": "timed out"})
        raise HTTPException(status_code=504, detail="Agent timed out after 5 minutes")
    except FileNotFoundError:
        finished = _utc_iso()
        _write_log(log_file, log_lines + ["(no YAML definition)", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": "no YAML definition"})
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{request.agent_id}' has no YAML definition and cannot run in chat mode",
        )
    except Exception as e:
        finished = _utc_iso()
        _write_log(log_file, log_lines + [f"(error: {e})", "", f"Finished: {finished}"])
        update_run(run_id, {"status": "failed", "finished_at": finished,
                             "exit_code": 1, "error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))

    finished = _utc_iso()

    current = get_run(run_id) or {}
    if current.get("status") == "stop":
        _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
        update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "stopped by user"})
        return {"response": "Stopped by user", "ok": False, "run_id": run_id}

    if result.ok:
        response_text = str(result.agent_output)
        _write_log(log_file, log_lines + [response_text, "", f"Finished: {finished}", "Status  : completed"])
        update_run(run_id, {"status": "completed", "finished_at": finished, "exit_code": 0})
        return {"response": response_text, "ok": True, "run_id": run_id}

    error_text = result.error or "Agent returned no output"
    _write_log(log_file, log_lines + [f"(error: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
    update_run(run_id, {"status": "failed", "finished_at": finished,
                         "exit_code": 1, "error": error_text})
    return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id}


@router.post("/stream")
async def stream_message(request: ChatRequest):
    """Stream chat response tokens and execution events (SSE)."""
    _validate_chat_request(request)
    _materialize_attachments(request)
    full_prompt, workspace_abs = _build_chat_context(request)
    run_id, msg_id, log_file, log_lines, session_id = _create_chat_run(request)

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)
        message_started = time.perf_counter()

        token_parts: list[str] = []
        final_response: str = ""
        final_ok: bool = False
        final_error: str | None = None

        async def _run_agent_async():
            overrides = _agent_overrides(request.agent_id)
            agent = create_agent(request.agent_id, workspace=workspace_abs, streaming=True, **overrides)
            return await agent.arun(full_prompt, callbacks=[callback])

        task = asyncio.create_task(_run_agent_async())

        try:
            meta_event = {"type": "meta", "run_id": run_id, "session_id": session_id}
            yield f"data: {json.dumps(meta_event)}\n\n"
            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    if event.get("type") == "token":
                        tok = event.get("token") or ""
                        token_parts.append(tok)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    current_status = (get_run(run_id) or {}).get("status")
                    if current_status in ("stop", "stopped"):
                        # Cancel the asyncio task — CancelledError is injected at the next
                        # await inside ainvoke(), interrupting the live LLM HTTP request.
                        callback.cancelled = True
                        task.cancel()
                        break
                    continue

            # Flush any queued events emitted before cancellation
            while not queue.empty():
                event = await queue.get()
                if event.get("type") == "token":
                    tok = event.get("token") or ""
                    token_parts.append(tok)
                yield f"data: {json.dumps(event)}\n\n"

            try:
                result = await task
                if result.ok:
                    final_response = str(result.agent_output)
                    final_ok = True
                else:
                    final_error = result.error or "Agent returned no output"
            except asyncio.CancelledError:
                pass  # task was cancelled by stop signal — handled below
            except Exception as e:
                final_error = str(e)

            if not final_response and token_parts:
                final_response = "".join(token_parts)
            if not final_response:
                final_response = "(no textual output)"

            finished = _utc_iso()
            duration_ms = int((time.perf_counter() - message_started) * 1000)
            current = get_run(run_id) or {}
            if current.get("status") in ("stop", "stopped") or callback.cancelled:
                _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
                update_run(run_id, {
                    "status": "stopped",
                    "finished_at": finished,
                    "exit_code": 1,
                    "error": "stopped by user",
                    "process": {
                        "llm_input_context": callback._last_prompt_text or full_prompt,
                        "tool_calls": callback.tool_history,
                        "thinking": callback.thinking_history,
                        "llm_invoke_responses": callback.llm_invoke_responses,
                        "token_usage": {
                            "inbound_tokens": callback.prompt_tokens,
                            "outbound_tokens": callback.completion_tokens,
                            "total_tokens": callback.total_tokens,
                        },
                        "duration_ms": duration_ms,
                    },
                })
                yield (
                    "data: "
                    + json.dumps({
                        "type": "done",
                        "ok": False,
                        "response": "Stopped by user",
                        "error": "stopped by user",
                        "run_id": run_id,
                        "session_id": session_id,
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
                return
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
                update_run(run_id, {
                    "status": "completed",
                    "finished_at": finished,
                    "exit_code": 0,
                    "process": {
                        "llm_input_context": callback._last_prompt_text or full_prompt,
                        "tool_calls": callback.tool_history,
                        "thinking": callback.thinking_history,
                        "llm_invoke_responses": callback.llm_invoke_responses,
                        "token_usage": {
                            "inbound_tokens": callback.prompt_tokens,
                            "outbound_tokens": callback.completion_tokens,
                            "total_tokens": callback.total_tokens,
                        },
                        "duration_ms": duration_ms,
                    },
                })
                yield (
                    "data: "
                    + json.dumps({
                        "type": "done",
                        "ok": True,
                        "response": final_response,
                        "run_id": run_id,
                        "session_id": session_id,
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
                update_run(run_id, {
                    "status": "failed",
                    "finished_at": finished,
                    "exit_code": 1,
                    "error": err,
                    "process": {
                        "llm_input_context": callback._last_prompt_text or full_prompt,
                        "tool_calls": callback.tool_history,
                        "thinking": callback.thinking_history,
                        "llm_invoke_responses": callback.llm_invoke_responses,
                        "token_usage": {
                            "inbound_tokens": callback.prompt_tokens,
                            "outbound_tokens": callback.completion_tokens,
                            "total_tokens": callback.total_tokens,
                        },
                        "duration_ms": duration_ms,
                    },
                })
                yield (
                    "data: "
                    + json.dumps({
                        "type": "done",
                        "ok": False,
                        "response": f"Error: {err}",
                        "error": err,
                        "run_id": run_id,
                        "session_id": session_id,
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
            callback.cancelled = True  # stop the LLM thread if still running
            finished = _utc_iso()
            _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
            update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
            raise
        except Exception as e:
            finished = _utc_iso()
            _write_log(log_file, log_lines + [f"(stream error: {e})", "", f"Finished: {finished}", "Status  : failed"])
            update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1, "error": str(e)})
            yield f"data: {json.dumps({'type': 'done', 'ok': False, 'response': f'Error: {e}', 'error': str(e), 'run_id': run_id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
