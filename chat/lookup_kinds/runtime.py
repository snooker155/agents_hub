"""Lookup kinds: runtime (see chat/lookup_kinds/__init__.py).

What is running, as the person sees it on the Instances, Services,
Deployments and Browser pages, plus the read only Environments catalog:

- ``instance``: a copy of an agent, resident or not (``/instances``,
  ``/nodes``: nodes are gone, the alias stays for what people still call it).
- ``service``: a desired state kept running as replicas (``/services``).
- ``deployment``: a project's app under ``/apps/<slug>/``, and the apps
  section of the ``/deployments`` page (its scheduled-job rows are the
  ``job`` kind of chat/lookup.py; this kind only answers for the apps).
- ``environment``: an execution profile (``/environments``), read only.
- ``browser``: a browser session the Browser page shows (``/browser``).

A card never carries an instance's or a service's messages, inbox or logs,
and a browser session's card never carries the page's own title (rendered
text a stranger's page could have written): see the module docstring of
chat/lookup.py, "Metadata, not content".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from chat import lookup
from chat.actions import HubAction, register_action
from chat.lookup import LookupError_, LookupKind


# ── instance (/instances, /nodes) ────────────────────────────────────────────

def _list_instances(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from instances import store as instance_store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        page = instance_store.list_instances(workspace=ws, limit=limit + 10)
        for inst in page.get("items", []):
            if not lookup.matches(query, inst.get("label"), inst.get("agent_id"), inst.get("instance_id")):
                continue
            at = inst.get("last_activity_at") or inst.get("started_at") or inst.get("created_at")
            rows.append(lookup.row(
                inst.get("instance_id"), inst.get("label") or inst.get("agent_id") or "instance",
                " · ".join(x for x in [inst.get("state"), inst.get("agent_id"), lookup.short_time(at)] if x),
                f"/instances/{quote(str(inst.get('instance_id')))}",
                workspace=ws if ctx.workspace is None else None, at=at,
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _instance_card(ctx: Any, instance_id: str) -> Optional[Dict[str, Any]]:
    from instances import carrier
    # One sync, for this one record only: a fresh read of whether the
    # carrier process is actually still there (chat/lookup.py's "cheap" rule
    # is about a list of many rows, not a single one the person just asked
    # about by id).
    inst = carrier.get(instance_id)
    if inst is None or not lookup.visible(ctx, inst.get("workspace")):
        return None
    service_name = None
    service_id = inst.get("service_id")
    if service_id:
        from services import store as service_store
        svc = service_store.get(service_id)
        service_name = (svc or {}).get("name")
    fields = {
        "instance_id": inst.get("instance_id"),
        "label": inst.get("label"),
        "status": inst.get("state"),
        "resident": carrier.is_resident(inst),
        "kind": inst.get("kind"),
        "agent": inst.get("agent_id"),
        "workspace": inst.get("workspace") or "default",
        "started": inst.get("started_at") or inst.get("created_at"),
        "last_seen": inst.get("last_activity_at"),
        "runs_count": inst.get("runs_count"),
        "environment": inst.get("environment_name") or inst.get("environment_id"),
        "service": service_name,
        "error": lookup.first_line(inst.get("error")),
    }
    return {"title": inst.get("label") or inst.get("agent_id") or instance_id, "fields": fields,
            "note": "Messages, the inbox and logs are on the instance's own page, not here.",
            "url": f"/instances/{quote(instance_id)}"}


def _instance_target(ctx: Any, instance_id: str) -> Optional[Dict[str, Any]]:
    from instances import carrier, store as instance_store
    inst = instance_store.get(instance_id)
    if inst is None or not lookup.visible(ctx, inst.get("workspace")):
        return None
    return {
        "workspace": inst.get("workspace") or "default",
        "label": inst.get("label") or inst.get("agent_id") or instance_id,
        "url": f"/instances/{quote(instance_id)}",
        "resident": carrier.is_resident(inst),
    }


def _instance_stop(ctx: Any, instance_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Same split the route makes (dashboard/backend/routes/instances.py
    ``stop_instance``): a resident copy's process is stopped, any other
    copy's current run is stopped and the row is marked stopped."""
    from instances import carrier, registry as instance_registry, store as instance_store
    from managers import run_manager
    inst = instance_store.get(instance_id)
    if inst is None:
        raise LookupError_(f"No instance '{instance_id}'.", code="not_found")
    if carrier.is_resident(inst):
        if inst.get("carrier_status") not in carrier.LIVE_CARRIER_STATUSES:
            raise LookupError_("This instance's process is already stopped.", code="conflict")
        stopped = carrier.stop(instance_id)
        return {"stopped": stopped}
    if inst.get("state") in instance_store.TERMINAL_STATES:
        raise LookupError_("This instance is already stopped.", code="conflict")
    stopped_run = False
    run_id = inst.get("current_run_id")
    if run_id:
        try:
            stopped_run = run_manager.stop_run_by_id(str(run_id))
        except Exception:  # noqa: BLE001 - the instance is still marked stopped below
            stopped_run = False
    instance_registry.mark_stopped(instance_id, "stopped by operator")
    return {"stopped_run": stopped_run}


def _instance_restart(ctx: Any, instance_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Only a resident copy has a process to replace (the route's own 400),
    matching ``carrier.restart``."""
    from instances import carrier
    if not target.get("resident"):
        raise LookupError_("Only an instance started with Run has a process of its own.", code="conflict")
    try:
        instance = carrier.restart(instance_id)
    except Exception as exc:  # noqa: BLE001 - the agent gets the reason, not a stack
        raise LookupError_(f"Could not restart the instance: {exc}", code="internal")
    if instance is None:
        raise LookupError_("The process runs on another host; restart it from there.", code="conflict")
    return {"state": instance.get("state")}


lookup.register(LookupKind(
    "instance", "resident and task copies of an agent: status, agent, workspace, runs, environment",
    _list_instances, _instance_card, ("/instances", "/nodes"), aliases=("node",),
))
register_action(HubAction(
    "instance", "stop", "stop an instance's process, or its current run",
    _instance_target, _instance_stop,
    "Stop instance {label} in {workspace}: it stops answering until started again; a run in progress fails.",
))
register_action(HubAction(
    "instance", "restart", "replace a resident instance's process with a fresh one",
    _instance_target, _instance_restart,
    "Restart instance {label} in {workspace}: a fresh process replaces it; its conversations and runs stay.",
))


# ── service (/services) ──────────────────────────────────────────────────────

def _list_services(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from services import store as service_store
    rows: List[Dict[str, Any]] = []
    for ws in ctx.workspaces:
        for svc in service_store.list_services(workspace=ws, include_runners=True):
            if not lookup.matches(query, svc.get("name"), svc.get("agent_id"), svc.get("service_id")):
                continue
            rows.append(lookup.row(
                svc.get("service_id"), svc.get("name") or svc.get("agent_id") or "service",
                " · ".join(x for x in [svc.get("status"), svc.get("kind"), svc.get("agent_id")] if x),
                f"/services/{quote(str(svc.get('service_id')))}",
                workspace=ws if ctx.workspace is None else None, at=svc.get("updated_at"),
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _service_card(ctx: Any, service_id: str) -> Optional[Dict[str, Any]]:
    from services import replicas, store as service_store
    svc = service_store.get(service_id)
    if svc is None or not lookup.visible(ctx, svc.get("workspace")):
        return None
    fields = {
        "name": svc.get("name"), "agent": svc.get("agent_id"), "workspace": svc.get("workspace") or "default",
        "kind": svc.get("kind"), "status": svc.get("status"), "paused_reason": svc.get("paused_reason"),
        "environment": svc.get("environment_name") or svc.get("environment_id"),
        "replicas_min": svc.get("replicas_min"), "replicas_max": svc.get("replicas_max"),
        "replicas_live": replicas.summary(service_id).get("live"),
        "concurrency": svc.get("concurrency"), "take_tasks": svc.get("take_tasks"),
        "idle_stop_seconds": svc.get("idle_stop_seconds"), "budget_usd": svc.get("budget_usd"),
        "agent_version": svc.get("agent_version"), "is_exposed": bool(svc.get("is_exposed")),
        "created_at": svc.get("created_at"), "updated_at": svc.get("updated_at"),
    }
    return {"title": svc.get("name") or service_id, "fields": fields,
            "note": "A replica's own conversations are on the service's page, not here.",
            "url": f"/services/{quote(service_id)}"}


def _service_target(ctx: Any, service_id: str) -> Optional[Dict[str, Any]]:
    from services import store as service_store
    svc = service_store.get(service_id)
    if svc is None or not lookup.visible(ctx, svc.get("workspace")):
        return None
    return {"workspace": svc.get("workspace") or "default", "label": svc.get("name") or service_id,
            "url": f"/services/{quote(service_id)}", "status": svc.get("status")}


def _service_pause(ctx: Any, service_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from services import replicas, store as service_store
    svc = service_store.get(service_id)
    if svc is None:
        raise LookupError_(f"No service '{service_id}'.", code="not_found")
    if svc.get("status") == service_store.STATUS_PAUSED:
        raise LookupError_("This service is already paused.", code="conflict")
    updated = service_store.pause(service_id, "paused by operator") or svc
    for rep in replicas.live_replicas(updated):
        replicas.stop_replica(updated, rep, reason="paused")
    return {"status": updated.get("status")}


def _service_resume(ctx: Any, service_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from services import replicas, store as service_store
    svc = service_store.get(service_id)
    if svc is None:
        raise LookupError_(f"No service '{service_id}'.", code="not_found")
    if svc.get("status") == service_store.STATUS_ACTIVE:
        raise LookupError_("This service is already active.", code="conflict")
    updated = service_store.resume(service_id) or svc
    if int(updated.get("replicas_min") or 0) > 0:
        try:
            replicas.start_replica(updated, reason="resumed")
        except Exception as exc:  # noqa: BLE001 - journaled; the supervisor retries on its tick
            service_store.add_event(service_id, "replica_failed", str(exc))
    return {"status": updated.get("status")}


lookup.register(LookupKind(
    "service", "an agent (or a runner) kept running as replicas: status, replicas, environment",
    _list_services, _service_card, ("/services",),
))
register_action(HubAction(
    "service", "pause", "stop every replica of a service and start none until resumed",
    _service_target, _service_pause,
    "Pause service {label} in {workspace}: every replica stops; nothing answers it until resumed.",
))
register_action(HubAction(
    "service", "resume", "let a paused service start its replicas again",
    _service_target, _service_resume,
    "Resume service {label} in {workspace}: it starts replicas again, up to its minimum.",
))


# ── deployment: project apps under /apps, and the /deployments page's apps ──
#
# The scheduled-job rows of /deployments (agent_task / flow / loop / heartbeat
# jobs) are chat/lookup.py's own "job" kind; this kind only answers for the
# project apps section of that page and for /apps/<slug>/ itself. "restart" is
# not registered: it runs the full deploy pipeline (build and start, with the
# project's own commands), which is what the already approval-gated
# deploy_project agent tool is for, not a plain one-step call.

def _list_deployments(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from deployments import store as dstore
    rows: List[Dict[str, Any]] = []
    seen = set()
    for ws in ctx.workspaces:
        for dep in dstore.list_all(workspace=ws):
            if dep.id in seen:
                continue
            seen.add(dep.id)
            if not lookup.matches(query, dep.name, dep.slug, dep.project_id):
                continue
            rows.append(lookup.row(
                dep.id, dep.name or dep.slug or dep.project_id,
                " · ".join(x for x in [dep.status, dep.mode, f"{len(dep.services)} services"] if x),
                f"/projects/{quote(dep.project_id)}",
                workspace=ws if ctx.workspace is None else None, at=dep.updated_at,
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _deployment_card(ctx: Any, deployment_id: str) -> Optional[Dict[str, Any]]:
    from deployments import store as dstore
    dep = dstore.get(deployment_id)
    if dep is None or not lookup.visible(ctx, dep.workspace):
        return None
    fields = {
        "name": dep.name, "project_id": dep.project_id, "workspace": dep.workspace,
        "mode": dep.mode, "status": dep.status, "desired": dep.desired,
        "paused_reason": dep.paused_reason, "last_error": lookup.first_line(dep.last_error),
        "services": [{"name": s.name, "kind": s.kind,
                      "state": dep.runtime[s.name].state if s.name in dep.runtime else "stopped"}
                     for s in dep.services],
        "slug": dep.slug, "visibility": dep.visibility, "restart_count": dep.restart_count,
        "created_at": dep.created_at, "updated_at": dep.updated_at, "deployed_at": dep.deployed_at,
    }
    return {"title": dep.name or dep.slug or dep.project_id, "fields": fields,
            "note": "Logs and the build output are on the project's Deploy tab, not here.",
            "url": f"/projects/{quote(dep.project_id)}"}


def _deployment_target(ctx: Any, deployment_id: str) -> Optional[Dict[str, Any]]:
    from deployments import store as dstore
    dep = dstore.get(deployment_id)
    if dep is None or not lookup.visible(ctx, dep.workspace):
        return None
    return {"workspace": dep.workspace, "label": dep.name or dep.slug or dep.project_id,
            "url": f"/projects/{quote(dep.project_id)}"}


def _deployment_stop(ctx: Any, deployment_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from deployments import service as deployments, store as dstore
    dep = dstore.get(deployment_id)
    if dep is None:
        raise LookupError_(f"No deployment '{deployment_id}'.", code="not_found")
    if dep.status == "stopped" and dep.desired == "stopped":
        raise LookupError_("This deployment is already stopped.", code="conflict")
    by = f"assistant (user {ctx.user_id})" if ctx.user_id else "assistant"
    try:
        dep = deployments.stop(dep, by=by)
    except deployments.DeploymentError as exc:
        raise LookupError_(str(exc), code="conflict")
    return {"status": dep.status}


lookup.register(LookupKind(
    "deployment", "a project's app under /apps, and the apps section of /deployments",
    _list_deployments, _deployment_card, ("/apps", "/deployments"),
))
register_action(HubAction(
    "deployment", "stop", "stop every service of a project's deployed app",
    _deployment_target, _deployment_stop,
    "Stop the deployment {label} in {workspace}: every service of the app goes down; "
    "its share link and configuration stay.",
))


# ── environment (/environments), read only ──────────────────────────────────

def _list_environments(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    from environments import service as env_service
    rows: List[Dict[str, Any]] = []
    seen = set()
    for ws in ctx.workspaces:
        for env in env_service.list_environments(ws, all_scopes=False):
            if env.id in seen:
                continue
            seen.add(env.id)
            if not lookup.matches(query, env.name, env.description, env.id):
                continue
            scope = env.workspace or "global"
            rows.append(lookup.row(
                env.id, env.name,
                " · ".join(x for x in [scope, env.mode, "default" if env.is_default else None] if x),
                "/environments",
                workspace=(env.workspace if (env.workspace and ctx.workspace is None) else None),
            ))
    rows.sort(key=lambda r: str(r.get("label") or ""))
    return rows[:limit]


def _environment_card(ctx: Any, environment_id: str) -> Optional[Dict[str, Any]]:
    from environments import service as env_service
    env = env_service.get_environment(environment_id)
    if env is None:
        return None
    if env.workspace and not lookup.visible(ctx, env.workspace):
        return None
    fields = {
        "name": env.name, "description": env.description, "workspace": env.workspace or "global",
        "mode": env.mode, "image": env.image, "size": env.size,
        "packages": list(env.packages), "sandbox_provider": env.sandbox_provider,
        "network_type": env.network.type, "allowed_hosts": list(env.network.allowed_hosts),
        "allow_package_managers": env.network.allow_package_managers,
        "memory": env.limits.memory, "cpus": env.limits.cpus, "pids_limit": env.limits.pids_limit,
        # Variable names only: an environment's values are whatever an editor
        # typed there, which can be a secret even though the field is called
        # "plain variables" (environments/models.py).
        "env_vars": sorted(env.env.keys()),
        "is_default": env.is_default, "archived": env.archived,
        "created_at": env.created_at, "updated_at": env.updated_at,
    }
    return {"title": env.name, "fields": fields, "url": "/environments"}


lookup.register(LookupKind(
    "environment", "execution profiles: mode, image, packages, network policy, limits",
    _list_environments, _environment_card, ("/environments",),
))


# ── browser (/browser) ───────────────────────────────────────────────────────
#
# A session lives only in the separate browser service (deploy/browser/),
# reached over HTTP the way dashboard/backend/routes/browser.py reaches it;
# there is no local store to degrade to. Not configured or not reachable
# reads as "no sessions" / "not found" rather than an error.

def _browser_epoch_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _browser_sessions(workspace: Optional[str]):
    from fastapi import HTTPException
    from routes import browser as browser_routes
    try:
        data = browser_routes._json(browser_routes._service(
            "GET", "/sessions", params={"workspace": workspace} if workspace else {}))
    except HTTPException:
        return []
    except Exception:  # noqa: BLE001 - the browser service is best-effort here
        return []
    return data.get("sessions") or []


def _browser_session(session_id: str) -> Optional[Dict[str, Any]]:
    from fastapi import HTTPException
    from routes import browser as browser_routes
    try:
        return browser_routes._load(session_id)
    except HTTPException:
        return None
    except Exception:  # noqa: BLE001
        return None


def _list_browser(ctx: Any, query: str, limit: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for ws in ctx.workspaces:
        for s in _browser_sessions(ws):
            sid = str(s.get("session_id") or "")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            if not lookup.matches(query, s.get("label"), s.get("owner")):
                continue
            rows.append(lookup.row(
                sid, s.get("label") or f"{s.get('owner') or 'a'} session",
                " · ".join(x for x in [s.get("owner"), ws] if x),
                "/browser", workspace=ws if ctx.workspace is None else None,
                at=_browser_epoch_iso(s.get("last_used_at")),
            ))
    rows.sort(key=lambda x: str(x.get("at") or ""), reverse=True)
    return rows[:limit]


def _browser_card(ctx: Any, session_id: str) -> Optional[Dict[str, Any]]:
    info = _browser_session(session_id)
    if info is None or not lookup.visible(ctx, info.get("workspace")):
        return None
    fields = {
        "session_id": session_id, "workspace": info.get("workspace") or "default",
        "owner": info.get("owner"), "label": info.get("label"),
        # The site, not the page's title or full address: both can be text a
        # stranger's page wrote (a path reads as easily as a title).
        "site": urlparse(str(info.get("url") or "")).hostname or None,
        "read_only": bool(info.get("read_only")), "controlled_by": info.get("controlled_by") or None,
        "run_id": info.get("run_id") or None,
        "created_at": _browser_epoch_iso(info.get("created_at")),
        "last_used_at": _browser_epoch_iso(info.get("last_used_at")),
    }
    return {"title": info.get("label") or f"{info.get('owner') or 'a'} session", "fields": fields,
            "note": "The page itself opens on the Browser page, not here.", "url": "/browser"}


def _browser_target(ctx: Any, session_id: str) -> Optional[Dict[str, Any]]:
    info = _browser_session(session_id)
    if info is None or not lookup.visible(ctx, info.get("workspace")):
        return None
    return {"workspace": info.get("workspace") or "default", "label": info.get("label") or session_id,
            "url": "/browser"}


def _browser_stop(ctx: Any, session_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
    from fastapi import HTTPException
    from routes import browser as browser_routes
    try:
        browser_routes._service("DELETE", f"/sessions/{session_id}")
    except HTTPException as exc:
        code = "not_found" if exc.status_code == 404 else "conflict"
        raise LookupError_(str(exc.detail), code=code)
    return {"closed": True}


lookup.register(LookupKind(
    "browser", "a browser session the Browser page shows: who has it, which site, read only or not",
    _list_browser, _browser_card, ("/browser",),
))
register_action(HubAction(
    "browser", "stop", "close a browser session",
    _browser_target, _browser_stop,
    "Close the browser session {label} in {workspace}: whoever is driving it loses the page; "
    "it can be reopened fresh.",
))
