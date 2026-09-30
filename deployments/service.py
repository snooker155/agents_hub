"""Project deployments: configure, deploy, watch, stop, expose.

The record (deployments/models.py) is the desired state plus what the
runner last saw; this module is every transition between the two, and the
only place that touches the runners (deployments/runner.py). The routes,
the agent tools and the supervisor all come through here, so a deploy
started from chat and one started from the project page behave the same and
land in the same journal.

Links. A deployment is reachable three ways, all of them through the hub,
none of them straight to the container:

* inside the dashboard, through the preview proxy with a short-lived ticket
  (``/preview/<ticket>/``, kind ``deployment``);
* from anywhere the hub is reachable, under ``/apps/<slug>/``: with the
  share key while the deployment is ``private`` (``?key=`` once, then a
  cookie), without one when it is ``public``;
* from the agent's own browser, the same ``/apps/<slug>/`` address on the
  origin the browser service reaches the hub at (common/hub_urls.py).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import runner, store
from .models import (
    DeployService, ProjectDeployment, ServiceRuntime, new_share_token, new_slug, now_iso,
)

log = logging.getLogger(__name__)

#: How long a service gets to start answering before it is called unhealthy.
STARTUP_GRACE_SECONDS = int(os.environ.get("AGENTS_HUB_DEPLOY_STARTUP_GRACE", "90"))
#: Restarts within this window that pause the deployment as a crash loop.
CRASH_WINDOW_SECONDS = 300
CRASH_LIMIT = 3

APPS_PREFIX = "/apps/"


class DeploymentError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── lookups ──────────────────────────────────────────────────────────────────

def _projects():
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    return ProjectStore(path=PROJECTS_FILE)


def project_root(project) -> Optional[Path]:
    """The folder a deployment runs from: the cloned repository when the
    project has one, else the project's own folder in its workspace."""
    from workspace import get_workspace_folder, project_folder_name
    ws = get_workspace_folder(project.workspace)
    if ws is None:
        return None
    local = getattr(project.repo, "local_path", None)
    if local:
        candidate = (ws / local).resolve()
        if candidate.is_dir():
            return candidate
    root = (ws / project_folder_name(project.name)).resolve()
    return root if root.is_dir() else None


def require_project(project_id: str):
    project = _projects().get(str(project_id))
    if project is None:
        raise DeploymentError("Project not found", 404)
    return project


def for_project(project_id: str, *, create: bool = False) -> Optional[ProjectDeployment]:
    """The project's deployment, detected and saved on first use when
    ``create`` is set."""
    dep = store.for_project(project_id)
    if dep is not None or not create:
        return dep
    project = require_project(project_id)
    from .detect import detect
    dep = detect(project_root(project), project_id=project.id, workspace=project.workspace,
                 name=project.name)
    dep.slug = new_slug(project.name)
    dep.add_event("created", "deployment proposed from the project folder")
    return store.save(dep)


def redetect(project_id: str) -> ProjectDeployment:
    """Replace the services with a fresh proposal; keeps the slug, the link
    and the journal. Refused while the deployment runs."""
    project = require_project(project_id)
    dep = for_project(project_id, create=True)
    assert dep is not None
    if dep.desired == "running":
        raise DeploymentError("Stop the deployment before re-detecting its services", 409)
    from .detect import detect
    fresh = detect(project_root(project), project_id=project.id, workspace=project.workspace,
                   name=project.name)

    def apply(d: ProjectDeployment) -> None:
        d.mode = fresh.mode
        d.compose_file = fresh.compose_file
        d.services = fresh.services
        d.primary_service = None
        d.runtime = {}
        d.status = "stopped"
        d.add_event("detected", f"{len(fresh.services)} service(s) proposed, mode {fresh.mode}")

    return store.mutate(dep.id, apply) or dep


_EDITABLE = ("name", "mode", "compose_file", "services", "environment_id", "env",
             "primary_service", "restart_on_exit")


def update_config(project_id: str, patch: Dict[str, Any]) -> ProjectDeployment:
    dep = for_project(project_id, create=True)
    assert dep is not None
    data = dep.model_dump(mode="json")
    for key in _EDITABLE:
        if key in patch:
            data[key] = patch[key]
    try:
        updated = ProjectDeployment.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - pydantic's message is the useful part
        raise DeploymentError(_validation_message(exc), 422) from exc
    if updated.primary_service and updated.service(updated.primary_service) is None:
        raise DeploymentError(f"primary_service {updated.primary_service!r} is not one of the services", 422)

    def apply(d: ProjectDeployment) -> None:
        for key in _EDITABLE:
            setattr(d, key, getattr(updated, key))
        d.add_event("configured", "configuration changed")

    return store.mutate(dep.id, apply) or updated


def _validation_message(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            parts = []
            for err in errors():
                loc = ".".join(str(p) for p in err.get("loc", ()))
                msg = str(err.get("msg", "")).replace("Value error, ", "")
                parts.append(f"{loc}: {msg}" if loc else msg)
            if parts:
                return "; ".join(parts)
        except Exception:  # noqa: BLE001 - fall through to str(exc)
            log.debug("could not format validation errors", exc_info=True)
    return str(exc)


# ── links ────────────────────────────────────────────────────────────────────

def public_path(dep: ProjectDeployment) -> str:
    return f"{APPS_PREFIX}{dep.slug}/"


def links(dep: ProjectDeployment) -> Dict[str, Any]:
    """Every address the deployment answers at (see the module docstring)."""
    from common import hub_urls
    path = public_path(dep)
    key = "" if dep.visibility == "public" else f"?key={dep.share_token}"
    public = hub_urls.public_base()
    browser = hub_urls.browser_base()
    return {
        "path": path,
        "external_url": f"{public}{path}{key}" if public else f"{path}{key}",
        "external_absolute": bool(public),
        "browser_url": f"{browser}{path}{key}",
        "visibility": dep.visibility,
        "services": {
            name: rt.url for name, rt in dep.runtime.items() if rt.url
        },
    }


def set_visibility(dep: ProjectDeployment, visibility: str) -> ProjectDeployment:
    if visibility not in ("private", "public"):
        raise DeploymentError("visibility is 'private' or 'public'", 422)

    def apply(d: ProjectDeployment) -> None:
        d.visibility = visibility  # type: ignore[assignment]
        d.add_event("visibility", f"link is now {visibility}")

    return store.mutate(dep.id, apply) or dep


def rotate_link(dep: ProjectDeployment) -> ProjectDeployment:
    """A new share key (and slug): every link handed out so far stops working."""
    def apply(d: ProjectDeployment) -> None:
        d.share_token = new_share_token()
        d.slug = new_slug(d.name or "app")
        d.add_event("link_reset", "share link replaced")

    return store.mutate(dep.id, apply) or dep


def share_key_matches(dep: ProjectDeployment, presented: Optional[str]) -> bool:
    import hmac
    if not presented:
        return False
    return hmac.compare_digest(str(presented), dep.share_token)


# ── the proxy target ─────────────────────────────────────────────────────────

def target_url(dep: ProjectDeployment, service: Optional[str] = None) -> Optional[str]:
    """Where the hub reaches the (primary or named) service right now, or
    None while it is not running."""
    svc = dep.service(service)
    if svc is None:
        return None
    rt = dep.runtime.get(svc.name)
    if rt is None or rt.state not in ("starting", "running") or not rt.url:
        return None
    return rt.url


# ── deploy / stop / restart ──────────────────────────────────────────────────

_inflight: set = set()
_inflight_lock = threading.Lock()


def in_flight(deployment_id: str) -> bool:
    with _inflight_lock:
        return deployment_id in _inflight


def _claim(deployment_id: str) -> bool:
    with _inflight_lock:
        if deployment_id in _inflight:
            return False
        _inflight.add(deployment_id)
        return True


def _release(deployment_id: str) -> None:
    with _inflight_lock:
        _inflight.discard(deployment_id)


def _limits_for(dep: ProjectDeployment) -> Optional[Dict[str, Any]]:
    if not dep.environment_id:
        return None
    try:
        from environments import service as envs
        from environments.launch import docker_options
        env = envs.get_environment(dep.environment_id)
        if env is None:
            return None
        opts = docker_options(env) or {}
        return {k: opts[k] for k in ("memory", "cpus", "pids_limit") if k in opts} or None
    except Exception:  # noqa: BLE001 - limits are a nicety; a deploy never fails on them
        log.debug("could not read environment %s for deployment %s", dep.environment_id, dep.id,
                  exc_info=True)
        return None


def _set(dep_id: str, **fields: Any) -> Optional[ProjectDeployment]:
    def apply(d: ProjectDeployment) -> None:
        for key, value in fields.items():
            setattr(d, key, value)
    return store.mutate(dep_id, apply)


def _event(dep_id: str, kind: str, detail: str = "", service: Optional[str] = None) -> None:
    store.mutate(dep_id, lambda d: d.add_event(kind, detail, service))


def _runtime(dep_id: str, name: str, rt: ServiceRuntime) -> None:
    def apply(d: ProjectDeployment) -> None:
        d.runtime[name] = rt
    store.mutate(dep_id, apply)


def deploy(dep: ProjectDeployment, *, by: Optional[str] = None, build: bool = True) -> ProjectDeployment:
    """Bring every service up. Blocks for as long as the build and the start
    take (call it off the event loop); the status moves through ``building``
    and ``starting`` on the record meanwhile, so a page polling it sees the
    progress. Health is settled by :func:`refresh` afterwards."""
    if not dep.services:
        raise DeploymentError("The deployment has no services: detect or add some first", 422)
    if not _claim(dep.id):
        raise DeploymentError("A deploy or stop of this project is already in progress", 409)
    try:
        return _deploy_locked(dep, by=by, build=build)
    finally:
        _release(dep.id)


def _deploy_locked(dep: ProjectDeployment, *, by: Optional[str], build: bool) -> ProjectDeployment:
    project = require_project(dep.project_id)
    root = project_root(project)
    if root is None:
        raise DeploymentError("The project has no folder to deploy from", 409)
    if not dep.slug:
        dep = _set(dep.id, slug=new_slug(project.name)) or dep

    _stop_services(dep, root, quiet=True)
    _set(dep.id, desired="running", status="building" if build else "starting", paused_reason=None,
         last_error=None, deployed_at=now_iso(), deployed_by=by, restart_count=0)
    _event(dep.id, "deploy", f"deploy started ({dep.mode})" + (f" by {by}" if by else ""))
    path = public_path(dep)
    build_log: List[str] = []
    failed: Optional[str] = None

    if dep.mode == "compose":
        ok, reason = runner.compose_runner.available()
        if not ok:
            failed = reason
        else:
            env = runner.service_env(dep, DeployService(name="compose", port=1), 0, public_path=path)
            env.pop("PORT", None)
            failed = runner.compose_runner.up(dep, root, build=build, env=env, log_lines=build_log)
            if failed is None:
                _set(dep.id, status="starting")
                for svc in dep.services:
                    rt = runner.compose_runner.runtime_for(dep, root, svc)
                    if rt.state == "running":
                        rt.state = "starting"
                    if rt.host_port is None and rt.state != "failed":
                        rt = rt.model_copy(update={"state": "failed", "error":
                                                   f"compose publishes no host port for {svc.name}:{svc.port}"})
                    _runtime(dep.id, svc.name, rt)
                    if rt.state == "failed":
                        _event(dep.id, "service_failed", rt.error or "failed", svc.name)
    else:
        use_docker = dep.mode == "docker"
        if use_docker:
            ok, reason = runner.docker_runner.available()
            if not ok:
                failed = reason
        if failed is None:
            limits = _limits_for(dep) if use_docker else None
            for svc in dep.services:
                if use_docker and svc.dockerfile and build:
                    _set(dep.id, status="building")
                    _event(dep.id, "build", f"building image from {svc.dockerfile}", svc.name)
                    err = runner.docker_runner.build(dep, root, svc, build_log)
                    if err:
                        rt = ServiceRuntime(state="failed", error=err)
                        _runtime(dep.id, svc.name, rt)
                        _event(dep.id, "build_failed", err, svc.name)
                        continue
                _set(dep.id, status="starting")
                if use_docker:
                    rt = runner.docker_runner.start(dep, root, svc, public_path=path, limits=limits)
                else:
                    rt = runner.local_runner.start(dep, root, svc, public_path=path)
                _runtime(dep.id, svc.name, rt)
                if rt.state == "failed":
                    _event(dep.id, "service_failed", rt.error or "failed", svc.name)
                else:
                    _event(dep.id, "service_started",
                           f"{svc.name} on port {rt.host_port}" + (f" ({rt.container})" if rt.container else ""),
                           svc.name)

    if build_log:
        _write_build_log(dep, "\n".join(build_log))

    if failed:
        _set(dep.id, status="failed", last_error=failed, desired="stopped")
        _event(dep.id, "deploy_failed", failed)
        return store.get(dep.id) or dep

    current = store.get(dep.id) or dep
    if all(rt.state == "failed" for rt in current.runtime.values()) and current.runtime:
        first = next(iter(current.runtime.values()))
        _set(dep.id, status="failed", last_error=first.error or "every service failed to start",
             desired="stopped")
        _event(dep.id, "deploy_failed", first.error or "every service failed to start")
        return store.get(dep.id) or current
    return refresh(current)


def _write_build_log(dep: ProjectDeployment, text: str) -> None:
    try:
        path = runner.LOG_DIR / dep.id / "build.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {now_iso()}\n{text}\n")
    except OSError:
        log.debug("could not write build log for %s", dep.id, exc_info=True)


def build_log(dep: ProjectDeployment, tail: int = 200) -> str:
    try:
        text = (runner.LOG_DIR / dep.id / "build.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-max(1, int(tail)):])


def _stop_services(dep: ProjectDeployment, root: Optional[Path], *, quiet: bool = False) -> None:
    if dep.mode == "compose":
        if root is not None and dep.runtime:
            runner.compose_runner.down(dep, root)
    else:
        run = runner.docker_runner if dep.mode == "docker" else runner.local_runner
        for svc in dep.services:
            rt = dep.runtime.get(svc.name)
            if rt is None:
                continue
            try:
                run.stop(dep, svc, rt)
            except Exception:  # noqa: BLE001 - a service that will not stop is journaled, not fatal
                log.warning("could not stop %s of deployment %s", svc.name, dep.id, exc_info=True)
                if not quiet:
                    _event(dep.id, "stop_failed", "could not stop", svc.name)
    _set(dep.id, runtime={})


def stop(dep: ProjectDeployment, *, by: Optional[str] = None, reason: str = "") -> ProjectDeployment:
    if not _claim(dep.id):
        raise DeploymentError("A deploy or stop of this project is already in progress", 409)
    try:
        project = _projects().get(dep.project_id)
        root = project_root(project) if project else None
        _stop_services(dep, root)
        _set(dep.id, desired="stopped", status="stopped")
        _event(dep.id, "stopped", reason or ("stopped" + (f" by {by}" if by else "")))
        return store.get(dep.id) or dep
    finally:
        _release(dep.id)


def restart(dep: ProjectDeployment, *, by: Optional[str] = None) -> ProjectDeployment:
    """Stop and start without rebuilding images."""
    return deploy(dep, by=by, build=False)


def remove(dep: ProjectDeployment) -> None:
    """Stop everything and delete the record."""
    try:
        stop(dep, reason="deployment removed")
    except DeploymentError:
        pass
    store.delete(dep.id)


# ── watching ─────────────────────────────────────────────────────────────────

def _grace_over(rt: ServiceRuntime) -> bool:
    if not rt.started_at:
        return True
    try:
        started = datetime.fromisoformat(rt.started_at)
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started > timedelta(seconds=STARTUP_GRACE_SECONDS)


def refresh(dep: ProjectDeployment) -> ProjectDeployment:
    """Ask the runner about every service and settle the deployment's
    status: ``running`` when all answer, ``unhealthy`` when one does not (or
    exited), ``stopped`` when nothing is left. Cheap enough for the
    supervisor to call every tick and a page to call on load."""
    if dep.desired != "running" or in_flight(dep.id):
        return dep
    project = _projects().get(dep.project_id)
    root = project_root(project) if project else None
    states: Dict[str, ServiceRuntime] = {}
    adopted_ports: List[tuple] = []
    for svc in dep.services:
        rt = dep.runtime.get(svc.name)
        if rt is None:
            continue
        if dep.mode == "compose":
            if root is None:
                continue
            fresh = runner.compose_runner.runtime_for(dep, root, svc)
            fresh = fresh.model_copy(update={"started_at": rt.started_at or fresh.started_at,
                                             "state": "starting" if (fresh.state == "running" and rt.state == "starting" and not rt.healthy) else fresh.state})
        elif dep.mode == "docker":
            fresh = runner.docker_runner.inspect(dep, svc, rt)
        else:
            fresh = runner.local_runner.inspect(dep, svc, rt)
        if fresh.state in ("starting", "running"):
            healthy, reason = runner.health_of(fresh, svc)
            if not healthy and dep.mode == "local" and fresh.pid:
                # The command ignored $PORT (a script with its own port baked
                # in): take the port the process group actually listens on.
                ports = runner.listening_ports(fresh.pid)
                if ports and fresh.host_port not in ports:
                    adopted = ports[0]
                    fresh = fresh.model_copy(update={"host_port": adopted, "url": runner.local_url(adopted)})
                    healthy, reason = runner.health_of(fresh, svc)
                    adopted_ports.append((svc.name, svc.port, adopted))
            fresh = fresh.model_copy(update={
                "healthy": healthy,
                "health_error": None if healthy else reason,
                "state": "running" if healthy else ("starting" if not _grace_over(fresh) else "running"),
            })
        states[svc.name] = fresh

    def apply(d: ProjectDeployment) -> None:
        for name, rt in states.items():
            d.runtime[name] = rt
        for name, declared, actual in adopted_ports:
            d.add_event("port_adopted",
                        f"the service listens on port {actual}, not the configured {declared}; using {actual}",
                        name)
        live = [rt for rt in d.runtime.values() if rt.state in ("starting", "running")]
        if not d.runtime:
            d.status = "stopped"
        elif not live:
            d.status = "failed" if any(rt.state == "failed" for rt in d.runtime.values()) else "unhealthy"
        elif all(rt.healthy for rt in live) and len(live) == len(d.services):
            d.status = "running"
        elif any(rt.state == "starting" for rt in live) and len(live) == len(d.services):
            d.status = "starting"
        else:
            d.status = "unhealthy"

    return store.mutate(dep.id, apply) or dep


def _crash_looping(dep: ProjectDeployment) -> bool:
    since = datetime.now(timezone.utc) - timedelta(seconds=CRASH_WINDOW_SECONDS)
    count = 0
    for ev in dep.events:
        if ev.kind != "service_restarted":
            continue
        try:
            at = datetime.fromisoformat(ev.at)
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at >= since:
            count += 1
    return count >= CRASH_LIMIT


def reconcile(dep: ProjectDeployment) -> Dict[str, int]:
    """One supervisor pass over one deployment: refresh, restart what exited
    (when asked to), pause on a crash loop."""
    counts = {"restarted": 0, "paused": 0}
    if dep.desired != "running" or dep.status == "paused" or in_flight(dep.id):
        return counts
    dep = refresh(dep)
    if not dep.restart_on_exit:
        return counts
    project = _projects().get(dep.project_id)
    root = project_root(project) if project else None
    if root is None:
        return counts
    for svc in dep.services:
        rt = dep.runtime.get(svc.name)
        if rt is None or rt.state != "exited":
            continue
        if _crash_looping(dep):
            _set(dep.id, status="paused", paused_reason="crash loop: a service kept exiting, fix the cause and deploy again")
            _event(dep.id, "paused", "crash loop", svc.name)
            counts["paused"] += 1
            return counts
        if not _claim(dep.id):
            return counts
        try:
            if dep.mode == "compose":
                err = runner.compose_runner.up(dep, root, build=False, env={}, log_lines=[])
                fresh = runner.compose_runner.runtime_for(dep, root, svc) if err is None else ServiceRuntime(state="failed", error=err)
            elif dep.mode == "docker":
                fresh = runner.docker_runner.start(dep, root, svc, public_path=public_path(dep),
                                                   limits=_limits_for(dep))
            else:
                fresh = runner.local_runner.start(dep, root, svc, public_path=public_path(dep))
            _runtime(dep.id, svc.name, fresh)
            store.mutate(dep.id, lambda d: setattr(d, "restart_count", d.restart_count + 1))
            _event(dep.id, "service_restarted",
                   f"exit code {rt.exit_code}" if rt.exit_code is not None else "exited", svc.name)
            counts["restarted"] += 1
        finally:
            _release(dep.id)
    return counts


# ── logs ─────────────────────────────────────────────────────────────────────

def logs(dep: ProjectDeployment, service: Optional[str] = None, tail: int = 200) -> Dict[str, Any]:
    svc = dep.service(service)
    if svc is None:
        raise DeploymentError("Unknown service", 404)
    rt = dep.runtime.get(svc.name) or ServiceRuntime()
    project = _projects().get(dep.project_id)
    root = project_root(project) if project else None
    if dep.mode == "compose":
        text = runner.compose_runner.logs(dep, root, svc, tail) if root else ""
    elif dep.mode == "docker":
        text = runner.docker_runner.logs(dep, svc, rt, tail)
    else:
        text = runner.local_runner.logs(dep, svc, rt, tail)
    return {"service": svc.name, "text": text, "state": rt.state, "exit_code": rt.exit_code,
            "error": rt.error}


# ── summary ──────────────────────────────────────────────────────────────────

def describe(dep: ProjectDeployment, *, with_token: bool = True) -> Dict[str, Any]:
    data = dep.to_dict(with_token=with_token)
    data["links"] = links(dep)
    data["in_flight"] = in_flight(dep.id)
    primary = dep.primary()
    data["primary_service"] = primary.name if primary else None
    return data


def wait_until_settled(dep_id: str, timeout: float = 120.0, poll: float = 2.0) -> ProjectDeployment:
    """For a caller that wants an answer rather than a status to poll (the
    agent tool): wait for ``running``, ``unhealthy`` or a terminal state."""
    deadline = time.monotonic() + timeout
    dep = store.get(dep_id)
    while dep is not None and time.monotonic() < deadline:
        if dep.status in ("running", "failed", "stopped", "paused"):
            return dep
        if dep.status == "unhealthy" and not any(rt.state == "starting" for rt in dep.runtime.values()):
            return dep
        time.sleep(poll)
        current = store.get(dep_id)
        if current is None:
            break
        dep = refresh(current) if not in_flight(dep_id) else current
    return dep or store.get(dep_id)  # type: ignore[return-value]


__all__ = [
    "APPS_PREFIX", "DeploymentError", "build_log", "deploy", "describe", "for_project", "in_flight",
    "links", "logs", "project_root", "public_path", "reconcile", "redetect", "refresh", "remove",
    "require_project", "restart", "rotate_link", "set_visibility", "share_key_matches", "stop",
    "target_url", "update_config", "wait_until_settled",
]
