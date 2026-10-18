"""
Credential connectors per workspace, along every path (not only inside a
tool call): a connector lives in the workspace that defines it, and the
default workspace's live everywhere. In workspace X: X's own definition if X
defines the connector, else the default's, never another workspace's.

Covers Jira (tools, a tracker sync of a project, the picker route) and the
GitLab token (store, routes, a project's issue sync). No network: every HTTP
call is stubbed.

Run: python -m pytest tests/test_connectors_per_workspace.py -q
"""
from __future__ import annotations

import json
import uuid

import pytest

from connectors.store import in_workspace


@pytest.fixture(autouse=True)
def _workspaces(monkeypatch):
    from workspace import create_workspace_folder
    for name in ("team-a", "team-b"):
        create_workspace_folder(name)
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    yield


def _client(*routers):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return TestClient(app)


# ── Jira ─────────────────────────────────────────────────────────────────────

def _jira(default_url="https://acme.atlassian.net", team_url="https://team-a.atlassian.net"):
    from connectors.trackers import JIRA_STORE
    JIRA_STORE.for_workspace("default").set_config(
        {"base_url": default_url, "email": "d@acme.com", "api_token": "tok-default"})
    JIRA_STORE.for_workspace("team-a").set_config(
        {"base_url": team_url, "email": "a@team.com", "api_token": "tok-team-a"})


def test_jira_default_lives_everywhere_and_team_a_only_in_team_a():
    from connectors.trackers.providers import get_provider
    _jira()
    assert in_workspace("team-a", get_provider, "jira").base_url == "https://team-a.atlassian.net"
    assert in_workspace("team-b", get_provider, "jira").base_url == "https://acme.atlassian.net"
    assert get_provider("jira").base_url == "https://acme.atlassian.net"
    # Named explicitly, as a route or a job does.
    assert get_provider("jira", "team-a").base_url == "https://team-a.atlassian.net"
    assert get_provider("jira", "team-b").base_url == "https://acme.atlassian.net"


def _project(workspace: str, **fields):
    from common.paths import PROJECTS_FILE
    from projects.models import Project, TrackerConfig
    from projects.storage import ProjectStore
    project = Project(name=f"p-{uuid.uuid4().hex[:6]}", type="code", workspace=workspace,
                      tracker=TrackerConfig(provider="jira", remote_id="PROJ"), **fields)
    ProjectStore(path=PROJECTS_FILE).add(project)
    return project


def _record_jira_calls(monkeypatch):
    from connectors.trackers.providers import JiraProvider
    seen: list[str] = []

    def _list(self, remote_id, state="all", limit=200):
        seen.append(self.base_url)
        return [{"key": "PROJ-1", "number": "PROJ-1", "title": "T", "body": "", "state": "open",
                 "status": "To Do", "labels": [], "url": f"{self.base_url}/browse/PROJ-1"}]

    monkeypatch.setattr(JiraProvider, "list_issues", _list)
    monkeypatch.setattr(JiraProvider, "list_remotes", lambda self: [{"base": self.base_url}])
    return seen


def test_a_tracker_sync_of_a_team_a_project_uses_team_a_jira_from_anywhere(monkeypatch):
    from connectors.trackers.sync import sync_tracker_issues
    _jira()
    seen = _record_jira_calls(monkeypatch)
    project = _project("team-a")
    # No workspace context at all (a route, a scheduled job) ...
    assert sync_tracker_issues(project)["imported"] == 1
    # ... and from inside another workspace's run: still the project's.
    in_workspace("team-b", sync_tracker_issues, project)
    assert seen == ["https://team-a.atlassian.net", "https://team-a.atlassian.net"]


def test_a_tracker_sync_of_a_team_b_project_uses_the_default_jira(monkeypatch):
    from connectors.trackers.sync import sync_tracker_issues
    _jira()
    seen = _record_jira_calls(monkeypatch)
    in_workspace("team-a", sync_tracker_issues, _project("team-b"))
    assert seen == ["https://acme.atlassian.net"]


def test_the_tracker_routes_use_the_named_and_the_project_workspace(monkeypatch):
    from routes import trackers as tracker_routes
    _jira()
    seen = _record_jira_calls(monkeypatch)
    client = _client(tracker_routes.router)
    picker = client.get("/api/trackers/jira/projects", params={"workspace": "team-a"})
    assert picker.json() == [{"base": "https://team-a.atlassian.net"}]
    assert client.get("/api/trackers/jira/projects").json() == [{"base": "https://acme.atlassian.net"}]
    project = _project("team-a")
    resp = client.post(f"/api/trackers/projects/{project.id}/sync")
    assert resp.status_code == 200, resp.text
    assert seen == ["https://team-a.atlassian.net"]


def test_the_tracker_sync_tool_checks_the_project_workspace_connector(monkeypatch):
    from connectors.trackers import JIRA_STORE
    import tools.trackers as tracker_tools
    # Only team-a has Jira; the default has none.
    JIRA_STORE.for_workspace("team-a").set_config(
        {"base_url": "https://team-a.atlassian.net", "email": "a@team.com", "api_token": "t"})
    seen = _record_jira_calls(monkeypatch)
    project = _project("team-a")
    monkeypatch.setattr(tracker_tools, "_resolve_project", lambda ref: project)
    out = json.loads(in_workspace("team-a", tracker_tools.tracker_sync.invoke, {"project": project.id}))
    assert out.get("ok") is not False, out
    assert seen == ["https://team-a.atlassian.net"]


# ── the GitLab token ─────────────────────────────────────────────────────────

def test_git_tokens_resolve_per_workspace():
    from connectors.git import store as git_store
    from connectors.git.providers import get_provider
    git_store.set_token("gitlab", "gl-default")
    git_store.set_token("gitlab", "gl-team-a", "team-a")
    assert git_store.get_token("gitlab", "team-a") == "gl-team-a"
    assert git_store.get_token("gitlab", "team-b") == "gl-default"
    assert in_workspace("team-a", git_store.get_token, "gitlab") == "gl-team-a"
    assert in_workspace("team-b", git_store.get_token, "gitlab") == "gl-default"
    assert git_store.get_token("gitlab") == "gl-default"
    assert get_provider("gitlab", "team-a").token == "gl-team-a"
    assert in_workspace("team-b", get_provider, "gitlab").token == "gl-default"
    assert git_store.defined_workspaces("gitlab") == ["team-a"]
    assert git_store.remove_workspace("gitlab", "team-a")
    assert git_store.get_token("gitlab", "team-a") == "gl-default"
    assert not git_store.remove_workspace("gitlab", "default")


def test_git_routes_work_per_workspace():
    from routes import git as git_routes
    client = _client(git_routes.router)
    client.put("/api/git/config", json={"provider": "gitlab", "token": "gl-default"})
    put = client.put("/api/git/config", params={"workspace": "team-a"},
                     json={"provider": "gitlab", "token": "gl-team-a"})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["workspace"] == "team-a" and body["sources"]["gitlab"] == "here"
    assert body["gitlab"]["has_token"] is True
    default = client.get("/api/git/config").json()
    assert default["defined_in"]["gitlab"] == ["team-a"]
    team_b = client.get("/api/git/config", params={"workspace": "team-b"}).json()
    assert team_b["sources"]["gitlab"] == "default" and team_b["gitlab"]["has_token"] is True

    from connectors.git import store as git_store
    assert git_store.get_token("gitlab", "default") == "gl-default"
    gone = client.request("DELETE", "/api/git/config", params={"workspace": "team-a", "provider": "gitlab"})
    assert gone.status_code == 200 and gone.json()["sources"]["gitlab"] == "default"
    assert client.request("DELETE", "/api/git/config", params={"provider": "gitlab"}).status_code == 400


def test_a_project_issue_sync_uses_the_project_workspace_git_token(monkeypatch):
    from connectors.git import issue_sync, store as git_store
    from connectors.git.providers import GitLabProvider
    from projects.models import RepoConfig
    git_store.set_token("gitlab", "gl-default")
    git_store.set_token("gitlab", "gl-team-a", "team-a")
    seen = []
    monkeypatch.setattr(GitLabProvider, "list_issues",
                        lambda self, remote_id, *a, **k: seen.append(self.token) or [])
    project = _project("team-a", repo=RepoConfig(type="gitlab", remote_id="acme/app"))
    issue_sync.sync_issues(project)
    in_workspace("team-b", issue_sync.sync_issues, project)
    assert seen == ["gl-team-a", "gl-team-a"]


def test_git_auth_args_use_the_named_workspace_token(monkeypatch):
    from connectors.git import git_ops, store as git_store
    git_store.set_token("gitlab", "gl-default")
    git_store.set_token("gitlab", "gl-team-a", "team-a")
    import base64
    header = git_ops._auth_args("gitlab", "team-a")[1]
    assert "gl-team-a" in header or "gl-team-a" in base64.b64decode(header.split()[-1]).decode()
    header = git_ops._auth_args("gitlab", "team-b")[1]
    assert "gl-default" in header or "gl-default" in base64.b64decode(header.split()[-1]).decode()
