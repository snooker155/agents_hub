"""
Credential connectors per workspace, along every path (not only inside a
tool call): a connector lives in the workspace that defines it, and the
default workspace's live everywhere. In workspace X: X's own definition if X
defines the connector, else the default's, never another workspace's.

Covers Jira (tools, a tracker sync of a project, the picker route), Google
(the OAuth state round trip with a stubbed token endpoint, access tokens
cached per workspace, Gmail status, a Google watcher), Microsoft Graph,
Notion, the git tokens (store, routes, a project's issue sync), the GitHub
App installation fallback, the consent portal's app registration and the
database connection list. No network: every HTTP call is stubbed.

Run: python -m pytest tests/test_connectors_per_workspace.py -q
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from connectors.channels.store import in_workspace


@pytest.fixture(autouse=True)
def _workspaces(monkeypatch):
    from workspace import create_workspace_folder
    for name in ("team-a", "team-b"):
        create_workspace_folder(name)
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    from connectors.google import auth as google_auth
    google_auth.reset_cache()
    yield
    google_auth.reset_cache()


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


# ── Google ───────────────────────────────────────────────────────────────────

GMAIL = "https://mail.google.com/"


class _GoogleHTTP:
    """Google's token and userinfo endpoints: a code exchange answers with a
    refresh token named after the OAuth client it was made with, a refresh
    with an access token named after the refresh token."""

    def __init__(self):
        self.posts: list[dict] = []

    def post(self, url, data=None, timeout=None, headers=None):
        data = dict(data or {})
        self.posts.append(data)
        if data.get("grant_type") == "authorization_code":
            body = {"access_token": f"at-code-{data['client_id']}",
                    "refresh_token": f"rt-{data['client_id']}",
                    "scope": f"https://www.googleapis.com/auth/drive {GMAIL}"}
        else:
            body = {"access_token": f"at-{data.get('refresh_token')}", "expires_in": 3600}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    def get(self, url, headers=None, timeout=None, params=None):
        token = str((headers or {}).get("Authorization", "")).removeprefix("Bearer ")
        email = {"at-code-cid-team-a": "team-a@gmail.com"}.get(token, "default@gmail.com")
        return httpx.Response(200, json={"email": email}, request=httpx.Request("GET", url))


@pytest.fixture
def google_http(monkeypatch):
    from connectors.google import auth as google_auth
    fake = _GoogleHTTP()
    monkeypatch.setattr(google_auth, "httpx", SimpleNamespace(
        post=fake.post, get=fake.get, HTTPError=httpx.HTTPError))
    return fake


def _google_clients():
    from connectors.google import STORE
    STORE.for_workspace("default").set_config({"client_id": "cid-default", "client_secret": "cs-d"})
    STORE.for_workspace("team-a").set_config({"client_id": "cid-team-a", "client_secret": "cs-a"})


def _google_connected():
    from connectors.google import STORE
    STORE.for_workspace("default").set_config({
        "client_id": "cid-default", "client_secret": "cs-d", "refresh_token": "rt-default",
        "account_email": "default@gmail.com", "granted_scopes": GMAIL})
    STORE.for_workspace("team-a").set_config({
        "client_id": "cid-team-a", "client_secret": "cs-a", "refresh_token": "rt-team-a",
        "account_email": "team-a@gmail.com", "granted_scopes": GMAIL})


def test_google_oauth_state_round_trip_lands_in_the_workspace_it_started_for(google_http):
    from connectors.google import STORE
    from routes import google as google_routes
    _google_clients()
    client = _client(google_routes.router)

    start = client.get("/api/google/oauth/start", params={"workspace": "team-a", "gmail": "1"},
                       follow_redirects=False)
    assert start.status_code in (302, 307), start.text
    qs = parse_qs(urlparse(start.headers["location"]).query)
    assert qs["client_id"] == ["cid-team-a"]
    state = qs["state"][0]

    back = client.get("/api/google/oauth/callback", params={"code": "c-1", "state": state},
                      follow_redirects=False)
    assert back.status_code in (302, 307), back.text
    assert back.headers["location"] == "/connectors?tab=google&workspace=team-a"
    # The exchange used team-a's OAuth client, and the grant is team-a's only.
    assert google_http.posts[-1]["client_id"] == "cid-team-a"
    assert google_http.posts[-1]["client_secret"] == "cs-a"
    team_a = STORE.for_workspace("team-a")
    assert team_a.get("refresh_token") == "rt-cid-team-a"
    assert team_a.get("account_email") == "team-a@gmail.com"
    assert STORE.for_workspace("default").get("refresh_token") == ""
    # The state is single use.
    again = client.get("/api/google/oauth/callback", params={"code": "c-1", "state": state})
    assert again.status_code == 400

    status = client.get("/api/google/gmail/status", params={"workspace": "team-a"}).json()
    assert status == {"connected": True, "gmail": True, "account_email": "team-a@gmail.com"}
    # team-b inherits the default's, which has no account connected.
    assert client.get("/api/google/gmail/status", params={"workspace": "team-b"}).json()["connected"] is False

    assert client.post("/api/google/oauth/disconnect", params={"workspace": "team-a"}).status_code == 200
    assert team_a.get("refresh_token") == ""
    assert STORE.for_workspace("default").get("client_id") == "cid-default"


def test_google_connect_needs_the_workspace_to_define_its_own_connector(google_http):
    from routes import google as google_routes
    _google_clients()
    client = _client(google_routes.router)
    # team-b inherits the default's connector: it cannot connect or
    # disconnect the default's account from inside itself.
    resp = client.get("/api/google/oauth/start", params={"workspace": "team-b"}, follow_redirects=False)
    assert resp.status_code == 400 and "default workspace" in resp.json()["detail"]
    assert client.post("/api/google/oauth/disconnect", params={"workspace": "team-b"}).status_code == 400
    assert client.get("/api/google/oauth/start", params={"workspace": "nope"},
                      follow_redirects=False).status_code == 404
    # The default's own start still uses the default's client.
    start = client.get("/api/google/oauth/start", follow_redirects=False)
    assert parse_qs(urlparse(start.headers["location"]).query)["client_id"] == ["cid-default"]


def test_google_access_tokens_are_per_workspace(google_http):
    from connectors.google import auth as google_auth
    _google_connected()
    assert in_workspace("team-a", google_auth.get_access_token) == "at-rt-team-a"
    assert in_workspace("team-b", google_auth.get_access_token) == "at-rt-default"
    assert google_auth.get_access_token() == "at-rt-default"
    assert google_auth.get_access_token("team-a") == "at-rt-team-a"
    # Cached per workspace: no further refresh, and no mixing.
    calls = len(google_http.posts)
    assert in_workspace("team-a", google_auth.get_access_token) == "at-rt-team-a"
    assert in_workspace("team-b", google_auth.get_access_token) == "at-rt-default"
    assert len(google_http.posts) == calls
    # Gmail sign in picks the same way.
    assert google_auth.gmail_login("team-a") == ("team-a@gmail.com", "at-rt-team-a")
    assert in_workspace("team-b", google_auth.gmail_login) == ("default@gmail.com", "at-rt-default")


def test_a_changed_refresh_token_is_not_served_from_the_cache(google_http):
    from connectors.google import STORE, auth as google_auth
    _google_connected()
    assert google_auth.get_access_token("team-a") == "at-rt-team-a"
    STORE.for_workspace("team-a").set_config({"refresh_token": "rt-team-a-2"})
    assert google_auth.get_access_token("team-a") == "at-rt-team-a-2"


class _Inbox:
    def select(self, folder, readonly=True):
        return "OK", [b"0"]

    def uid(self, command, *args):
        return "OK", [b""]

    def logout(self):
        pass


def test_a_google_watcher_in_team_a_signs_in_with_team_a_account(google_http, monkeypatch):
    from watchers import kinds
    _google_connected()
    seen = []

    def connect(cfg, credential):
        seen.append((credential.address, credential.token))
        return _Inbox()

    cfg = kinds.validate_config("imap", {"use_google": True})
    for ws in ("team-a", "team-b"):
        watcher = SimpleNamespace(workspace=ws, config=cfg, state={})
        result = kinds.probe_imap(watcher, lambda n: pytest.fail("no secret"), connect=connect)
        assert result.state[kinds.GOOGLE_FROM] == ("team-a" if ws == "team-a" else "default")
    assert seen == [("team-a@gmail.com", "at-rt-team-a"), ("default@gmail.com", "at-rt-default")]


def test_a_google_watcher_never_falls_back_to_the_default_account_unconfirmed(google_http, monkeypatch):
    from connectors.google import STORE
    from watchers import kinds, service
    from watchers.store import store as watcher_store
    _google_connected()
    monkeypatch.setattr(kinds, "_imap_connect", lambda cfg, credential: _Inbox())

    watcher = service.create("team-a", {"name": "inbox", "kind": "imap", "config": {"use_google": True}})
    watcher = service.stamp_google_source(watcher.id)
    assert watcher.state[kinds.GOOGLE_FROM] == "team-a"
    assert service.probe_once(watcher.id)["ok"] is True
    assert watcher_store.get(watcher.id).state[kinds.GOOGLE_FROM] == "team-a"

    # team-a drops its own Google connector: the watcher would now read the
    # default (the operator's) mailbox, which nobody approved for it.
    STORE.remove_workspace("team-a")
    failed = service.probe_once(watcher.id)
    assert failed["ok"] is False and "save the watcher again" in failed["error"]
    # Saved again past the route's role check: confirmed.
    service.stamp_google_source(watcher.id)
    assert service.probe_once(watcher.id)["ok"] is True


def test_watcher_google_admin_check_follows_the_workspace_connector(monkeypatch):
    from routes import watchers as watcher_routes
    _google_clients()
    calls = []
    monkeypatch.setattr(watcher_routes.identity, "require_role", lambda principal, **kw: calls.append(kw))
    # team-a has its own Google connector: its editors may use it.
    watcher_routes._require_google_admin(None, {"use_google": True}, "team-a")
    assert calls == []
    # team-b and the default use the operator's: an administrator only.
    watcher_routes._require_google_admin(None, {"use_google": True}, "team-b")
    watcher_routes._require_google_admin(None, {"use_google": True}, "default")
    assert calls == [{"admin": True}, {"admin": True}]


# ── Microsoft ────────────────────────────────────────────────────────────────

def _microsoft():
    from connectors.microsoft import STORE
    STORE.for_workspace("default").set_config(
        {"tenant_id": "t-default", "client_id": "c-default", "client_secret": "s-default"})
    STORE.for_workspace("team-a").set_config(
        {"tenant_id": "t-team-a", "client_id": "c-team-a", "client_secret": "s-team-a",
         "default_user": "a@team-a.com"})


def test_microsoft_graph_client_picks_the_run_workspace_app():
    import tools.microsoft_graph as ms_tools
    _microsoft()
    client, user = in_workspace("team-a", ms_tools._client)
    assert (client.tenant_id, client.client_secret, user) == ("t-team-a", "s-team-a", "a@team-a.com")
    client, user = in_workspace("team-b", ms_tools._client)
    assert (client.tenant_id, client.client_secret, user) == ("t-default", "s-default", "")


def test_microsoft_consent_app_registration_is_the_workspace(monkeypatch):
    from connectors.microsoft import graph
    _microsoft()
    assert graph._app_config("team-a")["client_id"] == "c-team-a"
    assert graph._app_config("team-b")["client_id"] == "c-default"
    url = graph.build_consent_url("https://hub/consent/callback", "s", ["User.Read"], workspace="team-a")
    assert "c-team-a" in url and "t-team-a" in url

    posted = []

    def _post(url, data=None, timeout=None):
        posted.append((url, dict(data)))
        return httpx.Response(200, json={"access_token": "x"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(graph.httpx, "post", _post)
    graph.refresh_delegated("rt", ["User.Read"], workspace="team-a")
    assert "t-team-a" in posted[-1][0] and posted[-1][1]["client_secret"] == "s-team-a"


def test_the_msal_app_cache_never_shares_an_app_across_secrets(monkeypatch):
    from connectors.microsoft import graph
    built = []

    class _App:
        def __init__(self, client_id, authority=None, client_credential=None):
            built.append(client_credential)
            self.secret = client_credential

        def acquire_token_for_client(self, scopes):
            return {"access_token": f"tok-{self.secret}"}

    monkeypatch.setitem(__import__("sys").modules, "msal", SimpleNamespace(ConfidentialClientApplication=_App))
    monkeypatch.setattr(graph, "_app_cache", {})
    assert graph.acquire_token("t", "c", "secret-1") == "tok-secret-1"
    assert graph.acquire_token("t", "c", "secret-2") == "tok-secret-2"
    assert built == ["secret-1", "secret-2"]


# ── Notion ───────────────────────────────────────────────────────────────────

def test_notion_client_is_the_run_workspace_or_the_default():
    from connectors.knowledge import NOTION_STORE, notion_client
    import tools.knowledge as knowledge_tools
    NOTION_STORE.for_workspace("default").set_config({"api_token": "ntn_default"})
    NOTION_STORE.for_workspace("team-a").set_config({"api_token": "ntn_team_a"})
    assert in_workspace("team-a", knowledge_tools._notion_client).token == "ntn_team_a"
    assert in_workspace("team-b", knowledge_tools._notion_client).token == "ntn_default"
    assert notion_client("team-a").token == "ntn_team_a"
    assert notion_client("team-b").token == "ntn_default"


def test_notion_defined_only_in_team_a_is_invisible_elsewhere():
    from connectors.knowledge import NOTION_STORE
    import tools.knowledge as knowledge_tools
    NOTION_STORE.for_workspace("team-a").set_config({"api_token": "ntn_team_a"})
    assert in_workspace("team-a", knowledge_tools._notion_client) is not None
    assert in_workspace("team-b", knowledge_tools._notion_client) is None
    assert knowledge_tools._notion_client() is None


# ── git tokens ───────────────────────────────────────────────────────────────

def test_git_tokens_resolve_per_workspace_and_per_provider():
    from connectors.git import store as git_store
    from connectors.git.providers import get_provider
    git_store.set_token("github", "gh-default")
    git_store.set_token("gitlab", "gl-default")
    git_store.set_token("github", "gh-team-a", "team-a")
    # team-a brings its own GitHub, keeps the default's GitLab.
    assert git_store.get_token("github", "team-a") == "gh-team-a"
    assert git_store.get_token("gitlab", "team-a") == "gl-default"
    assert git_store.get_token("github", "team-b") == "gh-default"
    assert in_workspace("team-a", git_store.get_token, "github") == "gh-team-a"
    assert in_workspace("team-b", git_store.get_token, "github") == "gh-default"
    assert git_store.get_token("github") == "gh-default"
    assert get_provider("github", "team-a").token == "gh-team-a"
    assert in_workspace("team-b", get_provider, "github").token == "gh-default"
    # A workspace's own entry starts empty: the default's token is not copied.
    git_store.set_base_url("gitea", "https://git.team-a.example", "team-a")
    assert git_store.get_token("gitea", "team-a") == ""
    assert git_store.defined_workspaces("github") == ["team-a"]
    assert git_store.remove_workspace("github", "team-a")
    assert git_store.get_token("github", "team-a") == "gh-default"
    assert not git_store.remove_workspace("github", "default")


def test_git_routes_work_per_workspace():
    from routes import git as git_routes
    client = _client(git_routes.router)
    client.put("/api/git/config", json={"provider": "github", "token": "gh-default"})
    put = client.put("/api/git/config", params={"workspace": "team-a"},
                     json={"provider": "github", "token": "gh-team-a"})
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["workspace"] == "team-a" and body["sources"]["github"] == "here"
    assert body["sources"]["gitlab"] == "default" and body["github"]["has_token"] is True
    default = client.get("/api/git/config").json()
    assert default["defined_in"]["github"] == ["team-a"]
    team_b = client.get("/api/git/config", params={"workspace": "team-b"}).json()
    assert team_b["sources"]["github"] == "default" and team_b["github"]["has_token"] is True

    from connectors.git import store as git_store
    assert git_store.get_token("github", "default") == "gh-default"
    gone = client.request("DELETE", "/api/git/config", params={"workspace": "team-a", "provider": "github"})
    assert gone.status_code == 200 and gone.json()["sources"]["github"] == "default"
    assert client.request("DELETE", "/api/git/config", params={"provider": "github"}).status_code == 400


def test_a_project_issue_sync_uses_the_project_workspace_git_token(monkeypatch):
    from connectors.git import issue_sync, store as git_store
    from connectors.git.providers import GitHubProvider
    from projects.models import RepoConfig
    git_store.set_token("github", "gh-default")
    git_store.set_token("github", "gh-team-a", "team-a")
    seen = []
    monkeypatch.setattr(GitHubProvider, "list_issues",
                        lambda self, remote_id, *a, **k: seen.append(self.token) or [])
    project = _project("team-a", repo=RepoConfig(type="github", remote_id="acme/app"))
    issue_sync.sync_issues(project)
    in_workspace("team-b", issue_sync.sync_issues, project)
    assert seen == ["gh-team-a", "gh-team-a"]


def test_git_auth_args_use_the_named_workspace_token(monkeypatch):
    from connectors.git import git_ops, store as git_store
    git_store.set_token("github", "gh-default")
    git_store.set_token("github", "gh-team-a", "team-a")
    import base64
    header = git_ops._auth_args("github", "team-a")[1]
    assert "gh-team-a" in header or "gh-team-a" in base64.b64decode(header.split()[-1]).decode()
    header = git_ops._auth_args("github", "team-b")[1]
    assert "gh-default" in header or "gh-default" in base64.b64decode(header.split()[-1]).decode()


# ── the GitHub App ───────────────────────────────────────────────────────────

def test_a_github_installation_of_the_default_serves_workspaces_without_their_own():
    from common import db
    from connectors.git import github_app
    with db.transaction() as conn:
        for iid, ws in ((11, "default"), (22, "team-a")):
            conn.execute("INSERT INTO github_installations (installation_id, workspace, created_at, "
                         "updated_at) VALUES (?, ?, '2026-01-01', '2026-01-01')", (iid, ws))
    assert github_app.installation_in_effect("team-a") == (22, "team-a")
    assert github_app.installation_in_effect("team-b") == (11, "default")
    assert github_app.installation_in_effect("default") == (11, "default")


# ── the consent portal ───────────────────────────────────────────────────────

def test_consent_uses_the_request_workspace_app_registration():
    from connectors.consent import flow
    from connectors.google import STORE
    from connectors.google.auth import build_consent_url
    STORE.for_workspace("team-a").set_config({"client_id": "cid-team-a", "client_secret": "cs-a"})
    assert flow.provider_ready("google", "team-a") is True
    assert flow.provider_ready("google", "team-b") is False
    url = build_consent_url("https://hub/consent/callback", "s", ["openid"], workspace="team-a")
    assert parse_qs(urlparse(url).query)["client_id"] == ["cid-team-a"]


# ── databases ────────────────────────────────────────────────────────────────

def test_the_database_connection_list_is_one_list_whatever_the_workspace():
    from connectors.databases import STORE, store as db_store
    # team-a saving a (field-less) databases connector config of its own
    # must not give its runs a different, empty connection list.
    STORE.for_workspace("team-a").set_config({})
    conn = in_workspace("team-a", lambda: db_store.create_connection(
        workspace="team-a", name="wh", kind="sqlite", dsn="/tmp/x.db"))
    assert [c["id"] for c in db_store.list_connections("team-a")] == [conn["id"]]
    assert in_workspace("team-a", db_store.find_connection, "team-a", "wh")["id"] == conn["id"]
    assert in_workspace("team-b", db_store.find_connection, "team-b", "wh") is None
