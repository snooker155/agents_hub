"""
Tests for connectors/trackers (Jira, Linear providers + sync), tools/trackers.py
and dashboard/backend/routes/trackers.py.

No network: httpx.get/httpx.post are monkeypatched per test, the same
approach tests/test_git_publish.py uses for GitHub/GitLab. The sync test
exercises the real tasks.service against the test database (conftest.py
already points AGENTS_HUB_ROOT at a scratch dir), with a project created
through projects.storage.ProjectStore and a workspace folder under
WORKSPACES_ROOT — the same setup tests/test_git_publish.py's _make_project
uses for git issue sync.

Run: python -m pytest tests/test_trackers.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from connectors.trackers.providers import JiraProvider, LinearProvider


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _jira_provider() -> JiraProvider:
    return JiraProvider("https://acme.atlassian.net", "me@acme.com", "token-123")


# ── Jira: issue normalization ────────────────────────────────────────────

_JIRA_ADF_DESCRIPTION = {
    "type": "doc", "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "First line."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Second "},
                                           {"type": "text", "text": "line."}]},
    ],
}

_JIRA_SEARCH_RESPONSE = {
    "issues": [
        {
            "key": "PROJ-12",
            "fields": {
                "summary": "Fix the thing",
                "description": _JIRA_ADF_DESCRIPTION,
                "status": {"name": "Done", "statusCategory": {"key": "done"}},
                "labels": ["bug"],
                "updated": "2026-01-01T00:00:00.000+0000",
                "creator": {"displayName": "Alice"},
                "assignee": {"displayName": "Bob"},
                "priority": {"name": "High"},
            },
        },
        {
            "key": "PROJ-13",
            "fields": {
                "summary": "Still open",
                "description": None,
                "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                "labels": [],
                "updated": "2026-01-02T00:00:00.000+0000",
                "creator": {"displayName": "Alice"},
                "assignee": None,
                "priority": None,
            },
        },
    ]
}


def test_jira_list_issues_normalizes_adf_and_done_category(monkeypatch):
    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, params=None, timeout=None: _FakeResponse(_JIRA_SEARCH_RESPONSE),
    )
    provider = _jira_provider()
    issues = provider.list_issues("PROJ", state="all", limit=50)

    assert len(issues) == 2
    closed = next(i for i in issues if i["key"] == "PROJ-12")
    assert closed["state"] == "closed"
    assert closed["status"] == "Done"
    assert closed["body"] == "First line.\nSecond line."
    assert closed["labels"] == ["bug"]
    assert closed["assignee"] == "Bob"
    assert closed["priority"] == "High"

    open_issue = next(i for i in issues if i["key"] == "PROJ-13")
    assert open_issue["state"] == "open"
    assert open_issue["body"] == ""


def test_jira_list_issues_falls_back_to_search_on_404(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(url)
        if url.endswith("/search/jql"):
            return _FakeResponse({"errorMessages": ["gone"]}, status_code=404)
        return _FakeResponse(_JIRA_SEARCH_RESPONSE)

    monkeypatch.setattr("httpx.get", fake_get)
    provider = _jira_provider()
    issues = provider.list_issues("PROJ")
    assert len(issues) == 2
    assert any(u.endswith("/search/jql") for u in calls)
    assert any(u.endswith("/search") for u in calls)


def test_jira_test_connection_returns_the_display_name(monkeypatch):
    monkeypatch.setattr(
        "httpx.get",
        lambda url, headers=None, params=None, timeout=None: _FakeResponse({"displayName": "Alice Example"}),
    )
    provider = _jira_provider()
    result = provider.test_connection()
    assert result == {"ok": True, "identity": "Alice Example", "error": None}


# ── Linear: issue normalization ──────────────────────────────────────────

_LINEAR_ISSUES_RESPONSE = {
    "issues": {
        "nodes": [
            {
                "id": "abc-1", "identifier": "ENG-123", "title": "Fix bug",
                "description": "Steps to repro", "state": {"name": "Done", "type": "completed"},
                "labels": {"nodes": [{"name": "bug"}]},
                "url": "https://linear.app/acme/issue/ENG-123",
                "updatedAt": "2026-01-01T00:00:00Z",
                "creator": {"name": "Alice"}, "assignee": {"name": "Bob"},
                "priority": 2,
            },
            {
                "id": "abc-2", "identifier": "ENG-124", "title": "Still open",
                "description": None, "state": {"name": "In Progress", "type": "started"},
                "labels": {"nodes": []},
                "url": "https://linear.app/acme/issue/ENG-124",
                "updatedAt": "2026-01-02T00:00:00Z",
                "creator": {"name": "Alice"}, "assignee": None,
                "priority": 0,
            },
        ]
    }
}


def test_linear_list_issues_normalizes_state(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda url, headers=None, json=None, timeout=None: _FakeResponse({"data": _LINEAR_ISSUES_RESPONSE}),
    )
    provider = LinearProvider("key-123")
    issues = provider.list_issues("ENG")

    assert len(issues) == 2
    closed = next(i for i in issues if i["key"] == "ENG-123")
    assert closed["state"] == "closed"
    assert closed["labels"] == ["bug"]
    assert closed["assignee"] == "Bob"

    open_issue = next(i for i in issues if i["key"] == "ENG-124")
    assert open_issue["state"] == "open"
    assert open_issue["body"] == ""


def test_linear_test_connection_returns_the_viewer_name(monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda url, headers=None, json=None, timeout=None:
            _FakeResponse({"data": {"viewer": {"id": "u1", "name": "Alice", "email": "a@acme.com"}}}),
    )
    provider = LinearProvider("key-123")
    result = provider.test_connection()
    assert result == {"ok": True, "identity": "Alice", "error": None}


# ── sync_tracker_issues: import then update, idempotently ───────────────

def _make_project_with_tracker(provider="jira", remote_id="PROJ"):
    from common.paths import PROJECTS_FILE
    from projects.models import Project, TrackerConfig
    from projects.storage import ProjectStore
    from workspace.storage import WORKSPACES_ROOT

    tag = uuid4().hex[:8]
    ws_name = f"trackers_ws_{tag}"
    ws_dir = WORKSPACES_ROOT / ws_name
    ws_dir.mkdir(parents=True)

    project = Project(
        name=f"trackers-project-{tag}",
        type="code",
        workspace=ws_name,
        tracker=TrackerConfig(provider=provider, remote_id=remote_id),
    )
    ProjectStore(path=PROJECTS_FILE).add(project)
    return project


class _FakeTrackerProvider:
    def __init__(self, issues):
        self.issues = issues

    def list_issues(self, remote_id, state="all", limit=200):
        return list(self.issues)


def test_sync_tracker_issues_imports_then_updates_idempotently(monkeypatch):
    import connectors.trackers.sync as sync_mod
    from tasks import service as tasks_service

    project = _make_project_with_tracker()

    issue_open = {
        "key": "PROJ-1", "number": "PROJ-1", "title": "Do the thing",
        "body": "Please do it", "state": "open", "status": "To Do",
        "labels": ["bug"], "url": "https://acme.atlassian.net/browse/PROJ-1",
        "updated_at": "2026-01-01T00:00:00Z", "author": "Alice",
        "assignee": None, "priority": "High",
    }
    fake = _FakeTrackerProvider([issue_open])
    monkeypatch.setattr(sync_mod, "get_provider", lambda name: fake)

    result = sync_mod.sync_tracker_issues(project)
    assert result == {"imported": 1, "updated": 0, "total": 1}

    tasks = [t for t in tasks_service.list_tasks() if t.project_id == project.id]
    assert len(tasks) == 1
    assert tasks[0].status.value == "todo"
    assert tasks[0].external_source["key"] == "PROJ-1"
    assert tasks[0].external_source["provider"] == "jira"

    # Re-sync with no changes: idempotent, nothing new, nothing updated.
    result = sync_mod.sync_tracker_issues(project)
    assert result == {"imported": 0, "updated": 0, "total": 1}
    assert len([t for t in tasks_service.list_tasks() if t.project_id == project.id]) == 1

    # The issue closes on the tracker: the still-todo task follows it.
    fake.issues = [dict(issue_open, state="closed", status="Done")]
    result = sync_mod.sync_tracker_issues(project)
    assert result == {"imported": 0, "updated": 1, "total": 1}

    tasks = [t for t in tasks_service.list_tasks() if t.project_id == project.id]
    assert tasks[0].status.value == "done"


# ── route: PUT /api/trackers/projects/{id} validates ─────────────────────

def test_put_tracker_config_validates():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.trackers import router as trackers_router

    project = _make_project_with_tracker(provider="none", remote_id=None)

    app = FastAPI()
    app.include_router(trackers_router)
    client = TestClient(app)

    resp = client.put(f"/api/trackers/projects/{project.id}", json={"provider": "bogus"})
    assert resp.status_code == 400

    resp = client.put("/api/trackers/projects/does-not-exist", json={"provider": "jira"})
    assert resp.status_code == 404

    resp = client.put(
        f"/api/trackers/projects/{project.id}",
        json={"provider": "jira", "remote_id": "PROJ"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "jira"
    assert body["remote_id"] == "PROJ"

    resp = client.get(f"/api/trackers/projects/{project.id}")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "jira"


# ── tools: not-configured error ──────────────────────────────────────────

def test_tracker_list_issues_tool_returns_not_configured_error():
    from tools.trackers import tracker_list_issues

    raw = tracker_list_issues.invoke({"provider": "jira", "remote_id": "PROJ"})
    data = json.loads(raw)
    assert data["ok"] is False
    assert "not configured" in data["error"]
    assert "Connectors page" in data["error"]


def test_tracker_sync_tool_returns_not_configured_error():
    from tools.trackers import tracker_sync

    project = _make_project_with_tracker(provider="linear", remote_id="ENG")
    raw = tracker_sync.invoke({"project": project.id})
    data = json.loads(raw)
    assert data["ok"] is False
    assert "not configured" in data["error"]


# ── tools: tracker_comment posts ADF to Jira ─────────────────────────────

def test_tracker_comment_posts_adf_to_jira(monkeypatch):
    from connectors.trackers import JIRA
    from tools.trackers import tracker_comment

    JIRA.store.set_config({
        "base_url": "https://acme.atlassian.net",
        "email": "me@acme.com",
        "api_token": "token-123",
    })

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"id": "999"})

    monkeypatch.setattr("httpx.post", fake_post)

    raw = tracker_comment.invoke({"provider": "jira", "key": "PROJ-1", "text": "Looks good"})
    data = json.loads(raw)
    assert data["ok"] is True

    assert captured["url"].endswith("/issue/PROJ-1/comment")
    assert captured["json"] == {
        "body": {
            "type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Looks good"}]}],
        }
    }
