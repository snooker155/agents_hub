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

import asyncio
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from common import access, browser_service, identity
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


# ── The service itself, from the Settings page ───────────────────────────────
#
# common/browser_service.py does the work; these routes are its API. Changing
# the address, the token or the mode writes .env (the same writer the Settings
# page uses) and applies at once, since tools/browser.py reads them live.
# Everything here is an operator action: admin only in multi mode.

class ServiceConfigBody(BaseModel):
    url: Optional[str] = Field(default=None, max_length=500)
    token: Optional[str] = Field(default=None, max_length=500)
    mode: Optional[Literal["local", "container"]] = None
    #: True: mint a fresh token server side (the answer carries it once).
    generate_token: bool = False


def _require_admin(request: Request) -> None:
    identity.require_role(identity.request_principal(request), admin=True)


def _service_error(exc: "browser_service.BrowserServiceError") -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _hub_port(request: Request) -> int:
    try:
        return int(request.url.port or 8000)
    except (TypeError, ValueError):
        return 8000


@router.get("/service")
async def service_status(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    return await asyncio.to_thread(browser_service.status)


@router.put("/service/config")
async def service_config(body: ServiceConfigBody, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    from routes.settings import _write_env_key
    minted: Optional[str] = None
    if body.url is not None:
        url = body.url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="url must start with http:// or https://")
        _write_env_key("AGENTS_HUB_BROWSER_URL", url)
    if body.generate_token:
        import secrets as _secrets
        minted = _secrets.token_urlsafe(32)
        _write_env_key("AGENTS_HUB_BROWSER_TOKEN", minted)
    elif body.token is not None:
        _write_env_key("AGENTS_HUB_BROWSER_TOKEN", body.token.strip())
    if body.mode is not None:
        _write_env_key("AGENTS_HUB_BROWSER_MODE", body.mode)
        if body.mode == "container" and not browser_service.setting("AGENTS_HUB_BROWSER_HUB_URL"):
            # The container reaches the hub through the host gateway, not
            # localhost (common/hub_urls.py); set it once so a deployed app
            # opens in the agent's browser without more configuration.
            _write_env_key("AGENTS_HUB_BROWSER_HUB_URL", f"http://host.docker.internal:{_hub_port(request)}")
    out = await asyncio.to_thread(browser_service.status)
    if minted:
        out["token"] = minted
    return out


@router.post("/service/start")
async def service_start(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    from routes.settings import _write_env_key
    mode = browser_service.configured_mode()
    token = browser_service.configured_token()
    url = browser_service.configured_url()
    port = browser_service.DEFAULT_PORT
    if url:
        from urllib.parse import urlsplit
        port = int(urlsplit(url).port or browser_service.DEFAULT_PORT)
    try:
        if mode == "container":
            result = await asyncio.to_thread(browser_service.start_container, token, port)
        else:
            result = await asyncio.to_thread(browser_service.start_local, token, port)
    except browser_service.BrowserServiceError as exc:
        raise _service_error(exc)
    if not url:
        # Nothing configured yet: the service this hub just started is the one.
        _write_env_key("AGENTS_HUB_BROWSER_URL", f"http://127.0.0.1:{port}")
    return {**result, "status": await asyncio.to_thread(browser_service.status)}


@router.post("/service/stop")
async def service_stop(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        stopped_local = await asyncio.to_thread(browser_service.stop_local)
        stopped_container = False
        if browser_service.docker_status()["available"]:
            stopped_container = await asyncio.to_thread(browser_service.stop_container)
    except browser_service.BrowserServiceError as exc:
        raise _service_error(exc)
    return {"stopped": stopped_local or stopped_container,
            "status": await asyncio.to_thread(browser_service.status)}


@router.post("/service/install-chromium")
async def service_install_chromium(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    try:
        return {"job": browser_service.install_chromium()}
    except browser_service.BrowserServiceError as exc:
        raise _service_error(exc)


@router.get("/service/jobs/{job_id}")
async def service_job(job_id: str, request: Request) -> Dict[str, Any]:
    _require_admin(request)
    job = browser_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job


@router.get("/service/log")
async def service_log(request: Request, tail: int = 200) -> Dict[str, Any]:
    _require_admin(request)
    tail = max(1, min(int(tail), 2000))
    if browser_service.configured_mode() == "container" and browser_service.docker_status()["available"]:
        text = await asyncio.to_thread(browser_service.container_logs, tail)
    else:
        text = browser_service.log_tail(tail)
    return {"text": text}


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


class ControlBody(BaseModel):
    on: bool = True


@router.post("/sessions/{session_id}/control")
async def set_control(session_id: str, body: ControlBody, request: Request) -> Dict[str, Any]:
    """Take or release control of a session. While a person holds it the
    agent's browser_open and browser_act wait (the service answers them 423),
    so the two never drive the page at once; the hold lapses on its own when
    the person stops watching (docs/browser.md)."""
    info = _load(session_id)
    _check(request, info.get("workspace"), write=True)
    return _json(_service("POST", f"/sessions/{session_id}/control",
                          json={"on": bool(body.on), "by": _username(request)}))


async def _service_frames(session_id: str) -> AsyncIterator[str]:
    """The service's frame stream for one session, message by message.
    Tests replace this function."""
    import websockets
    base, token, _timeout = browser_tools._config()
    ws_base = "ws" + base[len("http"):] if base.startswith("http") else base
    async with websockets.connect(
            f"{ws_base}/sessions/{session_id}/stream",
            additional_headers={"Authorization": f"Bearer {token}"},
            max_size=16 * 1024 * 1024, open_timeout=10) as upstream:
        async for message in upstream:
            yield message if isinstance(message, str) else message.decode("utf-8", "replace")


@router.websocket("/sessions/{session_id}/ws")
async def stream_session(websocket: WebSocket, session_id: str) -> None:
    """Frames pushed as the page paints, relayed from the service's stream.

    The auth middleware does not see WebSockets, so the credential is read
    here the same way the SSE stream reads it (a header, or ``?token=`` for
    a client that cannot set one) and the workspace check is the one every
    other route makes. A failure to reach the service is sent as one
    ``{"type": "error"}`` message before the close, which is the client's
    cue to fall back to polling ``/frame``.
    """
    principal = identity.current_principal(websocket)
    if principal is None:
        await websocket.close(code=1008, reason="unauthorized")
        return
    websocket.state.principal = principal
    if not browser_tools._configured():
        await websocket.close(code=1008, reason="the browser service is not configured")
        return
    try:
        info = await asyncio.to_thread(_load, session_id)
        _check(websocket, info.get("workspace"))
    except HTTPException as exc:
        await websocket.close(code=1008, reason=str(exc.detail)[:120])
        return
    await websocket.accept()

    async def _relay() -> None:
        async for message in _service_frames(session_id):
            await websocket.send_text(message)

    async def _watch() -> None:
        while True:
            await websocket.receive()

    relay = asyncio.create_task(_relay())
    watch = asyncio.create_task(_watch())
    try:
        done, _pending = await asyncio.wait({relay, watch}, return_when=asyncio.FIRST_COMPLETED)
        if relay in done and not relay.cancelled() and relay.exception() is not None:
            exc = relay.exception()
            if not isinstance(exc, WebSocketDisconnect):
                log.warning("browser frame relay for %s failed: %s", session_id, exc)
                try:
                    await websocket.send_text(json.dumps({"type": "error", "detail": f"{type(exc).__name__}: {exc}"[:300]}))
                    await websocket.close(code=1011, reason="frame stream failed")
                except Exception:  # noqa: BLE001 - the client may be gone already
                    log.debug("_watch: best-effort step failed", exc_info=True)
        elif relay in done and not relay.cancelled():
            try:
                await websocket.close(code=1000)
            except Exception:  # noqa: BLE001 - the client may be gone already
                log.debug("_watch: best-effort step failed", exc_info=True)
    finally:
        for task in (relay, watch):
            task.cancel()
        for task in (relay, watch):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - a cancelled or already failed relay task is fine here
                log.debug("_watch: best-effort step failed", exc_info=True)


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
    except Exception:  # noqa: BLE001 - unreadable or unavailable input falls back to the default
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
