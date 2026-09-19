"""Service health snapshot.

One function that reports whether the moving parts are alive: database
reachability and the row counts of the core stores, the liveness of every
background service, on-disk state sizes, provider readiness, and the agent
build cache.

It lives here rather than in the route so both callers can use it: the
``GET /api/health`` endpoint and the Service Agent's ``service_health`` tool.
Neither should have to go through HTTP to reach the other.

Never raises. A probe that fails is reported as failed, because a health check
that errors out tells an operator less than one that says which part is down.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from common import db
from common.paths import AGENTS_HUB_ROOT, DB_FILE

# Core stores whose row counts say how much state has accumulated.
_COUNTED_TABLES = (
    "runs", "run_payloads", "tasks", "sessions", "nodes",
    "continuations", "task_activity",
)


def _dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except Exception:
                    pass
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except Exception:
        return 0


def _database() -> tuple[Dict[str, Any], bool]:
    try:
        conn = db.get_conn()
        counts: Dict[str, Optional[int]] = {}
        for table in _COUNTED_TABLES:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                counts[table] = None
        running_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('running','stop')"
        ).fetchone()[0]
        return {"reachable": True, "counts": counts, "running_runs": running_runs}, True
    except Exception as e:
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
    except Exception:
        services["plan_scheduler"] = None
    try:
        from managers.run_watchdog import watchdog
        services["run_watchdog"] = watchdog.is_running()
    except Exception:
        services["run_watchdog"] = None
    try:
        from connectors.telegram.telegram_runner import service as tg
        services["telegram_poller"] = tg.is_running()
    except Exception:
        services["telegram_poller"] = None
    # The external-state publisher is an asyncio task on the app, so it is only
    # observable when a running app hands us its state.
    pub = getattr(app_state, "external_publisher", None) if app_state is not None else None
    services["external_publisher"] = bool(pub and not pub.done()) if pub is not None else None
    return services


def _storage() -> Dict[str, int]:
    logs_dir = AGENTS_HUB_ROOT / "run_logs"
    return {
        "db_bytes": _file_size(DB_FILE),
        "db_wal_bytes": _file_size(Path(str(DB_FILE) + "-wal")),
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
    }


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
    except Exception as e:
        return {"available": None, "error": str(e)}


def _agent_cache() -> Dict[str, Any]:
    try:
        from common.config import settings
        from agents.agent_cache import cache_stats
        return {"enabled": bool(settings.agent_cache_enabled), **cache_stats()}
    except Exception:
        return {"enabled": None}


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
    }
