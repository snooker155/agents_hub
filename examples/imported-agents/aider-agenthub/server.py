"""Aider behind the Agents Hub HTTP contract.

Aider (https://github.com/Aider-AI/aider) is a finished, widely used code agent
with its own prompts, its own repo-map heuristics and its own model access. None
of that should be reimplemented as a hub definition — this file is the whole
integration: it translates one HTTP call into one `aider --message` invocation
and translates the result back.

The contract implemented here is the one declared in ``agent-hub.json``:

    POST /run         {"prompt": str, "run_id": str|null, "workspace": str|null}
                  ->  {"ok": bool, "output": str, "error": str|null, "steps": [...]}
    POST /run/stream  same body -> NDJSON frames, one JSON object per line
    GET  /health      200

Streaming matters for a code agent more than for most: aider spends minutes
reading files, proposing edits and running commands, and without a stream the
hub's chat sits blank the whole time. `/run/stream` forwards aider's output line
by line as ``token`` frames and closes with a ``done`` frame carrying the
authoritative result, so the run still has one unambiguous outcome.

Everything else about the service — how it is packaged, which model it uses,
what it is allowed to touch — is this repository's business, not the hub's.
That separation is the point of importing over HTTP rather than importing code.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Aider — Agents Hub adapter")

# Repository aider edits when a run does not name a workspace of its own.
DEFAULT_WORKDIR = Path(os.environ.get("AIDER_WORKDIR", "/work"))

# Hard ceiling on a single run, independent of the hub's own timeout, so a
# wedged aider process cannot hold a worker forever.
RUN_TIMEOUT = int(os.environ.get("AIDER_RUN_TIMEOUT", "1800"))


class RunRequest(BaseModel):
    prompt: str
    run_id: Optional[str] = None
    workspace: Optional[str] = None


def _resolve_workdir(workspace: Optional[str]) -> Path:
    """Pick the git repository aider will edit.

    A workspace path handed over by the hub is only honoured when it exists in
    this container — the hub and the agent may well not share a filesystem, and
    silently running against the wrong tree is worse than falling back.
    """
    if workspace:
        candidate = Path(workspace)
        if candidate.is_dir():
            return candidate
    return DEFAULT_WORKDIR


def _aider_command(prompt: str) -> List[str]:
    """Build the aider invocation for a single non-interactive message.

    ``--yes-always`` is what makes aider run unattended; without it the process
    blocks on a confirmation prompt that no one is there to answer. The model
    flag is only passed when configured, so aider keeps its own default
    selection logic for whichever API key it finds.
    """
    cmd = ["aider", "--message", prompt, "--yes-always", "--no-pretty", "--no-stream"]
    model = (os.environ.get("AIDER_MODEL") or "").strip()
    if model:
        cmd += ["--model", model]
    # Auto-commits are aider's default and are usually what you want from a hub
    # run: the diff survives as a commit the operator can read or revert.
    if (os.environ.get("AIDER_NO_AUTO_COMMITS") or "").strip().lower() in ("1", "true", "yes"):
        cmd.append("--no-auto-commits")
    return cmd


def _child_env() -> Dict[str, str]:
    """Environment for the aider process.

    ``PYTHONUNBUFFERED`` is the part that matters for streaming: without it a
    Python child writing to a pipe buffers in 8 KB blocks, and the stream would
    arrive as a few large bursts at the end instead of line by line.
    """
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _changed_files(workdir: Path) -> List[str]:
    """Files aider touched in this run, read from git rather than from stdout."""
    try:
        result = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
            cwd=str(workdir), capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@app.get("/health")
def health() -> Dict[str, Any]:
    """Liveness plus enough context to debug a misconfigured container."""
    workdir_ok = DEFAULT_WORKDIR.is_dir()
    return {
        "status": "ok",
        "agent": "aider",
        "workdir": str(DEFAULT_WORKDIR),
        "workdir_present": workdir_ok,
        "model": os.environ.get("AIDER_MODEL") or "aider default",
    }


@app.post("/run")
def run(req: RunRequest) -> Dict[str, Any]:
    """Run one aider message and report the outcome.

    Failures are returned as ``{"ok": false, "error": ...}`` with HTTP 200
    rather than as a 5xx: the hub records both the same way, and a body carries
    aider's own explanation, which a bare status code would lose.
    """
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        return {
            "ok": False,
            "output": "",
            "error": f"Working directory {workdir} does not exist in this container. "
                     f"Mount a git repository there or set AIDER_WORKDIR.",
        }

    cmd = _aider_command(req.prompt)
    try:
        result = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=RUN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": f"aider exceeded the {RUN_TIMEOUT}s run timeout",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "output": "",
            "error": "aider is not installed in this container (pip install aider-chat)",
        }

    output = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        return {
            "ok": False,
            "output": output,
            "error": stderr or f"aider exited with code {result.returncode}",
            "steps": [{"name": "aider", "args": {"command": shlex.join(cmd)}, "output": stderr[:4000]}],
        }

    files = _changed_files(workdir)
    return {
        "ok": True,
        "output": output or "aider completed without producing output.",
        "error": None,
        "steps": [
            {
                "name": "aider",
                "args": {"command": shlex.join(cmd), "cwd": str(workdir)},
                "output": (output or "")[:4000],
            }
        ],
        "changed_files": files,
    }


# ── streaming ───────────────────────────────────────────────────────────────

def _frame(payload: Dict[str, Any]) -> str:
    """One NDJSON frame: a compact JSON object plus a newline.

    NDJSON rather than SSE because it is one line of code on both sides and the
    hub accepts either. Nothing here needs SSE's event names or reconnection.
    """
    return json.dumps(payload, ensure_ascii=False) + "\n"


def _stream_aider(prompt: str, workdir: Path) -> Iterator[str]:
    """Run aider, yielding its output line by line, then a final `done` frame.

    stderr is folded into stdout so aider's progress and its warnings arrive in
    the order it produced them; separating them would reorder the narrative for
    no benefit. The process is killed on timeout rather than left running, since
    an abandoned aider would keep editing the repository after the hub gave up.
    """
    cmd = _aider_command(prompt)
    yield _frame({"type": "tool_start", "name": "aider", "input": shlex.join(cmd)})

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(workdir), env=_child_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        yield _frame({
            "type": "done", "ok": False, "output": "",
            "error": "aider is not installed in this container (pip install aider-chat)",
        })
        return

    collected: List[str] = []
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            collected.append(line)
            # Each line is a token frame: the hub appends them into the chat
            # bubble in order, so the user watches aider work in real time.
            yield _frame({"type": "token", "token": line})
        proc.wait(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        yield _frame({
            "type": "done", "ok": False, "output": "".join(collected),
            "error": f"aider exceeded the {RUN_TIMEOUT}s run timeout",
        })
        return
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        proc.wait()
        yield _frame({
            "type": "done", "ok": False, "output": "".join(collected),
            "error": f"{type(exc).__name__}: {exc}",
        })
        return

    output = "".join(collected).strip()
    yield _frame({"type": "tool_end", "name": "aider", "output": output[:4000]})

    if proc.returncode != 0:
        yield _frame({
            "type": "done", "ok": False, "output": output,
            "error": f"aider exited with code {proc.returncode}",
        })
        return

    # The `done` frame is authoritative: the hub prefers it over the accumulated
    # tokens, so a run always has exactly one outcome even if the stream was
    # noisy. `usage` is omitted here because aider does not report token counts
    # on stdout — an agent that knows its own usage should include it, and the
    # hub will credit the run's cost with it.
    yield _frame({
        "type": "done",
        "ok": True,
        "output": output or "aider completed without producing output.",
        "error": None,
        "changed_files": _changed_files(workdir),
    })


@app.post("/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    """Stream one aider run as NDJSON frames."""
    workdir = _resolve_workdir(req.workspace)
    if not workdir.is_dir():
        def _missing() -> Iterator[str]:
            yield _frame({
                "type": "done", "ok": False, "output": "",
                "error": f"Working directory {workdir} does not exist in this container. "
                         f"Mount a git repository there or set AIDER_WORKDIR.",
            })
        return StreamingResponse(_missing(), media_type="application/x-ndjson")

    return StreamingResponse(
        _stream_aider(req.prompt, workdir),
        media_type="application/x-ndjson",
        # Proxies that buffer would defeat the point of streaming at all.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
