"""
project_deploy — the agent's side of project deployments (deployments/,
docs/project-deployments.md): deploy a project's frontend and backend from
inside the hub, see how it is going, read its logs, stop it.

The tools call the same service layer the project page's Deploy tab does, so
a deploy from chat lands in the same record and journal. What they add for an
agent is the *browser address*: ``project_deployment_status`` returns the
``/apps/<slug>/`` link on the origin the agent's browser reaches the hub at
(common/hub_urls.py), so ``browser_open`` on it works even though the hub's
own address is private from where the browser service sits. That is how an
agent checks its own work: deploy, open, click, read.

``deploy_project`` runs whatever the project's own commands say, on the hub's
host in local mode; it is listed in tools/approval.py's NEEDS_APPROVAL for
the same reason run_shell is. ``stop_project_deployment`` is there too: it
takes something running away.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2, default=str)


def _err(message: str, *, code: str = "bad_request", extra: Optional[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {"ok": False, "error": message, "code": code}
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False, indent=2, default=str)


def _resolve_project(ref: str):
    """A project by id, or by an unambiguous name/folder match (the same
    rule tools/git_publish.py applies)."""
    from common.paths import PROJECTS_FILE
    from projects.storage import ProjectStore
    from workspace import project_folder_name

    projects = ProjectStore(path=PROJECTS_FILE).list()
    for p in projects:
        if p.id == ref:
            return p
    matches = [p for p in projects if p.name == ref or project_folder_name(p.name) == ref]
    return matches[0] if len(matches) == 1 else None


def _summary(dep) -> Dict[str, Any]:
    from deployments import service as deployments
    links = deployments.links(dep)
    return {
        "deployment_id": dep.id,
        "project_id": dep.project_id,
        "mode": dep.mode,
        "status": dep.status,
        "desired": dep.desired,
        "paused_reason": dep.paused_reason,
        "last_error": dep.last_error,
        "services": [
            {
                "name": svc.name, "kind": svc.kind, "configured_port": svc.port, "command": svc.command,
                "state": (dep.runtime.get(svc.name).state if dep.runtime.get(svc.name) else "stopped"),
                "healthy": (dep.runtime.get(svc.name).healthy if dep.runtime.get(svc.name) else None),
                "error": (dep.runtime.get(svc.name).error if dep.runtime.get(svc.name) else None),
                "health_error": (dep.runtime.get(svc.name).health_error if dep.runtime.get(svc.name) else None),
                "port": (dep.runtime.get(svc.name).host_port if dep.runtime.get(svc.name) else svc.port),
                "path_under_app": "/" if svc is dep.primary() else f"/~{svc.name}/",
            }
            for svc in dep.services
        ],
        "primary_service": dep.primary().name if dep.primary() else None,
        # Open this with browser_open to look at the running app.
        "browser_url": links["browser_url"],
        "external_url": links["external_url"],
        "visibility": dep.visibility,
        "recent_events": [
            {"at": e.at, "kind": e.kind, "service": e.service, "detail": e.detail}
            for e in dep.events[-8:]
        ],
    }


def _by() -> Optional[str]:
    """Who to record as the deployer: the agent, with its task when it has one."""
    try:
        from common.agent_context import current_agent_id, current_task_id
        agent = current_agent_id.get() or ""
        task = current_task_id.get() or ""
    except Exception:  # noqa: BLE001 - no run context in a bare call
        agent, task = "", ""
    label = f"agent {agent}" if agent else "agent"
    return f"{label} (task {task})" if task else label


# ── inputs ───────────────────────────────────────────────────────────────────

class ProjectRefInput(BaseModel):
    project: str = Field(..., description="Project id, or its exact name")


class DeployProjectInput(BaseModel):
    project: str = Field(..., description="Project id, or its exact name")
    services: Optional[List[Dict[str, Any]]] = Field(
        None,
        description=("Replace the deployment's services before deploying. Each: {name, kind: "
                     "frontend|backend|other, path (folder inside the project), language: node|python|"
                     "static, port, install_command, command, dockerfile, env}. Omit to keep what is "
                     "configured (detected from the project folder on first use)."),
    )
    mode: Optional[str] = Field(None, description="docker (default), compose or local")
    rebuild: bool = Field(True, description="Rebuild images from Dockerfiles (docker/compose modes)")
    wait_seconds: int = Field(120, description="How long to wait for the services to come up before answering (0 = answer at once)")


class LogsInput(BaseModel):
    project: str = Field(..., description="Project id, or its exact name")
    service: Optional[str] = Field(None, description="Service name; default the primary one. 'build' for the image build log")
    tail: int = Field(100, description="Lines from the end (max 400)")


# ── tools ────────────────────────────────────────────────────────────────────

@tool("deploy_project", args_schema=DeployProjectInput)
def deploy_project(project: str, services: Optional[List[Dict[str, Any]]] = None,
                   mode: Optional[str] = None, rebuild: bool = True, wait_seconds: int = 120) -> str:
    """Deploy a project's frontend and backend from inside the hub, and get the address to open it.

    On first use the services are proposed from the project folder (a
    docker-compose file, Dockerfiles, package.json, a Python entrypoint) and
    you can override them with `services`. The result carries `browser_url`:
    open it with browser_open to see and test the running app, and read
    `services[].state`, `status` and `last_error` when something failed
    (then project_deployment_logs for the details). Deploying again restarts
    everything with the current configuration.
    """
    from deployments import service as deployments

    proj = _resolve_project(project)
    if proj is None:
        return _err(f"Project {project!r} not found (or the name is ambiguous)", code="not_found")
    try:
        dep = deployments.for_project(proj.id, create=True)
        patch: Dict[str, Any] = {}
        if services is not None:
            patch["services"] = services
        if mode:
            patch["mode"] = mode
        if patch:
            dep = deployments.update_config(proj.id, patch)
        if deployments.in_flight(dep.id):
            return _err("A deploy or stop of this project is already in progress", code="conflict")
        if wait_seconds <= 0:
            import threading
            threading.Thread(target=deployments.deploy, args=(dep,), kwargs={"by": _by(), "build": rebuild},
                             daemon=True, name=f"deploy-{dep.id[:8]}").start()
            return _ok({"message": "deploy started; call project_deployment_status for progress",
                        **_summary(dep)})
        dep = deployments.deploy(dep, by=_by(), build=rebuild)
        dep = deployments.wait_until_settled(dep.id, timeout=max(0, min(int(wait_seconds), 600)))
    except deployments.DeploymentError as exc:
        return _err(str(exc), code="conflict" if exc.status == 409 else "bad_request")
    summary = _summary(dep)
    if dep.status == "running":
        summary["message"] = "the app is up; open browser_url with browser_open to check it"
    elif dep.status == "failed":
        summary["message"] = "the deploy failed; read project_deployment_logs for the reason"
    else:
        summary["message"] = f"status is {dep.status}; poll project_deployment_status"
    return _ok(summary)


@tool("project_deployment_status", args_schema=ProjectRefInput)
def project_deployment_status(project: str) -> str:
    """How a project's deployment is doing: status, every service's state and health,
    the last events, and the browser_url to open the running app with browser_open."""
    from deployments import service as deployments

    proj = _resolve_project(project)
    if proj is None:
        return _err(f"Project {project!r} not found (or the name is ambiguous)", code="not_found")
    dep = deployments.for_project(proj.id, create=True)
    if dep.desired == "running" and not deployments.in_flight(dep.id):
        dep = deployments.refresh(dep)
    return _ok(_summary(dep))


@tool("project_deployment_logs", args_schema=LogsInput)
def project_deployment_logs(project: str, service: Optional[str] = None, tail: int = 100) -> str:
    """The last lines of a deployed service's output (or of the image build with service='build').
    Read this when a service is failed, exited or unhealthy."""
    from deployments import service as deployments

    proj = _resolve_project(project)
    if proj is None:
        return _err(f"Project {project!r} not found (or the name is ambiguous)", code="not_found")
    dep = deployments.for_project(proj.id, create=False)
    if dep is None:
        return _err("The project has not been deployed yet", code="not_found")
    tail = max(1, min(int(tail or 100), 400))
    try:
        if service == "build":
            payload = {"service": "build", "text": deployments.build_log(dep, tail)}
        else:
            payload = deployments.logs(dep, service, tail)
    except deployments.DeploymentError as exc:
        return _err(str(exc), code="not_found")
    text = str(payload.get("text") or "")
    if len(text) > 12_000:
        text = text[-12_000:]
        payload["truncated"] = True
    payload["text"] = text
    return _ok(payload)


@tool("stop_project_deployment", args_schema=ProjectRefInput)
def stop_project_deployment(project: str) -> str:
    """Stop every service of a project's deployment. The configuration and the share link stay."""
    from deployments import service as deployments

    proj = _resolve_project(project)
    if proj is None:
        return _err(f"Project {project!r} not found (or the name is ambiguous)", code="not_found")
    dep = deployments.for_project(proj.id, create=False)
    if dep is None:
        return _err("The project has not been deployed yet", code="not_found")
    try:
        dep = deployments.stop(dep, by=_by())
    except deployments.DeploymentError as exc:
        return _err(str(exc), code="conflict")
    return _ok({"message": "stopped", **_summary(dep)})


PROJECT_DEPLOY_TOOLS = [deploy_project, project_deployment_status, project_deployment_logs,
                        stop_project_deployment]

__all__ = ["deploy_project", "project_deployment_status", "project_deployment_logs",
           "stop_project_deployment", "PROJECT_DEPLOY_TOOLS"]
