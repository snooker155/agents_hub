"""Direct HTTP port of a resident instance.

The hub's public address (``/api/external/{token}/messages``) is how a
published instance is normally reached. When the agent asks for its own port
(``http_expose`` or a ``service`` node type on the agent) or the instance is
started with ``direct_port``, ``runtime/instance_run.py`` also starts this
server in a background thread, so the instance can be called on its container
port without going through the hub. A message sent here lands in the same
mailbox and is answered by the same process.

Endpoints
---------
GET  /health          basic liveness probe
GET  /status          the instance record, without secrets
POST /run             send a message, wait for the answer
GET  /logs?tail=100   last N lines of the carrier log
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

from common.logging_config import marker_logger

# This service's own timestamped lines (``[timestamp] ...``) go through a
# logger configured to emit the message only (the ``[timestamp]`` prefix below
# is added by ``_make_logger``'s ``_log`` itself), on stdout, at INFO
# regardless of ORCH_LOG_LEVEL — mirrors runtime/instance_run.py's ``log()``.
_marker_log = marker_logger(__name__)


# ── Request models (module-level so FastAPI/Pydantic v2 resolves them as body) ─

class RunRequest(BaseModel):
    prompt: str
    conversation_id: Optional[str] = None
    wait_seconds: float = 300.0


# ── Stdout tee ────────────────────────────────────────────────────────────────

class _TeeStream(io.TextIOBase):
    """Write to two text streams simultaneously, flushing after every write.

    Installed as sys.stdout/sys.stderr so that plain print() calls in request
    handlers appear in the carrier log file even when the process stdout is a
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
        _marker_log.info(line)
        if _fh is not None:
            try:
                _fh.write(line + "\n")
                _fh.flush()
            except Exception:
                pass

    return _log


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(instance_id: str, agent_id: str, workspace: Optional[str] = None,
               log_file: Optional[str] = None) -> FastAPI:
    app = FastAPI(
        title=f"Agent HTTP — {agent_id}",
        description="Direct HTTP port of an agents-hub resident instance.",
        version="2.0.0",
    )

    def _instance() -> dict:
        from instances import store
        return store.get(instance_id) or {}

    def _check_token(authorization: Optional[str]) -> None:
        """A published instance requires its token here too, in constant time."""
        token = _instance().get("expose_token") if _instance().get("is_exposed") else None
        if not token:
            return
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Authorization: Bearer <token> header required")
        import hmac
        if not hmac.compare_digest(authorization[7:].encode("utf-8"), str(token).encode("utf-8")):
            raise HTTPException(status_code=403, detail="Invalid access token")

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["system"])
    def health():
        """Liveness probe."""
        return {"status": "ok", "instance_id": instance_id, "agent_id": agent_id}

    # ── Status ────────────────────────────────────────────────────────────────

    @app.get("/status", tags=["system"])
    def status():
        """The instance record, without its token or inbound secret."""
        try:
            from instances import carrier
            inst = _instance()
            if not inst:
                return {"instance_id": instance_id, "agent_id": agent_id, "state": "unknown"}
            return {k: v for k, v in carrier.public_view(inst).items() if k != "expose_token"}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    # ── Run ───────────────────────────────────────────────────────────────────

    @app.post("/run", tags=["agent"])
    def run(req: RunRequest, authorization: Optional[str] = Header(None)):
        """Send a message and wait for the answer.

        The message goes into the instance's own mailbox, in the conversation
        named by ``conversation_id`` (the main one when left out), and is
        answered by this process like any other; the request stays open until
        the answer (or ``wait_seconds``, at most 300) and returns where the
        message stands either way.
        """
        _check_token(authorization)
        from instances import inbox, replies
        msg_id = inbox.enqueue(instance_id, req.prompt, origin="http",
                               conversation_id=req.conversation_id)
        reply = replies.wait_sync(msg_id, min(max(req.wait_seconds, 0.0), 300.0)) or {
            "msg_id": msg_id, "status": "queued"}
        out = {**reply, "instance_id": instance_id, "agent_id": agent_id,
               "ok": reply.get("status") == "completed"}
        return out

    # ── Logs ──────────────────────────────────────────────────────────────────

    @app.get("/logs", tags=["system"])
    def logs(tail: int = 100, authorization: Optional[str] = Header(None)):
        """Return the last *tail* lines of the instance's carrier log."""
        _check_token(authorization)
        try:
            path = _instance().get("carrier_log_file")
            if not path or not Path(path).exists():
                return {"instance_id": instance_id, "lines": []}
            all_lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            return {"instance_id": instance_id, "lines": all_lines[-tail:]}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return app


# ── Server helpers ────────────────────────────────────────────────────────────

def start_http_server(
    instance_id: str,
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

    app = create_app(instance_id, agent_id, workspace=workspace, log_file=log_file)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


def start_http_server_thread(
    instance_id: str,
    agent_id: str,
    port: int,
    workspace: Optional[str] = None,
    log_file: Optional[str] = None,
) -> threading.Thread:
    """Start the HTTP server in a daemon background thread."""
    t = threading.Thread(
        target=start_http_server,
        args=(instance_id, agent_id, port, workspace, log_file),
        daemon=True,
        name=f"agent-http-{port}",
    )
    t.start()
    return t
