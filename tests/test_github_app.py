"""The GitHub App against an in-process fake GitHub.

connectors/git/github_app.py keeps every HTTP call in one function
(``_http``), which is replaced here by a fake that verifies the app JWT with
the public half of a key generated for the test, issues installation tokens
and user tokens, and rotates refresh tokens. Everything else (the table,
encryption, the signed state cookie, the routes, the hand-out through
common/secrets.py) runs for real.
"""
from __future__ import annotations

import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"
API = "https://api.github.example"
WEB = "https://github.example"


def _iso(delta_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


class FakeGitHub:
    def __init__(self, public_pem: bytes):
        self.public_pem = public_pem
        self.installations = [
            {"id": 11, "account": {"login": "acme", "type": "Organization"},
             "repository_selection": "selected",
             "permissions": {"contents": "write", "pull_requests": "write"}},
            {"id": 22, "account": {"login": "octo", "type": "User"},
             "repository_selection": "all", "permissions": {"contents": "read"}},
        ]
        self.token_expires_in = 3600
        self.issued = 0
        self.codes = {}
        self.refresh_tokens = {}
        self.user_access_expires_in = 8 * 3600
        self.calls = []
        self.revoked = []

    def check_jwt(self, headers):
        from authlib.jose import jwt
        token = headers["Authorization"].split(" ", 1)[1]
        claims = jwt.decode(token, self.public_pem)
        claims.validate()
        assert claims["iss"] == "4242"
        return claims

    def issue_user_tokens(self, login):
        self.issued += 1
        refresh = f"ghr_refresh_{self.issued}"
        self.refresh_tokens[refresh] = login
        return {"access_token": f"ghu_access_{self.issued}", "token_type": "bearer",
                "expires_in": self.user_access_expires_in, "refresh_token": refresh,
                "refresh_token_expires_in": 15897600, "scope": ""}

    def __call__(self, method, url, *, headers=None, json_body=None, form=None, auth=None):
        headers = headers or {}
        self.calls.append((method, url))
        if method == "GET" and url.startswith(f"{API}/app/installations"):
            self.check_jwt(headers)
            return 200, list(self.installations)
        if method == "POST" and url.startswith(f"{API}/app/installations/"):
            self.check_jwt(headers)
            installation_id = int(url.split("/")[-2])
            if installation_id not in [i["id"] for i in self.installations]:
                return 404, {"message": "Not Found"}
            self.issued += 1
            return 201, {"token": f"ghs_install_{installation_id}_{self.issued}",
                         "expires_at": _iso(self.token_expires_in)}
        if method == "POST" and url == f"{WEB}/login/oauth/access_token":
            assert headers.get("Accept") == "application/json"
            assert form["client_id"] == "Iv1.client" and form["client_secret"] == "client-secret"
            if form.get("grant_type") == "refresh_token":
                login = self.refresh_tokens.pop(form["refresh_token"], None)
                if login is None:
                    return 200, {"error": "bad_refresh_token"}
                return 200, self.issue_user_tokens(login)
            login = self.codes.pop(form.get("code"), None)
            if login is None:
                return 200, {"error": "bad_verification_code"}
            return 200, self.issue_user_tokens(login)
        if method == "GET" and url == f"{API}/user":
            return 200, {"login": "alice-gh"}
        if method == "DELETE" and url.startswith(f"{API}/applications/"):
            self.revoked.append(json_body["access_token"])
            return 204, {}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def github(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from common.config import settings
    from connectors.git import github_app

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = private.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    fake = FakeGitHub(public)
    monkeypatch.setattr(github_app, "_http", fake)
    for name, value in {
        "github_app_id": "4242", "github_app_slug": "hub-bot",
        "github_app_client_id": "Iv1.client", "github_app_client_secret": "client-secret",
        "github_app_private_key": pem, "github_app_private_key_file": "",
        "github_api_url": API, "github_url": WEB,
        "secret_key": "correct horse battery staple", "secret_backend": "local",
        "auth_public_url": "http://testserver", "auth_cookie_secure": "auto",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    return fake


@pytest.fixture
def no_key(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "", raising=False)


@pytest.fixture
def single(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "single", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app, follow_redirects=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin_headers, username="bob"):
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin_headers)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    return created.json()["id"], _bearer(session.json()["token"]), session.json()["token"]


def _workspace(name: str) -> None:
    from workspace.storage import create_workspace_folder
    create_workspace_folder(name)


def _agent(agent_id: str, *, secrets=(), github_identity="app"):
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id=agent_id, name=agent_id, type="local", entrypoint="m:f",
                        secrets=list(secrets), github_identity=github_identity),
              user_edit=False)


def _connect(client, github, headers=None, token=None, login="alice-gh"):
    params = {"token": token} if token else {}
    start = client.get("/api/auth/github/connect", params=params, headers=headers or {})
    assert start.status_code == 302, start.text
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(start.headers["location"]).query))
    github.codes["the-code"] = login
    return client.get("/api/external/github/callback",
                      params={"code": "the-code", "state": query["state"]})


# ── the app JWT and installation tokens ──────────────────────────────────────

def test_app_jwt_is_a_valid_rs256_token(github):
    from authlib.jose import jwt
    from connectors.git import github_app
    token = github_app.app_jwt()
    header = jwt.decode(token, github.public_pem).header
    claims = github.check_jwt({"Authorization": f"Bearer {token}"})
    assert header["alg"] == "RS256"
    now = int(time.time())
    assert claims["iat"] <= now - 59 and now + 500 < claims["exp"] <= now + 540


def test_private_key_from_a_file(github, monkeypatch, tmp_path):
    from common.config import settings
    from connectors.git import github_app
    pem = settings.github_app_private_key
    path = tmp_path / "app.pem"
    path.write_text(pem)
    monkeypatch.setattr(settings, "github_app_private_key", "", raising=False)
    monkeypatch.setattr(settings, "github_app_private_key_file", str(path), raising=False)
    assert github_app.configured()
    github.check_jwt({"Authorization": f"Bearer {github_app.app_jwt()}"})


def test_not_configured_without_the_key(github, monkeypatch):
    from common.config import settings
    from connectors.git import github_app
    monkeypatch.setattr(settings, "github_app_private_key", "", raising=False)
    assert not github_app.configured()
    with pytest.raises(github_app.GitHubAppError):
        github_app.app_jwt()


def test_installation_token_is_cached_encrypted_until_near_expiry(github):
    from common import db
    from connectors.git import github_app
    github_app.list_installations()
    first = github_app.installation_token(11)
    assert first.startswith("ghs_install_11_")
    assert github_app.installation_token(11) == first
    stored = db.get_conn().execute(
        "SELECT token_enc FROM github_installations WHERE installation_id = 11").fetchone()
    assert stored["token_enc"] and first not in stored["token_enc"]
    # Within five minutes of expiry: a new one is issued.
    with db.transaction() as conn:
        conn.execute("UPDATE github_installations SET token_expires_at = ? "
                     "WHERE installation_id = 11", (_iso(120),))
    second = github_app.installation_token(11)
    assert second != first


def test_no_secret_key_means_no_cache(github, no_key):
    from common import db
    from connectors.git import github_app
    github_app.list_installations()
    first = github_app.installation_token(11)
    second = github_app.installation_token(11)
    assert first != second
    row = db.get_conn().execute(
        "SELECT token_enc FROM github_installations WHERE installation_id = 11").fetchone()
    assert not row["token_enc"]


def test_sync_upserts_keeps_bindings_and_drops_uninstalled(github):
    from connectors.git import github_app
    listed = github_app.list_installations()
    assert [(i["installation_id"], i["account_login"], i["account_type"]) for i in listed] == [
        (11, "acme", "Organization"), (22, "octo", "User")]
    assert listed[0]["permissions"]["contents"] == "write"
    github_app.bind_installation(11, "ws")
    github.installations[0]["account"]["login"] = "acme-renamed"
    github.installations.pop(1)
    listed = github_app.list_installations()
    assert [(i["installation_id"], i["account_login"], i["workspace"]) for i in listed] == [
        (11, "acme-renamed", "ws")]


def test_bind_moves_the_workspace_binding_and_unbind(github):
    from connectors.git import github_app
    github_app.list_installations()
    github_app.bind_installation(11, "ws")
    assert github_app.installation_for_workspace("ws") == 11
    github_app.bind_installation(22, "ws")
    assert github_app.installation_for_workspace("ws") == 22
    assert github_app.get_installation(11)["workspace"] == ""
    assert github_app.unbind(22) is True
    assert github_app.installation_for_workspace("ws") is None
    with pytest.raises(github_app.GitHubAppError):
        github_app.bind_installation(99, "ws")


# ── routes: admin view, setup, binding ───────────────────────────────────────

def test_overview_and_sync_in_single_mode(github, single, client):
    overview = client.get("/api/git/github-app").json()
    assert overview["configured"] is True
    assert overview["install_url"] == f"{WEB}/apps/hub-bot/installations/new"
    assert overview["installations"] == []
    synced = client.post("/api/git/github-app/sync").json()
    assert len(synced["installations"]) == 2


def test_setup_url_syncs_and_redirects(github, multi, client):
    response = client.get("/api/external/github-app/setup",
                          params={"installation_id": "11", "setup_action": "install"})
    assert response.status_code == 302
    assert response.headers["location"] == "http://testserver/connectors?tab=git"
    from connectors.git import github_app
    assert len(github_app.installations()) == 2


def test_overview_is_admin_only_in_multi(github, multi, client):
    admin = _admin(client)
    _, bob, _ = _member(client, admin)
    assert client.get("/api/git/github-app").status_code == 401
    assert client.get("/api/git/github-app", headers=bob).status_code == 403
    assert client.post("/api/git/github-app/sync", headers=bob).status_code == 403
    assert client.get("/api/git/github-app", headers=admin).status_code == 200


def test_binding_needs_the_workspace_owner_and_is_audited(github, multi, client):
    from common import db, identity
    from connectors.git import github_app
    admin = _admin(client)
    bob_id, bob, _ = _member(client, admin)
    carol_id, carol, _ = _member(client, admin, "carol")
    _workspace("ws1")
    _workspace("ws2")
    github_app.list_installations()
    identity.set_member("ws1", bob_id, "editor")
    refused = client.put("/api/workspaces/ws1/github-installation",
                         json={"installation_id": 11}, headers=bob)
    assert refused.status_code == 403
    identity.set_member("ws1", bob_id, "owner")
    bound = client.put("/api/workspaces/ws1/github-installation",
                       json={"installation_id": 11}, headers=bob)
    assert bound.status_code == 200, bound.text
    assert github_app.installation_for_workspace("ws1") == 11
    # Another owner may not take it away from ws1; an admin may.
    identity.set_member("ws2", carol_id, "owner")
    taken = client.put("/api/workspaces/ws2/github-installation",
                       json={"installation_id": 11}, headers=carol)
    assert taken.status_code == 403
    assert client.put("/api/workspaces/ws2/github-installation",
                      json={"installation_id": 11}, headers=admin).status_code == 200
    assert client.put("/api/workspaces/ws2/github-installation",
                      json={"installation_id": 999}, headers=admin).status_code == 404
    unbound = client.put("/api/workspaces/ws2/github-installation",
                         json={"installation_id": None}, headers=admin)
    assert unbound.status_code == 200
    assert github_app.installation_for_workspace("ws2") is None
    actions = [r["action"] for r in db.get_conn().execute(
        "SELECT action FROM audit_log WHERE action LIKE 'github.%' ORDER BY id").fetchall()]
    assert actions == ["github.install.bind", "github.install.bind", "github.install.unbind"]


# ── connecting a person's account ────────────────────────────────────────────

def test_connect_sets_a_signed_cookie_and_redirects(github, single, client):
    start = client.get("/api/auth/github/connect")
    assert start.status_code == 302
    location = urllib.parse.urlsplit(start.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == \
        f"{WEB}/login/oauth/authorize"
    query = dict(urllib.parse.parse_qsl(location.query))
    assert query["client_id"] == "Iv1.client"
    assert query["redirect_uri"] == "http://testserver/api/external/github/callback"
    cookie = start.headers["set-cookie"]
    assert "ah_github=" in cookie and "Path=/api/external/github" in cookie
    assert "HttpOnly" in cookie


def test_callback_stores_encrypted_tokens_and_audits(github, multi, client):
    from common import db
    admin = _admin(client)
    bob_id, _, bob_token = _member(client, admin)
    done = _connect(client, github, token=bob_token)
    assert done.status_code == 302
    assert done.headers["location"] == "http://testserver/account#github=connected"
    row = db.get_conn().execute("SELECT * FROM github_user_tokens WHERE user_id = ?",
                                (bob_id,)).fetchone()
    assert row["login"] == "alice-gh"
    for column in ("access_enc", "refresh_enc"):
        assert row[column] and "ghu_" not in row[column] and "ghr_" not in row[column]
    status = client.get("/api/auth/github", headers=_bearer(bob_token)).json()
    assert status["connected"] and status["login"] == "alice-gh" and status["access_expires_at"]
    audit_row = db.get_conn().execute(
        "SELECT actor_id, result FROM audit_log WHERE action = 'github.connect'").fetchone()
    assert audit_row["actor_id"] == bob_id and audit_row["result"] == "ok"


def test_callback_without_the_cookie_is_refused(github, single, client):
    github.codes["the-code"] = "mallory"
    response = client.get("/api/external/github/callback",
                          params={"code": "the-code", "state": "forged"})
    assert response.status_code == 302
    assert response.headers["location"].startswith("http://testserver/account#github=error")
    from common import db
    assert db.get_conn().execute("SELECT COUNT(*) FROM github_user_tokens").fetchone()[0] == 0


def test_an_oidc_cookie_does_not_pass_for_a_github_one(github, single, client):
    from common import oidc
    cookie = oidc.sign_state({"state": "s", "exp": int(time.time()) + 60, "uid": "local"})
    client.cookies.set("ah_github", cookie, path="/api/external/github")
    github.codes["the-code"] = "mallory"
    response = client.get("/api/external/github/callback",
                          params={"code": "the-code", "state": "s"})
    assert "github=error" in response.headers["location"]


def test_connect_is_refused_without_a_secret_key(github, no_key, single, client):
    response = client.get("/api/auth/github/connect")
    assert response.status_code == 400
    assert "AGENTS_HUB_SECRET_KEY" in response.json()["detail"]


def test_connect_needs_a_person_in_multi(github, multi, client):
    _admin(client)
    assert client.get("/api/auth/github/connect").status_code == 401


def test_refresh_on_expiry_rotates_the_refresh_token(github, single, client):
    from common import db
    from connectors.git import github_app
    assert _connect(client, github).status_code == 302
    first = github_app.user_token("local")
    assert first == "ghu_access_1"
    before = db.get_conn().execute(
        "SELECT refresh_enc FROM github_user_tokens WHERE user_id = 'local'").fetchone()[0]
    with db.transaction() as conn:
        conn.execute("UPDATE github_user_tokens SET access_expires_at = ? WHERE user_id = 'local'",
                     (_iso(60),))
    refreshed = github_app.user_token("local")
    assert refreshed == "ghu_access_2"
    after = db.get_conn().execute(
        "SELECT refresh_enc, access_expires_at FROM github_user_tokens "
        "WHERE user_id = 'local'").fetchone()
    assert after["refresh_enc"] != before
    assert "ghr_refresh_1" not in github.refresh_tokens and "ghr_refresh_2" in github.refresh_tokens
    assert github_app.user_token("local") == "ghu_access_2"  # fresh again, no new call


def test_an_expired_refresh_token_gives_none(github, single, client):
    from common import db
    from connectors.git import github_app
    _connect(client, github)
    with db.transaction() as conn:
        conn.execute("UPDATE github_user_tokens SET access_expires_at = ?, "
                     "refresh_expires_at = ? WHERE user_id = 'local'", (_iso(-10), _iso(-5)))
    assert github_app.user_token("local") is None


def test_disconnect_forgets_and_revokes(github, single, client):
    from common import db
    _connect(client, github)
    response = client.delete("/api/auth/github")
    assert response.status_code == 200
    assert github.revoked == ["ghu_access_1"]
    assert db.get_conn().execute("SELECT COUNT(*) FROM github_user_tokens").fetchone()[0] == 0
    assert client.delete("/api/auth/github").status_code == 404
    assert client.get("/api/auth/github").json()["connected"] is False
    actions = [r[0] for r in db.get_conn().execute(
        "SELECT action FROM audit_log WHERE action LIKE 'github.%' ORDER BY id").fetchall()]
    assert actions == ["github.connect", "github.disconnect"]


# ── the hand-out ─────────────────────────────────────────────────────────────

def test_token_for_run_precedence(github, single, client):
    from common import secrets
    from connectors.git import github_app
    _agent("bot", secrets=["GITHUB_TOKEN"])
    _agent("person", secrets=["GITHUB_TOKEN"], github_identity="user")
    # Nothing bound, nobody connected: nothing.
    assert github_app.token_for_run("ws", "bot", "local") is None
    github_app.list_installations()
    github_app.bind_installation(11, "ws")
    assert github_app.token_for_run("ws", "bot", "local").startswith("ghs_install_11_")
    # github_identity user, not connected yet: falls back to the installation.
    assert github_app.token_for_run("ws", "person", "local").startswith("ghs_install_11_")
    _connect(client, github)
    assert github_app.token_for_run("ws", "person", "local").startswith("ghu_access_")
    assert github_app.token_for_run("ws", "bot", "local").startswith("ghs_install_11_")
    # An explicit secret wins over both.
    secrets.set_secret("ws", "GITHUB_TOKEN", "ghp_explicit_token_value")
    assert secrets.env_for_run("ws", "person", "local") == {
        "GITHUB_TOKEN": "ghp_explicit_token_value"}


def test_env_for_run_only_for_agents_that_declare_it(github, single):
    from common import secrets
    from connectors.git import github_app
    _agent("declares", secrets=["GITHUB_TOKEN"])
    _agent("silent")
    github_app.list_installations()
    github_app.bind_installation(11, "ws")
    env = secrets.env_for_run("ws", "declares", "local")
    assert env["GITHUB_TOKEN"].startswith("ghs_install_11_")
    assert secrets.env_for_run("ws", "silent", "local") == {}
    assert secrets.env_for_run("other-ws", "declares", "local") == {}


def test_in_process_get_falls_through_to_the_app(github, single):
    from common import secrets
    from connectors.git import github_app
    _agent("declares", secrets=["GITHUB_TOKEN"])
    github_app.list_installations()
    github_app.bind_installation(11, "ws")
    assert secrets.get("GITHUB_TOKEN") is None  # no active scope
    with secrets.activate("ws", "declares", "local"):
        assert secrets.get("GITHUB_TOKEN").startswith("ghs_install_11_")
    with secrets.activate("ws", "silent-agent", "local"):
        assert secrets.get("GITHUB_TOKEN") is None


def test_token_for_run_never_raises(github, single, monkeypatch):
    from connectors.git import github_app
    _agent("bot", secrets=["GITHUB_TOKEN"])
    github_app.list_installations()
    github_app.bind_installation(11, "ws")

    def boom(*a, **k):
        raise ConnectionError("github is down")
    monkeypatch.setattr(github_app, "_http", boom)
    assert github_app.token_for_run("ws", "bot", "local") is None


def test_github_identity_round_trips_through_the_registry(single):
    from agents.registry import get_agent
    _agent("person", secrets=["GITHUB_TOKEN"], github_identity="user")
    _agent("bot", secrets=["GITHUB_TOKEN"])
    assert get_agent("person").github_identity == "user"
    assert get_agent("bot").github_identity == "app"
    assert "github_identity" not in get_agent("bot").to_dict()


def test_agent_secrets_route_sets_github_identity(single, client):
    _agent("worker")
    ok = client.put("/api/agents/worker/secrets",
                    json={"secrets": ["GITHUB_TOKEN"], "github_identity": "user"})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"secrets": ["GITHUB_TOKEN"], "github_identity": "user"}
    # Leaving it out keeps the choice.
    kept = client.put("/api/agents/worker/secrets", json={"secrets": ["GITHUB_TOKEN"]})
    assert kept.json()["github_identity"] == "user"
    assert client.get("/api/agents/worker/secrets").json()["github_identity"] == "user"
    assert client.put("/api/agents/worker/secrets",
                      json={"secrets": [], "github_identity": "robot"}).status_code == 400
