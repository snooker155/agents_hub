"""
The Google Workspace connector: connectors/google (two auth modes, the OAuth
three-legged flow, the HTTP client) and tools/google_workspace.py (Drive,
Sheets, Docs, Calendar). No network: the OAuth HTTP calls
(connectors/google/auth.py) and the API calls (connectors/google/client.py)
are both monkeypatched, per tests/test_connector_events.py and
tests/test_telegram_allowlist.py's pattern of patching the name as bound in
the module under test, not the module it was imported from.

Run: python -m pytest tests/test_google_workspace.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.google import CREDENTIALS, STORE  # noqa: E402
from connectors.google import auth as google_auth  # noqa: E402
import connectors.google as google_connector  # noqa: E402
import tools.google_workspace as gw  # noqa: E402


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


def _configure_oauth(**extra):
    STORE.set_config({"client_id": "cid-1", "client_secret": "csecret-1", **extra})


def _configure_service_account(json_text: str = '{"type": "service_account"}'):
    STORE.set_config({"service_account_json": json_text})


def _clear_config():
    STORE.set_config({}, clear=(
        "service_account_json", "client_id", "client_secret", "refresh_token", "account_email",
        "granted_scopes",
    ))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _clear_config()
    google_auth.reset_cache()
    yield
    google_auth.reset_cache()


# ── fakes for the API client (connectors/google/client.py) ─────────────────

class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: Any = None, content: Optional[bytes] = None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        if content is not None:
            self.content = content
        else:
            self.content = json.dumps(self._json).encode("utf-8")

    def json(self):
        return self._json


class _FakeHttpx:
    """Stands in for ``httpx`` inside connectors.google.client: records every
    call and answers from a queue of canned responses, one per call, in
    order; the last one repeats for any call past the end of the queue."""

    def __init__(self, responses: Optional[list[_FakeResponse]] = None):
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def _respond(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            return _FakeResponse(200, {})
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        return self._respond("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._respond("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._respond("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._respond("PATCH", url, **kwargs)


def _install_fake_client(monkeypatch, *responses: _FakeResponse) -> _FakeHttpx:
    from connectors.google import client as google_client

    fake = _FakeHttpx(list(responses))
    monkeypatch.setattr(google_client, "httpx", fake)
    return fake


def _use_token(monkeypatch, token: str = "tok") -> None:
    """Per the task's instruction: monkeypatch the token fetch, not the HTTP
    calls behind it. connectors/google/client.py calls this through the
    ``auth`` module object (not a direct `from .auth import ...` binding),
    so patching the attribute here is visible there too."""
    monkeypatch.setattr(google_auth, "get_access_token", lambda: token)


# ── is_configured: both modes, and none ─────────────────────────────────────

def test_is_configured_is_false_with_nothing_set():
    assert google_connector.is_configured() is False
    assert google_connector.mode() == "none"
    assert CREDENTIALS.is_configured() is False


def test_is_configured_is_true_for_a_service_account():
    _configure_service_account()
    assert google_connector.is_configured() is True
    assert google_connector.mode() == "service_account"
    assert CREDENTIALS.is_configured() is True


def test_is_configured_is_true_for_a_connected_oauth_client():
    _configure_oauth(refresh_token="rt-1", account_email="person@example.com")
    assert google_connector.is_configured() is True
    assert google_connector.mode() == "oauth"


def test_is_configured_is_false_for_an_oauth_client_with_no_refresh_token_yet():
    # client id/secret alone (oauth_ready) is not "connected" yet.
    _configure_oauth()
    assert google_connector.is_configured() is False
    assert google_connector.mode() == "none"


def test_extra_reports_mode_account_email_and_oauth_ready():
    _configure_oauth(refresh_token="rt-1", account_email="person@example.com")
    extra = CREDENTIALS.extra()
    assert extra == {"mode": "oauth", "account_email": "person@example.com", "oauth_ready": True, "gmail": False}


# ── the OAuth route: start ───────────────────────────────────────────────────

def test_oauth_start_redirects_to_google_with_client_id_scopes_and_state():
    from routes import google as google_routes

    _configure_oauth()
    client = _client(google_routes.router)

    resp = client.get("/api/google/oauth/start", follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "accounts.google.com"
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["cid-1"]
    assert qs["response_type"] == ["code"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]
    assert "drive" in qs["scope"][0] and "calendar" in qs["scope"][0]
    assert qs["state"][0]


def test_oauth_start_without_a_client_configured_refuses():
    from routes import google as google_routes

    client = _client(google_routes.router)
    resp = client.get("/api/google/oauth/start", follow_redirects=False)
    assert resp.status_code == 400


# ── the OAuth route: callback ────────────────────────────────────────────────

def test_oauth_callback_rejects_a_bad_state():
    from routes import google as google_routes

    client = _client(google_routes.router)
    resp = client.get("/api/google/oauth/callback", params={"code": "abc", "state": "not-a-real-state"})
    assert resp.status_code == 400


def test_oauth_callback_stores_the_refresh_token_and_account_email(monkeypatch):
    from routes import google as google_routes

    _configure_oauth()
    state = google_auth.new_state()

    monkeypatch.setattr(google_routes, "exchange_code",
                        lambda code, redirect_uri: {"access_token": "at-1", "refresh_token": "rt-xyz"})
    monkeypatch.setattr(google_routes, "fetch_userinfo", lambda token: {"email": "person@example.com"})
    calls: list[str] = []
    monkeypatch.setattr(google_routes, "notify_change", lambda resource, **meta: calls.append(resource))

    client = _client(google_routes.router)
    resp = client.get("/api/google/oauth/callback", params={"code": "abc", "state": state},
                      follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/connectors?tab=google"
    assert STORE.get("refresh_token") == "rt-xyz"
    assert STORE.get("account_email") == "person@example.com"
    assert calls == ["connector_google"]
    # the state is single use
    assert google_auth.consume_state(state) is False


def test_oauth_callback_without_a_refresh_token_in_the_response_fails(monkeypatch):
    from routes import google as google_routes

    _configure_oauth()
    state = google_auth.new_state()
    monkeypatch.setattr(google_routes, "exchange_code", lambda code, redirect_uri: {"access_token": "at-1"})

    client = _client(google_routes.router)
    resp = client.get("/api/google/oauth/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 400


# ── the OAuth route: disconnect ──────────────────────────────────────────────

def test_oauth_disconnect_clears_the_refresh_token(monkeypatch):
    from routes import google as google_routes

    _configure_oauth(refresh_token="rt-old", account_email="old@example.com")
    calls: list[str] = []
    monkeypatch.setattr(google_routes, "notify_change", lambda resource, **meta: calls.append(resource))

    client = _client(google_routes.router)
    resp = client.post("/api/google/oauth/disconnect")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert STORE.get("refresh_token") == ""
    assert STORE.get("account_email") == ""
    assert calls == ["connector_google"]


# ── tools: unconfigured ──────────────────────────────────────────────────────

def test_tools_refuse_when_google_is_not_configured():
    result = json.loads(gw.google_drive_search.invoke({"query": "roadmap"}))
    assert result == {
        "ok": False,
        "error": "Google connector is not configured on the Connectors page",
        "code": "not_configured",
    }


# ── google_drive_search ──────────────────────────────────────────────────────

def test_drive_search_builds_the_expected_query(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {"files": [{"id": "f1", "name": "Roadmap"}]}))

    result = json.loads(gw.google_drive_search.invoke({"query": "Q3 plan", "folder_id": "folder-9"}))

    assert result["ok"] is True
    assert result["files"] == [{"id": "f1", "name": "Roadmap"}]
    call = fake.calls[0]
    assert call["url"] == "https://www.googleapis.com/drive/v3/files"
    assert call["params"]["q"] == "fullText contains 'Q3 plan' and trashed = false and 'folder-9' in parents"
    assert call["params"]["supportsAllDrives"] == "true"


def test_drive_search_escapes_quotes_in_the_query(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {"files": []}))

    gw.google_drive_search.invoke({"query": "O'Brien's notes"})

    assert "O\\'Brien\\'s notes" in fake.calls[0]["params"]["q"]


# ── google_drive_import ──────────────────────────────────────────────────────

def test_drive_import_exports_a_doc_as_markdown_and_writes_a_workspace_file(monkeypatch):
    from workspace import create_workspace_folder

    _configure_service_account()
    _use_token(monkeypatch)
    monkeypatch.setattr(gw, "resolve_active_workspace", lambda preferred=None: "acme")
    create_workspace_folder("acme")

    meta = _FakeResponse(200, {
        "id": "doc-1", "name": "Project Plan",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc-1/view",
    })
    export = _FakeResponse(200, content=b"# Project Plan\n\nHello team")
    fake = _install_fake_client(monkeypatch, meta, export)

    result = json.loads(gw.google_drive_import.invoke({"file_id": "doc-1"}))

    assert result["ok"] is True
    assert result["name"] == "Project Plan.md"
    assert result["mime_type"] == "text/markdown"
    assert result["workspace"] == "acme"
    assert fake.calls[1]["params"]["mimeType"] == "text/markdown"

    from files import service as files_service
    record = files_service.get_file(result["file_id"])
    assert record is not None
    assert record["source"] == "google_drive"
    assert record["meta"]["google_file_id"] == "doc-1"
    assert files_service.read_bytes(record["file_id"]) == b"# Project Plan\n\nHello team"


def test_drive_import_falls_back_to_plain_text_when_markdown_export_is_refused(monkeypatch):
    from workspace import create_workspace_folder

    _configure_service_account()
    _use_token(monkeypatch)
    monkeypatch.setattr(gw, "resolve_active_workspace", lambda preferred=None: "acme")
    create_workspace_folder("acme")

    meta = _FakeResponse(200, {
        "id": "doc-2", "name": "Old Doc",
        "mimeType": "application/vnd.google-apps.document",
    })
    refused = _FakeResponse(400, {"error": {"message": "export format not available"}})
    plain = _FakeResponse(200, content=b"Old Doc plain text")
    fake = _install_fake_client(monkeypatch, meta, refused, plain)

    result = json.loads(gw.google_drive_import.invoke({"file_id": "doc-2"}))

    assert result["ok"] is True
    assert result["name"] == "Old Doc.txt"
    assert result["mime_type"] == "text/plain"
    assert fake.calls[1]["params"]["mimeType"] == "text/markdown"
    assert fake.calls[2]["params"]["mimeType"] == "text/plain"


def test_drive_import_downloads_a_non_native_file_as_is(monkeypatch):
    from workspace import create_workspace_folder

    _configure_service_account()
    _use_token(monkeypatch)
    monkeypatch.setattr(gw, "resolve_active_workspace", lambda preferred=None: "acme")
    create_workspace_folder("acme")

    meta = _FakeResponse(200, {"id": "pdf-1", "name": "report.pdf", "mimeType": "application/pdf"})
    media = _FakeResponse(200, content=b"%PDF-1.4 fake")
    fake = _install_fake_client(monkeypatch, meta, media)

    result = json.loads(gw.google_drive_import.invoke({"file_id": "pdf-1"}))

    assert result["ok"] is True
    assert result["name"] == "report.pdf"
    assert result["mime_type"] == "application/pdf"
    assert fake.calls[1]["params"]["alt"] == "media"


def test_drive_import_without_an_active_workspace_refuses(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    monkeypatch.setattr(gw, "resolve_active_workspace", lambda preferred=None: None)

    result = json.loads(gw.google_drive_import.invoke({"file_id": "doc-1"}))
    assert result["ok"] is False
    assert result["code"] == "no_workspace"


# ── google_sheets_read / append ──────────────────────────────────────────────

def test_sheets_read_requests_the_range_and_wraps_the_table(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {
        "range": "Sheet1!A1:B2", "values": [["name", "qty"], ["widgets", "12"]],
    }))

    result = json.loads(gw.google_sheets_read.invoke({"spreadsheet_id": "sheet-1", "range": "Sheet1!A1:B2"}))

    assert result["ok"] is True
    assert result["rows"] == [["name", "qty"], ["widgets", "12"]]
    assert "widgets\t12" in result["text"]
    assert "UNTRUSTED" in result["text"]
    call = fake.calls[0]
    assert call["url"] == "https://sheets.googleapis.com/v4/spreadsheets/sheet-1/values/Sheet1%21A1%3AB2"


def test_sheets_read_caps_at_a_thousand_rows(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    many_rows = [[str(i)] for i in range(1500)]
    _install_fake_client(monkeypatch, _FakeResponse(200, {"values": many_rows}))

    result = json.loads(gw.google_sheets_read.invoke({"spreadsheet_id": "sheet-1"}))
    assert len(result["rows"]) == 1000


def test_sheets_append_posts_with_user_entered_value_option(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {
        "updates": {"updatedRange": "Sheet1!A3:B3", "updatedRows": 1},
    }))

    result = json.loads(gw.google_sheets_append.invoke({
        "spreadsheet_id": "sheet-1", "range": "Sheet1", "rows": [["widgets", "5"]],
    }))

    assert result["ok"] is True
    assert result["updated_range"] == "Sheet1!A3:B3"
    assert result["updated_rows"] == 1
    call = fake.calls[0]
    assert call["url"] == "https://sheets.googleapis.com/v4/spreadsheets/sheet-1/values/Sheet1:append"
    assert call["params"]["valueInputOption"] == "USER_ENTERED"
    assert call["json"] == {"values": [["widgets", "5"]]}


# ── google_docs_create ───────────────────────────────────────────────────────

def test_docs_create_runs_create_then_batch_update(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(
        monkeypatch,
        _FakeResponse(200, {"documentId": "doc-9"}),
        _FakeResponse(200, {"replies": [{}]}),
    )

    result = json.loads(gw.google_docs_create.invoke({"title": "Weekly Notes", "body": "Hello team"}))

    assert result["ok"] is True
    assert result["document_id"] == "doc-9"
    assert result["url"] == "https://docs.google.com/document/d/doc-9/edit"
    assert len(fake.calls) == 2
    assert fake.calls[0]["url"] == "https://docs.googleapis.com/v1/documents"
    assert fake.calls[0]["json"] == {"title": "Weekly Notes"}
    assert fake.calls[1]["url"] == "https://docs.googleapis.com/v1/documents/doc-9:batchUpdate"
    assert fake.calls[1]["json"] == {
        "requests": [{"insertText": {"location": {"index": 1}, "text": "Hello team"}}],
    }


def test_docs_create_moves_the_file_when_a_folder_is_given(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(
        monkeypatch,
        _FakeResponse(200, {"documentId": "doc-10"}),
        _FakeResponse(200, {}),
        _FakeResponse(200, {"id": "doc-10", "parents": ["folder-1"]}),
    )

    result = json.loads(gw.google_docs_create.invoke({
        "title": "Filed Doc", "body": "", "folder_id": "folder-1",
    }))

    assert result["ok"] is True
    assert len(fake.calls) == 2  # no body -> no batchUpdate call, plus the move
    assert fake.calls[1]["method"] == "PATCH"
    assert fake.calls[1]["params"]["addParents"] == "folder-1"


# ── google_calendar_list / create ────────────────────────────────────────────

def test_calendar_list_defaults_to_a_seven_day_window(monkeypatch):
    from datetime import datetime, timezone

    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {"items": []}))

    json.loads(gw.google_calendar_list.invoke({}))

    call = fake.calls[0]
    assert call["url"] == "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    assert call["params"]["singleEvents"] == "true"
    assert call["params"]["orderBy"] == "startTime"
    time_min = datetime.fromisoformat(call["params"]["timeMin"].replace("Z", "+00:00"))
    time_max = datetime.fromisoformat(call["params"]["timeMax"].replace("Z", "+00:00"))
    assert (time_max - time_min).days == 7
    assert abs((time_min - datetime.now(timezone.utc)).total_seconds()) < 60


def test_calendar_list_wraps_the_description_and_collects_attendees(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    _install_fake_client(monkeypatch, _FakeResponse(200, {"items": [{
        "id": "evt-1", "summary": "Sync", "description": "Bring the slides",
        "start": {"dateTime": "2026-10-05T10:00:00Z"}, "end": {"dateTime": "2026-10-05T10:30:00Z"},
        "organizer": {"email": "lead@example.com"},
        "attendees": [{"email": "a@example.com"}, {"email": "b@example.com"}],
        "htmlLink": "https://calendar.google.com/x",
    }]}))

    result = json.loads(gw.google_calendar_list.invoke({}))
    event = result["events"][0]
    assert event["attendees"] == ["a@example.com", "b@example.com"]
    assert event["organizer"] == "lead@example.com"
    assert "Bring the slides" in event["description"]
    assert "UNTRUSTED" in event["description"]


def test_calendar_create_posts_the_expected_body(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    fake = _install_fake_client(monkeypatch, _FakeResponse(200, {
        "id": "evt-9", "htmlLink": "https://calendar.google.com/evt-9",
    }))

    result = json.loads(gw.google_calendar_create.invoke({
        "summary": "Kickoff", "start": "2026-10-05T10:00:00", "end": "2026-10-05T10:30:00",
        "attendees": ["a@example.com"], "description": "Agenda attached", "location": "Room 2",
        "timezone": "Europe/Berlin",
    }))

    assert result["ok"] is True
    assert result["event_id"] == "evt-9"
    assert result["html_link"] == "https://calendar.google.com/evt-9"
    call = fake.calls[0]
    assert call["url"] == "https://www.googleapis.com/calendar/v3/calendars/primary/events"
    assert call["json"] == {
        "summary": "Kickoff",
        "start": {"dateTime": "2026-10-05T10:00:00", "timeZone": "Europe/Berlin"},
        "end": {"dateTime": "2026-10-05T10:30:00", "timeZone": "Europe/Berlin"},
        "description": "Agenda attached",
        "location": "Room 2",
        "attendees": [{"email": "a@example.com"}],
    }


# ── GoogleClient error mapping ───────────────────────────────────────────────

def test_client_maps_401_to_a_ui_safe_error(monkeypatch):
    _configure_service_account()
    _use_token(monkeypatch)
    _install_fake_client(monkeypatch, _FakeResponse(401, {}))

    result = json.loads(gw.google_drive_search.invoke({"query": "x"}))
    assert result["ok"] is False
    assert "reconnect" in result["error"].lower() or "expired" in result["error"].lower()
