"""The user-facing browser: frames and input of an agent run's browser
session, and free browsing sessions on the same service. See docs/browser.md.

Everything here is a client of the browser service (deploy/browser/app.py),
through the same token and HTTP helper the agent's tools use
(tools/browser.py). The service keeps the registry of which session belongs to
which run and workspace, so nothing here holds state of its own.

Two rules carry over from the agent's tools unchanged:

* A session is created with its workspace's domain policy, computed by
  ``tools.browser.session_policy`` with that workspace active, and the service
  enforces it on every request the page makes, whoever drives it.
* An address a person types is checked on the hub side with
  ``tools.web.validate_url`` under the session's workspace before it is passed
  on, the same pre-flight ``browser_open`` does.

In ``multi`` mode a session is visible to members of its workspace, and only
an editor there may drive it, close it or hand it to an agent.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from common import access, identity
from common.auth import WS_EDITOR
from tools import browser as browser_tools

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])

NOT_CONFIGURED_DETAIL = (
    "the browser service is not configured: set AGENTS_HUB_BROWSER_URL and "
    "AGENTS_HUB_BROWSER_TOKEN (see docs/browser.md)"
)


# ── Service calls ────────────────────────────────────────────────────────────

def _service(method: str, path: str, **kwargs: Any):
    """One call to the browser service; 503 when it is not configured and
    502 when it does not answer. Tests replace this function."""
    if not browser_tools._configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_DETAIL)
    try:
        return browser_tools._request(method, path, **kwargs)
    except browser_tools.BrowserError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _json(resp) -> Dict[str, Any]:
    """The service's answer, or its status and detail as this route's error."""
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=browser_tools._detail(resp))
    return resp.json()


def _load(session_id: str) -> Dict[str, Any]:
    return _json(_service("GET", f"/sessions/{session_id}"))


@contextmanager
def _in_workspace(workspace: Optional[str]) -> Iterator[None]:
    """Make ``workspace`` the active one for policy lookups in this block, the
    way the chat route does before an agent runs, so ``session_policy`` and
    ``validate_url`` read that workspace's domain lists."""
    from common.workspace_context import _workspace_ctx
    token = _workspace_ctx.set(workspace or None)
    try:
        yield
    finally:
        _workspace_ctx.reset(token)


# ── Access ───────────────────────────────────────────────────────────────────

def _check(request: Request, workspace: Optional[str], *, write: bool = False) -> None:
    """Membership in the session's workspace: seeing it for a read, editor for
    anything that drives or ends it. A no-op outside ``multi`` mode."""
    principal = identity.request_principal(request)
    access.require_visible(principal, workspace)
    if write and workspace and principal is not None and principal.kind == "user":
        identity.require_role(principal, workspace=workspace, role=WS_EDITOR)


def _username(request: Request) -> str:
    principal = identity.request_principal(request)
    return str(getattr(principal, "username", "") or "you")


# ── Models ───────────────────────────────────────────────────────────────────

class CreateBody(BaseModel):
    workspace: str = "default"
    url: str = ""


class InputBody(BaseModel):
    kind: Literal["click", "dblclick", "mousemove", "type", "key", "scroll",
                  "navigate", "back", "forward", "reload"]
    x: float = 0
    y: float = 0
    text: str = Field(default="", max_length=10_000)
    key: str = Field(default="", max_length=64)
    dx: float = 0
    dy: float = 0
    url: str = Field(default="", max_length=4_000)


class HandoffBody(BaseModel):
    agent_id: str
    message: str = Field(default="", max_length=20_000)
    workspace: str = ""


# ── Routes ───────────────────────────────────────────────────────────────────

@router.get("/status")
async def status() -> Dict[str, Any]:
    url, _token, _timeout = browser_tools._config()
    return {"configured": browser_tools._configured(), "url": url or None}


@router.get("/sessions")
async def list_sessions(request: Request, workspace: Optional[str] = None) -> Dict[str, Any]:
    """Open sessions, agents' and people's, narrowed to a workspace when one
    is named and always to the workspaces the caller can see."""
    params = {"workspace": workspace} if workspace else {}
    if workspace:
        _check(request, workspace)
    sessions: List[Dict[str, Any]] = _json(_service("GET", "/sessions", params=params)).get("sessions") or []
    principal = identity.request_principal(request)
    return {"sessions": access.filter_by_workspace(principal, sessions)}


@router.post("/sessions")
async def create_session(body: CreateBody, request: Request) -> Dict[str, Any]:
    """A session a person drives, under the chosen workspace's domain policy.
    The first address, when given, is checked before the session exists."""
    workspace = Path(body.workspace.strip() or "default").name
    _check(request, workspace, write=True)
    url = body.url.strip()
    with _in_workspace(workspace):
        if url:
            ok, reason = browser_tools.validate_url(url)
            if not ok:
                raise HTTPException(status_code=400, detail=f"refused {url}: {reason}")
        policy = browser_tools.session_policy()
    created = _json(_service("POST", "/sessions", json={
        "policy": policy, "workspace": workspace, "owner": "user",
        "label": _username(request), "run_id": "",
    }))
    sid = str(created["session_id"])
    info: Dict[str, Any] = {"url": "about:blank", "title": ""}
    if url:
        info = _json(_service("POST", f"/sessions/{sid}/navigate", json={"url": url}))
    return {"session_id": sid, "url": info.get("url") or "", "title": info.get("title") or ""}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> Dict[str, Any]:
    info = _load(session_id)
    _check(request, info.get("workspace"))
    return info


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, request: Request) -> Dict[str, Any]:
    info = _load(session_id)
    _check(request, info.get("workspace"), write=True)
    _service("DELETE", f"/sessions/{session_id}")
    return {"ok": True}


@router.get("/sessions/{session_id}/frame")
async def get_frame(session_id: str, request: Request) -> Dict[str, Any]:
    """The viewport as the service returns it: ``{url, title, width, height,
    image}``, ``image`` a JPEG data URL."""
    info = _load(session_id)
    _check(request, info.get("workspace"))
    return _json(_service("GET", f"/sessions/{session_id}/frame"))


@router.post("/sessions/{session_id}/input")
async def send_input(session_id: str, body: InputBody, request: Request) -> Dict[str, Any]:
    """A click, keystroke, scroll or navigation from the dashboard, into the
    same page the agent works in. A typed address gets the hub's pre-flight
    under the session's workspace first; the service then checks it again and
    re-checks wherever any input led."""
    info = _load(session_id)
    workspace = info.get("workspace")
    _check(request, workspace, write=True)
    payload = body.model_dump()
    if body.kind == "navigate":
        url = body.url.strip()
        with _in_workspace(workspace):
            ok, reason = browser_tools.validate_url(url)
        if not ok:
            raise HTTPException(status_code=400, detail=f"refused {url}: {reason}")
        payload["url"] = url
    return _json(_service("POST", f"/sessions/{session_id}/input", json=payload))


@router.get("/runs/{run_id}/session")
async def run_session(run_id: str, request: Request) -> Dict[str, Any]:
    """The session an agent run is using, for the run's live view."""
    sessions = _json(_service("GET", "/sessions", params={"run_id": run_id})).get("sessions") or []
    principal = identity.request_principal(request)
    sessions = access.filter_by_workspace(principal, sessions)
    if not sessions:
        raise HTTPException(status_code=404, detail="this run has no open browser session")
    return sessions[0]


def _handoff_description(message: str, info: Dict[str, Any]) -> str:
    where = info.get("url") or "a blank page"
    note = (
        f"A browser session is attached to this task (session {info.get('session_id')}, "
        f"currently at {where}). Your browser tools continue in that same page, with "
        "whatever the person already did in it: call browser_read first to see where it "
        "is, and browser_open only when you need another address."
    )
    return f"{message.strip()}\n\n{note}" if message.strip() else note


def _launch(task_id, agent_id: str) -> Dict[str, Any]:
    """Assign and start, on the same terms as ``POST /api/tasks/{id}/assign``.
    Tests replace this function."""
    from tasks.assign import assign_agent_to_task
    from tasks.serialize import task_to_dict
    return assign_agent_to_task(task_id, agent_id, None, task_to_dict=task_to_dict)


@router.post("/sessions/{session_id}/handoff")
async def handoff(session_id: str, body: HandoffBody, request: Request) -> Dict[str, Any]:
    """Hand a session to an agent as a task, so the run is durable and visible
    like any other. The run continues in the same page: the launch carries
    ``AGENTS_HUB_BROWSER_SESSION`` for a subprocess, and the session is retagged
    with the new run id, which is what the agent's tools look up when neither
    reaches them (tools/browser.py ``_adopted_session``)."""
    from runtime.entity_launch import child_env
    from tasks import service as tasks_service
    from tasks.assign import AssignError

    info = _load(session_id)
    workspace = str(info.get("workspace") or "default")
    if body.workspace and Path(body.workspace).name != workspace:
        raise HTTPException(
            status_code=400,
            detail=f"this session carries the policy of workspace '{workspace}'; hand it to an agent there")
    _check(request, workspace, write=True)
    agent_id = body.agent_id.strip()
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")

    try:
        from workspace import create_workspace_folder
        create_workspace_folder(workspace)
    except Exception:
        log.debug("could not ensure workspace folder %s", workspace, exc_info=True)

    first_line = (body.message.strip().splitlines() or [""])[0][:80]
    title = first_line or f"Continue in the browser: {info.get('title') or info.get('url') or session_id[:8]}"
    task = tasks_service.create_task(
        title=title, description=_handoff_description(body.message, info), workspace=workspace)

    token = browser_tools.adopt_session(session_id)
    try:
        with child_env({browser_tools.ADOPT_ENV: session_id}):
            result = _launch(task.id, agent_id)
    except AssignError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    finally:
        browser_tools.adopted_session.reset(token)

    run_id = result.get("run_id") if isinstance(result, dict) else None
    patch: Dict[str, Any] = {"owner": "agent", "label": agent_id}
    if run_id:
        patch["run_id"] = str(run_id)
    try:
        _service("PATCH", f"/sessions/{session_id}", json=patch)
    except HTTPException:
        log.warning("could not retag browser session %s after hand-off", session_id)
    return {"task_id": str(task.id), "run_id": run_id}
