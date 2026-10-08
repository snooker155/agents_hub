"""Service health snapshot.

One function that reports whether the moving parts are alive: database
reachability and the row counts of the core stores, the liveness of every
background service, on-disk state sizes, provider readiness, the agent
build cache, and a summary of every flow/loop/team/scenario run by kind
(common/entity_runs.py); the individual active runs are the deployment
map's job (dashboard/backend/routes/deployment.py), this is just the count.

It lives here rather than in the route so both callers can use it: the
``GET /api/health`` endpoint and the Service Agent's ``service_health`` tool.
Neither should have to go through HTTP to reach the other.

Never raises. A probe that fails is reported as failed, because a health check
that errors out tells an operator less than one that says which part is down.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from common import db
from common.paths import AGENTS_HUB_ROOT, DB_FILE

log = logging.getLogger(__name__)

# Core stores whose row counts say how much state has accumulated.
_COUNTED_TABLES = (
    "runs", "run_payloads", "tasks", "sessions", "chats", "nodes",
    "continuations", "task_activity",
)


#: Runs still holding a slot. A stop request (``stop``) holds one until the
#: run ends, but a run stopped in process (a team turn, a chat turn) gets
#: ``stop`` and ``finished_at`` together and nothing writes it again, so a
#: ``stop`` with a finish time is over, not running.
RUNNING_RUNS_SQL = ("status = 'running' OR (status = 'stop' AND "
                    "(finished_at IS NULL OR finished_at = ''))")


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except Exception:  # noqa: BLE001 - a health probe must never raise
                    log.debug("stat failed for %s", f, exc_info=True)
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except Exception:  # noqa: BLE001 - a health probe must never raise
        log.debug("stat failed for %s", path, exc_info=True)
        return 0


def _database() -> tuple[Dict[str, Any], bool]:
    try:
        conn = db.get_conn()
        counts: Dict[str, Optional[int]] = {}
        for table in _COUNTED_TABLES:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:  # noqa: BLE001 - a health probe must never raise
                log.debug("row count failed for table %s", table, exc_info=True)
                counts[table] = None
        running_runs = conn.execute(f"SELECT COUNT(*) FROM runs WHERE {RUNNING_RUNS_SQL}").fetchone()[0]
        return {"reachable": True, "counts": counts, "running_runs": running_runs}, True
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("database probe failed", exc_info=True)
        return {"reachable": False, "error": str(e)}, False


def _services(app_state: Any = None) -> Dict[str, Optional[bool]]:
    """Liveness of each background service.

    ``None`` means "could not tell" (the module failed to import, or the
    publisher was never wired up), which is different from "not running".
    """
    services: Dict[str, Optional[bool]] = {}
    try:
        from plans.scheduler import scheduler
        services["plan_scheduler"] = scheduler.is_running()
    except Exception:  # noqa: BLE001 - module may not be importable; "could not tell"
        log.debug("plan_scheduler liveness check failed", exc_info=True)
        services["plan_scheduler"] = None
    try:
        from watchers.runner import runner as _watchers
        services["watchers"] = _watchers.is_running()
    except Exception:  # noqa: BLE001 - module may not be importable; "could not tell"
        log.debug("watchers liveness check failed", exc_info=True)
        services["watchers"] = None
    try:
        from managers.run_watchdog import watchdog
        services["run_watchdog"] = watchdog.is_running()
    except Exception:  # noqa: BLE001 - module may not be importable; "could not tell"
        log.debug("run_watchdog liveness check failed", exc_info=True)
        services["run_watchdog"] = None
    try:
        from connectors.telegram.telegram_runner import service as tg
        services["telegram_poller"] = tg.is_running()
    except Exception:  # noqa: BLE001 - module may not be importable; "could not tell"
        log.debug("telegram_poller liveness check failed", exc_info=True)
        services["telegram_poller"] = None
    try:
        from common.singletons import supervisor
        services["singleton_supervisor"] = supervisor.is_running()
    except Exception:  # noqa: BLE001 - module may not be importable; "could not tell"
        log.debug("singleton_supervisor liveness check failed", exc_info=True)
        services["singleton_supervisor"] = None
    # The external-state publisher is an asyncio task on the app, so it is only
    # observable when a running app hands us its state.
    pub = getattr(app_state, "external_publisher", None) if app_state is not None else None
    services["external_publisher"] = bool(pub and not pub.done()) if pub is not None else None
    return services


def _database_bytes() -> tuple[int, int]:
    """(database size, WAL size). On Postgres the server reports the first and
    there is no WAL file of ours to measure."""
    if db.is_postgres():
        try:
            row = db.get_conn().execute(
                "SELECT pg_database_size(current_database())").fetchone()
            return int(row[0] or 0), 0
        except Exception:  # noqa: BLE001 - a health probe must never raise
            log.debug("pg_database_size query failed", exc_info=True)
            return 0, 0
    return _file_size(DB_FILE), _file_size(Path(str(DB_FILE) + "-wal"))


def _storage() -> Dict[str, int]:
    logs_dir = AGENTS_HUB_ROOT / "run_logs"
    db_bytes, wal_bytes = _database_bytes()
    return {
        "db_bytes": db_bytes,
        "db_wal_bytes": wal_bytes,
        "run_logs_bytes": _dir_size(logs_dir),
        "run_logs_files": sum(1 for _ in logs_dir.glob("*.log")) if logs_dir.is_dir() else 0,
        "agents_hub_bytes": _dir_size(AGENTS_HUB_ROOT),
    }


def _providers() -> Dict[str, Any]:
    from common.config import settings
    return {
        "default_provider": settings.default_provider,
        "openai_key_set": bool(settings.openai_api_key),
        "anthropic_key_set": bool(settings.anthropic_api_key),
        "google_key_set": bool(settings.google_api_key),
        "api_auth_enabled": bool(settings.api_token),
        # The posture in force, resolved (a configured token alone still means
        # token mode). Reported so a configuration problem can be read off the
        # health snapshot rather than inferred from a 401. See docs/identity.md.
        "auth_mode": _auth_mode(),
    }


def _auth_mode() -> str:
    """The effective AUTH_MODE, or "unknown" if identity cannot be imported."""
    try:
        from common.identity import current_mode
        return current_mode()
    except Exception:  # noqa: BLE001 - a health probe must never raise
        log.debug("auth mode lookup failed", exc_info=True)
        return "unknown"


def _blender() -> Dict[str, Any]:
    """The geometry engine connector: reachable, and how many engines are up.

    Counted from the cross-process registry, so it reports the engines agents
    started in their own processes rather than only this one's. The binary probe
    behind ``availability`` is cached, so this stays cheap enough to answer on
    every health poll.
    """
    try:
        from connectors.blender import pool, store
        cfg = store.load()
        state = pool.availability()
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "mode": cfg.get("mode"),
            "available": state.get("available"),
            "version": state.get("version", ""),
            "engines_running": pool.running_count(),
            "max_daemons": int(cfg.get("max_daemons") or 0),
            "reason": state.get("reason", ""),
        }
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("blender probe failed", exc_info=True)
        return {"available": None, "error": str(e)}


def _entity_runs() -> Dict[str, Any]:
    """Counts of every flow, loop, team and scenario run, by kind and by kind
    in an active status: the one table every kind's run lives in now
    (common/entity_runs.py). A summary only: the individual active runs (with
    their host and heartbeat age) are the deployment map's job
    (dashboard/backend/routes/deployment.py), not this snapshot's."""
    try:
        from common import entity_runs
        from common.run_status import ACTIVE_STATUSES
        return {
            "total_by_kind": entity_runs.counts_by_kind(),
            "active_by_kind": entity_runs.counts_by_kind(statuses=ACTIVE_STATUSES),
        }
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("entity run counts failed", exc_info=True)
        return {"total_by_kind": {}, "active_by_kind": {}, "error": str(e)}


#: A replica's counts older than this are left out: it stopped beating.
BUILD_COUNTS_FRESH_SECONDS = 120.0


def _replica_build_counts() -> Dict[str, int]:
    """The agent build counts every live instance sent with its last
    heartbeat (runtime/instance_run.py ``_build_counts``), summed. The builds
    live in each replica's memory, where chat, /v1, widget and Telegram turns
    run, so they are only seen through what each replica reports."""
    from datetime import datetime, timezone
    from instances.store import LIVE_STATES

    out = {"entries": 0, "hits": 0, "misses": 0, "replicas": 0}
    now = datetime.now(timezone.utc)
    rows = db.get_conn().execute(
        f"SELECT extra FROM instances WHERE archived_at IS NULL "
        f"AND state IN ({', '.join('?' * len(LIVE_STATES))})", LIVE_STATES).fetchall()
    for row in rows:
        extra = db.loads(row["extra"], {}) or {}
        counts = extra.get("agent_builds")
        if not isinstance(counts, dict):
            continue
        try:  # heartbeat_at is not a column of instances: it lives in extra
            beat = datetime.fromisoformat(str(extra.get("heartbeat_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        if (now - beat).total_seconds() > BUILD_COUNTS_FRESH_SECONDS:
            continue
        out["replicas"] += 1
        for key in ("entries", "hits", "misses"):
            out[key] += int(counts.get(key) or 0)
    return out


def _agent_cache() -> Dict[str, Any]:
    """Agent build reuse (agents/agent_cache.py): how often a run found its
    agent already built instead of assembling prompt, tools and model again.
    Not the providers' prompt cache. Totals over this process and every live
    replica; ``here`` and ``replicas`` keep the two apart."""
    try:
        from common.config import settings
        from agents.agent_cache import cache_stats
        here = cache_stats()
    except Exception:  # noqa: BLE001 - a health probe must never raise
        log.debug("agent cache stats failed", exc_info=True)
        return {"enabled": None}
    try:
        replicas = _replica_build_counts()
    except Exception:  # noqa: BLE001 - this process's own counts still show
        log.debug("replica build counts failed", exc_info=True)
        replicas = {"entries": 0, "hits": 0, "misses": 0, "replicas": 0}
    return {
        "enabled": bool(settings.agent_cache_enabled),
        **{k: here.get(k, 0) + replicas[k] for k in ("entries", "hits", "misses")},
        "here": here,
        "replicas": replicas,
    }


def _cluster() -> Dict[str, Any]:
    """This process's role, who holds which singleton role, the launch queue
    and the outbox: the parts that only exist once a deployment has more
    than one process (docs/workers.md, docs/scaling.md)."""
    out: Dict[str, Any] = {}
    try:
        from common.config import hub_role
        from common import leases
        out["role"] = hub_role()
        out["instance"] = leases.owner_id()
        out["leases"] = leases.all_leases()
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("leases probe failed", exc_info=True)
        out["leases_error"] = str(e)
    try:
        from common import run_queue
        out["queue"] = run_queue.stats()
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("queue probe failed", exc_info=True)
        out["queue_error"] = str(e)
    try:
        from notify import outbound
        out["outbox"] = outbound.stats()
    except Exception as e:  # noqa: BLE001 - a health probe must never raise
        log.debug("outbox probe failed", exc_info=True)
        out["outbox_error"] = str(e)
    return out


def snapshot(app_state: Any = None) -> Dict[str, Any]:
    """Liveness plus a state snapshot.

    ``app_state`` is the FastAPI ``app.state`` when called from the running
    server; omit it out of process, where the app-bound services simply report
    as unknown.
    """
    database, ok = _database()
    return {
        "status": "ok" if ok else "degraded",
        "database": database,
        "services": _services(app_state),
        "storage": _storage(),
        "providers": _providers(),
        "blender": _blender(),
        "agent_cache": _agent_cache(),
        "cluster": _cluster(),
        "entity_runs": _entity_runs(),
    }
