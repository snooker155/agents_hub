"""HTTP server for agents running in Docker containers.

When a node is started with http_expose=True (via AgentSpec settings), the
node_runner starts this server in a background thread so the agent can be
reached via HTTP from outside the container.

Endpoints
---------
GET  /health          basic liveness probe
GET  /status          node status record
POST /run             submit a prompt and receive the agent's response
GET  /logs?tail=100   last N lines of the node log file
"""
from __future__ import annotations

import io
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel


# ── Request models (module-level so FastAPI/Pydantic v2 resolves them as body) ─

class RunRequest(BaseModel):
    prompt: str


# ── Stdout tee ────────────────────────────────────────────────────────────────

class _TeeStream(io.TextIOBase):
    """Write to two text streams simultaneously, flushing after every write.

    Installed as sys.stdout/sys.stderr so that plain print() calls in request
    handlers appear in the node log file even when the process stdout is a
    non-TTY pipe (which is fully buffered by default).
    """

    def __init__(self, primary: io.TextIOBase, secondary: io.TextIOBase) -> None:
        self._primary = primary
        self._secondary = secondary

    def write(self, s: str) -> int:
        n = self._primary.write(s)
        try:
            self._primary.flush()
        except Exception:
            pass
        try:
            self._secondary.write(s)
            self._secondary.flush()
        except Exception:
            pass
        return n

    def flush(self) -> None:
        for stream in (self._primary, self._secondary):
            try:
                stream.flush()
            except Exception:
                pass

    def fileno(self) -> int:
        return self._primary.fileno()

    @property
    def encoding(self) -> str:
        return getattr(self._primary, "encoding", "utf-8")

    @property
    def errors(self) -> Optional[str]:
        return getattr(self._primary, "errors", "replace")


# ── Logger factory ────────────────────────────────────────────────────────────

def _make_logger(log_file: Optional[str]) -> Callable[[str], None]:
    """Return a log() that writes timestamped lines to stdout and optionally a file."""
    _fh = None
    if log_file:
        try:
            p = Path(log_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            _fh = open(p, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass

    def _log(msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        if _fh is not None:
            try:
                _fh.write(line + "\n")
                _fh.flush()
            except Exception:
                pass

    return _log


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(node_id: str, agent_id: str, workspace: Optional[str] = None, log_file: Optional[str] = None) -> FastAPI:
    app = FastAPI(
        title=f"Agent HTTP — {agent_id}",
        description="HTTP interface for an agents-hub node running inside a container.",
        version="1.0.0",
    )

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["system"])
    def health():
        """Liveness probe."""
        return {"status": "ok", "node_id": node_id, "agent_id": agent_id}

    # ── Status ────────────────────────────────────────────────────────────────

    @app.get("/status", tags=["system"])
    def status():
        """Return the node's status record from nodes.json."""
        try:
            from agents.node_manager import get_node
            node = get_node(node_id)
            if node is None:
                return {"node_id": node_id, "agent_id": agent_id, "status": "unknown"}
            safe = {k: v for k, v in node.items() if k not in ("expose_token",)}
            return safe
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Run ───────────────────────────────────────────────────────────────────

    @app.post("/run", tags=["agent"])
    def run(req: RunRequest, authorization: Optional[str] = Header(None)):
        """Run the agent with the given prompt and return the result.

        Synchronous blocking call — the HTTP request stays open until the agent
        finishes.  If the node has an expose_token set, a matching
        Authorization: Bearer <token> header is required.
        """
        run_id: Optional[str] = None
        try:
            import time as _time
            from agents.node_manager import get_node as _get_node
            _node = _get_node(node_id)
            _token = _node.get("expose_token") if _node else None
            if _token:
                if not authorization or not authorization.startswith("Bearer "):
                    raise HTTPException(status_code=401, detail="Authorization: Bearer <token> header required")
                if authorization[7:] != _token:
                    raise HTTPException(status_code=403, detail="Invalid access token")

            from agents.run_manager import new_unique_run_id, open_run, close_run
            from agents.stats_callback import RunStatsCallback
            run_id = new_unique_run_id()
            ws_name = (_node or {}).get("workspace") or workspace
            open_run(
                run_id, agent_id,
                node_id=node_id,
                session_type="http",
                title=req.prompt[:80],
                input=req.prompt,
                workspace=ws_name,
                link_to_session=False,
            )

            from agents.agent_factory import create_agent
            agent = create_agent(agent_id, workspace=workspace)
            callback = RunStatsCallback()
            t0 = _time.perf_counter()
            result = agent.run(req.prompt, callbacks=[callback])
            duration_ms = int((_time.perf_counter() - t0) * 1000)
            process = callback.build_process(duration_ms)

            if result.ok:
                output = str(result.agent_output or "")
                close_run(run_id, status="completed", exit_code=0, output=output, process=process)
                return {
                    "ok": True,
                    "output": output,
                    "run_id": run_id,
                    "node_id": node_id,
                    "agent_id": agent_id,
                }
            error_str = str(result.error or "agent error")
            close_run(run_id, status="failed", exit_code=1, error=error_str, process=process)
            return {
                "ok": False,
                "error": error_str,
                "run_id": run_id,
                "node_id": node_id,
                "agent_id": agent_id,
            }
        except HTTPException:
            raise
        except Exception as exc:
            if run_id:
                try:
                    from agents.run_manager import close_run as _close
                    _close(run_id, status="failed", exit_code=1, error=str(exc))
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Logs ──────────────────────────────────────────────────────────────────

    @app.get("/logs", tags=["system"])
    def logs(tail: int = 100):
        """Return the last *tail* lines of the node's log file."""
        try:
            from agents.node_manager import get_node
            node = get_node(node_id)
            if not node or not node.get("log_file"):
                return {"node_id": node_id, "lines": []}
            log_path = Path(node["log_file"])
            if not log_path.exists():
                return {"node_id": node_id, "lines": []}
            all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return {"node_id": node_id, "lines": all_lines[-tail:]}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return app


# ── Server helpers ────────────────────────────────────────────────────────────

def start_http_server(
    node_id: str,
    agent_id: str,
    port: int,
    workspace: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """Start the uvicorn HTTP server — blocks until the server stops."""
    import uvicorn

    if log_file:
        try:
            p = Path(log_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            _fh = open(p, "a", encoding="utf-8", buffering=1)
            sys.stdout = _TeeStream(sys.stdout, _fh)  # type: ignore[assignment]
            sys.stderr = _TeeStream(sys.stderr, _fh)  # type: ignore[assignment]
        except Exception:
            pass

    app = create_app(node_id, agent_id, workspace=workspace, log_file=log_file)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


def start_http_server_thread(
    node_id: str,
    agent_id: str,
    port: int,
    workspace: Optional[str] = None,
    log_file: Optional[str] = None,
) -> threading.Thread:
    """Start the HTTP server in a daemon background thread."""
    t = threading.Thread(
        target=start_http_server,
        args=(node_id, agent_id, port, workspace, log_file),
        daemon=True,
        name=f"agent-http-{port}",
    )
    t.start()
    return t
