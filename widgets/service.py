"""
Widgets: what a site owner manages, and the checks a visitor's request passes.

Management (the Widgets page, normal hub auth): create, update, rotate the
publishable key, delete, and the embed snippet. A widget acts as the person
who created it (``owner_id``): its turns run as that principal, narrowed to
the widget's workspace, so a widget can never do more than its owner could do
in the chat. Creating one therefore needs the editor role in the workspace
and an agent the workspace may run; a turn re-checks both, so an owner who
loses the workspace (or an agent taken out of it) takes the widget down with
them instead of leaving it answering on their behalf.

A visitor's request (``/api/widgets/public/{widget_id}/...``, open in
``common.auth.is_open_path``) carries no hub credential at all. What it
carries instead, and what :func:`check_public` verifies in this order:

1. the widget exists and is enabled;
2. ``X-Widget-Key`` is the widget's publishable key. Public by design: it is
   in the page source. It says which widget, not who; rotating it cuts off
   every copy of the old snippet at once;
3. ``Origin`` is one of the widget's allowed origins (a request with no
   Origin, or another one, is refused). A browser cannot lie about it, so a
   page elsewhere cannot embed the widget; a script outside a browser can,
   which is what the limits are for;
4. per-address request limits, then (for the thread routes) a valid visitor
   token, then the per-visitor and per-address message limits and the
   widget's daily token cap.

The dashboard's live preview runs on the hub's own origin, which is not in
the widget's list. It presents a short-lived preview ticket minted by an
editor (``X-Widget-Preview``); with it, the hub's own origins pass step 3,
and a disabled widget can be previewed before it is switched on.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from common.rate_limit import SlidingWindow

from . import store
from .agents import agent_usable
from .models import (
    ACCENTS, LANGUAGES, LIMIT_BOUNDS, MAX_ATTACHMENT_BYTES, MAX_GREETING_CHARS, MAX_MESSAGE_CHARS, MAX_NAME_CHARS,
    MAX_PLACEHOLDER_CHARS, MAX_TITLE_CHARS, WidgetValidationError, validate_accent,
    validate_language, validate_limits, validate_origins, validate_text,
)

#: Every publishable key starts with this. Distinct from a personal API key's
#: ``ahk_`` so neither is ever mistaken for the other in a config file or a
#: leaked-credential scanner's rules.
KEY_PREFIX = "ahw_"

#: One address may carry a few visitors (a household, an office behind one
#: NAT), so its message limit is this many times one visitor's.
IP_MESSAGE_FACTOR = 4
#: Every public request (config, thread lists, visitor minting), per address
#: per minute, across widgets: bounds enumeration and scripted floods before
#: any widget-specific check runs.
IP_REQUESTS_PER_MINUTE = 120
#: New visitor ids per address per minute. A renewal counts too.
IP_VISITORS_PER_MINUTE = 10
#: Threads one visitor may keep open (deleted ones do not count).
MAX_THREADS_PER_VISITOR = 50
#: All of one message's attachments together: the chat pipeline's own total
#: (chat.attachments), whatever the per-file limit allows.
MAX_TOTAL_ATTACHMENT_BYTES = MAX_ATTACHMENT_BYTES
#: Prior turns sent to the agent with a new message. Compaction folds what
#: does not fit the model; this bounds what is read from the database.
HISTORY_MESSAGES = 40

# Separate windows so a widget's visitor key and an address never collide.
visitor_window = SlidingWindow()
ip_message_window = SlidingWindow()
ip_request_window = SlidingWindow()
ip_visitor_window = SlidingWindow()

#: Threads with a turn in flight in this process: a second message to the same
#: thread waits for the first answer instead of racing it.
_active_threads: set = set()


class WidgetError(Exception):
    """A refusal with an HTTP status and a stable code the script maps to a
    message in the visitor's language. ``detail`` is for the page owner and
    the logs; it never carries internals (no paths, no stack traces)."""

    def __init__(self, status: int, code: str, detail: str,
                 retry_after: Optional[int] = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.retry_after = retry_after


def reset_limits() -> None:
    """Forget every window and in-flight marker (tests)."""
    for window in (visitor_window, ip_message_window, ip_request_window, ip_visitor_window):
        window.reset()
    _active_threads.clear()


def _multi() -> bool:
    from common import identity
    return identity.current_mode() == identity.MULTI


def new_public_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


# ── who may own and use a widget ─────────────────────────────────────────────

def owner_can_run(owner_id: str, workspace: str) -> bool:
    """Whether the principal a widget acts as may still run agents in its
    workspace: always outside ``multi``; there, an existing account that is
    an administrator or at least an editor of the workspace."""
    if not _multi():
        return True
    from common import identity
    from common.auth import WS_EDITOR, role_satisfies
    if owner_id in ("service", ""):
        return owner_id == "service"
    user = identity.get_user(owner_id)
    if user is None or user.get("disabled"):
        return False
    if user.get("role") == "admin":
        return True
    return role_satisfies(identity.membership_role(workspace, owner_id), WS_EDITOR)


def _workspace_exists(workspace: str) -> bool:
    if workspace == "default":
        return True
    try:
        from workspace import get_workspace_folder
        return get_workspace_folder(workspace) is not None
    except Exception:  # noqa: BLE001 - an invalid name reads as a missing workspace
        return False


def _require_agent(agent_id: str, workspace: str) -> str:
    agent_id = str(agent_id or "").strip()
    if not agent_id:
        raise WidgetValidationError("agent_id is required")
    from agents import registry
    if registry.get_agent(agent_id) is None:
        raise WidgetValidationError(f"agent '{agent_id}' is not registered")
    if not agent_usable(agent_id, workspace):
        raise WidgetValidationError(
            f"agent '{agent_id}' is not available in workspace '{workspace}'")
    return agent_id


def validate_agent_version(agent_id: str, value: Any) -> Optional[int]:
    """A stored version of ``agent_id`` the widget's turns are built from
    (agents/versions.py), or None for the live definition."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise WidgetValidationError("agent_version must be a positive integer")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise WidgetValidationError("agent_version must be a positive integer") from exc
    if version < 1:
        raise WidgetValidationError("agent_version must be a positive integer")
    from tasks.service import validate_agent_version as _validate
    try:
        _validate(agent_id, version)
    except ValueError as exc:
        raise WidgetValidationError(str(exc)) from exc
    return version


def agent_name(agent_id: Optional[str]) -> str:
    if not agent_id:
        return ""
    try:
        from agents import registry
        spec = registry.get_agent(agent_id)
    except Exception:  # noqa: BLE001 - a registry hiccup shows the id instead of a name
        spec = None
    return (getattr(spec, "name", "") or agent_id) if spec else agent_id


# ── management ───────────────────────────────────────────────────────────────

def _texts(payload: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for field, limit in (("title", MAX_TITLE_CHARS), ("greeting", MAX_GREETING_CHARS),
                         ("placeholder", MAX_PLACEHOLDER_CHARS)):
        value = payload[field] if field in payload and payload[field] is not None else base.get(field, "")
        out[field] = validate_text(value, field=field, limit=limit)
    return out


def create_widget(payload: Dict[str, Any], *, owner_id: str) -> Dict[str, Any]:
    """A new widget acting as ``owner_id``. Raises WidgetValidationError."""
    workspace = validate_text(payload.get("workspace") or "default", field="workspace", limit=200,
                              required=True)
    if not _workspace_exists(workspace):
        raise WidgetValidationError(f"workspace '{workspace}' does not exist")
    name = validate_text(payload.get("name"), field="name", limit=MAX_NAME_CHARS, required=True)
    agent_id = _require_agent(payload.get("agent_id"), workspace)
    now = store.now_iso()
    record = {
        "widget_id": store.new_widget_id(),
        "workspace": workspace,
        "name": name,
        "agent_id": agent_id,
        "owner_id": str(owner_id or "local"),
        "public_key": new_public_key(),
        "allowed_origins": validate_origins(payload.get("allowed_origins"), multi_mode=_multi()),
        "enabled": bool(payload.get("enabled", True)),
        "accent": validate_accent(payload.get("accent")),
        "language": validate_language(payload.get("language")),
        "limits": validate_limits(payload.get("limits")),
        "agent_version": validate_agent_version(agent_id, payload.get("agent_version")),
        "created_at": now,
        "updated_at": now,
        **_texts(payload, {}),
    }
    return store.insert_widget(record)


def update_widget(widget: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the fields present in ``payload``. The workspace and the owner
    are fixed at creation: moving a widget would silently change whose
    principal answers its visitors."""
    record = dict(widget)
    if "name" in payload and payload["name"] is not None:
        record["name"] = validate_text(payload["name"], field="name", limit=MAX_NAME_CHARS,
                                       required=True)
    if "agent_id" in payload and payload["agent_id"] is not None:
        record["agent_id"] = _require_agent(payload["agent_id"], record["workspace"])
    if "allowed_origins" in payload and payload["allowed_origins"] is not None:
        record["allowed_origins"] = validate_origins(payload["allowed_origins"], multi_mode=_multi())
    if "enabled" in payload and payload["enabled"] is not None:
        record["enabled"] = bool(payload["enabled"])
    if "accent" in payload and payload["accent"] is not None:
        record["accent"] = validate_accent(payload["accent"])
    if "language" in payload and payload["language"] is not None:
        record["language"] = validate_language(payload["language"])
    if "limits" in payload and payload["limits"] is not None:
        record["limits"] = validate_limits(payload["limits"], base=record.get("limits"))
    # A null agent_version clears the pin; a new agent without a pin given
    # drops the old agent's pin (it names a version of the other agent).
    if "agent_version" in payload:
        record["agent_version"] = validate_agent_version(record["agent_id"], payload["agent_version"])
    elif record.get("agent_id") != widget.get("agent_id"):
        record["agent_version"] = None
    record.update(_texts(payload, record))
    record["updated_at"] = store.now_iso()
    return store.update_widget(record)


def rotate_key(widget: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(widget, public_key=new_public_key(), updated_at=store.now_iso())
    return store.update_widget(record)


def snippet(widget: Dict[str, Any], hub: str) -> str:
    hub = (hub or "").rstrip("/")
    return (f'<script src="{hub}/widget.js" data-widget="{widget["widget_id"]}" '
            f'data-key="{widget["public_key"]}" async></script>')


def _utc_midnight() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def tokens_today(widget_id: str) -> int:
    return store.tokens_since(widget_id, _utc_midnight())


def to_dict(widget: Dict[str, Any], *, stats: bool = True) -> Dict[str, Any]:
    """The management view: everything, plus the agent's name and, with
    ``stats``, the thread count and today's tokens."""
    out = dict(widget)
    out["agent_name"] = agent_name(widget.get("agent_id"))
    out["owner_ok"] = owner_can_run(widget.get("owner_id") or "", widget.get("workspace") or "")
    if stats:
        out["thread_count"] = store.count_threads(widget["widget_id"])
        out["tokens_today"] = tokens_today(widget["widget_id"])
    return out


def options() -> Dict[str, Any]:
    """What the form offers, from the same constants the validation uses."""
    return {"accents": list(ACCENTS), "languages": list(LANGUAGES),
            "limits": LIMIT_BOUNDS, "wildcard_allowed": not _multi(),
            "max_message_chars": MAX_MESSAGE_CHARS}


# ── the public side ──────────────────────────────────────────────────────────

_DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173", "http://0.0.0.0:5173",
                "http://localhost:3000", "http://127.0.0.1:3000")
_LOOPBACK_RE = re.compile(r"http://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?")


def request_origin_of(headers: Any, scheme: str) -> str:
    """The origin this hub is being reached at, honouring a proxy's
    ``X-Forwarded-Host``/``-Proto`` (the rule routes/openai_compat.py's
    serving info follows)."""
    host = headers.get("x-forwarded-host") or headers.get("host") or ""
    proto = headers.get("x-forwarded-proto") or scheme or "http"
    host = host.split(",")[0].strip()
    proto = proto.split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


def public_hub_url(headers: Any, scheme: str) -> str:
    """The address visitors' browsers load the script from: ``AUTH_PUBLIC_URL``
    when configured (the URL the browser reaches this hub at), else the
    request's own origin."""
    from common.config import settings
    configured = (getattr(settings, "auth_public_url", "") or "").strip().rstrip("/")
    return configured or request_origin_of(headers, scheme)


def is_hub_origin(origin: str, headers: Any, scheme: str) -> bool:
    """Whether ``origin`` is where the dashboard itself runs: this hub's own
    origin, ``AUTH_PUBLIC_URL``, or an origin the dashboard's CORS setup
    trusts (``ALLOW_ORIGINS``, or the local development defaults when it is
    unset, as in dashboard/backend/main.py). ``*`` trusts nothing here: a
    preview is the hub's own page, not any page."""
    if not origin:
        return False
    if origin in (request_origin_of(headers, scheme), public_hub_url(headers, scheme)):
        return True
    configured = (os.getenv("ALLOW_ORIGINS", "") or "").strip()
    if configured == "*":
        return False
    listed = [o.strip().rstrip("/") for o in configured.split(",") if o.strip()]
    if listed:
        return origin in listed
    return origin in _DEV_ORIGINS or bool(_LOOPBACK_RE.fullmatch(origin))


def origin_allowed(widget: Dict[str, Any], origin: Optional[str]) -> bool:
    """The widget's own list. ``*`` counts only outside ``multi`` mode, and
    even then a request must carry an Origin."""
    if not origin:
        return False
    allowed = widget.get("allowed_origins") or []
    if origin in allowed:
        return True
    return "*" in allowed and not _multi()


def cors_origin(widget: Optional[Dict[str, Any]], origin: Optional[str], headers: Any,
                scheme: str) -> Optional[str]:
    """The origin to echo in ``Access-Control-Allow-Origin`` for a public
    request, or None to send no CORS header at all. A hub origin passes here
    (the preflight cannot carry the preview ticket); the request itself is
    still refused without one."""
    if not widget or not origin:
        return None
    if origin_allowed(widget, origin) or is_hub_origin(origin, headers, scheme):
        return origin
    return None


def check_public(widget_id: str, *, key: Optional[str], origin: Optional[str],
                 preview_ticket: Optional[str], headers: Any, scheme: str,
                 ip: Optional[str]) -> Tuple[Dict[str, Any], bool]:
    """Steps 1 to 3 of the module docstring, plus the per-address request
    limit. Returns ``(widget, is_preview)``; raises :class:`WidgetError`."""
    allowed, retry_after = ip_request_window.check(f"ip:{ip or 'unknown'}",
                                                   IP_REQUESTS_PER_MINUTE, 60.0)
    if not allowed:
        raise WidgetError(429, "rate_limited", "Too many requests from this address",
                          retry_after=retry_after)
    widget = store.get_widget(widget_id)
    # One answer for "no such widget" and "wrong key": which of the two it
    # was is not something an unauthenticated caller has earned.
    if widget is None or not key or not hmac.compare_digest(
            str(key).encode("utf-8"), str(widget["public_key"]).encode("utf-8")):
        raise WidgetError(401, "bad_key", "Unknown widget or wrong publishable key")
    from .visitor import verify_preview
    preview = verify_preview(preview_ticket, widget["widget_id"]) if preview_ticket else False
    if preview:
        if origin and not (origin_allowed(widget, origin) or is_hub_origin(origin, headers, scheme)):
            raise WidgetError(403, "origin_not_allowed", "This origin may not embed the widget")
    else:
        if not widget["enabled"]:
            raise WidgetError(403, "widget_disabled", "This widget is switched off")
        if not origin_allowed(widget, origin):
            raise WidgetError(403, "origin_not_allowed", "This origin may not embed the widget")
    return widget, preview


def check_visitor(widget: Dict[str, Any], token: Optional[str]) -> str:
    from .visitor import verify_visitor
    visitor_id = verify_visitor(token, widget["widget_id"])
    if not visitor_id:
        raise WidgetError(401, "visitor_token_invalid", "The visitor token is missing or expired")
    return visitor_id


def check_mint(ip: Optional[str]) -> None:
    allowed, retry_after = ip_visitor_window.check(f"ip:{ip or 'unknown'}",
                                                   IP_VISITORS_PER_MINUTE, 60.0)
    if not allowed:
        raise WidgetError(429, "rate_limited", "Too many new visitors from this address",
                          retry_after=retry_after)


def check_message_limits(widget: Dict[str, Any], visitor_id: str, ip: Optional[str]) -> None:
    """Per visitor, per address, then the widget's day. Only a message counts
    against the minute windows: reading threads does not."""
    limits = widget.get("limits") or {}
    per_minute = int(limits.get("messages_per_minute") or 0)
    wid = widget["widget_id"]
    allowed, retry_after = visitor_window.check(f"{wid}:{visitor_id}", per_minute, 60.0)
    if not allowed:
        raise WidgetError(429, "rate_limited", "Too many messages; wait a moment",
                          retry_after=retry_after)
    allowed, retry_after = ip_message_window.check(
        f"{wid}:{ip or 'unknown'}", per_minute * IP_MESSAGE_FACTOR, 60.0)
    if not allowed:
        raise WidgetError(429, "rate_limited", "Too many messages from this address",
                          retry_after=retry_after)
    cap = int(limits.get("tokens_per_day") or 0)
    if cap > 0 and tokens_today(wid) >= cap:
        from common.rate_limit import seconds_until_utc_midnight
        raise WidgetError(429, "daily_limit", "This widget has used its tokens for today",
                          retry_after=seconds_until_utc_midnight())


def check_owner(widget: Dict[str, Any]) -> None:
    if not owner_can_run(widget.get("owner_id") or "", widget["workspace"]):
        raise WidgetError(403, "widget_unavailable",
                          "The widget's owner can no longer run agents in its workspace")


def visitor_config(widget: Dict[str, Any]) -> Dict[str, Any]:
    """What the script needs to draw itself. Nothing about the workspace,
    the owner, the key or the origins."""
    limits = widget.get("limits") or {}
    return {
        "widget_id": widget["widget_id"],
        "title": widget.get("title") or widget.get("name") or "",
        "greeting": widget.get("greeting") or "",
        "placeholder": widget.get("placeholder") or "",
        "accent": widget.get("accent") or "navy",
        "language": widget.get("language") or "auto",
        "agent_name": agent_name(widget.get("agent_id")),
        "limits": {
            "attachment_max_bytes": int(limits.get("attachment_max_bytes") or 0),
            "max_attachments": int(limits.get("max_attachments") or 0),
            "max_message_chars": MAX_MESSAGE_CHARS,
        },
    }


# ── threads, as the visitor sees them ────────────────────────────────────────

def visitor_thread(thread: Dict[str, Any]) -> Dict[str, Any]:
    return {"thread_id": thread["thread_id"], "title": thread.get("title") or "",
            "agent_name": agent_name(thread.get("agent_id")),
            "created_at": thread["created_at"], "updated_at": thread["updated_at"]}


def visitor_message(message: Dict[str, Any]) -> Dict[str, Any]:
    out = {"message_id": message["message_id"],
           "role": "user" if message["role"] == "user" else "assistant",
           "text": message.get("text") or "", "attachments": list(message.get("attachments") or []),
           "citations": list(message.get("citations") or []),
           "status": message.get("status") or "ok", "created_at": message["created_at"]}
    handoff = message.get("handoff")
    if isinstance(handoff, dict):
        out["handoff"] = {"to_agent_name": handoff.get("to_agent_name") or ""}
    return out


def own_thread(widget: Dict[str, Any], visitor_id: str, thread_id: str) -> Dict[str, Any]:
    """The thread when it belongs to this visitor of this widget. Anything
    else (another visitor's, another widget's, one the visitor deleted) is
    the same 404: a thread id is not proof of anything."""
    thread = store.get_thread(thread_id)
    if (thread is None or thread["widget_id"] != widget["widget_id"]
            or thread["visitor_id"] != visitor_id or thread.get("visitor_deleted_at")):
        raise WidgetError(404, "thread_not_found", "No such thread")
    return thread


def create_visitor_thread(widget: Dict[str, Any], visitor_id: str, *, title: str = "",
                          preview: bool = False) -> Dict[str, Any]:
    if store.count_visitor_threads(widget["widget_id"], visitor_id) >= MAX_THREADS_PER_VISITOR:
        raise WidgetError(409, "too_many_threads",
                          f"At most {MAX_THREADS_PER_VISITOR} conversations; delete an old one")
    title = validate_text(title, field="title", limit=MAX_TITLE_CHARS) if title else ""
    return store.insert_thread(widget_id=widget["widget_id"], visitor_id=visitor_id,
                               agent_id=widget["agent_id"], title=title, preview=preview)


def answering_agent(widget: Dict[str, Any], thread: Dict[str, Any]) -> str:
    """The thread's current agent while it may still run in the widget's
    workspace; the widget's own agent otherwise (a handoff target that was
    since removed from the workspace must not keep answering)."""
    agent_id = thread.get("agent_id") or widget["agent_id"]
    if agent_id != widget["agent_id"] and not agent_usable(agent_id, widget["workspace"]):
        return widget["agent_id"]
    return agent_id


def history_for(thread_id: str) -> List[Dict[str, str]]:
    """Prior turns as ``ChatHistoryMessage`` fields: the visitor's messages
    and the replies that completed. A failed or stopped reply is left out,
    as the web chat leaves it out of what it sends back."""
    out: List[Dict[str, str]] = []
    for message in store.list_messages(thread_id, limit=HISTORY_MESSAGES):
        text = (message.get("text") or "").strip()
        if not text:
            continue
        if message["role"] == "user":
            out.append({"role": "user", "content": text})
        elif message.get("status") == "ok":
            out.append({"role": "agent", "content": text})
    return out


def claim_thread(thread_id: str) -> None:
    if thread_id in _active_threads:
        raise WidgetError(409, "busy", "This conversation is still answering")
    _active_threads.add(thread_id)


def release_thread(thread_id: str) -> None:
    _active_threads.discard(thread_id)


__all__ = [
    "HISTORY_MESSAGES", "IP_MESSAGE_FACTOR", "IP_REQUESTS_PER_MINUTE", "IP_VISITORS_PER_MINUTE",
    "KEY_PREFIX", "MAX_THREADS_PER_VISITOR", "MAX_TOTAL_ATTACHMENT_BYTES", "WidgetError", "WidgetValidationError",
    "agent_name", "answering_agent", "check_message_limits", "check_mint", "check_owner",
    "check_public", "check_visitor", "claim_thread", "cors_origin", "create_visitor_thread",
    "create_widget", "history_for", "is_hub_origin", "new_public_key", "options",
    "origin_allowed", "own_thread", "owner_can_run", "public_hub_url", "release_thread",
    "request_origin_of", "reset_limits", "rotate_key", "snippet", "to_dict", "tokens_today",
    "update_widget", "visitor_config", "visitor_message", "visitor_thread",
]
