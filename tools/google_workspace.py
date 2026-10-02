"""
Google Workspace tools: Drive search and import, Sheets read/append, Docs
creation, Calendar read/write — over the credentials configured on the
Connectors page (``connectors/google``). ``connectors/google/client.py``
does the HTTP work and maps Google's errors to :class:`GoogleError`; this
module turns each call into the tool shape the rest of ``tools/`` uses
(``tools/git_publish.py``, ``tools/channel_send.py``): a ``@tool`` function
with a Pydantic ``args_schema``, returning ``json_ok``/``json_err``.

``google_drive_import`` is the one tool that touches workspace state: it
downloads or exports a Drive file into the active workspace's files
(``files/service.py``), the same registry ``tools/workspace_files.py`` reads
from, so an imported Doc or Sheet shows on the Files page and can be
attached, read and cited like anything uploaded by hand.

Spreadsheet cells and event descriptions are third-party text an agent did
not write, so both pass through ``tools/web.py``'s ``wrap_untrusted`` before
coming back, same as a fetched web page or a tracker issue body.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from common.workspace_context import resolve_active_workspace
from tools._json import json_err, json_ok
from tools.web import wrap_untrusted

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
_DOCS_API = "https://docs.googleapis.com/v1/documents"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3/calendars"

_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
_GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"

_MAX_SHEET_ROWS = 1000
_NOT_CONFIGURED = "Google connector is not configured on the Connectors page"


def _unconfigured() -> Optional[str]:
    """``json_err(...)`` when Google is not configured, else None."""
    from connectors.google import is_configured
    if not is_configured():
        return json_err(_NOT_CONFIGURED, code="not_configured")
    return None


def _client():
    from connectors.google.client import GoogleClient
    return GoogleClient()


def _escape_drive_literal(value: str) -> str:
    """Escape a string for Drive's ``q`` syntax: backslash, then quote."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ── google_drive_search ──────────────────────────────────────────────────────

class GoogleDriveSearchInput(BaseModel):
    query: str = Field(..., min_length=1, description="Text to search for across file contents")
    max_results: int = Field(default=20, description="How many files to return, at most 100")
    folder_id: Optional[str] = Field(default=None, description="Restrict the search to this Drive folder's id")


@tool("google_drive_search", args_schema=GoogleDriveSearchInput)
def google_drive_search(query: str, max_results: int = 20, folder_id: Optional[str] = None) -> str:
    """Search Drive files by content, returning name, type, link and owner for
    each match. Use google_drive_import to bring a result into this
    workspace's files.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    q = f"fullText contains '{_escape_drive_literal(query)}' and trashed = false"
    if folder_id:
        q += f" and '{_escape_drive_literal(folder_id)}' in parents"
    try:
        resp = _client().get(f"{_DRIVE_API}/files", params={
            "q": q,
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink,size,owners(emailAddress))",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": max(1, min(int(max_results or 20), 100)),
        })
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    data = resp.json()
    files = data.get("files") or []
    return json_ok({"files": files})


# ── google_drive_import ──────────────────────────────────────────────────────

class GoogleDriveImportInput(BaseModel):
    file_id: str = Field(..., min_length=1, description="The Drive file id, from google_drive_search")
    name: Optional[str] = Field(default=None, description="Name to save as; defaults to the Drive file's own name")


def _export_mime_and_suffix(google_mime: str) -> tuple[Optional[str], str]:
    """The export MIME type for a Google-native file, and the extension to
    add when the saved name does not already carry one. None export MIME
    means the file downloads as-is (``alt=media``)."""
    if google_mime == _GOOGLE_DOC_MIME:
        return "text/markdown", ".md"
    if google_mime == _GOOGLE_SHEET_MIME:
        return "text/csv", ".csv"
    if google_mime == _GOOGLE_SLIDES_MIME:
        return "text/plain", ".txt"
    return None, ""


@tool("google_drive_import", args_schema=GoogleDriveImportInput)
def google_drive_import(file_id: str, name: Optional[str] = None) -> str:
    """Download a Drive file into this workspace's files. A Google Doc comes
    in as markdown, a Sheet as CSV, a Slides deck as plain text; anything
    else downloads as-is. The result shows on the Files page like an upload.
    """
    err = _unconfigured()
    if err:
        return err
    workspace = resolve_active_workspace()
    if not workspace:
        return json_err("No workspace is active for this run.", code="no_workspace")

    from connectors.google.auth import GoogleError
    from files import service as files_service

    client = _client()
    try:
        meta_resp = client.get(f"{_DRIVE_API}/files/{quote(file_id, safe='')}", params={
            "fields": "id,name,mimeType,size,webViewLink",
            "supportsAllDrives": "true",
        })
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    meta = meta_resp.json()
    google_mime = str(meta.get("mimeType") or "")
    export_mime, suffix = _export_mime_and_suffix(google_mime)

    try:
        if export_mime:
            try:
                content_resp = client.get(
                    f"{_DRIVE_API}/files/{quote(file_id, safe='')}/export",
                    params={"mimeType": export_mime},
                )
            except GoogleError as exc:
                # A Doc that refuses markdown export falls back to plain text;
                # anything else is a real failure, not a format the caller can
                # retry its way around.
                if export_mime == "text/markdown" and "400" in str(exc):
                    export_mime = "text/plain"
                    suffix = ".txt"
                    content_resp = client.get(
                        f"{_DRIVE_API}/files/{quote(file_id, safe='')}/export",
                        params={"mimeType": export_mime},
                    )
                else:
                    raise
            mime_type = export_mime
        else:
            content_resp = client.get(
                f"{_DRIVE_API}/files/{quote(file_id, safe='')}",
                params={"alt": "media", "supportsAllDrives": "true"},
            )
            mime_type = google_mime or "application/octet-stream"
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")

    data = content_resp.content
    limit = files_service.max_file_bytes()
    if len(data) > limit:
        return json_err(
            f"'{meta.get('name')}' is {len(data)} bytes; the limit is {limit} bytes per file",
            code="too_large",
        )

    saved_name = (name or "").strip() or str(meta.get("name") or file_id)
    if suffix and not saved_name.lower().endswith(suffix):
        saved_name += suffix

    try:
        record = files_service.create_file(
            workspace, saved_name, data, mime_type=mime_type, source="google_drive",
            meta={"google_file_id": file_id, "web_view_link": meta.get("webViewLink")},
        )
    except files_service.FileError as exc:
        return json_err(str(exc), code="refused")

    return json_ok({
        "file_id": record["file_id"], "name": record["name"], "size": record["size"],
        "mime_type": record["mime_type"], "workspace": workspace,
    })


# ── google_sheets_read / append ──────────────────────────────────────────────

class GoogleSheetsReadInput(BaseModel):
    spreadsheet_id: str = Field(..., min_length=1, description="The spreadsheet's id, from its URL")
    range: str = Field(default="A1:Z200", description="A1 notation range, e.g. 'Sheet1!A1:D20'")


@tool("google_sheets_read", args_schema=GoogleSheetsReadInput)
def google_sheets_read(spreadsheet_id: str, range: str = "A1:Z200") -> str:
    """Read a range of cells from a Google Sheet as rows of values, capped at
    1000 rows. ``text`` carries the same rows as tab-separated lines, wrapped
    as untrusted data since the content is whatever is in the sheet.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    try:
        resp = _client().get(f"{_SHEETS_API}/{quote(spreadsheet_id, safe='')}/values/{quote(range, safe='')}")
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    data = resp.json()
    rows = (data.get("values") or [])[:_MAX_SHEET_ROWS]
    tsv = "\n".join("\t".join(str(cell) for cell in row) for row in rows)
    text = wrap_untrusted(f"google_sheet:{spreadsheet_id}:{range}", tsv)
    return json_ok({"range": data.get("range") or range, "rows": rows, "text": text})


class GoogleSheetsAppendInput(BaseModel):
    spreadsheet_id: str = Field(..., min_length=1, description="The spreadsheet's id, from its URL")
    range: str = Field(..., min_length=1, description="A1 notation range/sheet to append after, e.g. 'Sheet1'")
    rows: list[list[Any]] = Field(..., description="Rows of cell values to append")


@tool("google_sheets_append", args_schema=GoogleSheetsAppendInput)
def google_sheets_append(spreadsheet_id: str, range: str, rows: list[list[Any]]) -> str:
    """Append rows to the end of a Google Sheet's data in the given range.
    Values are interpreted the way typing them into Sheets would be (numbers,
    dates, formulas), not forced to plain text.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    url = f"{_SHEETS_API}/{quote(spreadsheet_id, safe='')}/values/{quote(range, safe='')}:append"
    try:
        resp = _client().post(url, params={"valueInputOption": "USER_ENTERED"}, json={"values": rows})
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    data = resp.json()
    updates = data.get("updates") or {}
    return json_ok({
        "updated_range": updates.get("updatedRange"),
        "updated_rows": updates.get("updatedRows"),
    })


# ── google_docs_create ───────────────────────────────────────────────────────

class GoogleDocsCreateInput(BaseModel):
    title: str = Field(..., min_length=1, description="The new document's title")
    body: str = Field(default="", description="Plain text to insert into the document")
    folder_id: Optional[str] = Field(default=None, description="Drive folder id to move the document into")


@tool("google_docs_create", args_schema=GoogleDocsCreateInput)
def google_docs_create(title: str, body: str, folder_id: Optional[str] = None) -> str:
    """Create a new Google Doc with the given title and body text, optionally
    filed into a Drive folder. Returns its id and a link to open it.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    client = _client()
    try:
        created = client.post(_DOCS_API, json={"title": title}).json()
        document_id = created.get("documentId")
        if not document_id:
            return json_err("Google did not return a document id", code="google_error")
        if body:
            client.post(f"{_DOCS_API}/{quote(document_id, safe='')}:batchUpdate", json={
                "requests": [{"insertText": {"location": {"index": 1}, "text": body}}],
            })
        if folder_id:
            client.patch(
                f"{_DRIVE_API}/files/{quote(document_id, safe='')}",
                params={"addParents": folder_id, "fields": "id,parents", "supportsAllDrives": "true"},
                json={},
            )
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")

    return json_ok({
        "document_id": document_id,
        "url": f"https://docs.google.com/document/d/{document_id}/edit",
    })


# ── google_calendar_list / create ────────────────────────────────────────────

def _to_rfc3339(value: Optional[str], default: datetime) -> str:
    if value:
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            dt = default
    else:
        dt = default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class GoogleCalendarListInput(BaseModel):
    calendar_id: str = Field(default="primary", description="Calendar id, or 'primary' for the account's own")
    start: Optional[str] = Field(default=None, description="ISO window start; defaults to now")
    end: Optional[str] = Field(default=None, description="ISO window end; defaults to seven days from start")
    max_results: int = Field(default=20, description="How many events to return, at most 250")


@tool("google_calendar_list", args_schema=GoogleCalendarListInput)
def google_calendar_list(calendar_id: str = "primary", start: Optional[str] = None,
                         end: Optional[str] = None, max_results: int = 20) -> str:
    """List upcoming events on a calendar, default window now through seven
    days out. Each event's description is wrapped as untrusted data: it is
    text someone else put in an invite, not something the agent wrote.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    now = datetime.now(timezone.utc)
    time_min = _to_rfc3339(start, now)
    time_max = _to_rfc3339(end, now + timedelta(days=7))
    try:
        resp = _client().get(f"{_CALENDAR_API}/{quote(calendar_id, safe='')}/events", params={
            "timeMin": time_min, "timeMax": time_max,
            "singleEvents": "true", "orderBy": "startTime",
            "maxResults": max(1, min(int(max_results or 20), 250)),
        })
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    data = resp.json()
    events = []
    for it in data.get("items") or []:
        attendees = [a.get("email") for a in (it.get("attendees") or []) if a.get("email")]
        description = str(it.get("description") or "")
        events.append({
            "id": it.get("id"),
            "summary": it.get("summary") or "",
            "start": (it.get("start") or {}).get("dateTime") or (it.get("start") or {}).get("date"),
            "end": (it.get("end") or {}).get("dateTime") or (it.get("end") or {}).get("date"),
            "location": it.get("location") or "",
            "organizer": (it.get("organizer") or {}).get("email") or "",
            "attendees": attendees,
            "html_link": it.get("htmlLink"),
            "description": wrap_untrusted(f"google_calendar_event:{it.get('id')}", description) if description else "",
        })
    return json_ok({"events": events})


class GoogleCalendarCreateInput(BaseModel):
    summary: str = Field(..., min_length=1, description="Event title")
    start: str = Field(..., min_length=1, description="ISO start date-time")
    end: str = Field(..., min_length=1, description="ISO end date-time")
    calendar_id: str = Field(default="primary", description="Calendar id, or 'primary' for the account's own")
    attendees: Optional[list[str]] = Field(default=None, description="Attendee email addresses to invite")
    description: Optional[str] = Field(default=None, description="Event description")
    location: Optional[str] = Field(default=None, description="Event location")
    timezone: str = Field(default="UTC", description="IANA timezone name for start/end")


@tool("google_calendar_create", args_schema=GoogleCalendarCreateInput)
def google_calendar_create(summary: str, start: str, end: str, calendar_id: str = "primary",
                           attendees: Optional[list[str]] = None, description: Optional[str] = None,
                           location: Optional[str] = None, timezone: str = "UTC") -> str:
    """Create a calendar event, optionally inviting attendees by email.
    Returns the new event's id and a link to it.
    """
    err = _unconfigured()
    if err:
        return err
    from connectors.google.auth import GoogleError

    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees]

    try:
        resp = _client().post(f"{_CALENDAR_API}/{quote(calendar_id, safe='')}/events", json=body)
    except GoogleError as exc:
        return json_err(str(exc), code="google_error")
    data = resp.json()
    return json_ok({"event_id": data.get("id"), "html_link": data.get("htmlLink")})


CONNECTOR_TOOLS = [
    google_drive_search, google_drive_import, google_sheets_read, google_sheets_append,
    google_docs_create, google_calendar_list, google_calendar_create,
]

__all__ = [
    "google_drive_search", "google_drive_import", "google_sheets_read", "google_sheets_append",
    "google_docs_create", "google_calendar_list", "google_calendar_create", "CONNECTOR_TOOLS",
]
