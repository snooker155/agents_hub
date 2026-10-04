"""
Routes: a terminal into a run's container or a service replica's container
(docs/terminal.md). The sessions themselves live in ``common/terminal.py``.

``POST /api/terminal/{kind}/{id}/ticket`` (``kind`` is ``run`` or
``replica``) checks, with the caller's ordinary credential, that the target
has a running container here and that the caller may open a shell in it, and
answers a one-time, minute-long terminal ticket for it
(``common/preview_tickets.py``, ``mint_terminal``). A refusal is answered
here with a message the panel shows as is: a run in local mode, a finished
run, a container on another host, a role that does not reach, the per-user
session limit. With ``{"session_id": ...}`` the ticket resumes that session
instead, which must be the caller's and still open.

``GET /api/terminal/{kind}/{id}/ws?ticket=...`` is the socket. The auth
middleware does not see WebSockets, and a ticket is the only credential
accepted here, in every mode: it names the target, so a ticket for one
container never opens another. Messages:

- client to hub, JSON text: ``{"type": "input", "data": "..."}``,
  ``{"type": "resize", "cols": 120, "rows": 32}``, ``{"type": "ping"}``, and
  ``{"type": "close"}`` to end the session now rather than leave it waiting
  out its grace period;
- hub to client: one JSON ``{"type": "session", ...}`` first (the session id
  to resume with, whether this was a resume, the limits), then the replayed
  buffer and the live output as binary frames, JSON ``{"type": "exit"}`` when
  the shell ends, ``{"type": "taken_over"}`` when another socket attached to
  the same session, ``{"type": "error", "detail": ...}`` before a refusal's
  close.

``GET /api/terminal/sessions`` lists the caller's open sessions (an admin's:
everyone's), so a page reload can offer to resume one.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from common import identity, preview_tickets
from common import terminal as term

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/terminal", tags=["terminal"])

#: Largest input frame accepted; a paste bigger than this is cut, not refused.
MAX_INPUT_CHARS = 64 * 1024

#: Close codes the panel reads: 4401 no or bad ticket, 4403 refused by role,
#: 4404 gone, 4409 cannot open, 4410 session ended, 4429 limit.
_CLOSE_FOR_STATUS = {401: 4401, 403: 4403, 404: 4404, 410: 4410, 429: 4429}


class TicketBody(BaseModel):
    session_id: Optional[str] = None


def _refusal(exc: term.TerminalRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _session_for(principal: Any, session_id: str, kind: str, target_id: str) -> term.TerminalSession:
    session = term.manager().get(session_id)
    if session is None:
        raise term.TerminalRefused(
            "That terminal session has ended (it was closed, went idle, or no one "
            "reattached within its grace period).", 410)
    if session.target.kind != kind or session.target.id != target_id:
        raise term.TerminalRefused("That terminal session belongs to another target.", 404)
    if session.user_id != getattr(principal, "id", None):
        raise term.TerminalRefused("That terminal session is not yours.", 403)
    return session


@router.post("/{kind}/{target_id}/ticket")
async def mint_ticket(kind: str, target_id: str, request: Request,
                      body: Optional[TicketBody] = None) -> Dict[str, Any]:
    principal = identity.request_principal(request)
    session_id = (body.session_id if body else None) or None
    try:
        target = await asyncio.to_thread(term.resolve_target, kind, target_id)
        term.authorize(principal, target)
        if session_id:
            _session_for(principal, session_id, kind, target_id)
        else:
            term.manager().check_limit(principal.id)
    except term.TerminalRefused as exc:
        raise _refusal(exc)
    ttl = preview_tickets.TERMINAL_TTL_SECONDS
    return {
        "ticket": preview_tickets.mint_terminal(principal, target_kind=kind, target_id=target_id,
                                                session_id=session_id, ttl_seconds=ttl),
        "expires_in": ttl,
        "container": target.container,
        "label": target.label,
    }


@router.get("/sessions")
async def list_sessions(request: Request) -> Dict[str, Any]:
    principal = identity.request_principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    sessions = term.manager().all()
    if not (principal.is_admin or principal.kind in ("local", "token")):
        sessions = [s for s in sessions if s.user_id == principal.id]
    return {"items": [s.describe() for s in sessions]}


async def _refuse(websocket: WebSocket, exc: term.TerminalRefused) -> None:
    """Tell the panel why, then close: a close reason is cut at 123 bytes,
    a message is not."""
    try:
        await websocket.send_text(json.dumps({"type": "error", "detail": exc.detail,
                                              "status": exc.status}))
        await websocket.close(code=_CLOSE_FOR_STATUS.get(exc.status, 4409))
    except Exception:  # noqa: BLE001 - the client may already be gone
        log.debug("terminal: refusal not delivered", exc_info=True)


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@router.websocket("/{kind}/{target_id}/ws")
async def terminal_socket(websocket: WebSocket, kind: str, target_id: str) -> None:
    found = preview_tickets.verify_terminal(websocket.query_params.get("ticket") or "",
                                            target_kind=kind, target_id=target_id)
    principal = term.principal_from_ticket(found) if found else None
    if principal is None:
        await websocket.close(code=4401, reason="a valid terminal ticket is required")
        return
    await websocket.accept()
    ip = identity.client_ip(websocket)
    cols = _int(websocket.query_params.get("cols"), 80)
    rows = _int(websocket.query_params.get("rows"), 24)
    try:
        target = await asyncio.to_thread(term.resolve_target, kind, target_id)
        term.authorize(principal, target)
        resumed = bool(found.get("session"))
        if resumed:
            session = _session_for(principal, str(found["session"]), kind, target_id)
        else:
            session = await asyncio.to_thread(term.manager().open, principal, target,
                                               cols=cols, rows=rows, ip=ip)
    except term.TerminalRefused as exc:
        await _refuse(websocket, exc)
        return

    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[Any]" = asyncio.Queue()

    def sink(item: Any) -> None:
        # Called from the session's reader thread (under its lock): hand the
        # item to this socket's loop and return at once.
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            pass  # the loop is closed: this socket is gone, the reaper takes over

    replay = session.attach(sink)
    if resumed:
        term.manager().resumed(session, principal, ip=ip)
    await websocket.send_text(json.dumps({
        "type": "session", "session_id": session.id, "resumed": resumed,
        "container": session.target.container, "label": session.target.label,
        "grace_seconds": term.grace_seconds(), "idle_seconds": term.idle_seconds(),
    }))
    if replay:
        await websocket.send_bytes(replay)
    if resumed:
        session.resize(cols, rows)

    closing = {"by_user": False}

    async def _send() -> None:
        while True:
            item = await queue.get()
            if item == term.EXITED:
                await websocket.send_text(json.dumps({
                    "type": "exit", "code": session.exit_code,
                    "why": session.close_code or "exited",
                    "reason": session.close_reason or "the shell exited"}))
                await websocket.close(code=1000)
                return
            if item == term.TAKEN_OVER:
                await websocket.send_text(json.dumps({"type": "taken_over"}))
                await websocket.close(code=4000)
                return
            await websocket.send_bytes(item)

    async def _receive() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            if mtype == "input":
                data = str(msg.get("data") or "")[:MAX_INPUT_CHARS]
                if data:
                    await asyncio.to_thread(session.write, data.encode("utf-8"))
            elif mtype == "resize":
                session.resize(_int(msg.get("cols"), 80), _int(msg.get("rows"), 24))
            elif mtype == "close":
                closing["by_user"] = True
                return

    sender = asyncio.create_task(_send())
    receiver = asyncio.create_task(_receive())
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (sender, receiver):
            task.cancel()
        for task in (sender, receiver):
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:  # noqa: BLE001 - a send on a closed socket, nothing to do
                log.debug("terminal: socket task ended with an error", exc_info=True)
        session.detach(sink)
        if closing["by_user"]:
            await asyncio.to_thread(term.manager().close, session, reason="closed by the user",
                                    code="closed")
            try:
                await websocket.close(code=1000)
            except Exception:  # noqa: BLE001 - already closed
                log.debug("terminal: close after user close failed", exc_info=True)
