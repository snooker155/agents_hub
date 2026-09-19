"""
view_serve — the full-stack tier: a generated web service behind a scoped,
origin-isolated proxy.

An ``html`` (or ``serve``) view can register a **localhost** upstream that a
generated backend runs on; the dashboard exposes it under the scoped path
``/api/views/{id}/proxy/*`` (see ``routes/views``). The proxy is the isolation
boundary: it forwards only to loopback upstreams (no SSRF to arbitrary hosts,
no external binding) and never passes the dashboard's own auth/cookies through.

This module owns the upstream policy so both the tool (on registration) and the
proxy route (on every forward) share one allow-check — plus the optional
**launcher**: ``start_service`` runs the generated backend as a workspace-scoped
subprocess tied to the view's lifecycle (killed on ``stop_service`` /
``delete_view``). Launching is gated behind the ``views_serve_launch_enabled``
setting (off by default), matching the shell-allowlist opt-in posture; the
proxy-only registration path needs no opt-in.
"""
from __future__ import annotations

import logging
import shlex
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

log = logging.getLogger("views.serve")

# Only loopback upstreams are ever proxied. The service must bind to localhost;
# the scoped proxy is how the browser reaches it, origin-isolated.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def is_allowed_upstream(url: str) -> bool:
    """True when ``url`` is an ``http(s)://<loopback>[:port]`` upstream."""
    try:
        p = urlparse(str(url))
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    return host in _ALLOWED_HOSTS


# ── managed backend processes (one per view) ─────────────────────────────────

_PROCS: Dict[str, subprocess.Popen] = {}

# A launched backend must come up within this window or it is killed.
STARTUP_TIMEOUT_SECONDS = 10.0


def launch_enabled() -> bool:
    try:
        from common.config import settings
        return bool(getattr(settings, "views_serve_launch_enabled", False))
    except Exception:
        return False


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def start_service(view_id: str, command: str, cwd: str, port: int,
                  log_path: Optional[str] = None) -> Dict[str, Any]:
    """Start a backend for ``view_id`` and wait for it to listen on ``port``.

    One process per view (a second start replaces the first). The process runs
    with ``cwd`` as its working directory, its output tee'd to ``log_path`` (or
    ``<cwd>/.view_serve.log``), and is killed by :func:`stop_service`. Returns
    ``{"pid", "upstream"}`` or raises ``ValueError`` with an agent-readable
    reason (gate off, bad port, failed to come up).
    """
    if not launch_enabled():
        raise ValueError(
            "launching services is disabled — set VIEWS_SERVE_LAUNCH_ENABLED=1 "
            "to opt in, or start the backend yourself and pass its upstream URL")
    port = int(port)
    if not (1024 <= port <= 65535):
        raise ValueError("port must be in 1024..65535")
    if _port_open(port):
        raise ValueError(f"port {port} is already in use — pick another or pass the running service's upstream")
    argv = shlex.split(str(command or ""))
    if not argv:
        raise ValueError("a non-empty command is required")
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        raise ValueError(f"working directory not found: {cwd}")

    stop_service(view_id)  # replace any previous backend for this view
    out = open(log_path or (cwd_path / ".view_serve.log"), "ab")
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd_path), stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        out.close()
        raise ValueError(f"could not start {argv[0]!r}: {exc}")
    finally:
        # Popen dup'ed the fd (or failed); the parent's handle is done either way.
        try:
            out.close()
        except Exception:
            pass

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ValueError(
                f"service exited immediately (code {proc.returncode}) — check .view_serve.log in the workspace")
        if _port_open(port):
            _PROCS[view_id] = proc
            log.info("view_serve started view=%s pid=%s port=%s", view_id, proc.pid, port)
            return {"pid": proc.pid, "upstream": f"http://127.0.0.1:{port}"}
        time.sleep(0.2)
    proc.kill()
    raise ValueError(f"service did not listen on port {port} within {STARTUP_TIMEOUT_SECONDS:.0f}s (killed)")


def stop_service(view_id: str) -> bool:
    """Kill the backend launched for ``view_id`` (True if one was running)."""
    proc = _PROCS.pop(view_id, None)
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass
    log.info("view_serve stopped view=%s pid=%s", view_id, proc.pid)
    return True


def service_status(view_id: str) -> Optional[Dict[str, Any]]:
    """``{"pid", "running"}`` for a launched backend, or None if never launched."""
    proc = _PROCS.get(view_id)
    if proc is None:
        return None
    return {"pid": proc.pid, "running": proc.poll() is None}


__all__ = ["is_allowed_upstream", "start_service", "stop_service",
           "service_status", "launch_enabled", "STARTUP_TIMEOUT_SECONDS"]
