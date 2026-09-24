"""Environment rules: naming, defaults, archiving, deletion, resolution, usage.

Scope. An environment belongs to one workspace, or to none (``workspace``
None), in which case every workspace may use it. Names are unique within a
scope, compared without case, so "Sandbox" in workspace a and a global
"Sandbox" may coexist but two "Sandbox" in a may not.

Defaults. At most one default per scope. A run that names no environment gets
its workspace's default, else the global default, else none at all (the
workspace's execution mode, no fence), see :func:`resolve_for`.

Archive, not edit in place. Archiving freezes a profile: it cannot be edited,
made default or picked for a new task, node or job, but whatever already runs
in it keeps running, and a task that already names it still launches with its
fence (dropping the fence of a profile that says ``network: none`` because
someone archived it would be the wrong way to fail). Deletion is only for a
profile nothing references: no active node and no pending scheduled job.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from . import store
from .models import Environment, now_iso

log = logging.getLogger(__name__)


class EnvironmentServiceError(Exception):
    """Base for the service's refusals; ``status`` is the HTTP status a route
    answers with."""
    status = 400


class EnvironmentNotFound(EnvironmentServiceError):
    status = 404


class EnvironmentConflict(EnvironmentServiceError):
    status = 409


class EnvironmentInvalid(EnvironmentServiceError):
    status = 400


# Node statuses that still hold an environment: a stopped or failed node
# record is history and does not block deleting the profile it ran in.
_ACTIVE_NODE_STATUSES = {"starting", "running", "stopping"}
# Scheduled job statuses that will still fire and create work.
_PENDING_JOB_STATUSES = {"scheduled", "paused"}


def _scope_key(workspace: Optional[str]) -> str:
    return (workspace or "").strip()


def _same_name(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


# ── reads ────────────────────────────────────────────────────────────────────

def get_environment(env_id: str) -> Optional[Environment]:
    return store.get(env_id) if env_id else None


def require_environment(env_id: str) -> Environment:
    env = get_environment(env_id)
    if env is None:
        raise EnvironmentNotFound(f"Environment '{env_id}' not found")
    return env


def list_environments(workspace: Optional[str] = None, *, include_archived: bool = False,
                      all_scopes: bool = False) -> List[Environment]:
    """Environments a workspace may use (its own plus the global ones), or
    every environment with ``all_scopes``. Sorted: defaults first, then by name."""
    ws = _scope_key(workspace)
    out = []
    for env in store.all():
        if env.archived and not include_archived:
            continue
        if not all_scopes and env.workspace and env.workspace != ws:
            continue
        out.append(env)
    out.sort(key=lambda e: (not e.is_default, e.workspace is not None, e.name.casefold()))
    return out


# ── writes ───────────────────────────────────────────────────────────────────

def _check_unique(name: str, workspace: Optional[str], exclude_id: Optional[str] = None) -> None:
    scope = _scope_key(workspace)
    for other in store.all():
        if other.id == exclude_id:
            continue
        if _scope_key(other.workspace) == scope and _same_name(other.name, name):
            where = f"workspace '{scope}'" if scope else "the global scope"
            raise EnvironmentConflict(f"An environment named '{name}' already exists in {where}")


def _clear_defaults(workspace: Optional[str], except_id: str) -> None:
    scope = _scope_key(workspace)
    for other in store.all():
        if other.id != except_id and other.is_default and _scope_key(other.workspace) == scope:
            other.is_default = False
            other.updated_at = now_iso()
            store.put(other)


def _build(data: Dict[str, Any]) -> Environment:
    try:
        return Environment.model_validate(data)
    except Exception as exc:  # pydantic ValidationError, or a ValueError from a validator
        raise EnvironmentInvalid(_validation_message(exc)) from exc


def _validation_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            parts = []
            for err in errors():
                loc = ".".join(str(p) for p in err.get("loc") or ())
                msg = str(err.get("msg") or "").removeprefix("Value error, ")
                parts.append(f"{loc}: {msg}" if loc else msg)
            if parts:
                return "; ".join(parts)
        except Exception:  # noqa: BLE001, S110 - fall through to the plain message
            pass
    return str(exc)


_EDITABLE = ("name", "description", "workspace", "mode", "image", "packages", "network",
             "limits", "env", "is_default")


def create_environment(data: Dict[str, Any]) -> Environment:
    """Create one environment. 409 on a name clash in its scope; a new default
    takes the flag from the scope's previous default."""
    payload = {k: v for k, v in (data or {}).items() if k in _EDITABLE}
    env = _build(payload)
    with store.transaction():
        _check_unique(env.name, env.workspace)
        if env.is_default:
            _clear_defaults(env.workspace, env.id)
        store.put(env)
    return env


def update_environment(env_id: str, patch: Dict[str, Any]) -> Environment:
    """Apply the fields present in ``patch``. An archived environment is
    read only (409)."""
    with store.transaction():
        current = require_environment(env_id)
        if current.archived:
            raise EnvironmentConflict("An archived environment is read only")
        merged = current.model_dump(mode="json")
        for key, value in (patch or {}).items():
            if key not in _EDITABLE:
                continue
            if key in ("network", "limits") and isinstance(value, dict):
                merged[key] = {**(merged.get(key) or {}), **value}
            else:
                merged[key] = value
        merged["updated_at"] = now_iso()
        env = _build(merged)
        _check_unique(env.name, env.workspace, exclude_id=env.id)
        if env.is_default:
            _clear_defaults(env.workspace, env.id)
        store.put(env)
    return env


def archive_environment(env_id: str) -> Environment:
    """Freeze an environment. Idempotent; an archived default stops being the
    default, so new runs in the scope fall back to the next rule."""
    with store.transaction():
        env = require_environment(env_id)
        if not env.archived:
            env.archived_at = now_iso()
            env.is_default = False
            env.updated_at = env.archived_at
            store.put(env)
    return env


def set_default(env_id: str) -> Environment:
    with store.transaction():
        env = require_environment(env_id)
        if env.archived:
            raise EnvironmentConflict("An archived environment cannot be the default")
        _clear_defaults(env.workspace, env.id)
        if not env.is_default:
            env.is_default = True
            env.updated_at = now_iso()
            store.put(env)
    return env


def delete_environment(env_id: str) -> bool:
    """Remove an environment nothing references (409 otherwise)."""
    env = require_environment(env_id)
    counts = usage_counts(env.id)
    if counts["nodes"] or counts["jobs"]:
        raise EnvironmentConflict(
            f"Environment '{env.name}' is in use by {counts['nodes']} node(s) and "
            f"{counts['jobs']} scheduled job(s); stop or change them first, or archive it")
    return store.delete(env.id)


# ── resolution ───────────────────────────────────────────────────────────────

def default_for(workspace: Optional[str]) -> Optional[Environment]:
    """The workspace's own default, else the global default, else None."""
    ws = _scope_key(workspace)
    own = glob = None
    for env in store.all():
        if env.archived or not env.is_default:
            continue
        scope = _scope_key(env.workspace)
        if ws and scope == ws:
            own = env
        elif not scope:
            glob = env
    return own or glob


def resolve_for(workspace: Optional[str], environment_id: Optional[str] = None, *,
                allow_archived: bool = False) -> Optional[Environment]:
    """The environment a new run, node or job in ``workspace`` gets.

    An explicit id wins; it must exist, be usable from the workspace (its own
    or global) and, unless ``allow_archived``, not be archived. Without one,
    :func:`default_for`. Raises :class:`EnvironmentNotFound` /
    :class:`EnvironmentConflict` for an explicit id that fails those checks,
    so a caller picking one learns why instead of silently getting another.
    """
    if environment_id:
        env = require_environment(environment_id)
        ws = _scope_key(workspace)
        if env.workspace and ws and env.workspace != ws:
            raise EnvironmentConflict(
                f"Environment '{env.name}' belongs to workspace '{env.workspace}', not '{ws}'")
        if env.archived and not allow_archived:
            raise EnvironmentConflict(f"Environment '{env.name}' is archived")
        return env
    return default_for(workspace)


# ── usage ────────────────────────────────────────────────────────────────────

def _nodes_using(env_id: str) -> List[Dict[str, Any]]:
    try:
        from managers import node_manager
        return [n for n in node_manager._load_nodes() if n.get("environment_id") == env_id]
    except Exception:  # noqa: BLE001 - usage is informational; an unreadable table counts as none
        log.debug("could not list nodes for environment %s", env_id, exc_info=True)
        return []


def _jobs_using(env_id: str) -> List[Any]:
    try:
        from plans import service as plans_service
        return [j for j in plans_service.list_jobs() if getattr(j, "environment_id", None) == env_id]
    except Exception:  # noqa: BLE001 - same as _nodes_using
        log.debug("could not list jobs for environment %s", env_id, exc_info=True)
        return []


def _status(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _runs_using(env_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Recent runs whose record says they ran in ``env_id`` (the launcher
    writes ``environment_id`` into the run's ``extra`` bag)."""
    try:
        from common import db
        from managers.runs.store import _RUN_LIST_COLUMNS, _list_row_to_record
        rows = db.get_conn().execute(
            f"SELECT {', '.join(_RUN_LIST_COLUMNS)} FROM runs "
            f"WHERE {db.json_text('extra', 'environment_id')} = ? "
            "ORDER BY COALESCE(started_at, created_at) DESC LIMIT ?",
            (env_id, int(limit)),
        ).fetchall()
        return [_list_row_to_record(r) for r in rows]
    except Exception:  # noqa: BLE001 - same as _nodes_using
        log.debug("could not list runs for environment %s", env_id, exc_info=True)
        return []


def usage_counts(env_id: str) -> Dict[str, int]:
    """What blocks a delete: active nodes and pending scheduled jobs."""
    nodes = [n for n in _nodes_using(env_id) if n.get("status") in _ACTIVE_NODE_STATUSES]
    jobs = [j for j in _jobs_using(env_id) if _status(getattr(j, "status", "")) in _PENDING_JOB_STATUSES]
    return {"nodes": len(nodes), "jobs": len(jobs)}


def usage(env_id: str) -> Dict[str, Any]:
    """Every node record, scheduled job and recent run (50) tied to ``env_id``."""
    require_environment(env_id)
    nodes = [{
        "node_id": n.get("node_id"), "agent_id": n.get("agent_id"), "label": n.get("label"),
        "workspace": n.get("workspace"), "status": n.get("status"),
        "execution_mode": n.get("execution_mode"), "started_at": n.get("started_at"),
    } for n in _nodes_using(env_id)]
    jobs = [{
        "id": str(getattr(j, "id", "")), "title": getattr(j, "title", ""),
        "kind": _status(getattr(j, "kind", "")), "status": _status(getattr(j, "status", "")),
        "workspace": getattr(j, "workspace", None),
    } for j in _jobs_using(env_id)]
    runs = [{
        "run_id": r.get("run_id"), "task_id": r.get("task_id"), "agent_id": r.get("agent_id"),
        "workspace": r.get("workspace"), "status": r.get("status"),
        "execution_mode": r.get("execution_mode"), "started_at": r.get("started_at"),
        "finished_at": r.get("finished_at"), "session_id": r.get("session_id"),
    } for r in _runs_using(env_id)]
    return {"nodes": nodes, "jobs": jobs, "runs": runs}


def to_dict(env: Environment, *, with_usage: bool = False) -> Dict[str, Any]:
    data = env.model_dump(mode="json")
    if with_usage:
        data["usage_counts"] = usage_counts(env.id)
    return data


__all__ = [
    "EnvironmentServiceError", "EnvironmentNotFound", "EnvironmentConflict", "EnvironmentInvalid",
    "get_environment", "require_environment", "list_environments", "create_environment",
    "update_environment", "archive_environment", "set_default", "delete_environment",
    "default_for", "resolve_for", "usage_counts", "usage", "to_dict",
]
