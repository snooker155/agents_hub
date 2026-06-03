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
import base64
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
from common.orchestrator_context import _workspace_ctx
from common import artifact_sink

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _get_pool_id(agent_id: str) -> str | None:
    """Return the shared memory pool id for agent_id, or None."""
    try:
        spec = registry.get_agent(agent_id)
        if spec and spec.memory_type == "shared" and spec.memory_data:
            return str(spec.memory_data)
    except Exception:
        pass
    return None


def _auto_journal(agent_id: str, pool_id: str | None, user_message: str, response: str, run_id: str) -> None:
    """Fire-and-forget silent journal append + interaction episode for agents with shared memory."""
    if not pool_id:
        return
    try:
        from memory.tool import silent_journal_append, silent_interaction_episode
        silent_journal_append(pool_id, agent_id, user_message, response, run_id=run_id)
        silent_interaction_episode(pool_id, agent_id, user_message, response, run_id=run_id)
    except Exception:
        pass
    # Auto-extraction is off by default; opt-in via GRAPH_AUTO_EXTRACT env flag.
    try:
        from memory.graph_extract import silent_graph_extract
        silent_graph_extract(pool_id, user_message, response)
    except Exception:
        pass


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


def _format_tool_payload(value) -> str:
    """Render a tool input/output as a compact, single-line readable string.

    Tool args arrive as dicts (or their str() repr) and outputs as JSON-ish
    strings. Normalize structured payloads into compact valid JSON so the line
    is readable, but keep it on ONE line — these log lines are later re-parsed
    line-by-line (=== Message blocks, tool_start/tool_end regexes), so newlines
    would leak the payload into the response field and break tool extraction.
    """
    import ast

    def _compact(obj) -> str:
        return json.dumps(_to_json_safe(obj), ensure_ascii=False, separators=(", ", ": "))

    # Already structured (e.g. LangChain passes a dict of tool args).
    if isinstance(value, (dict, list, tuple)):
        try:
            return _compact(value)
        except Exception:
            return str(value)
    text = str(value)
    stripped = text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        # Try real JSON first, then a Python literal (single-quoted dicts).
        for parser in (json.loads, ast.literal_eval):
            try:
                return _compact(parser(stripped))
            except Exception:
                continue
    return text


_MAX_DIFF_BYTES = 200_000  # guard against diffing huge files into the SSE stream


def _build_artifact(op: str, path: str, before: str | None, after: str | None) -> dict:
    """Build an artifact record with a unified diff and add/delete line counts.

    ``before``/``after`` are full file text, or None when the file did not exist
    (add: before=None; delete: after=None). Non-text or oversized changes are
    recorded with the op/path but an empty diff and a ``binary`` flag so the UI
    can still list the file.
    """
    import difflib

    b = before if before is not None else ""
    a = after if after is not None else ""

    too_big = len(b) > _MAX_DIFF_BYTES or len(a) > _MAX_DIFF_BYTES
    if too_big:
        return {
            "op": op,
            "path": path,
            "diff": "",
            "additions": 0,
            "deletions": 0,
            "binary": True,
            "truncated": True,
        }

    b_lines = b.splitlines(keepends=True)
    a_lines = a.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            b_lines,
            a_lines,
            fromfile=("/dev/null" if before is None else f"a/{path}"),
            tofile=("/dev/null" if after is None else f"b/{path}"),
            n=3,
        )
    )
    diff_text = "".join(diff_lines)
    additions = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    deletions = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )
    return {
        "op": op,
        "path": path,
        "diff": diff_text,
        "additions": additions,
        "deletions": deletions,
        "binary": False,
    }


def _validate_chat_request(request: ChatRequest):
    if request.flow_id:
        # Flow target validated lazily in the flow pipeline.
        return None
    spec = registry.get_agent(request.agent_id)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Agent '{request.agent_id}' not found")

    return spec


def _load_flow_definition(flow_id: str) -> dict:
    """Load a flow from agents/state/flows.json or raise 404."""
    from pathlib import Path as _Path
    flows_file = _Path(__file__).resolve().parents[3] / "agents" / "state" / "flows.json"
    if not flows_file.exists():
        raise HTTPException(status_code=404, detail=f"Flow '{flow_id}' not found")
    try:
        flows = json.loads(flows_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read flows.json: {e}")
    flow = next((f for f in flows if f.get("id") == flow_id), None)
    if not flow:
        raise HTTPException(status_code=404, detail=f"Flow '{flow_id}' not found")
    return flow


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
            stored_note = ""
            if getattr(att, "stored_workspace_path", None):
                stored_note = f" (stored at workspace path: {att.stored_workspace_path})"
            is_binary = bool(getattr(att, "content_b64", None))
            mime = getattr(att, "mime_type", None) or ""
            mime_note = f" mime={mime}" if mime else ""
            lines.append(f"[Attachment {idx}] {att.filename}{stored_note}{mime_note}")
            if is_binary:
                # Don't dump binary bytes into the prompt; reference the stored path.
                lines.append("(binary file — see stored workspace path above)")
            else:
                content = (att.content or "")
                if len(content) > 40000:
                    content = content[:40000] + "\n...[truncated]"
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
            if getattr(att, "content_b64", None):
                # base64 length is ~4/3 of the binary size; rough estimate is fine for the log.
                bytes_len = int(len(att.content_b64) * 3 / 4)
                kind = "binary"
            else:
                bytes_len = len((att.content or "").encode("utf-8", errors="ignore"))
                kind = "text"
            stored = getattr(att, "stored_workspace_path", None)
            store_text = f"stored={stored}" if stored else f"store_requested={bool(att.store_to_workspace)}"
            log_lines.append(f"[{idx}] file={att.filename} kind={kind} bytes={bytes_len} {store_text}")
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
        self.artifact_history: list[dict] = []  # file changes (diffs) made this run
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
        formatted = _format_tool_payload(input_str)
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        line = f"[tool_start] step={self._step} tool={name} input={formatted}"
        _append_log(self.log_lines, line, self.log_file)
        self._pending_tool = {"step": self._step, "tool": name, "input": input_full}
        # The reasoning tools (think/plan) are pass-through scratchpads: their
        # input *is* the content. Surface them as dedicated events so the UI can
        # render a reasoning/plan panel instead of a generic tool call, and skip
        # the generic tool_start/tool_end (the echoed output adds nothing).
        if name in ("think", "plan"):
            self._emit({"type": name, "step": self._step, "content": input_full})
        else:
            self._emit({"type": "tool_start", "step": self._step, "tool": name, "input": input_full})

    def on_tool_end(self, output, **kwargs):
        if self.cancelled:
            return
        output_full = str(output)
        formatted = _format_tool_payload(output)
        if len(formatted) > 2000:
            formatted = formatted[:2000] + "... (truncated)"
        line = f"[tool_end] output={formatted}"
        _append_log(self.log_lines, line, self.log_file)
        pending = self._pending_tool
        if pending is not None:
            entry = dict(pending)
            entry["output"] = output_full
            self.tool_history.append(entry)
            self._pending_tool = None
        # think/plan already surfaced their content on tool_start; no generic end.
        if pending is not None and pending.get("tool") in ("think", "plan"):
            return
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

    def record_artifact(self, op: str, path: str, before: str | None, after: str | None) -> None:
        """Receive a file change from the filesystem tools (via the artifact sink).

        Builds a diff record, stores it for the run's process payload, and emits
        an ``artifact`` SSE event so the UI can render the diff live. Called from
        the agent's execution thread, so it must be thread-safe — _emit already
        marshals onto the event loop.
        """
        try:
            artifact = _build_artifact(op, path, before, after)
        except Exception:
            return
        self.artifact_history.append(artifact)
        line = f"[artifact] op={op} path={path} +{artifact.get('additions', 0)} -{artifact.get('deletions', 0)}"
        _append_log(self.log_lines, line, self.log_file)
        self._emit({"type": "artifact", **artifact})


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

    max_file_bytes = 5 * 1024 * 1024   # 5 MB per file
    max_total_bytes = 5 * 1024 * 1024  # 5 MB total
    total = 0
    workspace_root = None

    for idx, att in enumerate(request.attachments, start=1):
        att.filename = _safe_attachment_filename(att.filename, idx)

        # Resolve the actual payload bytes — binary (content_b64) takes priority
        # over text (content) when both are present.
        binary_bytes: bytes | None = None
        if att.content_b64:
            try:
                binary_bytes = base64.b64decode(att.content_b64, validate=False)
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Attachment '{att.filename}' has invalid base64: {e}",
                )
            content_bytes = len(binary_bytes)
        else:
            content_bytes = len((att.content or "").encode("utf-8", errors="ignore"))

        if content_bytes > max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment '{att.filename}' is too large ({content_bytes} bytes). Limit is 5 MB per file.",
            )
        total += content_bytes
        if total > max_total_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Total attachment size exceeds 5 MB.",
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
                if binary_bytes is not None:
                    target.write_bytes(binary_bytes)
                else:
                    target.write_text(att.content or "", encoding="utf-8")
                att.stored_workspace_path = target.relative_to(workspace_root).as_posix()
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to store attachment '{att.filename}': {e}")


@router.post("/message")
async def send_message(request: ChatRequest):
    """Send a message to an agent and get a response."""
    if request.flow_id:
        raise HTTPException(
            status_code=400,
            detail="Flow chat requires the streaming endpoint (/api/chat/stream)",
        )
    _validate_chat_request(request)
    _materialize_attachments(request)
    full_prompt, workspace_abs = _build_chat_context(request)
    run_id, _, log_file, log_lines, __ = _create_chat_run(request)

    # Propagate workspace to agent tools (e.g. list_tasks) via a context var
    # that is thread-safe and copied into asyncio.to_thread's execution context.
    ws_name = request.workspace or (Path(workspace_abs).name if workspace_abs else None)
    _workspace_ctx.set(ws_name)

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
        _auto_journal(request.agent_id, _get_pool_id(request.agent_id), request.message, response_text, run_id)
        return {"response": response_text, "ok": True, "run_id": run_id}

    error_text = result.error or "Agent returned no output"
    _write_log(log_file, log_lines + [f"(error: {error_text})", "", f"Finished: {finished}", "Status  : failed"])
    update_run(run_id, {"status": "failed", "finished_at": finished,
                         "exit_code": 1, "error": error_text})
    return {"response": f"Error: {error_text}", "ok": False, "run_id": run_id}


async def run_chat_pipeline(request: ChatRequest):
    """
    Drive the full chat run lifecycle and yield events as dicts.

    Validates the request, materializes attachments, builds the prompt,
    records a run, executes the agent with streaming callbacks, and emits
    the same events that the SSE endpoint forwards to the browser. The
    Telegram adapter consumes the dict stream directly to assemble its
    own reply, so both surfaces share one execution path.

    Yielded event shapes:
    - {"type": "meta", "run_id", "session_id"}
    - callback events: token / thinking / tool_start / tool_end / tool_error / usage / error
    - {"type": "done", "ok", "response", "error", "run_id", "session_id", "usage", "tool_calls", "duration_ms"}
    """
    _validate_chat_request(request)
    _materialize_attachments(request)
    full_prompt, workspace_abs = _build_chat_context(request)
    run_id, msg_id, log_file, log_lines, session_id = _create_chat_run(request)

    ws_name = request.workspace or (Path(workspace_abs).name if workspace_abs else None)
    _workspace_ctx.set(ws_name)

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

    # Install the artifact recorder so filesystem tools report file changes as
    # diffs through this run's callback. create_task copies the current context,
    # so setting it here propagates into the agent execution (and any threads it
    # spawns via copy_context). Reset after the task is scheduled.
    _artifact_token = artifact_sink.set_recorder(callback.record_artifact)
    task = asyncio.create_task(_run_agent_async())
    artifact_sink.reset_recorder(_artifact_token)

    yield {"type": "meta", "run_id": run_id, "session_id": session_id}

    try:
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.2)
                if event.get("type") == "token":
                    token_parts.append(event.get("token") or "")
                yield event
            except asyncio.TimeoutError:
                current_status = (get_run(run_id) or {}).get("status")
                if current_status in ("stop", "stopped"):
                    callback.cancelled = True
                    task.cancel()
                    break
                continue

        while not queue.empty():
            event = await queue.get()
            if event.get("type") == "token":
                token_parts.append(event.get("token") or "")
            yield event

        try:
            result = await task
            if result.ok:
                final_response = str(result.agent_output)
                final_ok = True
            else:
                final_error = result.error or "Agent returned no output"
        except asyncio.CancelledError:
            pass
        except Exception as e:
            final_error = str(e)

        if not final_response and token_parts:
            final_response = "".join(token_parts)
        if not final_response:
            final_response = "(no textual output)"

        finished = _utc_iso()
        duration_ms = int((time.perf_counter() - message_started) * 1000)
        current = get_run(run_id) or {}
        usage = {
            "inbound_tokens": callback.prompt_tokens,
            "outbound_tokens": callback.completion_tokens,
            "total_tokens": callback.total_tokens,
        }
        process_payload = {
            "llm_input_context": callback._last_prompt_text or full_prompt,
            "tool_calls": callback.tool_history,
            "thinking": callback.thinking_history,
            "llm_invoke_responses": callback.llm_invoke_responses,
            "artifacts": callback.artifact_history,
            "token_usage": usage,
            "duration_ms": duration_ms,
        }

        if current.get("status") in ("stop", "stopped") or callback.cancelled:
            _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
            update_run(run_id, {
                "status": "stopped",
                "finished_at": finished,
                "exit_code": 1,
                "error": "stopped by user",
                "process": process_payload,
            })
            yield {
                "type": "done",
                "ok": False,
                "response": "Stopped by user",
                "error": "stopped by user",
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
            return

        summary_line = (
            f"[message_summary] id={msg_id} "
            f"inbound_tokens={callback.prompt_tokens} "
            f"outbound_tokens={callback.completion_tokens} "
            f"total_tokens={callback.total_tokens} "
            f"tool_calls={callback.tool_calls} "
            f"duration_ms={duration_ms}"
        )

        if final_ok:
            _append_log(log_lines, final_response, log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : completed"])
            _auto_journal(request.agent_id, _get_pool_id(request.agent_id), request.message, final_response, run_id)
            update_run(run_id, {
                "status": "completed",
                "finished_at": finished,
                "exit_code": 0,
                "process": process_payload,
            })
            yield {
                "type": "done",
                "ok": True,
                "response": final_response,
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
        else:
            err = final_error or "unknown error"
            _append_log(log_lines, f"(error: {err})", log_file)
            _append_log(log_lines, summary_line, log_file)
            _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : failed"])
            update_run(run_id, {
                "status": "failed",
                "finished_at": finished,
                "exit_code": 1,
                "error": err,
                "process": process_payload,
            })
            yield {
                "type": "done",
                "ok": False,
                "response": f"Error: {err}",
                "error": err,
                "run_id": run_id,
                "session_id": session_id,
                "usage": usage,
                "tool_calls": callback.tool_calls,
                "duration_ms": duration_ms,
            }
    except asyncio.CancelledError:
        callback.cancelled = True
        finished = _utc_iso()
        _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
        update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
        raise
    except Exception as e:
        finished = _utc_iso()
        _write_log(log_file, log_lines + [f"(stream error: {e})", "", f"Finished: {finished}", "Status  : failed"])
        update_run(run_id, {"status": "failed", "finished_at": finished, "exit_code": 1, "error": str(e)})
        yield {"type": "done", "ok": False, "response": f"Error: {e}", "error": str(e), "run_id": run_id}


async def run_chat_flow_pipeline(request: ChatRequest):
    """
    Drive a multi-agent flow conversation in-process and yield SSE events.

    Each user message is processed by every node in the flow's DAG (topological
    order). Predecessor outputs are passed forward via the shared-context block
    that run_flow.py uses. The full shared conversation history (user+assistant
    turns across all nodes) is included so each agent has the full context.

    Yielded events extend the single-agent shape with per-node markers:
    - {"type": "flow_meta", "flow_id", "flow_name", "session_id", "nodes": [{node_id, agent_id, label}]}
    - {"type": "node_start", "node_id", "agent_id", "agent_label", "run_id"}
    - token / thinking / tool_start / tool_end / tool_error / usage / error events
      (each carries node_id / agent_id so the frontend can route them to the right bubble)
    - {"type": "node_done", "node_id", "run_id", "ok", "response", "error", "usage", "tool_calls", "duration_ms"}
    - {"type": "done", "ok", "flow_id", "session_id", "run_id" (last node), "responses": [{node_id, response}]}
    """
    # Local imports keep the surface narrow and avoid circulars at module load.
    from agents.agent_factory import create_agent
    from agents.run_manager import (
        STATE_DIR,
        open_run as register_run,
        update_run as _update_run,
        get_run_by_id as get_run,
    )
    from common.session_service import get_or_create_chat_session, add_run_to_session
    from collections import deque

    if not request.flow_id:
        raise HTTPException(status_code=400, detail="flow_id is required for flow chat")

    _materialize_attachments(request)
    flow = _load_flow_definition(request.flow_id)
    nodes: list[dict] = flow.get("nodes", []) or []
    edges: list[dict] = flow.get("edges", []) or []

    # Filter to nodes that resolve to a real agent.
    _FACTORY_AGENT_MAP = {
        "factory-pm": "pm_agent", "factory-ba": "ba_agent",
        "factory-sd": "sd_agent", "factory-tl": "tl_agent",
        "factory-be": "dev_agent", "factory-fe": "dev_agent",
        "factory-qa": "qa_agent", "factory-ops": "devops_agent",
    }

    def _resolve_node_agent(node: dict) -> str | None:
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        agent_id = node.get("agent_id") or data.get("agent_id")
        if not agent_id:
            return None
        return _FACTORY_AGENT_MAP.get(agent_id, agent_id)

    def _topo_order() -> list[str]:
        adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
        indeg: dict[str, int] = {n["id"]: 0 for n in nodes}
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s in adj and t in indeg:
                adj[s].append(t)
                indeg[t] += 1
        q = deque(nid for nid, d in indeg.items() if d == 0)
        ordered: list[str] = []
        while q:
            cur = q.popleft()
            ordered.append(cur)
            for nb in adj.get(cur, []):
                indeg[nb] -= 1
                if indeg[nb] == 0:
                    q.append(nb)
        return ordered if len(ordered) == len(nodes) else [n["id"] for n in nodes]

    def _predecessors() -> dict[str, list[str]]:
        preds: dict[str, list[str]] = {}
        for e in edges:
            s, t = e.get("source"), e.get("target")
            if s and t:
                preds.setdefault(t, []).append(s)
        return preds

    order = _topo_order()
    preds = _predecessors()
    runnable_node_ids = [nid for nid in order
                        if _resolve_node_agent(next((n for n in nodes if n["id"] == nid), {}))]

    if not runnable_node_ids:
        raise HTTPException(status_code=400, detail="Flow has no nodes with an assigned agent")

    # Resolve workspace
    workspace_abs: str | None = None
    if request.workspace:
        try:
            from workspace import create_workspace_folder
            workspace_abs = str(create_workspace_folder(request.workspace))
        except Exception:
            workspace_abs = None
    ws_name = request.workspace or (Path(workspace_abs).name if workspace_abs else None)
    _workspace_ctx.set(ws_name)

    # One session per conversation, same as agent chat
    conv_id = request.conversation_id or str(uuid4())
    flow_name = flow.get("name") or request.flow_id
    session_title = request.conversation_title or f"Flow: {flow_name}"
    try:
        session_id = get_or_create_chat_session(
            conversation_id=conv_id,
            title=session_title,
            workspace=request.workspace,
            agent_id=f"flow:{request.flow_id}",
        )
    except Exception:
        session_id = None

    # Build the shared history block (same shape as _build_chat_context but
    # without the trailing latest-user-message — we add it in per-node prompts)
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

    attachment_lines: list[str] = []
    if request.attachments:
        attachment_lines.append("=== Attached files ===")
        for idx, att in enumerate(request.attachments, start=1):
            stored_note = ""
            if getattr(att, "stored_workspace_path", None):
                stored_note = f" (stored at workspace path: {att.stored_workspace_path})"
            is_binary = bool(getattr(att, "content_b64", None))
            mime = getattr(att, "mime_type", None) or ""
            mime_note = f" mime={mime}" if mime else ""
            attachment_lines.append(f"[Attachment {idx}] {att.filename}{stored_note}{mime_note}")
            if is_binary:
                attachment_lines.append("(binary file — see stored workspace path above)")
            else:
                content = (att.content or "")
                if len(content) > 40000:
                    content = content[:40000] + "\n...[truncated]"
                attachment_lines.extend(["```", content, "```"])
            attachment_lines.append("")

    user_message = request.message
    # node_outputs maps node_id -> textual output produced this turn so successors can read them
    node_outputs: dict[str, str] = {}
    node_run_ids: dict[str, str] = {}

    # Emit the flow meta once so the frontend can scaffold per-node bubbles
    flow_meta_nodes = []
    for nid in runnable_node_ids:
        node = next((n for n in nodes if n["id"] == nid), None)
        if not node:
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        label = node.get("label") or data.get("label") or _resolve_node_agent(node) or nid
        flow_meta_nodes.append({
            "node_id": nid,
            "agent_id": _resolve_node_agent(node),
            "agent_label": label,
        })

    yield {
        "type": "flow_meta",
        "flow_id": request.flow_id,
        "flow_name": flow_name,
        "session_id": session_id,
        "nodes": flow_meta_nodes,
    }

    # Mirror execution events into the shared flow log so the Flow editor's
    # "Logs" tab shows chat-driven runs the same way it shows runFlow() runs.
    # Format matches run_flow.py / routes.flows so the existing UI parses it.
    flow_log_path = STATE_DIR / "flow_logs" / f"{request.flow_id}.json"

    # All turns of one flow chat conversation collapse into a single History
    # record, so every event is tagged with the conversation id as its run_group
    # and kind="chat" (vs kind="task" emitted by run_flow.py).
    def _log_flow_event(payload: dict) -> None:
        payload.setdefault("run_group", conv_id)
        payload.setdefault("kind", "chat")
        try:
            flow_log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                existing = json.loads(flow_log_path.read_text(encoding="utf-8")) if flow_log_path.exists() else []
            except Exception:
                existing = []
            existing.append(payload)
            flow_log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    _log_flow_event({
        "timestamp": _utc_iso(),
        "type": "flow_start",
        "content": f"Starting flow: {flow_name}",
        "status": "running",
        "title": session_title,
        "session_id": session_id,
        "conversation_id": conv_id,
    })

    overall_started = time.perf_counter()
    any_failure = False
    last_run_id: str | None = None
    responses: list[dict] = []

    for node_id in runnable_node_ids:
        node = next((n for n in nodes if n["id"] == node_id), None)
        if not node:
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        yaml_agent_id = _resolve_node_agent(node)
        agent_label = node.get("label") or data.get("label") or yaml_agent_id
        node_task = node.get("nodeTask") or data.get("nodeTask") or ""

        # Per-node run record
        run_id = new_unique_run_id()
        last_run_id = run_id
        node_run_ids[node_id] = run_id
        log_file = CHAT_LOGS_DIR / f"chat_flow_{run_id}.log"
        started = _utc_iso()
        run_title = f"{agent_label}: {user_message[:50]}" + ("…" if len(user_message) > 50 else "")
        # Log format mirrors the standard agent chat log so the chat-log parser in
        # routes/messages.py (_extract_chat_message_runs) picks up tool calls,
        # thinking entries, and token usage for the message-insights UI.
        node_msg_id = str(uuid4())[:8]
        log_lines = [
            f"=== Chat message  run_id={run_id} ===",
            f"Started   : {started}",
            f"Agent     : {yaml_agent_id}",
            f"Workspace : {request.workspace or '—'}",
            f"Conv ID   : {conv_id}",
            f"Session ID: {session_id or '—'}",
            f"Flow      : {flow_name} ({request.flow_id})",
            f"Node      : {node_id} / {agent_label}",
            f"Title     : {run_title}",
            "",
            f"=== Message at {started} id={node_msg_id} ===",
            f"Agent: {yaml_agent_id}",
            "",
            "--- User message ---",
            user_message,
            "",
            "--- Agent response (stream) ---",
        ]
        _write_log(log_file, log_lines)

        register_run(
            run_id,
            yaml_agent_id,
            task_id=conv_id,
            session_id=session_id,
            session_type="chat",
            message_origin="chat",
            workspace=request.workspace,
            title=run_title,
            log_file=str(log_file),
            flow_id=request.flow_id,
            flow_node_id=node_id,
            flow_node_label=agent_label,
        )

        # Build this node's prompt. Downstream nodes (those with predecessor
        # outputs this turn) work ONLY from the previous agents' output — the
        # user message and chat history are intentionally not forwarded to them.
        # Root nodes (no predecessors) receive the user message + chat history.
        prompt_parts: list[str] = []
        pred_outputs = [(pid, node_outputs[pid]) for pid in preds.get(node_id, []) if pid in node_outputs]
        if pred_outputs:
            prompt_parts.extend([
                "=" * 60,
                "OUTPUT FROM PREVIOUS AGENTS IN THIS FLOW (this turn)",
                "=" * 60,
            ])
            for pid, out in pred_outputs:
                pred_label = next(
                    (m["agent_label"] for m in flow_meta_nodes if m["node_id"] == pid),
                    pid,
                )
                prompt_parts.append(f"\n[{pred_label}]:\n{out}\n")
            prompt_parts.extend(["=" * 60, "", "Continue the work based on the above output."])
        else:
            if history_lines:
                prompt_parts.extend([
                    "Use the conversation history for context when answering the latest user message.",
                    "",
                    "Conversation history:",
                    *history_lines,
                    "",
                ])
            prompt_parts.extend(["Latest user message:", user_message])

        if node_task:
            prompt_parts.extend(["", "## Your specific task for this step:", node_task])
        if attachment_lines:
            prompt_parts.append("")
            prompt_parts.extend(attachment_lines)
        full_prompt = "\n".join(prompt_parts)

        try:
            _update_run(run_id, {"input": full_prompt})
        except Exception:
            pass

        node_domain = node.get("domain") or data.get("domain") or "general"
        _log_flow_event({
            "timestamp": _utc_iso(),
            "type": "agent_start",
            "node_id": node_id,
            "agent_id": yaml_agent_id,
            "agent_name": agent_label,
            "tag": node_domain,
            "content": f"Running {agent_label}",
            "status": "running",
            "input": full_prompt,
        })

        yield {
            "type": "node_start",
            "node_id": node_id,
            "agent_id": yaml_agent_id,
            "agent_label": agent_label,
            "run_id": run_id,
            "session_id": session_id,
        }
        # Also emit the legacy "meta" event so the existing token/tool handling
        # in older clients still has a run_id to anchor to.
        yield {"type": "meta", "run_id": run_id, "session_id": session_id, "node_id": node_id}

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        callback = ChatStreamCallback(loop, queue, log_lines, log_file, session_id=session_id)
        node_started_ts = time.perf_counter()
        token_parts: list[str] = []
        final_response: str = ""
        final_ok: bool = False
        final_error: str | None = None

        async def _run_node_agent():
            agent = create_agent(yaml_agent_id, workspace=workspace_abs, streaming=True)
            return await agent.arun(full_prompt, callbacks=[callback])

        # Per-node artifact recorder so each node's file changes are attributed to
        # its own run (and tagged with node_id when emitted — see _emit tagging below).
        _node_artifact_token = artifact_sink.set_recorder(callback.record_artifact)
        task = asyncio.create_task(_run_node_agent())
        artifact_sink.reset_recorder(_node_artifact_token)

        try:
            while not task.done():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                    # Tag events with the current node so the UI can route them
                    enriched = {**event, "node_id": node_id, "agent_id": yaml_agent_id}
                    if event.get("type") == "token":
                        token_parts.append(event.get("token") or "")
                    yield enriched
                except asyncio.TimeoutError:
                    current_status = (get_run(run_id) or {}).get("status")
                    if current_status in ("stop", "stopped"):
                        callback.cancelled = True
                        task.cancel()
                        any_failure = True
                        break
                    continue

            while not queue.empty():
                event = await queue.get()
                enriched = {**event, "node_id": node_id, "agent_id": yaml_agent_id}
                if event.get("type") == "token":
                    token_parts.append(event.get("token") or "")
                yield enriched

            try:
                result = await task
                if result.ok:
                    final_response = str(result.agent_output)
                    final_ok = True
                else:
                    final_error = result.error or "Agent returned no output"
            except asyncio.CancelledError:
                pass
            except Exception as e:
                final_error = str(e)

            if not final_response and token_parts:
                final_response = "".join(token_parts)
            if not final_response:
                final_response = "(no textual output)"

            finished = _utc_iso()
            node_duration_ms = int((time.perf_counter() - node_started_ts) * 1000)
            usage = {
                "inbound_tokens": callback.prompt_tokens,
                "outbound_tokens": callback.completion_tokens,
                "total_tokens": callback.total_tokens,
            }
            process_payload = {
                "llm_input_context": callback._last_prompt_text or full_prompt,
                "tool_calls": callback.tool_history,
                "thinking": callback.thinking_history,
                "llm_invoke_responses": callback.llm_invoke_responses,
                "artifacts": callback.artifact_history,
                "token_usage": usage,
                "duration_ms": node_duration_ms,
            }

            current = get_run(run_id) or {}
            stopped = current.get("status") in ("stop", "stopped") or callback.cancelled

            summary_line = (
                f"[message_summary] id={node_msg_id} "
                f"inbound_tokens={callback.prompt_tokens} "
                f"outbound_tokens={callback.completion_tokens} "
                f"total_tokens={callback.total_tokens} "
                f"tool_calls={callback.tool_calls} "
                f"duration_ms={node_duration_ms}"
            )

            if stopped:
                any_failure = True
                _append_log(log_lines, summary_line, log_file)
                _write_log(log_file, log_lines + ["(stopped by user)", "", f"Finished: {finished}", "Status  : stopped"])
                _update_run(run_id, {
                    "status": "stopped",
                    "finished_at": finished,
                    "exit_code": 1,
                    "error": "stopped by user",
                    "process": process_payload,
                })
                _log_flow_event({
                    "timestamp": finished,
                    "type": "agent_stopped",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_name": agent_label,
                    "tag": node_domain,
                    "content": f"{agent_label} stopped by user",
                    "status": "stopped",
                    "input": full_prompt,
                    "output": "Stopped by user",
                })
                yield {
                    "type": "node_done",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "run_id": run_id,
                    "ok": False,
                    "response": "Stopped by user",
                    "error": "stopped by user",
                    "usage": usage,
                    "tool_calls": callback.tool_calls,
                    "duration_ms": node_duration_ms,
                }
                # Short-circuit the rest of the flow when stopped
                break

            if final_ok:
                _append_log(log_lines, final_response, log_file)
                _append_log(log_lines, summary_line, log_file)
                _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : completed"])
                _update_run(run_id, {
                    "status": "completed",
                    "finished_at": finished,
                    "exit_code": 0,
                    "process": process_payload,
                })
                node_outputs[node_id] = final_response
                responses.append({
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_label": agent_label,
                    "response": final_response,
                })
                _log_flow_event({
                    "timestamp": finished,
                    "type": "agent_finish",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_name": agent_label,
                    "tag": node_domain,
                    "content": f"Completed {agent_label}",
                    "status": "completed",
                    "input": full_prompt,
                    "output": final_response,
                })
                yield {
                    "type": "node_done",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_label": agent_label,
                    "run_id": run_id,
                    "ok": True,
                    "response": final_response,
                    "usage": usage,
                    "tool_calls": callback.tool_calls,
                    "duration_ms": node_duration_ms,
                }
            else:
                any_failure = True
                err = final_error or "unknown error"
                _append_log(log_lines, f"(error: {err})", log_file)
                _append_log(log_lines, summary_line, log_file)
                _write_log(log_file, log_lines + ["", f"Finished: {finished}", "Status  : failed"])
                _update_run(run_id, {
                    "status": "failed",
                    "finished_at": finished,
                    "exit_code": 1,
                    "error": err,
                    "process": process_payload,
                })
                responses.append({
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_label": agent_label,
                    "response": f"Error: {err}",
                    "error": err,
                })
                _log_flow_event({
                    "timestamp": finished,
                    "type": "agent_error",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_name": agent_label,
                    "tag": node_domain,
                    "content": f"{agent_label} failed: {err}",
                    "status": "failed",
                    "input": full_prompt,
                    "output": err,
                })
                yield {
                    "type": "node_done",
                    "node_id": node_id,
                    "agent_id": yaml_agent_id,
                    "agent_label": agent_label,
                    "run_id": run_id,
                    "ok": False,
                    "response": f"Error: {err}",
                    "error": err,
                    "usage": usage,
                    "tool_calls": callback.tool_calls,
                    "duration_ms": node_duration_ms,
                }
        except asyncio.CancelledError:
            callback.cancelled = True
            try:
                task.cancel()
            except Exception:
                pass
            finished = _utc_iso()
            _write_log(log_file, log_lines + ["(cancelled)", "", f"Finished: {finished}", "Status  : stopped"])
            _update_run(run_id, {"status": "stopped", "finished_at": finished, "exit_code": 1, "error": "cancelled"})
            raise

    total_duration_ms = int((time.perf_counter() - overall_started) * 1000)
    _log_flow_event({
        "timestamp": _utc_iso(),
        "type": "flow_stopped" if any_failure else "flow_finish",
        "content": f"Flow '{flow_name}' " + ("stopped" if any_failure else "finished"),
        "status": "stopped" if any_failure else "completed",
    })
    yield {
        "type": "done",
        "ok": not any_failure,
        "flow_id": request.flow_id,
        "flow_name": flow_name,
        "session_id": session_id,
        "run_id": last_run_id,
        "responses": responses,
        "duration_ms": total_duration_ms,
    }


@router.post("/stream")
async def stream_message(request: ChatRequest):
    """Stream chat response tokens and execution events (SSE)."""
    pipeline = run_chat_flow_pipeline(request) if request.flow_id else run_chat_pipeline(request)

    async def event_stream():
        async for event in pipeline:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

