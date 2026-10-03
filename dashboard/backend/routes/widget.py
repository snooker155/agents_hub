"""The embeddable chat widget over REST (widgets/, docs/widget.md).

Two halves under one prefix.

Management, ``/api/widgets/...``: the Widgets page's backend. Normal hub
auth; reading a widget and its conversations follows workspace visibility,
changing one needs the editor role in its workspace (``multi`` mode; a no-op
otherwise). Create, update, delete, key rotation and a thread's deletion are
written to the audit log as ``widget.<verb>``.

Visitors, ``/api/widgets/public/{widget_id}/...``: open in
``common.auth.is_open_path`` and guarded by the widget itself (publishable
key, Origin allowlist, signed visitor token, limits; see widgets/service.py).
A message streams its turn back as SSE over the POST response. Refusals are
``{"detail", "code"}`` with a stable ``code`` the script maps to a message in
the visitor's language. CORS for these paths is answered per widget by
``widgets.edge.WidgetEdgeMiddleware``, which also maps ``/widget.js`` to the
script route here.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from common import access, audit, identity
from common.auth import WS_EDITOR
from widgets import service, store
from widgets.models import MAX_MESSAGE_CHARS, WidgetValidationError
from widgets.visitor import mint_preview, mint_visitor, verify_visitor

router = APIRouter(prefix="/api/widgets", tags=["widgets"])

#: The script the snippet loads. Plain JavaScript with no build step, served
#: as is.
SCRIPT_FILE = Path(__file__).resolve().parents[2] / "frontend" / "widget" / "widget.js"
#: Browsers may reuse the script this long before asking again (with the
#: ETag, so an unchanged script is a 304). Short, so a hub upgrade reaches
#: every embedding site within minutes.
SCRIPT_MAX_AGE = 300

_script_cache: Dict[str, Any] = {}


class WidgetCreate(BaseModel):
    workspace: str = "default"
    name: str
    agent_id: str
    allowed_origins: List[str] = []
    enabled: bool = True
    title: str = ""
    greeting: str = ""
    placeholder: str = ""
    accent: str = "navy"
    language: str = "auto"
    limits: Optional[Dict[str, Any]] = None
    # A stored version of the agent the widget's visitors talk to; None = live.
    agent_version: Optional[int] = None


class WidgetUpdate(BaseModel):
    name: Optional[str] = None
    agent_id: Optional[str] = None
    allowed_origins: Optional[List[str]] = None
    enabled: Optional[bool] = None
    title: Optional[str] = None
    greeting: Optional[str] = None
    placeholder: Optional[str] = None
    accent: Optional[str] = None
    language: Optional[str] = None
    limits: Optional[Dict[str, Any]] = None
    # Sent as null, clears the pin (the update applies only the fields sent).
    agent_version: Optional[int] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _principal(request: Request):
    return identity.request_principal(request)


def _load(request: Request, widget_id: str, *, write: bool = False) -> Dict[str, Any]:
    widget = store.get_widget(widget_id)
    if widget is None:
        raise HTTPException(status_code=404, detail="Widget not found")
    principal = _principal(request)
    if write:
        identity.require_role(principal, workspace=widget["workspace"], role=WS_EDITOR)
    else:
        access.require_visible(principal, widget["workspace"])
    return widget


def _record(request: Request, action: str, widget: Dict[str, Any],
            details: Optional[Dict[str, Any]] = None) -> None:
    audit.record(action, principal=_principal(request), object_type="widget",
                 object_id=widget["widget_id"], workspace=widget["workspace"],
                 ip=identity.client_ip(request), method=request.method, path=request.url.path,
                 details={"name": widget.get("name"), **(details or {})})


def _bad(exc: WidgetValidationError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _refuse(exc: service.WidgetError) -> JSONResponse:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail, "code": exc.code},
                        headers=headers)


def _public(request: Request, widget_id: str):
    return service.check_public(
        widget_id, key=request.headers.get("x-widget-key"),
        origin=request.headers.get("origin"),
        preview_ticket=request.headers.get("x-widget-preview"),
        headers=request.headers, scheme=request.url.scheme, ip=identity.client_ip(request))


def _visitor(request: Request, widget: Dict[str, Any]) -> str:
    return service.check_visitor(widget, request.headers.get("x-visitor-token"))


async def _json_body(request: Request, cap: int) -> Dict[str, Any]:
    """The request body as a JSON object, refusing more than ``cap`` bytes
    before reading it all: a visitor's message is bounded by the widget's
    attachment limits, and a larger body is not read into memory."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise service.WidgetError(413, "attachment_too_large", "The message is too large")
    chunks: List[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cap:
            raise service.WidgetError(413, "attachment_too_large", "The message is too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise service.WidgetError(400, "bad_request", "The body is not valid JSON")
    if not isinstance(body, dict):
        raise service.WidgetError(400, "bad_request", "The body must be a JSON object")
    return body


def _body_cap(widget: Dict[str, Any]) -> int:
    limits = widget.get("limits") or {}
    per_file = int(limits.get("attachment_max_bytes") or 0)
    count = int(limits.get("max_attachments") or 0)
    # base64 is 4/3 of the bytes; the rest is the text, the names and JSON.
    return count * math.ceil(per_file * 4 / 3) + MAX_MESSAGE_CHARS * 4 + 64 * 1024


def _script() -> Dict[str, Any]:
    """The script's bytes and ETag, re-read when the file changes."""
    try:
        stat = SCRIPT_FILE.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="widget.js is not installed")
    if _script_cache.get("mtime") != stat.st_mtime_ns:
        body = SCRIPT_FILE.read_bytes()
        _script_cache.update(mtime=stat.st_mtime_ns, body=body,
                             etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"')
    return _script_cache


# ── the script ───────────────────────────────────────────────────────────────

@router.get("/public/widget.js", include_in_schema=False)
async def widget_script(request: Request):
    """``/widget.js`` (mapped here by the edge middleware) and its ``/api``
    alias. A classic script, loaded by a ``<script>`` tag on any site."""
    script = _script()
    headers = {
        "Cache-Control": f"public, max-age={SCRIPT_MAX_AGE}",
        "ETag": script["etag"],
        "X-Content-Type-Options": "nosniff",
        # Any site may load it; a hub that sets a strict resource policy
        # elsewhere must not block the one file meant to be embedded.
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    if request.headers.get("if-none-match") == script["etag"]:
        return Response(status_code=304, headers=headers)
    return Response(content=script["body"], media_type="application/javascript; charset=utf-8",
                    headers=headers)


# ── the visitor's side ───────────────────────────────────────────────────────

@router.get("/public/{widget_id}/config")
async def public_config(widget_id: str, request: Request):
    try:
        widget, preview = _public(request, widget_id)
    except service.WidgetError as exc:
        return _refuse(exc)
    return {**service.visitor_config(widget), "preview": preview}


@router.post("/public/{widget_id}/visitor")
async def public_visitor(widget_id: str, request: Request):
    """A visitor token: a renewal of the presented one when it is still
    valid (same visitor, new expiry), else a new visitor."""
    try:
        widget, _preview = _public(request, widget_id)
        existing = verify_visitor(request.headers.get("x-visitor-token"), widget["widget_id"])
        if not existing:
            service.check_mint(identity.client_ip(request))
    except service.WidgetError as exc:
        return _refuse(exc)
    return mint_visitor(widget["widget_id"], existing)


@router.get("/public/{widget_id}/threads")
async def public_threads(widget_id: str, request: Request):
    try:
        widget, _preview = _public(request, widget_id)
        visitor_id = _visitor(request, widget)
    except service.WidgetError as exc:
        return _refuse(exc)
    threads = store.list_threads(widget["widget_id"], visitor_id=visitor_id,
                                 include_visitor_deleted=False, limit=service.MAX_THREADS_PER_VISITOR)
    return {"threads": [service.visitor_thread(t) for t in threads]}


@router.post("/public/{widget_id}/threads")
async def public_create_thread(widget_id: str, request: Request):
    try:
        widget, preview = _public(request, widget_id)
        visitor_id = _visitor(request, widget)
        body = await _json_body(request, 16 * 1024)
        thread = service.create_visitor_thread(widget, visitor_id, title=str(body.get("title") or ""),
                                               preview=preview)
    except service.WidgetError as exc:
        return _refuse(exc)
    except WidgetValidationError as exc:
        return _refuse(service.WidgetError(400, "bad_request", str(exc)))
    return service.visitor_thread(thread)


@router.get("/public/{widget_id}/threads/{thread_id}")
async def public_thread(widget_id: str, thread_id: str, request: Request):
    try:
        widget, _preview = _public(request, widget_id)
        visitor_id = _visitor(request, widget)
        thread = service.own_thread(widget, visitor_id, thread_id)
    except service.WidgetError as exc:
        return _refuse(exc)
    messages = store.list_messages(thread["thread_id"])
    return {"thread": service.visitor_thread(thread),
            "messages": [service.visitor_message(m) for m in messages]}


@router.delete("/public/{widget_id}/threads/{thread_id}")
async def public_delete_thread(widget_id: str, thread_id: str, request: Request):
    """Hidden from the visitor from now on. The owner still sees it, marked
    as deleted by the visitor, and its runs stay on the Messages page."""
    try:
        widget, _preview = _public(request, widget_id)
        visitor_id = _visitor(request, widget)
        thread = service.own_thread(widget, visitor_id, thread_id)
    except service.WidgetError as exc:
        return _refuse(exc)
    store.mark_thread_deleted_by_visitor(thread["thread_id"])
    return {"deleted": True}


@router.post("/public/{widget_id}/threads/{thread_id}/messages")
async def public_message(widget_id: str, thread_id: str, request: Request):
    """One visitor message, answered as an SSE stream of visitor-safe events
    (widgets/turn.py): ``meta``, ``token``, ``tool_start``, ``tool_end``,
    ``handoff``, then one ``done``."""
    from widgets.agents import agent_usable
    from widgets.turn import VisitorTurn, decode_attachments

    try:
        widget, _preview = _public(request, widget_id)
        visitor_id = _visitor(request, widget)
        thread = service.own_thread(widget, visitor_id, thread_id)
        service.check_owner(widget)
        if not agent_usable(widget["agent_id"], widget["workspace"]):
            raise service.WidgetError(403, "widget_unavailable",
                                      "The widget's agent is not available in its workspace")
        body = await _json_body(request, _body_cap(widget))
        text = str(body.get("text") or "").strip()
        if len(text) > MAX_MESSAGE_CHARS:
            raise service.WidgetError(400, "message_too_long",
                                      f"A message is at most {MAX_MESSAGE_CHARS} characters")
        attachments = decode_attachments(body.get("attachments"), widget.get("limits") or {})
        if not text and not attachments:
            raise service.WidgetError(400, "empty_message", "The message is empty")
        service.check_message_limits(widget, visitor_id, identity.client_ip(request))
        service.claim_thread(thread["thread_id"])
    except service.WidgetError as exc:
        return _refuse(exc)

    turn = VisitorTurn(widget, thread, text=text, attachments=attachments)
    try:
        turn.start()
    except Exception:
        service.release_thread(thread["thread_id"])
        raise
    return StreamingResponse(turn.stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── management ───────────────────────────────────────────────────────────────

@router.get("/options")
async def widget_options():
    """The accents, languages and limit bounds the form offers."""
    return service.options()


@router.get("")
async def list_widgets(request: Request, workspace: str = "default"):
    access.require_visible(_principal(request), workspace)
    return [service.to_dict(w) for w in store.list_widgets(workspace)]


@router.post("")
async def create_widget(request: Request, payload: WidgetCreate):
    principal = _principal(request)
    workspace = (payload.workspace or "default").strip() or "default"
    identity.require_role(principal, workspace=workspace, role=WS_EDITOR)
    owner_id = getattr(principal, "id", None) or identity.current_user_id()
    try:
        widget = service.create_widget(payload.model_dump(), owner_id=owner_id)
    except WidgetValidationError as exc:
        raise _bad(exc)
    _record(request, "widget.create", widget,
            {"agent_id": widget["agent_id"], "origins": widget["allowed_origins"]})
    return service.to_dict(widget)


@router.get("/{widget_id}")
async def get_widget(widget_id: str, request: Request):
    return service.to_dict(_load(request, widget_id))


@router.patch("/{widget_id}")
async def update_widget(widget_id: str, request: Request, payload: WidgetUpdate):
    widget = _load(request, widget_id, write=True)
    changes = payload.model_dump(exclude_unset=True)
    try:
        updated = service.update_widget(widget, changes)
    except WidgetValidationError as exc:
        raise _bad(exc)
    _record(request, "widget.update", updated, {"fields": sorted(changes)})
    return service.to_dict(updated)


@router.delete("/{widget_id}")
async def delete_widget(widget_id: str, request: Request):
    widget = _load(request, widget_id, write=True)
    store.delete_widget(widget["widget_id"])
    _record(request, "widget.delete", widget)
    return {"deleted": True}


@router.post("/{widget_id}/rotate-key")
async def rotate_widget_key(widget_id: str, request: Request):
    """A new publishable key. Every copy of the old snippet stops working at
    once; visitors keep their threads (their tokens name the widget, not the
    key)."""
    widget = _load(request, widget_id, write=True)
    updated = service.rotate_key(widget)
    _record(request, "widget.rotate_key", updated)
    return service.to_dict(updated)


@router.get("/{widget_id}/snippet")
async def widget_snippet(widget_id: str, request: Request):
    widget = _load(request, widget_id)
    hub = service.public_hub_url(request.headers, request.url.scheme)
    return {"snippet": service.snippet(widget, hub), "script_url": f"{hub}/widget.js", "hub": hub}


@router.post("/{widget_id}/preview")
async def widget_preview(widget_id: str, request: Request):
    """A short-lived ticket that lets the Widgets page's live preview run the
    widget from the hub's own origin (and before it is enabled)."""
    widget = _load(request, widget_id, write=True)
    principal = _principal(request)
    ticket = mint_preview(widget["widget_id"], minted_by=getattr(principal, "id", "") or "")
    return {**ticket, "widget_id": widget["widget_id"], "public_key": widget["public_key"],
            "script_path": "/api/widgets/public/widget.js"}


@router.get("/{widget_id}/threads")
async def widget_threads(widget_id: str, request: Request, limit: int = 200):
    """Every visitor's threads, newest activity first, including the ones a
    visitor deleted (marked) and the preview's (flagged)."""
    widget = _load(request, widget_id)
    threads = store.list_threads(widget["widget_id"], limit=limit)
    return [{**t, "agent_name": service.agent_name(t.get("agent_id"))} for t in threads]


@router.get("/{widget_id}/threads/{thread_id}")
async def widget_thread(widget_id: str, thread_id: str, request: Request):
    widget = _load(request, widget_id)
    thread = store.get_thread(thread_id)
    if thread is None or thread["widget_id"] != widget["widget_id"]:
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = store.list_messages(thread["thread_id"])
    return {"thread": {**thread, "agent_name": service.agent_name(thread.get("agent_id"))},
            "messages": [{**m, "agent_name": service.agent_name(m.get("agent_id"))}
                         if m["role"] != "user" else m for m in messages]}


@router.delete("/{widget_id}/threads/{thread_id}")
async def delete_widget_thread(widget_id: str, thread_id: str, request: Request):
    widget = _load(request, widget_id, write=True)
    thread = store.get_thread(thread_id)
    if thread is None or thread["widget_id"] != widget["widget_id"]:
        raise HTTPException(status_code=404, detail="Thread not found")
    store.delete_thread(thread["thread_id"])
    _record(request, "widget.thread_delete", widget, {"thread_id": thread["thread_id"]})
    return {"deleted": True}
