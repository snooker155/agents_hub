"""
The members of a deployment: every process that is part of it, and what
each one is doing.

Once runs, nodes and containers can live on different hosts, "is the
service up" stops being one process's answer. A backend replica and a worker
each register a row in ``members`` (``common/migrations/0008_members.sql``)
when they start and refresh it every :data:`BEAT_SECONDS` with a small
``load`` snapshot (runs they carry, SSE clients they serve, launches they
track). The deployment map (``dashboard/backend/routes/deployment.py``,
``ah deployment``) reads those rows beside the leases, the launch queue and
the per-host state of runs, nodes and containers, so an operator sees where
everything runs, how busy each process is and which one holds which role.

A member is *live* while its heartbeat is younger than
:data:`STALE_AFTER_SECONDS`, *stopped* once it wrote ``stopped_at`` on a
clean shutdown, and *stale* in between: a process that went away without
saying so. Stale rows older than a day are pruned by the maintenance sweep.

Each member also writes its own log to ``service_logs/<member id>.log``
under the state root (a rotating file, see :func:`attach_log_file`), so the
map can show a process's log the way it shows a node's, and mirrors it to
the object store when one is configured (common/blobs.py).
"""
from __future__ import annotations

import logging
import os
import re
import socket
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import db
from common.leases import owner_id
from common.paths import AGENTS_HUB_ROOT, PROJECT_ROOT

log = logging.getLogger("common.members")

BEAT_SECONDS = float(os.environ.get("AGENTS_HUB_MEMBER_BEAT_SECONDS", "15"))
STALE_AFTER_SECONDS = float(os.environ.get("AGENTS_HUB_MEMBER_STALE_SECONDS", "60"))
#: Stale rows older than this are dropped by :func:`prune`.
FORGET_AFTER_SECONDS = 24 * 3600.0

SERVICE_LOGS_DIR = AGENTS_HUB_ROOT / "service_logs"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3

_version_cache: Optional[str] = None
_log_state: Dict[str, Any] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def app_version() -> str:
    """The commit this checkout runs, or the package version when there is
    no git (an installed image built from a tarball)."""
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    version = os.environ.get("AGENTS_HUB_VERSION", "").strip()
    if not version:
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=5)
            version = out.stdout.strip() if out.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
    _version_cache = version or "unknown"
    return _version_cache


def _safe_name(member_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", member_id)[:120] or "member"


def log_path(member_id: Optional[str] = None) -> Path:
    return SERVICE_LOGS_DIR / f"{_safe_name(member_id or owner_id())}.log"


# ── the row ──────────────────────────────────────────────────────────────────

def register(role: str, *, capabilities: Optional[Dict[str, Any]] = None,
             load: Optional[Dict[str, Any]] = None, member_id: Optional[str] = None) -> Dict[str, Any]:
    """Create or take over this process's row. Called once at startup; a row
    left by an earlier process with the same id (a pinned
    AGENTS_HUB_INSTANCE_ID restarted) is simply overwritten."""
    member_id = member_id or owner_id()
    now = _now().isoformat()
    row = {
        "member_id": member_id, "role": role, "host": socket.gethostname(),
        "pid": os.getpid(), "version": app_version(), "started_at": now,
        "heartbeat_at": now, "stopped_at": None,
        "capabilities": db.dumps(capabilities or {}), "load": db.dumps(load or {}),
        "log_file": str(_log_state.get("path") or log_path(member_id)),
    }
    with db.transaction() as conn:
        conn.execute(
            db.upsert_sql("members", tuple(row), ("member_id",)), tuple(row.values()))
    log.info("member %s registered as %s on %s", member_id, role, row["host"])
    return get(member_id) or row


def beat(load: Optional[Dict[str, Any]] = None, *, member_id: Optional[str] = None) -> bool:
    """Refresh the heartbeat and the load snapshot. False when the row is
    missing (never registered, or pruned): the caller registers again."""
    member_id = member_id or owner_id()
    with db.transaction() as conn:
        if load is None:
            cur = conn.execute(
                "UPDATE members SET heartbeat_at = ?, stopped_at = NULL WHERE member_id = ?",
                (_now().isoformat(), member_id))
        else:
            cur = conn.execute(
                "UPDATE members SET heartbeat_at = ?, stopped_at = NULL, load = ? WHERE member_id = ?",
                (_now().isoformat(), db.dumps(load), member_id))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def mark_stopped(member_id: Optional[str] = None) -> None:
    """A clean shutdown: the row stays (the map shows it as stopped, with its
    log) but no longer counts as a place where anything runs."""
    member_id = member_id or owner_id()
    try:
        with db.transaction() as conn:
            conn.execute("UPDATE members SET stopped_at = ? WHERE member_id = ?",
                         (_now().isoformat(), member_id))
    except Exception:  # noqa: BLE001 - best-effort at shutdown, must not raise
        log.debug("could not mark member %s stopped", member_id, exc_info=True)


def forget(member_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM members WHERE member_id = ?", (member_id,))
        return int(getattr(cur, "rowcount", 0) or 0) > 0


def _decorate(rec: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    rec["capabilities"] = db.loads(rec.get("capabilities"), {}) or {}
    rec["load"] = db.loads(rec.get("load"), {}) or {}
    beat_at = _parse(rec.get("heartbeat_at"))
    started = _parse(rec.get("started_at"))
    age = (now - beat_at).total_seconds() if beat_at else None
    rec["heartbeat_age_seconds"] = age
    rec["uptime_seconds"] = (now - started).total_seconds() if started else None
    if rec.get("stopped_at"):
        rec["status"] = "stopped"
    elif age is not None and age <= STALE_AFTER_SECONDS:
        rec["status"] = "live"
    else:
        rec["status"] = "stale"
    rec["self"] = rec.get("member_id") == owner_id()
    return rec


def get(member_id: str) -> Optional[Dict[str, Any]]:
    row = db.get_conn().execute(
        "SELECT * FROM members WHERE member_id = ?", (member_id,)).fetchone()
    return _decorate(dict(row)) if row is not None else None


def list_members(*, include_stopped: bool = True) -> List[Dict[str, Any]]:
    """Every member, live first, then stale, then stopped, each with its
    status, heartbeat age and uptime worked out."""
    rows = db.get_conn().execute(
        "SELECT * FROM members ORDER BY started_at DESC").fetchall()
    now = _now()
    out = [_decorate(dict(r), now) for r in rows]
    if not include_stopped:
        out = [m for m in out if m["status"] != "stopped"]
    order = {"live": 0, "stale": 1, "stopped": 2}
    out.sort(key=lambda m: (order.get(m["status"], 3), m.get("role") or "", m.get("host") or ""))
    return out


def prune(older_than_seconds: float = FORGET_AFTER_SECONDS) -> int:
    """Drop stopped or stale rows whose last beat is older than the window."""
    cutoff = (_now() - timedelta(seconds=older_than_seconds)).isoformat()
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM members WHERE heartbeat_at < ?", (cutoff,))
        return int(getattr(cur, "rowcount", 0) or 0)


# ── the process's own log ────────────────────────────────────────────────────

def attach_log_file(member_id: Optional[str] = None) -> Optional[Path]:
    """Send this process's logging to ``service_logs/<member id>.log`` as
    well as wherever it already goes (stdout under uvicorn, the terminal for
    ``ah worker``). Idempotent. Never raises: a process that cannot write its
    own log file still runs, the map just has no log for it."""
    member_id = member_id or owner_id()
    if _log_state.get("path"):
        return _log_state["path"]
    try:
        SERVICE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = log_path(member_id)
        handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        _log_state["path"] = path
        _log_state["handler"] = handler
        _log_state["mirrored_size"] = 0
        return path
    except Exception:  # noqa: BLE001 - never raises (see docstring)
        log.debug("could not attach the member log file", exc_info=True)
        return None


def detach_log_file() -> None:
    handler = _log_state.pop("handler", None)
    if handler is not None:
        try:
            logging.getLogger().removeHandler(handler)
            handler.close()
        except Exception:  # noqa: BLE001 - best-effort at shutdown, must not raise
            log.debug("could not detach the member log file", exc_info=True)
    _log_state.pop("path", None)


def mirror_log(*, force: bool = False) -> bool:
    """Copy the member log to the object store when one is configured and
    the file grew since the last copy. Cheap when nothing changed."""
    path = _log_state.get("path")
    if not path:
        return False
    try:
        from common import blobs
        if not blobs.configured():
            return False
        size = Path(path).stat().st_size if Path(path).exists() else 0
        if not force and size == _log_state.get("mirrored_size", 0):
            return False
        ok = blobs.mirror(blobs.rel(path))
        if ok:
            _log_state["mirrored_size"] = size
        return ok
    except Exception:  # noqa: BLE001 - best-effort mirror, must not block the beat loop
        log.debug("log mirror failed", exc_info=True)
        return False


def read_log(member_id: str, tail: int = 500) -> Optional[str]:
    """The last ``tail`` lines of a member's log: the local file when this
    is that member's host, else the mirrored copy, else None."""
    rec = get(member_id)
    if rec is None:
        return None
    path_str = rec.get("log_file") or str(log_path(member_id))
    text: Optional[str] = None
    path = Path(path_str)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
    if text is None:
        try:
            from common import blobs
            text = blobs.read_text(blobs.rel(path))
        except ValueError:
            # blobs.rel() rejects a path outside AGENTS_HUB_ROOT; blobs.read_text
            # itself never raises (common/blobs.py's own contract).
            text = None
    if text is None:
        return None
    lines = text.splitlines()
    return "\n".join(lines[-max(1, int(tail)):])


# ── the heartbeat loop for a process ─────────────────────────────────────────

class MemberBeat(threading.Thread):
    """Registers the process and keeps its row fresh from a daemon thread.

    ``load_fn`` returns the load snapshot to store on each beat; it runs on
    this thread, so it must be cheap and must not touch the event loop. The
    backend and the worker both use this class; the worker could beat from
    its own tick, but one implementation is easier to keep honest.
    """

    def __init__(self, role: str, *, capabilities: Optional[Dict[str, Any]] = None,
                 load_fn=None, interval: float = BEAT_SECONDS) -> None:
        super().__init__(name="member-beat", daemon=True)
        self.role = role
        self.capabilities = dict(capabilities or {})
        self.load_fn = load_fn
        self.interval = interval
        self._halt = threading.Event()
        self.beats = 0
        self.registered = False

    def _load(self) -> Dict[str, Any]:
        if self.load_fn is None:
            return {}
        try:
            return dict(self.load_fn() or {})
        except Exception:  # noqa: BLE001 - a caller-supplied callback must not break the beat loop
            log.debug("load_fn failed", exc_info=True)
            return {}

    def start(self) -> None:  # type: ignore[override]
        attach_log_file()
        try:
            register(self.role, capabilities=self.capabilities, load=self._load())
            self.registered = True
        except Exception:  # noqa: BLE001 - startup must not raise, the next beat retries
            log.warning("member registration failed; will retry on the next beat", exc_info=True)
        super().start()

    def run(self) -> None:
        while not self._halt.wait(self.interval):
            try:
                if not beat(self._load()):
                    register(self.role, capabilities=self.capabilities, load=self._load())
                self.beats += 1
            except Exception:  # noqa: BLE001 - background loop, must keep running until stop()
                log.debug("member beat failed", exc_info=True)
            # The log mirror rides on the beat: once a minute is plenty.
            if self.beats % max(1, int(60 / max(self.interval, 1))) == 0:
                mirror_log()

    def stop(self) -> None:
        self._halt.set()
        mark_stopped()
        mirror_log(force=True)


__all__ = [
    "BEAT_SECONDS", "STALE_AFTER_SECONDS", "MemberBeat", "app_version", "attach_log_file",
    "beat", "detach_log_file", "forget", "get", "list_members", "log_path", "mark_stopped",
    "mirror_log", "prune", "read_log", "register",
]

