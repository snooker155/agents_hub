"""
What the terminal client calls, in two flavours.

``DirectBackend`` imports the service layer and calls it in this process: no
server, no sockets, no serialization. It is the default, and it is what makes
the CLI usable on a machine where the dashboard is not running at all.

``HttpBackend`` speaks to a running backend over its REST API. It exists for the
one case direct calls cannot serve — the service running somewhere else, most
often in a container — and is selected only by setting ``AGENTS_HUB_URL``.

Both satisfy the same small interface, so every command in ``cli/main.py`` is written
once. Return shapes are the API's shapes in both cases: the direct path returns
what the route would have returned, because it calls the same functions the
route calls.

Direct mode needs the service's dependencies importable and its state directory
reachable — it is the application, not a client of it. Errors from either
backend surface as :class:`BackendError`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional


class BackendError(Exception):
    """An operation the backend refused or could not complete."""


def _fail(exc: Exception) -> "BackendError":
    return BackendError(str(exc))


# ---------------------------------------------------------------------------
# Direct: call the functions
# ---------------------------------------------------------------------------


class DirectBackend:
    """Run everything in this process, against the local state directory."""

    kind = "direct"

    def __init__(self) -> None:
        # The service seeds agents, workspaces and the database schema on
        # startup; a CLI that skipped it would act on an uninitialized state
        # directory the first time it ran. Same call the backend makes.
        from common.bootstrap import ensure_initial_state
        ensure_initial_state()

        # Writes from here announce themselves to a running dashboard the way an
        # agent subprocess does — over a debounced relay. That debounce outlives
        # a one-command process, so flush it on the way out or the event is lost
        # and an open tab keeps showing stale data until its next fetch.
        import atexit
        from common.session_broker import flush_relayed_notifications
        atexit.register(flush_relayed_notifications)

    def describe(self) -> str:
        from common.paths import AGENTS_HUB_ROOT
        return f"in-process, state at {AGENTS_HUB_ROOT}"

    # ---- agents ----

    def list_agents(self, workspace: Optional[str] = None) -> List[dict]:
        from agents import registry
        from common.workspace_context import filter_agents_for_workspace
        specs = registry.list_agents()
        if workspace:
            specs = filter_agents_for_workspace(specs, workspace)
        return _jsonable([self._agent_to_dict(s) for s in specs])

    def get_agent(self, agent_id: str) -> dict:
        from agents import registry
        spec = registry.get_agent(agent_id)
        if not spec:
            raise BackendError(f"Agent '{agent_id}' not found")
        return _jsonable(self._agent_to_dict(spec))

    @staticmethod
    def _agent_to_dict(spec: Any) -> dict:
        if isinstance(spec, dict):
            return spec
        dump = getattr(spec, "model_dump", None) or getattr(spec, "dict", None)
        return dump() if dump else dict(spec.__dict__)

    # ---- chat ----

    def send_message(self, body: dict) -> dict:
        from chat.models import ChatRequest
        from chat.send import send_chat_message_sync, ChatSendError
        try:
            return _jsonable(send_chat_message_sync(ChatRequest(**body)))
        except ChatSendError as e:
            raise BackendError(e.detail)
        except Exception as e:
            raise _fail(e)

    # ---- tasks ----

    def list_tasks(self, workspace: Optional[str] = None) -> List[dict]:
        from tasks import service as tasks_service
        from tasks.serialize import task_to_dict
        tasks = tasks_service.list_tasks()
        if workspace:
            tasks = [t for t in tasks if (t.workspace or "").strip() == workspace]
        return _jsonable([task_to_dict(t) for t in tasks])

    def get_task(self, task_id: str) -> dict:
        from uuid import UUID
        from tasks import service as tasks_service
        from tasks.serialize import task_to_dict
        tid = UUID(task_id)
        t = tasks_service.get_task(tid)
        if not t:
            raise BackendError("Task not found")
        result = task_to_dict(t)
        result["subtasks"] = [task_to_dict(st) for st in tasks_service.list_tasks() if st.parent_id == tid]
        return _jsonable(result)

    def create_task(self, body: dict) -> dict:
        from tasks import service as tasks_service
        from tasks.serialize import task_to_dict
        try:
            t = tasks_service.create_task(
                title=body["title"],
                description=body.get("description") or "",
                workspace=body.get("workspace_name"),
                project_id=body.get("project_id"),
            )
        except Exception as e:
            raise _fail(e)
        return _jsonable(task_to_dict(t))

    def assign_task(self, task_id: str, agent_id: str, params: Optional[dict] = None) -> dict:
        from uuid import UUID
        from tasks.assign import assign_agent_to_task, AssignError
        from tasks.serialize import task_to_dict
        try:
            return _jsonable(assign_agent_to_task(UUID(task_id), agent_id, params, task_to_dict=task_to_dict))
        except AssignError as e:
            raise BackendError(e.detail)
        except Exception as e:
            raise _fail(e)

    def decompose_task(self, task_id: str) -> dict:
        # Still only expressed as a route handler, and it carries policy of its
        # own (user-created tasks only). Called as a function, not over a socket.
        from uuid import UUID
        from dashboard.backend.routes.tasks import decompose_task as _decompose
        return _run_coroutine(_decompose(UUID(task_id), None))

    def stop_task(self, task_id: str) -> dict:
        from uuid import UUID
        from tasks import service as tasks_service
        from tasks.models import TaskStatus
        try:
            tasks_service.update_task(UUID(task_id), status=TaskStatus.stopped)
        except Exception as e:
            raise _fail(e)
        return {"stopped": True}

    def delete_task(self, task_id: str) -> None:
        from uuid import UUID
        from tasks import service as tasks_service
        if not tasks_service.delete_task(UUID(task_id)):
            raise BackendError("Task not found")

    # ---- workspaces ----

    def list_workspaces(self) -> List[dict]:
        from workspace import list_workspace_folders
        from tasks import service as tasks_service
        all_tasks = tasks_service.list_tasks()
        items = []
        for p in list_workspace_folders():
            attached = p.is_symlink()
            items.append({
                "name": p.name,
                "path": str(p),
                "tasks_count": sum(1 for t in all_tasks if (t.workspace or "").strip() == p.name),
                "attached": attached,
                "target": str(p.resolve()) if attached else None,
            })
        return items

    # The three workspace writes announce themselves the way their routes do.
    # The notification cannot live in the storage layer instead: almost every
    # request calls create_workspace_folder just to ensure the directory, so
    # notifying there would fire "workspaces.changed" on ordinary reads.

    def create_workspace(self, name: str) -> dict:
        from workspace import create_workspace_folder
        try:
            p = create_workspace_folder(name)
        except Exception as e:
            raise _fail(e)
        _notify_workspaces()
        return {"name": p.name, "path": str(p)}

    def attach_workspace(self, path: str, name: Optional[str] = None) -> dict:
        from workspace import attach_workspace_folder
        try:
            link = attach_workspace_folder(path, name)
        except (ValueError, OSError) as e:
            raise BackendError(str(e))
        _notify_workspaces()
        return {"name": link.name, "path": str(link), "target": str(link.resolve()), "attached": True}

    def delete_workspace(self, name: str) -> dict:
        from workspace import delete_workspace_folder, is_attached_workspace, workspace_target
        if name == "default":
            raise BackendError("The default workspace cannot be deleted")
        was_attached = is_attached_workspace(name)
        target = str(workspace_target(name)) if was_attached else None
        if not delete_workspace_folder(name):
            raise BackendError("Workspace not found")
        _notify_workspaces()
        return {"deleted": True, "detached": was_attached, "target_kept": target}

    # ---- projects ----

    def list_projects(self) -> List[dict]:
        from projects.storage import ProjectStore
        from common.paths import PROJECTS_FILE
        from tasks import service as tasks_service
        from workspace import project_folder_name
        all_tasks = tasks_service.list_tasks()
        items = []
        for pr in ProjectStore(PROJECTS_FILE).list():
            d = pr.model_dump(mode="json")
            d["tasks_count"] = sum(1 for t in all_tasks if str(getattr(t, "project_id", "")) == pr.id)
            d["folder"] = project_folder_name(pr.name)
            items.append(d)
        return _jsonable(items)

    def attach_project(self, body: dict) -> dict:
        from dashboard.backend.routes.projects import attach_project as _attach
        from models import ProjectAttach
        return _run_coroutine(_attach(ProjectAttach(**body)))

    def project_git_status(self, project_id: str) -> dict:
        from dashboard.backend.routes.projects import git_status as _git_status
        return _run_coroutine(_git_status(project_id))

    # ---- nodes ----

    def list_nodes(self, workspace: Optional[str] = None) -> List[dict]:
        from dashboard.backend.routes.nodes import list_nodes as _list
        return _run_coroutine(_list(workspace))

    def start_node(self, body: dict) -> dict:
        from dashboard.backend.routes.nodes import start_node as _start
        from dashboard.backend.routes.nodes import NodeCreate
        return _run_coroutine(_start(NodeCreate(**body)))

    def stop_node(self, node_id: str) -> dict:
        from dashboard.backend.routes.nodes import stop_node as _stop
        return _run_coroutine(_stop(node_id))

    # ---- settings ----

    def get_settings(self) -> dict:
        from dashboard.backend.routes.settings import get_settings as _get
        result = _run_coroutine(_get())
        dump = getattr(result, "model_dump", None)
        return dump(mode="json") if dump else dict(result)


def _notify_workspaces() -> None:
    """Tell a running dashboard its workspace list changed."""
    from common.session_broker import notify_change
    notify_change("workspaces")


def _jsonable(value):
    """Normalise a direct-call result to the shape the HTTP path would produce.

    Without this the two backends disagree: FastAPI serializes enums and
    datetimes on the way out, so ``status`` arrives as ``"active"`` over HTTP but
    as ``ProjectStatus.active`` from a direct call, and every renderer would have
    to cope with both.
    """
    from fastapi.encoders import jsonable_encoder
    return jsonable_encoder(value)


def _run_coroutine(coro):
    """Await a coroutine from sync code, translating HTTP refusals.

    Several operations are still only expressed as async route handlers. Calling
    them directly is not a round trip — no server, no socket — but they raise
    HTTPException, which means nothing outside the API, so it is unwrapped here.
    """
    import asyncio
    from fastapi import HTTPException
    try:
        return _jsonable(asyncio.run(coro))
    except HTTPException as e:
        raise BackendError(str(e.detail))
    except Exception as e:
        raise _fail(e)


# ---------------------------------------------------------------------------
# HTTP: talk to a service running elsewhere
# ---------------------------------------------------------------------------


class HttpBackend:
    """Call a running backend over REST. Selected by setting AGENTS_HUB_URL."""

    kind = "http"

    # A read or write of stored state answers quickly or something is wrong.
    API_TIMEOUT = 30

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def describe(self) -> str:
        return f"over HTTP, {self.base_url}"

    # ---- transport ----

    def _request(self, method: str, path: str, *, params=None, json=None, timeout=None):
        import requests
        from common.auth import auth_headers
        try:
            r = requests.request(
                method, f"{self.base_url}{path}",
                params=params, json=json, headers=auth_headers(),
                timeout=self.API_TIMEOUT if timeout is None else timeout,
            )
            r.raise_for_status()
            return r.json() if r.content else None
        except requests.ConnectionError:
            raise BackendError(
                f"Cannot reach the backend at {self.base_url}. Start it with "
                "`ah server start`, or unset AGENTS_HUB_URL to run in process."
            )
        except requests.Timeout:
            raise BackendError(
                f"Timed out waiting for {self.base_url}{path}. For a call that runs an "
                "agent, raise AGENTS_HUB_AGENT_TIMEOUT (0 = no limit)."
            )
        except requests.HTTPError as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            raise BackendError(str(detail))

    # ---- agents ----

    def list_agents(self, workspace=None):
        return self._request("GET", "/api/agents", params={"workspace": workspace} if workspace else None)

    def get_agent(self, agent_id):
        return self._request("GET", f"/api/agents/{agent_id}")

    # ---- chat ----

    def send_message(self, body):
        return self._request("POST", "/api/chat/message", json=body, timeout=agent_timeout())

    # ---- tasks ----

    def list_tasks(self, workspace=None):
        return self._request("GET", "/api/tasks", params={"workspace": workspace} if workspace else None)

    def get_task(self, task_id):
        return self._request("GET", f"/api/tasks/{task_id}")

    def create_task(self, body):
        return self._request("POST", "/api/tasks", json=body)

    def assign_task(self, task_id, agent_id, params=None):
        body = {"agent_id": agent_id, "mode": "live"}
        if params:
            body["params"] = params
        return self._request("POST", f"/api/tasks/{task_id}/assign", json=body, timeout=agent_timeout())

    def decompose_task(self, task_id):
        return self._request("POST", f"/api/tasks/{task_id}/decompose", json={}, timeout=agent_timeout())

    def stop_task(self, task_id):
        return self._request("POST", f"/api/tasks/{task_id}/stop", json={})

    def delete_task(self, task_id):
        self._request("DELETE", f"/api/tasks/{task_id}")

    # ---- workspaces ----

    def list_workspaces(self):
        return self._request("GET", "/api/workspaces")

    def create_workspace(self, name):
        return self._request("POST", "/api/workspaces", json={"name": name})

    def attach_workspace(self, path, name=None):
        body = {"path": path}
        if name:
            body["name"] = name
        return self._request("POST", "/api/workspaces/attach", json=body)

    def delete_workspace(self, name):
        return self._request("DELETE", f"/api/workspaces/{name}") or {}

    # ---- projects ----

    def list_projects(self):
        return self._request("GET", "/api/projects")

    def attach_project(self, body):
        return self._request("POST", "/api/projects/attach", json=body)

    def project_git_status(self, project_id):
        return self._request("GET", f"/api/projects/{project_id}/git-status")

    # ---- nodes ----

    def list_nodes(self, workspace=None):
        return self._request("GET", "/api/nodes", params={"workspace": workspace} if workspace else None)

    def start_node(self, body):
        return self._request("POST", "/api/nodes", json=body)

    def stop_node(self, node_id):
        return self._request("POST", f"/api/nodes/{node_id}/stop", json={})

    # ---- settings ----

    def get_settings(self):
        return self._request("GET", "/api/settings")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def agent_timeout() -> Optional[float]:
    """Seconds to allow a call that runs an agent, over HTTP.

    A turn takes as long as the model takes — minutes, on a local one. Direct
    mode needs no equivalent: there is no socket to time out, and Ctrl-C reaches
    the work itself.
    """
    raw = os.environ.get("AGENTS_HUB_AGENT_TIMEOUT", "900")
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    return None if seconds <= 0 else seconds


def get_backend():
    """Direct calls unless AGENTS_HUB_URL names a service somewhere else."""
    url = os.environ.get("AGENTS_HUB_URL", "").strip()
    if url:
        return HttpBackend(url)
    _ensure_importable()
    return DirectBackend()


def _ensure_importable() -> None:
    """Make the service's modules importable from any working directory.

    Two entries, mirroring what ``dashboard/backend/main.py`` sets up: the
    repository root for ``agents`` / ``tasks`` / ``workspace`` and friends, and
    the backend directory, because the route modules import their Pydantic
    models by bare name (``from models import ...``).
    """
    import sys
    root = Path(__file__).resolve().parent
    for entry in (root, root / "dashboard" / "backend"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
