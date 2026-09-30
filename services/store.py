"""
Service persistence: the ``services`` and ``service_events`` tables.

A row is the desired state; the replicas that realise it are resident
instances with this row's id in ``instances.service_id`` (services/replicas.py).
"""
from __future__ import annotations

import hmac
import secrets as _secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from common import db

SERVICE_COLUMNS: Tuple[str, ...] = (
    "service_id", "name", "agent_id", "workspace", "environment_id", "environment_name",
    "kind", "is_default", "status", "paused_reason",
    "replicas_min", "replicas_max", "concurrency", "take_tasks", "idle_stop_seconds",
    "budget_usd", "agent_version",
    "is_exposed", "expose_token", "exposed_at", "inbound_secret",
    "created_by", "created_at", "updated_at",
)

KIND_AGENT = "agent"
KIND_RUNNER = "runner"
KINDS = (KIND_AGENT, KIND_RUNNER)

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"

MAX_REPLICAS = 64
MAX_CONCURRENCY = 32
EVENTS_KEEP = 500

# Fields an update may set to None on purpose.
CLEARABLE = ("paused_reason", "budget_usd", "agent_version", "expose_token", "exposed_at",
             "inbound_secret", "environment_id", "environment_name")

_BOOLS = ("is_default", "take_tasks", "is_exposed")
_INTS = ("replicas_min", "replicas_max", "concurrency", "idle_stop_seconds", "agent_version")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_service_id() -> str:
    return "svc_" + uuid.uuid4().hex[:16]


def normalize_workspace(workspace: Optional[str]) -> str:
    from common.workspace_context import normalize_workspace_name
    return normalize_workspace_name(workspace) or "default"


def _row_to_dict(row) -> Dict[str, Any]:
    rec: Dict[str, Any] = {k: row[k] for k in SERVICE_COLUMNS}
    extra = db.loads(row["extra"], {}) or {}
    if isinstance(extra, dict):
        rec.update(extra)
    for k in _BOOLS:
        rec[k] = bool(rec.get(k))
    for k in _INTS:
        if rec.get(k) is not None:
            try:
                rec[k] = int(rec[k])
            except (TypeError, ValueError):
                rec[k] = None
    if rec.get("budget_usd") is not None:
        try:
            rec["budget_usd"] = float(rec["budget_usd"])
        except (TypeError, ValueError):
            rec["budget_usd"] = None
    return rec


def _write(conn, merged: Dict[str, Any]) -> None:
    data = dict(merged)
    cols = {k: data.pop(k, None) for k in SERVICE_COLUMNS}
    for k in _BOOLS:
        cols[k] = 1 if cols.get(k) else 0
    conn.execute(
        db.upsert_sql("services", SERVICE_COLUMNS + ("extra",), ("service_id",)),
        [cols[k] for k in SERVICE_COLUMNS] + [db.dumps(data)],
    )


def clamp_replicas(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return min(max(n, 0), MAX_REPLICAS)


def clamp_concurrency(value: Any, default: int = 4) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return min(max(n, 1), MAX_CONCURRENCY)


# ── Reads ────────────────────────────────────────────────────────────────────

def get(service_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not service_id:
        return None
    row = db.get_conn().execute(
        "SELECT * FROM services WHERE service_id = ?", (str(service_id),)).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_services(*, workspace: Optional[str] = None, agent_id: Optional[str] = None,
                  kind: Optional[str] = None, status: Optional[str] = None,
                  environment_id: Optional[str] = None,
                  include_runners: bool = True, limit: int = 500) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if workspace:
        clauses.append("workspace = ?")
        params.append(normalize_workspace(workspace))
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if environment_id:
        clauses.append("environment_id = ?")
        params.append(environment_id)
    if not include_runners:
        clauses.append("kind != ?")
        params.append(KIND_RUNNER)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.get_conn().execute(
        f"SELECT * FROM services{where} ORDER BY kind DESC, created_at DESC LIMIT ?",
        params + [int(limit)],
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def find_agent_service(workspace: Optional[str], agent_id: str,
                       environment_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The agent's own service in the workspace: the one matching the
    environment first, else one with no environment recorded. A paused one
    counts too: pausing an agent's service must stop its turns, not send
    them to the runner behind the operator's back."""
    ws = normalize_workspace(workspace)
    rows = db.get_conn().execute(
        "SELECT * FROM services WHERE workspace = ? AND agent_id = ? AND kind = ? "
        "ORDER BY created_at",
        (ws, str(agent_id), KIND_AGENT),
    ).fetchall()
    services = [_row_to_dict(r) for r in rows]
    for svc in services:
        if (svc.get("environment_id") or None) == (environment_id or None):
            return svc
    for svc in services:
        if not svc.get("environment_id"):
            return svc
    return None


def find_runner(workspace: Optional[str], environment_id: Optional[str]) -> Optional[Dict[str, Any]]:
    ws = normalize_workspace(workspace)
    sql = "SELECT * FROM services WHERE workspace = ? AND kind = ? AND is_default = 1 AND "
    params: List[Any] = [ws, KIND_RUNNER]
    if environment_id:
        sql += "environment_id = ?"
        params.append(environment_id)
    else:
        sql += "(environment_id IS NULL OR environment_id = '')"
    row = db.get_conn().execute(sql + " ORDER BY created_at LIMIT 1", params).fetchone()
    return _row_to_dict(row) if row is not None else None


def runner_defaults() -> Dict[str, Any]:
    """What a new runner service is created with (common/config.py)."""
    from common.config import live_setting, settings

    def _int(key: str, default: int) -> int:
        try:
            return int(live_setting(key, str(default)))
        except ValueError:
            return default

    return {
        "replicas_min": clamp_replicas(_int("AGENTS_HUB_RUNNER_MIN", settings.runner_min), 1),
        "replicas_max": max(1, clamp_replicas(_int("AGENTS_HUB_RUNNER_MAX", settings.runner_max), 4)),
        "concurrency": clamp_concurrency(_int("AGENTS_HUB_RUNNER_CONCURRENCY", settings.runner_concurrency), 8),
        "idle_stop_seconds": max(0, _int("AGENTS_HUB_RUNNER_IDLE_SECONDS", settings.runner_idle_seconds)),
    }


def ensure_runner(workspace: Optional[str], environment_id: Optional[str] = None,
                  environment_name: Optional[str] = None) -> Dict[str, Any]:
    """The workspace's runner service for the environment, created on first use."""
    found = find_runner(workspace, environment_id)
    if found is not None:
        return found
    ws = normalize_workspace(workspace)
    defaults = runner_defaults()
    name = f"{ws} runner" + (f" · {environment_name}" if environment_name else "")
    return create(
        name=name, agent_id=None, workspace=ws, environment_id=environment_id,
        environment_name=environment_name, kind=KIND_RUNNER, is_default=True,
        **defaults,
    )


def get_by_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """The published service holding ``token``, compared in constant time."""
    if not token or not isinstance(token, str):
        return None
    presented = token.encode("utf-8")
    rows = db.get_conn().execute(
        "SELECT * FROM services WHERE is_exposed = 1 AND expose_token IS NOT NULL").fetchall()
    found = None
    for row in rows:
        stored = row["expose_token"]
        if isinstance(stored, str) and stored and hmac.compare_digest(
                stored.encode("utf-8"), presented) and found is None:
            found = _row_to_dict(row)
    return found


# ── Writes ───────────────────────────────────────────────────────────────────

def create(*, name: str, agent_id: Optional[str], workspace: Optional[str],
           environment_id: Optional[str] = None, environment_name: Optional[str] = None,
           kind: Optional[str] = None, is_default: bool = False,
           replicas_min: int = 1, replicas_max: int = 1, concurrency: int = 4,
           take_tasks: bool = False, idle_stop_seconds: int = 600,
           budget_usd: Optional[float] = None, agent_version: Optional[int] = None,
           created_by: Optional[str] = None, service_id: Optional[str] = None,
           **extra: Any) -> Dict[str, Any]:
    now = utc_iso()
    kind = kind or (KIND_AGENT if agent_id else KIND_RUNNER)
    if kind not in KINDS:
        raise ValueError(f"unknown service kind: {kind}")
    if kind == KIND_AGENT and not agent_id:
        raise ValueError("an agent service needs an agent")
    replicas_min = clamp_replicas(replicas_min, 1)
    replicas_max = max(replicas_min, clamp_replicas(replicas_max, 1), 1 if kind == KIND_RUNNER else 0)
    rec: Dict[str, Any] = {
        "service_id": service_id or new_service_id(),
        "name": (name or "").strip()[:120] or (agent_id or "runner"),
        "agent_id": agent_id or None,
        "workspace": normalize_workspace(workspace),
        "environment_id": environment_id or None,
        "environment_name": environment_name or None,
        "kind": kind,
        "is_default": bool(is_default),
        "status": STATUS_ACTIVE,
        "paused_reason": None,
        "replicas_min": replicas_min,
        "replicas_max": replicas_max,
        "concurrency": clamp_concurrency(concurrency),
        "take_tasks": bool(take_tasks) and kind == KIND_AGENT,
        "idle_stop_seconds": max(0, int(idle_stop_seconds or 0)),
        "budget_usd": float(budget_usd) if budget_usd is not None else None,
        "agent_version": int(agent_version) if agent_version is not None else None,
        "is_exposed": False,
        "expose_token": None,
        "exposed_at": None,
        "inbound_secret": None,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }
    rec.update({k: v for k, v in extra.items() if v is not None})
    with db.transaction() as conn:
        _write(conn, rec)
    _notify()
    return rec


def update(service_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM services WHERE service_id = ?", (str(service_id),)).fetchone()
        if row is None:
            return None
        current = _row_to_dict(row)
        merged = {**current, **{k: v for k, v in updates.items() if v is not None}}
        for k in CLEARABLE:
            if k in updates and updates[k] is None:
                merged[k] = None
        if "replicas_min" in updates or "replicas_max" in updates:
            merged["replicas_min"] = clamp_replicas(merged.get("replicas_min"), 0)
            floor = 1 if merged.get("kind") == KIND_RUNNER else 0
            merged["replicas_max"] = max(merged["replicas_min"], clamp_replicas(merged.get("replicas_max"), 1), floor)
        if "concurrency" in updates:
            merged["concurrency"] = clamp_concurrency(merged.get("concurrency"))
        if "idle_stop_seconds" in updates:
            merged["idle_stop_seconds"] = max(0, int(merged.get("idle_stop_seconds") or 0))
        if merged.get("kind") == KIND_RUNNER:
            merged["take_tasks"] = False
        merged["service_id"] = str(service_id)
        merged["updated_at"] = utc_iso()
        _write(conn, merged)
    _notify()
    return merged


def delete(service_id: str) -> bool:
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM services WHERE service_id = ?", (str(service_id),))
        conn.execute("DELETE FROM service_events WHERE service_id = ?", (str(service_id),))
        conn.execute("UPDATE instances SET service_id = NULL WHERE service_id = ?", (str(service_id),))
        removed = cur.rowcount > 0
    if removed:
        _notify()
    return removed


def pause(service_id: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    svc = update(service_id, status=STATUS_PAUSED, paused_reason=(reason or "paused")[:300])
    if svc:
        add_event(service_id, "paused", reason or "paused by operator")
    return svc


def resume(service_id: str) -> Optional[Dict[str, Any]]:
    svc = update(service_id, status=STATUS_ACTIVE, paused_reason=None)
    if svc:
        add_event(service_id, "resumed", "resumed by operator")
    return svc


# ── Publication ──────────────────────────────────────────────────────────────

def publish(service_id: str) -> Optional[Dict[str, Any]]:
    token = _secrets.token_hex(32)
    svc = update(service_id, is_exposed=True, expose_token=token, exposed_at=utc_iso())
    if svc:
        add_event(service_id, "published", f"token {token[:8]}…")
    return svc


def unpublish(service_id: str) -> Optional[Dict[str, Any]]:
    svc = update(service_id, is_exposed=False, expose_token=None, exposed_at=None)
    if svc:
        add_event(service_id, "unpublished", "public address withdrawn")
    return svc


def set_inbound_secret(service_id: str, secret: Optional[str]) -> Optional[Dict[str, Any]]:
    return update(service_id, inbound_secret=(secret or None))


def public_view(service: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in service.items() if k != "inbound_secret"}
    out["inbound_secret_configured"] = bool(service.get("inbound_secret"))
    return out


# ── Events ───────────────────────────────────────────────────────────────────

def add_event(service_id: str, kind: str, detail: str = "",
              instance_id: Optional[str] = None) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO service_events (event_id, service_id, at, kind, detail, instance_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("sev_" + uuid.uuid4().hex[:16], str(service_id), utc_iso(), str(kind),
             str(detail or "")[:2000], instance_id),
        )
        # Keep the journal bounded: only the newest EVENTS_KEEP rows per service.
        rows = conn.execute(
            "SELECT event_id FROM service_events WHERE service_id = ? ORDER BY at DESC LIMIT ? OFFSET ?",
            (str(service_id), db.NO_LIMIT(), EVENTS_KEEP),
        ).fetchall()
        if rows:
            conn.executemany("DELETE FROM service_events WHERE event_id = ?",
                             [(r["event_id"],) for r in rows])


def events(service_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    rows = db.get_conn().execute(
        "SELECT * FROM service_events WHERE service_id = ? ORDER BY at DESC LIMIT ?",
        (str(service_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def recent_events(service_id: str, kinds: Tuple[str, ...], since_iso: str) -> int:
    row = db.get_conn().execute(
        f"SELECT COUNT(*) FROM service_events WHERE service_id = ? AND at >= ? "
        f"AND kind IN ({', '.join('?' * len(kinds))})",
        [str(service_id), since_iso, *kinds]).fetchone()
    return int(row[0] or 0)


def _notify() -> None:
    try:
        from common.session_broker import notify_change
        notify_change("services")
    except Exception:  # noqa: BLE001 - a missed list refresh is not a failure of the write
        import logging
        logging.getLogger(__name__).debug("services: change notification failed", exc_info=True)
