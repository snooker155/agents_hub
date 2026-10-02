"""Microsoft Graph connector (connectors/microsoft) and its calendar tools.

No network: token acquisition is monkeypatched at ``graph.acquire_token``
(the one function both the msal path and the httpx fallback funnel into),
and the HTTP call inside ``GraphClient._request`` is monkeypatched directly
so no real request ever leaves the process.

Run: ``python -m pytest tests/test_microsoft_graph.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _configure(tenant_id="tenant-1", client_id="client-1", client_secret="secret-1",
               default_user=""):
    from connectors.microsoft import CREDENTIALS
    CREDENTIALS.store.set_config({
        "tenant_id": tenant_id, "client_id": client_id, "client_secret": client_secret,
        "default_user": default_user,
    })
    return CREDENTIALS


def _fake_token(monkeypatch, token="fake-graph-token"):
    from connectors.microsoft import graph
    monkeypatch.setattr(graph, "acquire_token", lambda *a, **kw: token)
    return token


# ── GraphClient ──────────────────────────────────────────────────────────────


def test_acquire_token_falls_back_to_httpx_without_msal(monkeypatch):
    from connectors.microsoft import graph

    monkeypatch.setitem(sys.modules, "msal", None)  # import msal -> ImportError

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"access_token": "tok-via-httpx"}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return _Resp()

    monkeypatch.setattr(graph.httpx, "post", fake_post)
    token = graph.acquire_token("tenant-x", "client-x", "secret-x")
    assert token == "tok-via-httpx"
    assert captured["data"]["grant_type"] == "client_credentials"
    assert captured["data"]["scope"] == "https://graph.microsoft.com/.default"
    assert "tenant-x" in captured["url"]


def test_graph_client_get_builds_the_request_and_raises_graph_error(monkeypatch):
    from connectors.microsoft import graph

    monkeypatch.setattr(graph, "acquire_token", lambda *a, **kw: "tok-1")
    captured = {}

    class _Resp:
        status_code = 404
        content = b"{}"

        def json(self):
            return {"error": {"message": "not found"}}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(method=method, url=url, params=params, headers=headers)
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)
    client = graph.GraphClient("t", "c", "s")
    try:
        client.get("/users/x/calendarView", params={"a": "b"})
        assert False, "expected GraphError"
    except graph.GraphError as exc:
        assert "not found" in str(exc)
    assert captured["method"] == "GET"
    assert captured["headers"]["Authorization"] == "Bearer tok-1"


# ── unconfigured ─────────────────────────────────────────────────────────────


def test_outlook_calendar_list_reports_unconfigured():
    from connectors.microsoft import CREDENTIALS
    CREDENTIALS.store.set_config({"tenant_id": "", "client_id": "", "client_secret": ""},
                                 clear=["client_secret"])
    from tools.microsoft_graph import outlook_calendar_list

    out = json.loads(outlook_calendar_list.invoke({}))
    assert out["ok"] is False
    assert "not configured" in out["error"]


def test_outlook_calendar_create_reports_unconfigured():
    from connectors.microsoft import CREDENTIALS
    CREDENTIALS.store.set_config({"tenant_id": "", "client_id": "", "client_secret": ""},
                                 clear=["client_secret"])
    from tools.microsoft_graph import outlook_calendar_create

    out = json.loads(outlook_calendar_create.invoke({
        "subject": "Sync", "start": "2026-10-05T10:00:00Z", "end": "2026-10-05T10:30:00Z",
    }))
    assert out["ok"] is False
    assert "not configured" in out["error"]


# ── outlook_calendar_list ────────────────────────────────────────────────────


def test_outlook_calendar_list_builds_calendar_view_request_and_normalises_events(monkeypatch):
    _configure(default_user="anna@contoso.com")
    _fake_token(monkeypatch)

    from connectors.microsoft import graph

    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {
                "value": [
                    {
                        "id": "evt-1",
                        "subject": "Planning",
                        "start": {"dateTime": "2026-10-05T10:00:00.0000000"},
                        "end": {"dateTime": "2026-10-05T10:30:00.0000000"},
                        "location": {"displayName": "Room 4"},
                        "organizer": {"emailAddress": {"name": "Anna", "address": "anna@contoso.com"}},
                        "attendees": [
                            {"emailAddress": {"name": "Bob", "address": "bob@contoso.com"}},
                        ],
                        "webLink": "https://outlook.office.com/evt-1",
                        "bodyPreview": "Let's sync on the roadmap",
                    },
                ],
            }

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(method=method, url=url, params=params)
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)

    from tools.microsoft_graph import outlook_calendar_list

    out = json.loads(outlook_calendar_list.invoke({"max_results": 5}))
    assert out["ok"] is True
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/users/anna@contoso.com/calendarView")
    assert captured["params"]["$orderby"] == "start/dateTime"
    assert captured["params"]["$top"] == 5
    assert "startDateTime" in captured["params"] and "endDateTime" in captured["params"]

    events = out["events"]
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "evt-1"
    assert ev["subject"] == "Planning"
    assert ev["location"] == "Room 4"
    assert ev["organizer"] == "Anna"
    assert ev["attendees"] == ["Bob"]
    assert ev["web_link"] == "https://outlook.office.com/evt-1"
    assert "Let's sync on the roadmap" in ev["body_preview"]


def test_outlook_calendar_list_explicit_user_overrides_default(monkeypatch):
    _configure(default_user="anna@contoso.com")
    _fake_token(monkeypatch)
    from connectors.microsoft import graph

    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"value": []}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)

    from tools.microsoft_graph import outlook_calendar_list
    out = json.loads(outlook_calendar_list.invoke({"user": "bob@contoso.com"}))
    assert out["ok"] is True
    assert "/users/bob@contoso.com/calendarView" in captured["url"]


def test_outlook_calendar_list_without_user_or_default_fails():
    _configure(default_user="")
    from tools.microsoft_graph import outlook_calendar_list
    out = json.loads(outlook_calendar_list.invoke({}))
    assert out["ok"] is False
    assert "default_user" in out["error"]


# ── outlook_calendar_create ──────────────────────────────────────────────────


def test_outlook_calendar_create_posts_the_expected_body(monkeypatch):
    _configure(default_user="anna@contoso.com")
    _fake_token(monkeypatch)
    from connectors.microsoft import graph

    captured = {}

    class _Resp:
        status_code = 201
        content = b"{}"

        def json(self):
            return {"id": "evt-new", "webLink": "https://outlook.office.com/evt-new"}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(method=method, url=url, json=json)
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)

    from tools.microsoft_graph import outlook_calendar_create
    out = json.loads(outlook_calendar_create.invoke({
        "subject": "Roadmap sync", "start": "2026-10-05T10:00:00", "end": "2026-10-05T10:30:00",
        "attendees": ["bob@contoso.com"], "body": "Agenda attached", "location": "Room 4",
        "timezone": "Europe/Berlin",
    }))

    assert out["ok"] is True
    assert out["id"] == "evt-new"
    assert out["web_link"] == "https://outlook.office.com/evt-new"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/users/anna@contoso.com/events")
    body = captured["json"]
    assert body["subject"] == "Roadmap sync"
    assert body["start"] == {"dateTime": "2026-10-05T10:00:00", "timeZone": "Europe/Berlin"}
    assert body["end"] == {"dateTime": "2026-10-05T10:30:00", "timeZone": "Europe/Berlin"}
    assert body["location"] == {"displayName": "Room 4"}
    assert body["body"] == {"contentType": "text", "content": "Agenda attached"}
    assert body["attendees"] == [{"emailAddress": {"address": "bob@contoso.com"}, "type": "required"}]


def test_outlook_calendar_create_without_user_or_default_fails():
    _configure(default_user="")
    from tools.microsoft_graph import outlook_calendar_create
    out = json.loads(outlook_calendar_create.invoke({
        "subject": "X", "start": "2026-10-05T10:00:00", "end": "2026-10-05T10:30:00",
    }))
    assert out["ok"] is False
    assert "default_user" in out["error"]


# ── credential test() callback ───────────────────────────────────────────────


def test_credentials_test_with_default_user(monkeypatch):
    _configure(default_user="anna@contoso.com")
    _fake_token(monkeypatch)
    from connectors.microsoft import graph

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"displayName": "Anna Admin", "userPrincipalName": "anna@contoso.com"}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)

    from connectors.microsoft import CREDENTIALS
    result = CREDENTIALS.test()
    assert result["ok"] is True
    assert result["identity"] == "Anna Admin"


def test_credentials_test_without_default_user_checks_organization(monkeypatch):
    _configure(default_user="")
    _fake_token(monkeypatch)
    from connectors.microsoft import graph

    captured = {}

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"value": [{"displayName": "Contoso Ltd"}]}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(graph.httpx, "request", fake_request)

    from connectors.microsoft import CREDENTIALS
    result = CREDENTIALS.test()
    assert result["ok"] is True
    assert result["identity"] == "Contoso Ltd"
    assert captured["url"].endswith("/organization")


def test_credentials_test_reports_missing_fields():
    from connectors.microsoft import CREDENTIALS
    CREDENTIALS.store.set_config({"tenant_id": "", "client_id": "", "client_secret": ""},
                                 clear=["client_secret"])
    result = CREDENTIALS.test()
    assert result["ok"] is False
