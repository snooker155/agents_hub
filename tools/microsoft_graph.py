"""
tools/microsoft_graph.py — the Outlook calendar over Microsoft Graph.

The two tool ids pre-assigned to this connector in tools/capabilities.py:
``outlook_calendar_list`` (reads a mailbox's calendar, so it carries
INGESTS_UNTRUSTED + READS_PRIVATE — the events were written by whoever
scheduled them, not the agent) and ``outlook_calendar_create``
(CAN_EXFILTRATE — creating an event sends agent-written text, and an invite,
out to the attendees). Both act on connectors/microsoft's configured
client-credentials app; the mailbox is whichever ``user`` the call names, or
the connector's ``default_user`` when it names none.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from tools.web import wrap_untrusted

_NOT_CONFIGURED = "Microsoft connector is not configured on the Connectors page"


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _client() -> Tuple[Optional[Any], str]:
    """A configured ``GraphClient`` and the connector's default_user.

    Returns ``(None, "")`` when the connector is not configured.
    """
    from connectors.microsoft import CREDENTIALS
    if not CREDENTIALS.is_configured():
        return None, ""
    from connectors.microsoft.graph import GraphClient
    store = CREDENTIALS.store
    client = GraphClient(store.get("tenant_id"), store.get("client_id"), store.get("client_secret"))
    return client, str(store.get("default_user") or "")


def _resolve_user(user: Optional[str], default_user: str) -> str:
    return (user or "").strip() or default_user.strip()


def _attendee_names(attendees: Any) -> List[str]:
    out = []
    for a in attendees or []:
        email = (a or {}).get("emailAddress") or {}
        out.append(email.get("name") or email.get("address") or "")
    return [n for n in out if n]


def _normalise_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    organizer = (ev.get("organizer") or {}).get("emailAddress") or {}
    preview = ev.get("bodyPreview") or ""
    return {
        "id": ev.get("id"),
        "subject": ev.get("subject"),
        "start": (ev.get("start") or {}).get("dateTime"),
        "end": (ev.get("end") or {}).get("dateTime"),
        "location": (ev.get("location") or {}).get("displayName"),
        "organizer": organizer.get("name") or organizer.get("address"),
        "attendees": _attendee_names(ev.get("attendees")),
        "web_link": ev.get("webLink"),
        "body_preview": wrap_untrusted("outlook_calendar", preview) if preview else "",
    }


class OutlookCalendarListInput(BaseModel):
    user: Optional[str] = Field(None, description="Mailbox UPN or id; defaults to the connector's default_user")
    start: Optional[str] = Field(None, description="ISO 8601 start of the window; defaults to now")
    end: Optional[str] = Field(None, description="ISO 8601 end of the window; defaults to start + 7 days")
    max_results: int = Field(20, ge=1, le=100, description="Maximum number of events to return")


@tool("outlook_calendar_list", args_schema=OutlookCalendarListInput)
def outlook_calendar_list(user: Optional[str] = None, start: Optional[str] = None,
                          end: Optional[str] = None, max_results: int = 20) -> str:
    """List events on an Outlook calendar over Microsoft Graph.

    Defaults to the next 7 days on the connector's default_user mailbox when
    neither a user nor a window is given. Event bodies come from whoever
    scheduled them, not the agent, and are wrapped as untrusted content.
    """
    client, default_user = _client()
    if client is None:
        return _err(_NOT_CONFIGURED)
    mailbox = _resolve_user(user, default_user)
    if not mailbox:
        return _err("No user given and no default_user configured on the Microsoft connector")

    now = datetime.now(timezone.utc)
    start_dt = start or now.isoformat()
    end_dt = end or (now + timedelta(days=7)).isoformat()

    from connectors.microsoft.graph import GraphError
    try:
        body = client.get(
            f"/users/{mailbox}/calendarView",
            params={
                "startDateTime": start_dt, "endDateTime": end_dt,
                "$orderby": "start/dateTime", "$top": max_results,
            },
        )
    except GraphError as exc:
        return _err(str(exc))
    events = [_normalise_event(e) for e in (body.get("value") or [])]
    return _ok({"events": events, "user": mailbox})


class OutlookCalendarCreateInput(BaseModel):
    subject: str = Field(..., min_length=1, description="Event title")
    start: str = Field(..., description="ISO 8601 start time")
    end: str = Field(..., description="ISO 8601 end time")
    user: Optional[str] = Field(None, description="Mailbox UPN or id; defaults to the connector's default_user")
    attendees: Optional[List[str]] = Field(None, description="Attendee email addresses")
    body: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Location display name")
    timezone: str = Field("UTC", description="Time zone name for the start and end times")


@tool("outlook_calendar_create", args_schema=OutlookCalendarCreateInput)
def outlook_calendar_create(subject: str, start: str, end: str, user: Optional[str] = None,
                            attendees: Optional[List[str]] = None, body: Optional[str] = None,
                            location: Optional[str] = None, timezone: str = "UTC") -> str:
    """Create an event on an Outlook calendar over Microsoft Graph.

    Defaults to the connector's default_user mailbox when user is not given.
    Attendees are invited by email, which sends an invite out, so this tool
    is classified CAN_EXFILTRATE.
    """
    client, default_user = _client()
    if client is None:
        return _err(_NOT_CONFIGURED)
    mailbox = _resolve_user(user, default_user)
    if not mailbox:
        return _err("No user given and no default_user configured on the Microsoft connector")

    payload: Dict[str, Any] = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    if location:
        payload["location"] = {"displayName": location}
    if body:
        payload["body"] = {"contentType": "text", "content": body}
    if attendees:
        payload["attendees"] = [{"emailAddress": {"address": a}, "type": "required"} for a in attendees]

    from connectors.microsoft.graph import GraphError
    try:
        created = client.post(f"/users/{mailbox}/events", json=payload)
    except GraphError as exc:
        return _err(str(exc))
    return _ok({"id": created.get("id"), "web_link": created.get("webLink"), "user": mailbox})


CONNECTOR_TOOLS = [outlook_calendar_list, outlook_calendar_create]

__all__ = ["outlook_calendar_list", "outlook_calendar_create", "CONNECTOR_TOOLS"]
