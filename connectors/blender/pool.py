"""
The daemon pool: how many engines exist, who they belong to, and who may start one.

The pool is **file-backed and cross-process**. Agents run in their own processes
(subprocess, container or node), so an in-memory pool would let two processes
start two Blenders for the same scene — one scene, two divergent geometries —
and would show an operator an empty connector page while four engines ran.

A registry record per engine (``.agents_hub/blender/daemons/<key>.json``) fixes
both: a process that wants a scene finds the engine already serving it and
attaches to its socket, and any process can enumerate, probe and stop the lot.
Records are cheap to verify — a live pid plus a socket that answers ``state`` —
and a record that fails either check is stale and gets dropped.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from filelock import FileLock, Timeout

from common.session_broker import notify_change
from connectors.blender import store
from connectors.blender.daemon import BlenderDaemon, DaemonError, _pid_alive, _safe

#: Engines this process started or attached to, so repeat commands reuse one
#: connection instead of reconnecting per call.
_LOCAL: Dict[str, BlenderDaemon] = {}
_LOCK = threading.RLock()

#: How long a cross-process probe waits for `state` before calling an engine dead.
PROBE_TIMEOUT = 3.0


# ── the registry ─────────────────────────────────────────────────────────────

def _record_path(key: str) -> Path:
    return store.daemons_dir() / f"{_safe(key)}.json"


def _write_record(daemon: BlenderDaemon) -> None:
    path = _record_path(daemon.key)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(daemon.record(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_record(key: str) -> Optional[Dict[str, Any]]:
    path = _record_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _all_records() -> List[Dict[str, Any]]:
    out = []
    for path in store.daemons_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("key"):
            out.append(data)
    return out


def _drop_record(key: str) -> None:
    try:
        _record_path(key).unlink()
    except OSError:
        pass


def _probe(record: Dict[str, Any], timeout: float = PROBE_TIMEOUT) -> Optional[Dict[str, Any]]:
    """Ask an engine what it is doing. ``None`` means it is not there any more.

    A pid check alone is not enough: a Blender that is wedged or has closed its
    socket is still a running process, and reporting it as healthy would send
    the next command into a wait that only a timeout ends.
    """
    if not _pid_alive(record.get("pid")):
        return None
    path = str(record.get("socket") or "")
    if not path or not os.path.exists(path):
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(path)
    except OSError:
        return None
    try:
        with sock:
            sock.sendall(b'{"id":"probe","cmd":"state"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    return None
                buf += chunk
        resp = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        return resp.get("result") if resp.get("ok") else None
    except socket.timeout:
        # It accepted the connection and did not answer in time: an engine busy
        # with a long operation, not a dead one. Reporting it as gone would drop
        # its record and start a second engine for the same scene.
        return {"busy": True}
    except (OSError, ValueError):
        return None


# ── availability ─────────────────────────────────────────────────────────────

def availability() -> Dict[str, Any]:
    """Can this machine run an engine at all, and is it allowed to?

    Answered without starting anything: the connector page and the health
    snapshot both call it, and neither should cost a Blender launch.
    """
    cfg = store.load()
    if not cfg.get("enabled", True):
        return {"available": False, "reason": "the Blender connector is disabled",
                "mode": cfg["mode"]}
    if cfg["mode"] == "docker":
        return {"available": False, "mode": "docker",
                "reason": "docker mode is configured but the container launcher is not built yet; "
                          "switch to local mode and set a binary path"}
    probe = store.probe()
    return {"available": bool(probe.get("ok")), "mode": cfg["mode"],
            "binary": probe.get("path", ""), "version": probe.get("version", ""),
            "reason": probe.get("error", "") if not probe.get("ok") else ""}


# ── acquiring an engine ──────────────────────────────────────────────────────

def acquire(key: str, *, start: bool = True) -> BlenderDaemon:
    """The engine for ``key``: this process's connection, another process's, or a new one.

    Serialized per key with a file lock, so two agents opening the same view at
    the same moment end up sharing one engine instead of racing to create two.
    """
    cfg = store.load()
    if not cfg.get("enabled", True):
        raise DaemonError("the Blender connector is disabled")

    with _LOCK:
        local = _LOCAL.get(key)
        if local is not None and local.is_alive():
            return local
        if local is not None:
            _LOCAL.pop(key, None)

    lock_path = store.daemons_dir() / f"{_safe(key)}.lock"
    try:
        guard = FileLock(str(lock_path), timeout=60.0)
        guard.acquire()
    except Timeout:
        raise DaemonError(f"timed out waiting for another process to start the engine for {key!r}")
    try:
        record = _read_record(key)
        if record and _probe(record) is not None:
            daemon = BlenderDaemon.attach(record)
            with _LOCK:
                _LOCAL[key] = daemon
            return daemon
        if record:
            _drop_record(key)          # the process is gone; the scene will be replayed
        if not start:
            raise DaemonError(f"no engine running for {key!r}")

        # Busy engines count towards the ceiling but are never the ones evicted:
        # taking an engine away mid-command fails somebody else's build, and the
        # whole point of the ceiling is memory, which a queued build will free
        # on its own soon enough.
        live, idle = [], []
        for other in _all_records():
            if other.get("key") == key:
                continue
            state = _probe(other)
            if state is None:
                continue
            live.append(other)
            if not state.get("busy"):
                idle.append(other)
        if len(live) >= int(cfg["max_daemons"]):
            if not _evict_one(idle):
                raise DaemonError(
                    f"all {int(cfg['max_daemons'])} Blender engines are busy; wait for one to "
                    "finish or raise the limit on the connector page")

        daemon = BlenderDaemon(key)
        daemon.start()
        _write_record(daemon)
        with _LOCK:
            _LOCAL[key] = daemon
        # A daemon can be registered by any agent's process, not just the
        # dashboard's, so the connector page's live-update event fires here
        # rather than only from the dashboard's own routes.
        notify_change("blender_daemons", key=key)
        return daemon
    finally:
        guard.release()


def _evict_one(idle: List[Dict[str, Any]]) -> bool:
    """Stop the idle engine that has been running longest. False if none could go.

    Oldest rather than least-recently-used: last-used is per-connection state
    that a record cannot carry across processes, and an engine's scene is
    rebuilt from its log anyway, so evicting the wrong one costs a replay rather
    than any work.
    """
    if not idle:
        return False
    oldest = min(idle, key=lambda r: float(r.get("started_at") or 0))
    return stop(str(oldest["key"]))


def call(key: str, cmd: str, args: Optional[Dict[str, Any]] = None,
         *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Run one command on ``key``'s engine, starting it if necessary."""
    return acquire(key).call(cmd, args, timeout=timeout)


def get(key: str) -> Optional[BlenderDaemon]:
    with _LOCK:
        daemon = _LOCAL.get(key)
        return daemon if daemon is not None and daemon.is_alive() else None


# ── stopping and reporting ───────────────────────────────────────────────────

def stop(key: str) -> bool:
    """Stop one engine, whichever process started it."""
    with _LOCK:
        daemon = _LOCAL.pop(key, None)
    if daemon is not None:
        daemon.stop()
        _drop_record(key)
        notify_change("blender_daemons", key=key)
        return True

    record = _read_record(key)
    if record is None:
        return False
    pid = record.get("pid")
    if _pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        for _ in range(50):                       # give it half a second to go
            if not _pid_alive(pid):
                break
            time.sleep(0.01)
    _drop_record(key)
    notify_change("blender_daemons", key=key)
    return True


def stop_all() -> int:
    stopped = 0
    for record in _all_records():
        if stop(str(record["key"])):
            stopped += 1
    leftover = False
    with _LOCK:
        for key, daemon in list(_LOCAL.items()):
            daemon.stop()
            _LOCAL.pop(key, None)
            leftover = True
    if leftover:
        notify_change("blender_daemons")
    return stopped


def reap() -> int:
    """Drop records whose engine exited on its own (idle timeout, crash)."""
    dropped = 0
    for record in _all_records():
        if _probe(record) is None:
            _drop_record(str(record["key"]))
            dropped += 1
    with _LOCK:
        for key, daemon in list(_LOCAL.items()):
            if not daemon.is_alive():
                _LOCAL.pop(key, None)
    if dropped:
        notify_change("blender_daemons")
    return dropped


def info() -> Dict[str, Any]:
    """The connector page's view: capacity, availability and every live engine.

    Each engine is asked what it is doing rather than inferred from a file, so
    the page reports the truth about processes this one never started.
    """
    cfg = store.load()
    daemons: List[Dict[str, Any]] = []
    for record in sorted(_all_records(), key=lambda r: str(r.get("key"))):
        state = _probe(record)
        if state is None:
            _drop_record(str(record["key"]))
            continue
        started = float(record.get("started_at") or 0)
        daemons.append({
            "key": record.get("key"),
            "alive": True,
            # Connected but not answering within the probe window: an engine in
            # the middle of a long operation, which is worth showing as such
            # rather than as an engine with no objects.
            "busy": bool(state.get("busy")),
            "pid": record.get("pid"),
            "owner_pid": record.get("owner_pid"),
            "version": record.get("version", ""),
            "objects": state.get("objects") or [],
            "revision": state.get("revision"),
            "commands": state.get("commands"),
            "uptime_s": round(time.time() - started, 1) if started else state.get("uptime_s"),
            "rss_bytes": _rss(record.get("pid")),
            "log": record.get("log"),
        })
    return {
        "daemons": daemons,
        "running": len(daemons),
        "max_daemons": int(cfg["max_daemons"]),
        "mode": cfg["mode"],
        "enabled": bool(cfg.get("enabled", True)),
        **availability(),
    }


def running_count(*, probe: bool = False) -> int:
    """How many engines are up.

    Cheap by default (a live pid), because the health snapshot asks on every
    call and a socket round-trip per engine is not worth it there. ``probe=True``
    also checks that each one still answers.
    """
    records = _all_records()
    if probe:
        return sum(1 for r in records if _probe(r) is not None)
    return sum(1 for r in records if _pid_alive(r.get("pid")))


def _rss(pid: Any) -> Optional[int]:
    from connectors.blender.daemon import _rss as rss
    try:
        return rss(int(pid))
    except (TypeError, ValueError):
        return None


__all__ = ["acquire", "call", "get", "stop", "stop_all", "reap", "info",
           "availability", "running_count"]
