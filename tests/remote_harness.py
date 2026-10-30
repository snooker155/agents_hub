"""
A hub and an agent process with no database, for the remote state tests.

:func:`hub_server` serves, from this test process and its test database, what
an agent container reaches over HTTP: the ``/api/run-state`` routes (the
remote store calls among them) and a fake OpenAI-compatible ``/v1`` that
answers every chat completion with a fixed text. Relay posts the tests do not
look at (session events, stream notifications) are accepted and dropped.

:func:`run_child` starts a runtime entry point as a real subprocess the way a
container would: ``AGENTS_HUB_REMOTE_STATE=1``, a run token, the hub's address,
and a state root of its own that holds nothing (the hub's database is not
there to open). Any store access that was not routed through the hub fails in
that process with ``RemoteStateError``, which is what these tests look for.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import Request

REPO = Path(__file__).resolve().parents[1]

#: What the fake model answers.
FAKE_ANSWER = "remote run finished"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_completion(body: Dict[str, Any], step: Any) -> Any:
    """One completion: ``step`` is the text to answer, or ``{"tool": name,
    "args": {...}}`` for a tool call."""
    from fastapi.responses import JSONResponse, StreamingResponse

    model = str(body.get("model") or "fake")
    usage = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    call = None
    text = step if isinstance(step, str) else ""
    if isinstance(step, dict):
        call = {"id": f"call_{int(time.time() * 1000) % 100000}", "type": "function",
                "function": {"name": step["tool"], "arguments": json.dumps(step.get("args") or {})}}
    finish = "tool_calls" if call else "stop"
    if not body.get("stream"):
        message: Dict[str, Any] = {"role": "assistant", "content": text or None}
        if call:
            message["tool_calls"] = [call]
        return JSONResponse({
            "id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": usage,
        })

    def chunks() -> Iterator[str]:
        base = {"id": "chatcmpl-fake", "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model}
        delta: Dict[str, Any] = {"role": "assistant", "content": text}
        if call:
            delta["tool_calls"] = [{"index": 0, **call}]
        yield "data: " + json.dumps({**base, "choices": [
            {"index": 0, "delta": delta, "finish_reason": None}]}) + "\n\n"
        yield "data: " + json.dumps({**base, "choices": [
            {"index": 0, "delta": {}, "finish_reason": finish}]}) + "\n\n"
        if (body.get("stream_options") or {}).get("include_usage"):
            yield "data: " + json.dumps({**base, "choices": [], "usage": usage}) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(chunks(), media_type="text/event-stream")


def _app(script: Optional[List[Any]] = None) -> Any:
    from fastapi import FastAPI
    from routes import run_state

    from runtime import tool_host
    app = FastAPI()
    app.include_router(run_state.router)
    # The tool host a container's launcher serves (runtime/tool_host.py).
    app.include_router(tool_host.router())
    seen: List[Dict[str, Any]] = []
    app.state.seen = seen
    steps = list(script or [])

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        seen.append({"path": "/v1/chat/completions", "body": body,
                     "authorization": request.headers.get("authorization")})
        return _fake_completion(body, steps.pop(0) if steps else FAKE_ANSWER)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(path: str, request: Request):
        seen.append({"path": "/" + path, "method": request.method})
        return {"ok": True}

    return app


@contextmanager
def hub_server(script: Optional[List[Any]] = None) -> Iterator[Dict[str, Any]]:
    """The hub side, on a free local port, until the block ends. Yields
    ``{"url": ..., "seen": [...]}``; ``seen`` lists the requests the catch-all
    and the fake model got. ``script`` is what the fake model answers, one
    step per completion (see :func:`_fake_completion`), then FAKE_ANSWER."""
    import uvicorn

    app = _app(script)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test hub did not start")
        time.sleep(0.05)
    try:
        yield {"url": f"http://127.0.0.1:{port}", "seen": app.state.seen}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def child_env(hub_url: str, token: str, *, workspace: str, root: Path,
              extra: Optional[Dict[str, str]] = None, audit: Optional[Path] = None) -> Dict[str, str]:
    """What a container's process would get: no database, a token, the hub.

    With ``audit`` the child opens the test database after all and writes
    every direct access it makes into that file (``AGENTS_HUB_REMOTE_STATE=
    audit``), which lists everything left to route in one run."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "AGENTS_HUB_TEST_"))}
    for name in ("AGENTS_HUB_API_TOKEN", "AGENTS_HUB_SERVICE_TOKEN", "AGENTS_HUB_API_KEY",
                 "AGENT_SESSION_ID", "AGENTS_HUB_SNAPSHOT_DIR"):
        env.pop(name, None)
    root.mkdir(parents=True, exist_ok=True)
    # The developer's .env stays out, as it does from a real container (the
    # image has none): an empty one of its own.
    empty_env = root / "empty.env"
    empty_env.write_text("", encoding="utf-8")
    env.update({
        "AGENTS_HUB_ENV_FILE": str(empty_env),
        "AGENTS_HUB_REMOTE_STATE": "1",
        "AGENTS_HUB_ROOT": str(root),
        "AGENTS_HUB_DATABASE_URL": "",
        "AGENTS_HUB_RUN_TOKEN": token,
        "AGENTS_HUB_CALLBACK_URL": hub_url,
        "AGENTS_HUB_TOOL_HOST_URL": hub_url,
        "AGENT_RUN_STATE_TRANSPORT": "http",
        "AGENT_WORKSPACE": workspace,
        "OPENAI_API_KEY": "sk-fake",
        "OPENAI_BASE_URL": hub_url + "/v1",
        "PYTHONPATH": str(REPO),
        "PYTHONUNBUFFERED": "1",
    })
    if audit is not None:
        env.update({
            "AGENTS_HUB_REMOTE_STATE": "audit",
            "AGENTS_HUB_REMOTE_STATE_AUDIT_FILE": str(audit),
            # The test database's folder: the test opened it as
            # <root>/agents_hub.db (reopen_db) for this to find it.
            "AGENTS_HUB_ROOT": str(_test_db_root()),
            "AGENTS_HUB_DATABASE_URL": os.environ.get("AGENTS_HUB_DATABASE_URL", ""),
        })
    env.update(extra or {})
    return env


def _test_db_root() -> Path:
    from common import db
    if Path(db.DB_FILE).name != "agents_hub.db":
        raise RuntimeError("an audit run needs the test database opened as <dir>/agents_hub.db "
                           "(reopen_db) so the child finds it")
    return Path(db.DB_FILE).parent


def audit_report(path: Path) -> Dict[str, List[str]]:
    """The audit file grouped by the frame closest to the database (the store
    function that opened it), each with the call paths that reached it."""
    out: Dict[str, List[str]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        frames = [f for f in line.split(" <- ")
                  if "common/docstore.py" not in f and "contextlib.py" not in f]
        head = frames[0] if frames else line
        out.setdefault(head, []).append(line)
    return out


def run_child(args: List[str], env: Dict[str, str], *, timeout: float = 120) -> subprocess.CompletedProcess:
    """``python -m <args>`` in the repo, as the container would run it."""
    return subprocess.run([sys.executable, "-m", *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=timeout)


def start_child(args: List[str], env: Dict[str, str], log_path: Path) -> subprocess.Popen:
    """``python -m <args>`` left running (a carrier), its output in ``log_path``."""
    fh = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen([sys.executable, "-m", *args], cwd=str(REPO), env=env,
                            stdout=fh, stderr=subprocess.STDOUT, text=True)


def wait_for(predicate, *, timeout: float = 90.0, every: float = 0.25, proc=None) -> Any:
    """Poll ``predicate`` until it returns something truthy; fail on timeout or
    when ``proc`` exits first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"the child exited ({proc.returncode}) before the condition held")
        time.sleep(every)
    raise AssertionError("timed out waiting for the condition")


def misses(output: str) -> List[str]:
    """The lines of a child's output that name a store access nobody routed
    through the hub."""
    return [line for line in output.splitlines() if "RemoteStateError" in line
            or "has no database" in line]
