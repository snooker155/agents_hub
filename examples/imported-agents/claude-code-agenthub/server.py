"""Claude Code behind the Agents Hub HTTP contract.

Claude Code (https://github.com/anthropics/claude-code) is Anthropic's own
finished, tested coding agent CLI, with its own prompt, its own tool loop and
its own model access. None of that is reimplemented here: this file is the
whole integration. It runs `claude -p <prompt> --output-format stream-json
--verbose` in the run's workspace and translates the CLI's own JSON lines into
the hub's frame vocabulary, one message at a time.

The contract implemented here is the one declared in ``agent-hub.json``:

    POST /run         {"prompt": str, "run_id": str|null, "workspace": str|null}
                  ->  {"ok": bool, "output": str, "error": str|null, "steps": [...]}
    POST /run/stream  same body -> NDJSON frames, one JSON object per line
    GET  /health      200

What `claude -p --output-format stream-json --verbose` actually prints (as of
the CLI installed while writing this adapter; verify with `claude --help` and
a live run against your own version, since the CLI's own JSON shape is not a
versioned public contract):

    {"type": "system", "subtype": "init", "session_id": "...", "model": "...", ...}
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {...}}
    ]}, "session_id": "..."}
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "..."}
    ]}, "session_id": "..."}
    {"type": "result", "subtype": "success", "is_error": false,
     "result": "the final answer", "num_turns": 4, "session_id": "...",
     "total_cost_usd": 0.0412,
     "usage": {"input_tokens": 812, "output_tokens": 340,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 6100}}

The translation, in full:

- an assistant `text` block becomes a ``token`` frame
- an assistant `thinking` block becomes a ``thinking`` frame
- an assistant `tool_use` block becomes a ``tool_start`` frame; its id is
  remembered so the matching `tool_result` (which carries no tool name, only
  the id it answers) can be reported as the right tool's ``tool_end``
- the closing `result` message becomes a ``usage`` frame carrying
  ``cost_usd``. Claude Code prices its own call and reports the dollar
  figure directly, which the hub prefers over pricing token counts against
  its own catalog (see ``agents.remote_agent._extract_reported_cost``), plus
  a ``done`` frame with the final answer

``ClaudeCodeTranslator`` is kept pure and stateful-but-side-effect-free (it
only turns one CLI event into hub frames; it performs no I/O) so it can be
imported and fed recorded lines directly in tests, without a real `claude`
process or a real API key.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Claude Code: Agents Hub adapter")

# Repository Claude Code edits when a run does not name a workspace of its own.
DEFAULT_WORKDIR = Path(os.environ.get("CLAUDE_WORKDIR", "/work"))

# Hard ceiling on a single run, independent of the hub's own timeout, so a
# wedged claude process cannot hold a worker forever.
RUN_TIMEOUT = int(os.environ.get("CLAUDE_RUN_TIMEOUT", "1800"))


class RunRequest(BaseModel):
    prompt: str
    run_id: Optional[str] = None
    workspace: Optional[str] = None


def _resolve_workdir(workspace: Optional[str]) -> Path:
    """Pick the git repository Claude Code will edit.

    A workspace path handed over by the hub is only honoured when it exists in
    this container. The hub and the agent may well not share a filesystem, and
    silently running against the wrong tree is worse than falling back.
    """
    if workspace:
        candidate = Path(workspace)
        if candidate.is_dir():
            return candidate
    return DEFAULT_WORKDIR


def _claude_command(prompt: str, *, output_format: str) -> List[str]:
    """Build the claude invocation for a single non-interactive turn.

    ``--permission-mode bypassPermissions`` is what makes the run unattended:
    without it the CLI blocks on a tool-permission prompt that nobody in this
    container is there to answer, the same role ``--yes-always`` plays for
    aider. This container *is* the sandbox: the repository it edits is a
    throwaway mount, which is the situation that flag is for.
    """
    cmd = ["claude", "-p", prompt, "--output-format", output_format, "--verbose",
           "--permission-mode", "bypassPermissions"]
    model = (os.environ.get("CLAUDE_MODEL") or "").strip()
    if model:
        cmd += ["--model", model]
    return cmd


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ── translation: CLI JSON lines -> hub frames ───────────────────────────────

def _tool_input_str(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _content_text(content: Any) -> str:
    """A tool_result's content is either a bare string or a list of content
    blocks (the same shape the Messages API uses everywhere else)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return "" if content is None else str(content)


class ClaudeCodeTranslator:
    """Turns Claude Code's own `stream-json` events into hub frames.

    Stateful only to bridge a `tool_use` block to the `tool_result` that
    answers it later: the result carries the call's id, not its name, so the
    name has to be remembered from when the call started. Nothing else here
    carries state across events.
    """

    def __init__(self) -> None:
        self._tool_names: Dict[str, str] = {}
        # The CLI's own final figure, once the closing `result` event has been
        # seen. Exposed for a caller that wants to read it without re-parsing
        # the frames (the synchronous /run endpoint does).
        self.total_cost_usd: Optional[float] = None

    def feed(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Translate one parsed CLI event. Returns the hub frames it produces,
        possibly none. An event type this adapter does not know yet (the CLI
        may grow one) is skipped rather than guessed at."""
        if not isinstance(event, dict):
            return []
        kind = event.get("type")
        if kind == "assistant":
            return self._on_assistant(event)
        if kind == "user":
            return self._on_user(event)
        if kind == "result":
            return self._on_result(event)
        return []

    def _on_assistant(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        frames: List[Dict[str, Any]] = []
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                if text:
                    frames.append({"type": "token", "token": text})
            elif btype == "thinking":
                text = block.get("thinking") or block.get("text") or ""
                if text:
                    frames.append({"type": "thinking", "message": text})
            elif btype == "tool_use":
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "tool")
                if tool_id:
                    self._tool_names[tool_id] = name
                frames.append({
                    "type": "tool_start",
                    "name": name,
                    "input": _tool_input_str(block.get("input")),
                })
        return frames

    def _on_user(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        frames: List[Dict[str, Any]] = []
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id") or "")
            name = self._tool_names.pop(tool_id, "tool")
            frames.append({
                "type": "tool_end",
                "name": name,
                "output": _content_text(block.get("content")),
            })
        return frames

    def _on_result(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        usage = event.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_creation = int(usage.get("cache_creation_input_tokens") or 0)
        # The hub's `cached_tokens` means specifically a cache *read* (billed
        # at a fraction of the input rate); a cache *write* is billed above the
        # ordinary input rate, so it is folded into the fresh input count
        # rather than reported as cached. Reporting it as cached would price
        # this run as cheaper than Claude Code was actually charged for it.
        prompt_tokens = input_tokens + cache_creation

        usage_frame: Dict[str, Any] = {
            "type": "usage",
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
            "cached_tokens": cache_read,
        }
        total_cost = event.get("total_cost_usd")
        if isinstance(total_cost, (int, float)):
            self.total_cost_usd = float(total_cost)
            usage_frame["cost_usd"] = self.total_cost_usd

        is_error = bool(event.get("is_error"))
        result_text = event.get("result")
        output = result_text if isinstance(result_text, str) else ""

        done_frame: Dict[str, Any] = {
            "type": "done",
            "ok": not is_error,
            "output": output,
            "error": None,
        }
        if is_error:
            done_frame["error"] = output or str(event.get("subtype") or "claude reported an error")

        return [usage_frame, done_frame]


# ── health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    workdir_ok = DEFAULT_WORKDIR.is_dir()
    return {
        "status": "ok",
        "agent": "claude-code",
        "workdir": str(DEFAULT_WORKDIR),
        "workdir_present": workdir_ok,
        "model": os.environ.get("CLAUDE_MODEL") or "claude default",
    }


# ── synchronous run ─────────────────────────────────────────────────────────

@app.post("/run")
def run(req: RunRequest) -> Dict[str, Any]:
    """Run one Claude Code turn and report the outcome.

    Uses `--output-format json` (a single JSON object on stdout, not a
    stream) since nothing here is listening for the frames a streamed run
    would produce. Token/cost accounting is only wired for the streaming path
    (see ``docs/imported-agents.md``): a caller of this endpoint gets Claude
    Code's own totals inside the response body, but the hub does not credit
    them to the run: the same limitation the bundled aider example documents
    for its own synchronous endpoint.
    """
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        return {
            "ok": False, "output": "",
            "error": f"Working directory {workdir} does not exist in this container. "
                     f"Mount a git repository there or set CLAUDE_WORKDIR.",
        }

    cmd = _claude_command(req.prompt, output_format="json")
    try:
        result = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"claude exceeded the {RUN_TIMEOUT}s run timeout"}
    except FileNotFoundError:
        return {
            "ok": False, "output": "",
            "error": "claude is not installed in this container (npm install -g @anthropic-ai/claude-code)",
        }

    stdout = (result.stdout or "").strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        payload = {}

    if not payload:
        stderr = (result.stderr or "").strip()
        return {
            "ok": False, "output": "",
            "error": stderr or f"claude exited with code {result.returncode} and produced no JSON",
        }

    is_error = bool(payload.get("is_error"))
    output = payload.get("result") if isinstance(payload.get("result"), str) else ""
    response: Dict[str, Any] = {
        "ok": not is_error,
        "output": output,
        "error": None if not is_error else (output or "claude reported an error"),
        "steps": [{"name": "claude", "args": {"command": shlex.join(cmd)}, "output": output[:4000]}],
    }
    usage = payload.get("usage") or {}
    total_cost = payload.get("total_cost_usd")
    if usage or isinstance(total_cost, (int, float)):
        response["usage"] = {
            "prompt_tokens": int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "cached_tokens": int(usage.get("cache_read_input_tokens") or 0),
        }
        if isinstance(total_cost, (int, float)):
            response["usage"]["cost_usd"] = float(total_cost)
    return response


# ── streaming ────────────────────────────────────────────────────────────────

def _frame(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _stream_claude(prompt: str, workdir: Path) -> Iterator[str]:
    """Run claude, translating its stream-json events into hub frames as they
    arrive. stderr is drained on a side thread (rather than folded into
    stdout, as the aider example does) because Claude Code's stdout is
    already structured JSON that must not be interleaved with anything else."""
    cmd = _claude_command(prompt, output_format="stream-json")
    yield _frame({"type": "thinking", "message": f"$ {shlex.join(cmd)}"})

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(workdir), env=_child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        yield _frame({
            "type": "done", "ok": False, "output": "",
            "error": "claude is not installed in this container (npm install -g @anthropic-ai/claude-code)",
        })
        return

    stderr_tail: List[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_tail.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    translator = ClaudeCodeTranslator()
    saw_result = False
    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                saw_result = True
            for frame in translator.feed(event):
                yield _frame(frame)
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        yield _frame({
            "type": "done", "ok": False, "output": "",
            "error": f"claude exceeded the {RUN_TIMEOUT}s run timeout",
        })
        return
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        proc.wait()
        yield _frame({"type": "done", "ok": False, "output": "", "error": f"{type(exc).__name__}: {exc}"})
        return
    finally:
        stderr_thread.join(timeout=2)

    if not saw_result:
        # The process exited without ever sending a `result` event: a crash,
        # or a version whose JSON shape has moved. Whatever it explained on
        # stderr is the best available error.
        detail = "".join(stderr_tail).strip()[:2000]
        yield _frame({
            "type": "done", "ok": proc.returncode == 0, "output": "",
            "error": detail or f"claude exited with code {proc.returncode} without a result event",
        })


@app.post("/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        def _missing() -> Iterator[str]:
            yield _frame({
                "type": "done", "ok": False, "output": "",
                "error": f"Working directory {workdir} does not exist in this container. "
                         f"Mount a git repository there or set CLAUDE_WORKDIR.",
            })
        return StreamingResponse(_missing(), media_type="application/x-ndjson")

    return StreamingResponse(
        _stream_claude(req.prompt, workdir),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
