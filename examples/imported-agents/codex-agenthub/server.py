"""OpenAI Codex behind the Agents Hub HTTP contract.

Codex (https://github.com/openai/codex) is OpenAI's own coding agent CLI, with
its own prompt, its own tool loop and its own model access. None of that is
reimplemented here: this file is the whole integration. It runs
`codex exec --json <prompt>` in the run's workspace and translates the CLI's
own JSONL events into the hub's frame vocabulary.

The contract implemented here is the one declared in ``agent-hub.json``:

    POST /run         {"prompt": str, "run_id": str|null, "workspace": str|null}
                  ->  {"ok": bool, "output": str, "error": str|null, "steps": [...]}
    POST /run/stream  same body -> NDJSON frames, one JSON object per line
    GET  /health      200

**Unverified against a live run.** The `codex` CLI was not installed in the
environment this adapter was written in, so the event shapes below are
implemented from the documented facts rather than a captured transcript:

    {"type": "thread.started", "thread_id": "..."}
    {"type": "turn.started"}
    {"type": "item.started",   "item": {"id": "...", "type": "command_execution", "command": "..."}}
    {"type": "item.completed", "item": {"id": "...", "type": "command_execution",
                                         "exit_code": 0, "aggregated_output": "..."}}
    {"type": "item.completed", "item": {"id": "...", "type": "reasoning", "text": "..."}}
    {"type": "item.completed", "item": {"id": "...", "type": "agent_message", "text": "..."}}
    {"type": "item.completed", "item": {"id": "...", "type": "file_change", "changes": [...]}}
    {"type": "turn.completed", "usage": {"input_tokens": N, "cached_input_tokens": N,
                                          "output_tokens": N}}

Before relying on this in production, run `codex exec --help` and a real
`codex exec --json "..."` against the installed version and adjust
``CodexTranslator`` (and, if the non-interactive flag has a different name,
``_codex_command``) to match. The test suite for this adapter
(``tests/test_bundled_agents.py``) feeds it exactly the events documented
above and would need updating too. Unlike Claude Code, Codex is not known to
report a dollar cost of its own; this adapter reports tokens only, and the hub
prices the run from those against its own catalog, same as any other agent
that reports usage but no cost.
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

app = FastAPI(title="Codex: Agents Hub adapter")

DEFAULT_WORKDIR = Path(os.environ.get("CODEX_WORKDIR", "/work"))
RUN_TIMEOUT = int(os.environ.get("CODEX_RUN_TIMEOUT", "1800"))


class RunRequest(BaseModel):
    prompt: str
    run_id: Optional[str] = None
    workspace: Optional[str] = None


def _resolve_workdir(workspace: Optional[str]) -> Path:
    if workspace:
        candidate = Path(workspace)
        if candidate.is_dir():
            return candidate
    return DEFAULT_WORKDIR


def _codex_command(prompt: str) -> List[str]:
    """Build the codex invocation for a single non-interactive turn.

    ``codex exec`` is documented as the automation-friendly subcommand (as
    opposed to the interactive TUI), but this CLI was not available to verify
    whether it still pauses for command-approval in an unattended container.
    ``CODEX_ARGS`` is the escape hatch: set it to whatever your installed
    version's `codex exec --help` names for that (an approval-mode or sandbox
    flag) without needing to edit this file.
    """
    cmd = ["codex", "exec", "--json", prompt]
    model = (os.environ.get("CODEX_MODEL") or "").strip()
    if model:
        cmd += ["--model", model]
    extra = (os.environ.get("CODEX_ARGS") or "").strip()
    if extra:
        cmd += shlex.split(extra)
    return cmd


def _child_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ── translation: CLI JSONL events -> hub frames ─────────────────────────────

def _describe_file_change(item: Dict[str, Any]) -> str:
    changes = item.get("changes")
    if not isinstance(changes, list):
        return ""
    parts = []
    for c in changes:
        if isinstance(c, dict):
            parts.append(f"{c.get('kind', 'change')} {c.get('path', '')}".strip())
    return "; ".join(p for p in parts if p)


class CodexTranslator:
    """Turns Codex's own `exec --json` events into hub frames.

    Stateful for two reasons: a `command_execution`/`file_change` item is
    reported as a ``tool_start``/``tool_end`` pair, so its label is kept
    between the `item.started` and `item.completed` events; and the final
    answer is accumulated from every `agent_message` item so the closing
    ``done`` frame has something to report even if a run has several turns.
    """

    def __init__(self) -> None:
        self._open_items: Dict[str, str] = {}
        self._final_text_parts: List[str] = []

    def feed(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(event, dict):
            return []
        kind = event.get("type")
        if kind == "item.started":
            return self._on_item_started(event)
        if kind == "item.completed":
            return self._on_item_completed(event)
        if kind == "turn.completed":
            return self._on_turn_completed(event)
        if kind in ("turn.failed", "error"):
            return self._on_error(event)
        # thread.started / turn.started and anything this adapter does not
        # know yet carry nothing this contract can use.
        return []

    def _on_item_started(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        item = event.get("item") or {}
        itype = item.get("type")
        if itype not in ("command_execution", "file_change"):
            return []
        item_id = str(item.get("id") or "")
        label = item.get("command") if itype == "command_execution" else _describe_file_change(item)
        label = str(label or itype)
        if item_id:
            self._open_items[item_id] = itype
        return [{"type": "tool_start", "name": itype, "input": label}]

    def _on_item_completed(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        item = event.get("item") or {}
        itype = item.get("type")
        if itype == "agent_message":
            text = str(item.get("text") or "")
            if not text:
                return []
            self._final_text_parts.append(text)
            return [{"type": "token", "token": text}]
        if itype == "reasoning":
            text = str(item.get("text") or "")
            return [{"type": "thinking", "message": text}] if text else []
        if itype in ("command_execution", "file_change"):
            item_id = str(item.get("id") or "")
            self._open_items.pop(item_id, None)
            output = (
                item.get("aggregated_output") if itype == "command_execution"
                else _describe_file_change(item)
            )
            return [{"type": "tool_end", "name": itype, "output": str(output or "")}]
        return []

    def _on_turn_completed(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        usage = event.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        cached = int(usage.get("cached_input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        # No cost_usd: Codex is not known to report a dollar figure of its
        # own the way Claude Code does, so this run is priced by the hub from
        # these token counts against its own catalog instead.
        usage_frame = {
            "type": "usage",
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cached_tokens": cached,
        }
        done_frame = {
            "type": "done",
            "ok": True,
            "output": "".join(self._final_text_parts).strip(),
            "error": None,
        }
        return [usage_frame, done_frame]

    def _on_error(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        message = str(event.get("message") or event.get("error") or "codex reported an error")
        return [{
            "type": "done", "ok": False,
            "output": "".join(self._final_text_parts).strip(),
            "error": message,
        }]


# ── health ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> Dict[str, Any]:
    workdir_ok = DEFAULT_WORKDIR.is_dir()
    return {
        "status": "ok",
        "agent": "codex",
        "workdir": str(DEFAULT_WORKDIR),
        "workdir_present": workdir_ok,
        "model": os.environ.get("CODEX_MODEL") or "codex default",
    }


# ── synchronous run ─────────────────────────────────────────────────────────

@app.post("/run")
def run(req: RunRequest) -> Dict[str, Any]:
    """Run one Codex turn and report the outcome.

    Captures the whole JSONL output and feeds it through the same translator
    the streaming endpoint uses, then reduces the resulting frames to the
    hub's single-response shape. Token accounting is only wired for the
    streaming path (see ``docs/imported-agents.md``): a caller of this
    endpoint gets Codex's own totals inside the response body, but the hub
    does not credit them to the run from here.
    """
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        return {
            "ok": False, "output": "",
            "error": f"Working directory {workdir} does not exist in this container. "
                     f"Mount a git repository there or set CODEX_WORKDIR.",
        }

    cmd = _codex_command(req.prompt)
    try:
        result = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True,
            timeout=RUN_TIMEOUT, env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "", "error": f"codex exceeded the {RUN_TIMEOUT}s run timeout"}
    except FileNotFoundError:
        return {
            "ok": False, "output": "",
            "error": "codex is not installed in this container (npm install -g @openai/codex)",
        }

    translator = CodexTranslator()
    frames: List[Dict[str, Any]] = []
    for raw_line in (result.stdout or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        frames.extend(translator.feed(event))

    done = next((f for f in reversed(frames) if f.get("type") == "done"), None)
    if done is None:
        stderr = (result.stderr or "").strip()
        return {
            "ok": False, "output": "",
            "error": stderr or f"codex exited with code {result.returncode} without a turn.completed event",
        }

    usage = next((f for f in frames if f.get("type") == "usage"), None)
    response: Dict[str, Any] = {
        "ok": bool(done.get("ok")),
        "output": done.get("output") or "",
        "error": done.get("error"),
        "steps": [{"name": "codex", "args": {"command": shlex.join(cmd)}, "output": (done.get("output") or "")[:4000]}],
    }
    if usage:
        response["usage"] = {k: v for k, v in usage.items() if k != "type"}
    return response


# ── streaming ────────────────────────────────────────────────────────────────

def _frame(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _stream_codex(prompt: str, workdir: Path) -> Iterator[str]:
    cmd = _codex_command(prompt)
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
            "error": "codex is not installed in this container (npm install -g @openai/codex)",
        })
        return

    stderr_tail: List[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_tail.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    translator = CodexTranslator()
    saw_done = False
    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            for frame in translator.feed(event):
                if frame.get("type") == "done":
                    saw_done = True
                yield _frame(frame)
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        yield _frame({
            "type": "done", "ok": False, "output": "",
            "error": f"codex exceeded the {RUN_TIMEOUT}s run timeout",
        })
        return
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        proc.wait()
        yield _frame({"type": "done", "ok": False, "output": "", "error": f"{type(exc).__name__}: {exc}"})
        return
    finally:
        stderr_thread.join(timeout=2)

    if not saw_done:
        detail = "".join(stderr_tail).strip()[:2000]
        yield _frame({
            "type": "done", "ok": proc.returncode == 0, "output": "",
            "error": detail or f"codex exited with code {proc.returncode} without a turn.completed event",
        })


@app.post("/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        def _missing() -> Iterator[str]:
            yield _frame({
                "type": "done", "ok": False, "output": "",
                "error": f"Working directory {workdir} does not exist in this container. "
                         f"Mount a git repository there or set CODEX_WORKDIR.",
            })
        return StreamingResponse(_missing(), media_type="application/x-ndjson")

    return StreamingResponse(
        _stream_codex(req.prompt, workdir),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
