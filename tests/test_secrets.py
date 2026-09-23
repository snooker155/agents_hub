"""Workspace secrets (common/secrets.py): storage, hand-out and the routes.

The properties that matter most, in order: a value never comes back out of
the API; a run receives only the names its agent declares; a secrets problem
hands out nothing rather than crashing or leaking; and declaring a secret is
visible to the capability guard.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

FERNET_KEY = "0" * 43 + "="  # 44 chars of urlsafe base64, 32 bytes
PASSWORD = "hunter2-but-longer"


@pytest.fixture
def key(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "correct horse battery staple", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)


@pytest.fixture
def no_key(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", "", raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)


def _agent(agent_id: str, *, tools=(), secrets=()):
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id=agent_id, name=agent_id, type="local", entrypoint="m:f",
                        tools=list(tools), secrets=list(secrets)), user_edit=False)


# ── encryption ───────────────────────────────────────────────────────────────

def test_round_trip_and_ciphertext_is_not_the_value(key):
    from common import db, secrets
    secrets.set_secret("ws", "API_TOKEN", "sk-live-abcdef123456")
    assert secrets.get_secret("ws", "API_TOKEN") == "sk-live-abcdef123456"
    stored = db.get_conn().execute("SELECT ciphertext, key_version FROM secrets").fetchone()
    assert "abcdef123456" not in stored["ciphertext"]
    assert stored["key_version"] == 1


def test_a_fernet_key_is_used_as_is_and_a_passphrase_is_derived():
    from common.secrets import _fernet_key_from
    assert _fernet_key_from(FERNET_KEY) == FERNET_KEY.encode()
    derived = _fernet_key_from("a passphrase")
    assert derived != b"a passphrase" and len(derived) == 44
    assert _fernet_key_from("a passphrase") == derived  # deterministic


def test_a_fernet_key_round_trips(monkeypatch):
    from common import secrets
    from common.config import settings
    monkeypatch.setattr(settings, "secret_key", secrets.keygen(), raising=False)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)
    secrets.set_secret("ws", "X", "value-long-enough")
    assert secrets.get_secret("ws", "X") == "value-long-enough"


def test_a_changed_key_reads_nothing_rather_than_garbage(key, monkeypatch):
    from common import secrets
    from common.config import settings
    secrets.set_secret("ws", "X", "value-long-enough")
    monkeypatch.setattr(settings, "secret_key", "another passphrase", raising=False)
    assert secrets.get_secret("ws", "X") is None
    assert secrets.list_secrets("ws")[0]["name"] == "X"


def test_hint_is_last_four_only_for_long_values(key):
    from common import secrets
    assert secrets.set_secret("ws", "LONG", "abcdefghijkl")["hint"] == "ijkl"
    assert secrets.set_secret("ws", "SHORT", "abcd1234")["hint"] == "****"


def test_no_key_refuses_to_store_but_lists_and_reads_nothing(key, monkeypatch):
    from common import secrets
    from common.config import settings
    secrets.set_secret("ws", "X", "value-long-enough")
    monkeypatch.setattr(settings, "secret_key", "", raising=False)
    with pytest.raises(secrets.SecretKeyMissing, match="ah secrets keygen"):
        secrets.set_secret("ws", "Y", "value")
    assert secrets.get_secret("ws", "X") is None
    assert secrets.resolve_for_run("ws", "", "", ["X"]) == {}
    assert [r["name"] for r in secrets.list_secrets("ws")] == ["X"]


@pytest.mark.parametrize("bad", ["lower", "1ABC", "A-B", "A B", "", "A" * 65])
def test_names_must_be_environment_variable_names(key, bad):
    from common import secrets
    with pytest.raises(secrets.SecretsError):
        secrets.set_secret("ws", bad, "value")


def test_list_never_carries_values(key):
    from common import secrets
    secrets.set_secret("ws", "X", "value-long-enough", agent_id="a1", created_by="u1")
    rows = secrets.list_secrets("ws")
    assert rows == [{"name": "X", "agent_id": "a1", "user_id": "", "hint": "ough",
                     "updated_at": rows[0]["updated_at"], "created_at": rows[0]["created_at"],
                     "created_by": "u1"}]
    assert "value-long-enough" not in repr(rows)


def test_set_replaces_at_the_same_scope_and_delete_is_exact(key):
    from common import secrets
    secrets.set_secret("ws", "X", "first-value-1")
    secrets.set_secret("ws", "X", "second-value-2")
    secrets.set_secret("ws", "X", "agent-value-3", agent_id="a1")
    assert len(secrets.list_secrets("ws")) == 2
    assert secrets.get_secret("ws", "X") == "second-value-2"
    assert secrets.delete_secret("ws", "X", agent_id="a1") is True
    assert secrets.delete_secret("ws", "X", agent_id="a1") is False
    assert secrets.get_secret("ws", "X") == "second-value-2"


# ── precedence and the allowlist ─────────────────────────────────────────────

def test_precedence_both_then_agent_then_user_then_workspace(key):
    from common import secrets
    secrets.set_secret("ws", "T", "workspace-wide")
    secrets.set_secret("ws", "T", "user-scoped", user_id="u1")
    secrets.set_secret("ws", "T", "agent-scoped", agent_id="a1")
    secrets.set_secret("ws", "T", "agent-and-user", agent_id="a1", user_id="u1")
    resolve = secrets.resolve_for_run
    assert resolve("ws", "a1", "u1", ["T"]) == {"T": "agent-and-user"}
    assert resolve("ws", "a1", "u2", ["T"]) == {"T": "agent-scoped"}
    assert resolve("ws", "a2", "u1", ["T"]) == {"T": "user-scoped"}
    assert resolve("ws", "a2", "u2", ["T"]) == {"T": "workspace-wide"}
    assert resolve("ws", "a2", None, ["T"]) == {"T": "workspace-wide"}
    assert resolve("other", "a1", "u1", ["T"]) == {}


def test_only_allowed_names_are_resolved(key):
    from common import secrets
    secrets.set_secret("ws", "A", "value-of-a-long")
    secrets.set_secret("ws", "B", "value-of-b-long")
    assert secrets.resolve_for_run("ws", "x", "", ["A"]) == {"A": "value-of-a-long"}
    assert secrets.resolve_for_run("ws", "x", "", []) == {}
    assert secrets.resolve_for_run("ws", "x", "", ["MISSING"]) == {}


def test_env_for_run_follows_the_agents_declared_list(key):
    from common import secrets
    _agent("holder", secrets=["GITHUB_TOKEN"])
    _agent("bystander")
    secrets.set_secret("ws", "GITHUB_TOKEN", "ghp_agentsown1234", agent_id="holder")
    secrets.set_secret("ws", "OTHER", "not-declared-by-anyone")
    assert secrets.env_for_run("ws", "holder", None) == {"GITHUB_TOKEN": "ghp_agentsown1234"}
    assert secrets.env_for_run("ws", "bystander", None) == {}
    assert secrets.env_for_run("ws", "nobody", None) == {}


def test_env_for_run_hands_out_nothing_on_error(key, monkeypatch):
    from common import secrets
    _agent("holder", secrets=["X"])
    secrets.set_secret("ws", "X", "value-long-enough")

    def boom(*a, **k):
        raise RuntimeError("database gone")
    monkeypatch.setattr(secrets, "resolve_for_run", boom)
    assert secrets.env_for_run("ws", "holder", None) == {}


def test_base_subprocess_env_merges_only_with_an_agent(key, monkeypatch):
    from common import secrets
    from common.subprocess_env import base_subprocess_env
    monkeypatch.delenv("DEPLOY_KEY", raising=False)
    _agent("holder", secrets=["DEPLOY_KEY"])
    _agent("plain")
    secrets.set_secret("ws", "DEPLOY_KEY", "deploy-key-value")
    assert "DEPLOY_KEY" not in base_subprocess_env("ws")  # old call shape unchanged
    assert base_subprocess_env("ws", agent_id="holder")["DEPLOY_KEY"] == "deploy-key-value"
    assert "DEPLOY_KEY" not in base_subprocess_env("ws", agent_id="plain")


def test_base_subprocess_env_resolves_for_the_launching_user(key, monkeypatch):
    from common import secrets
    from common.subprocess_env import base_subprocess_env
    _agent("holder", secrets=["TOKEN"])
    secrets.set_secret("ws", "TOKEN", "workspace-token")
    secrets.set_secret("ws", "TOKEN", "alices-own-token", user_id="alice")
    assert base_subprocess_env("ws", agent_id="holder", user_id="alice")["TOKEN"] == "alices-own-token"
    assert base_subprocess_env("ws", agent_id="holder", user_id="bob")["TOKEN"] == "workspace-token"


def test_in_process_get_uses_the_active_scope_and_allowlist(key):
    from common import secrets
    _agent("holder", secrets=["TOKEN"])
    secrets.set_secret("ws", "TOKEN", "workspace-token")
    secrets.set_secret("ws", "UNDECLARED", "never-for-holder")
    assert secrets.get("TOKEN") is None
    with secrets.activate("ws", "holder", None):
        assert secrets.get("TOKEN") == "workspace-token"
        assert secrets.get("UNDECLARED") is None
    assert secrets.get("TOKEN") is None


def test_agent_spec_round_trips_secrets():
    from agents.registry import AgentSpec, _validate_agent_dict
    spec = AgentSpec(id="a", name="A", type="local", entrypoint="m:f", secrets=["X", "Y"])
    assert spec.to_dict()["secrets"] == ["X", "Y"]
    assert _validate_agent_dict(spec.to_dict()).secrets == ["X", "Y"]
    assert "secrets" not in AgentSpec(id="a", name="A", type="local", entrypoint="m:f").to_dict()


# ── capability guard ─────────────────────────────────────────────────────────

def test_declared_secrets_count_as_reads_private():
    from tools.capabilities import READS_PRIVATE, capability_sources, check_combination, secret_grant_ids
    ids = ["fetch_url"] + secret_grant_ids(["GITHUB_TOKEN"])
    assert capability_sources(ids)[READS_PRIVATE] == ["secrets:GITHUB_TOKEN"]
    violation = check_combination(ids)
    assert violation is not None and violation.blocking
    assert "secrets:GITHUB_TOKEN" in violation.sources[READS_PRIVATE]
    blocked = check_combination(["fetch_url"])
    assert blocked is None or not blocked.blocking


def test_saving_an_agent_that_declares_a_secret_is_guarded(monkeypatch):
    from agents.capability_guard import CapabilityViolation
    from common.config import settings
    monkeypatch.setattr(settings, "capability_guard", "block", raising=False)
    _agent("reader", tools=["fetch_url"])
    with pytest.raises(CapabilityViolation, match="secrets:GITHUB_TOKEN"):
        _agent("reader", tools=["fetch_url"], secrets=["GITHUB_TOKEN"])


def test_a_delegates_secrets_are_reached_through_delegation():
    from tools.capabilities import READS_PRIVATE, effective_capability_sources
    _agent("keeper", secrets=["TOKEN"])
    sources = effective_capability_sources(
        "boss", ["run_agent_tool"], resolve_agent_tools=lambda aid: [],
        root_delegates=["keeper"])
    assert any("secrets:TOKEN" in label for label in sources.get(READS_PRIVATE, []))


# ── git: the run's own token wins ────────────────────────────────────────────

def test_git_token_prefers_the_runs_environment(monkeypatch):
    from connectors.git import store
    store.set_token("github", "connector-token")
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "shell-token")
    # Outside a run, a shell export does not replace the configured token.
    assert store.get_token("github") == "connector-token"
    monkeypatch.setenv("AGENT_WORKSPACE", "ws")
    assert store.get_token("github") == "shell-token"
    monkeypatch.delenv("GITHUB_TOKEN")
    assert store.get_token("github") == "connector-token"


def test_git_token_prefers_the_active_in_process_secret(key, monkeypatch):
    from common import secrets
    from connectors.git import store
    monkeypatch.delenv("AGENT_WORKSPACE", raising=False)
    store.set_token("gitlab", "connector-token")
    _agent("publisher", secrets=["GITLAB_TOKEN"])
    secrets.set_secret("ws", "GITLAB_TOKEN", "glpat-agents-own", agent_id="publisher")
    with secrets.activate("ws", "publisher", None):
        assert store.get_token("gitlab") == "glpat-agents-own"
    assert store.get_token("gitlab") == "connector-token"


# ── vault ────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def test_vault_backend_keeps_the_value_in_vault(monkeypatch):
    import requests
    from common import db, secrets
    vault: dict = {}
    calls: list = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(("POST", url, headers))
        vault[url] = json["data"]["value"]
        return _Resp(200)

    def get(url, headers=None, timeout=None):
        calls.append(("GET", url, headers))
        if url in vault:
            return _Resp(200, {"data": {"data": {"value": vault[url]}}})
        return _Resp(404)

    def delete(url, headers=None, timeout=None):
        calls.append(("DELETE", url, headers))
        vault.pop(url.replace("/metadata/", "/data/"), None)
        return _Resp(204)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(requests, "delete", delete)
    backend = secrets.VaultBackend(url="http://vault:8200", token="vt", mount="kv")
    backend.set("ws", "TOKEN", "vault-held-value", "a1", None)
    assert calls[0][1] == "http://vault:8200/v1/kv/data/agents-hub/ws/a1/_/TOKEN"
    assert calls[0][2] == {"X-Vault-Token": "vt"}
    row = db.get_conn().execute("SELECT ciphertext, hint FROM secrets").fetchone()
    assert row["ciphertext"] == "" and row["hint"] == "alue"
    assert backend.list("ws")[0]["agent_id"] == "a1"
    assert backend.resolve_for_run("ws", "a1", None, ["TOKEN"]) == {"TOKEN": "vault-held-value"}
    assert backend.delete("ws", "TOKEN", "a1", None) is True
    assert calls[-1][0] == "DELETE" and "/v1/kv/metadata/agents-hub/ws/a1/_/TOKEN" in calls[-1][1]
    assert vault == {}


def test_vault_backend_without_configuration_refuses_to_store():
    from common import secrets
    with pytest.raises(secrets.SecretsError, match="AGENTS_HUB_VAULT_URL"):
        secrets.VaultBackend(url="", token="").set("ws", "X", "value")


def test_backend_is_picked_by_settings(monkeypatch):
    from common import secrets
    from common.config import settings
    monkeypatch.setattr(settings, "secret_backend", "vault", raising=False)
    assert isinstance(secrets.backend(), secrets.VaultBackend)
    monkeypatch.setattr(settings, "secret_backend", "local", raising=False)
    assert isinstance(secrets.backend(), secrets.LocalBackend)


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _admin(client):
    r = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert r.status_code == 200, r.text
    return _bearer(r.json()["token"])


def _member(client, admin, username):
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    return created.json()["id"], _bearer(session.json()["token"])


def test_routes_store_list_and_delete_without_ever_returning_a_value(single, key, client):
    client.post("/api/workspaces", json={"name": "alpha"})
    put = client.put("/api/workspaces/alpha/secrets/API_KEY", json={"value": "sk-very-secret-1"})
    assert put.status_code == 200, put.text
    assert "sk-very-secret-1" not in put.text
    listed = client.get("/api/workspaces/alpha/secrets")
    assert listed.status_code == 200
    assert [r["name"] for r in listed.json()] == ["API_KEY"]
    assert "sk-very-secret-1" not in listed.text
    assert client.delete("/api/workspaces/alpha/secrets/API_KEY").status_code == 200
    assert client.delete("/api/workspaces/alpha/secrets/API_KEY").status_code == 404
    from common import audit
    rows = audit.query(action="secret.set")
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert rows and "sk-very-secret-1" not in repr(rows)


def test_routes_validate_the_name_the_workspace_and_the_key(single, no_key, client):
    client.post("/api/workspaces", json={"name": "alpha"})
    bad = client.put("/api/workspaces/alpha/secrets/lower_case", json={"value": "v"})
    assert bad.status_code == 400
    missing = client.put("/api/workspaces/nope/secrets/X", json={"value": "v"})
    assert missing.status_code == 404
    nokey = client.put("/api/workspaces/alpha/secrets/X", json={"value": "v"})
    assert nokey.status_code == 400 and "AGENTS_HUB_SECRET_KEY" in nokey.json()["detail"]
    unknown_agent = client.put("/api/workspaces/alpha/secrets/X",
                               json={"value": "v", "agent_id": "no_such_agent"})
    assert unknown_agent.status_code == 400


def test_multi_mode_secrets_are_owner_only(multi, key, client):
    admin = _admin(client)
    client.post("/api/workspaces", json={"name": "alpha"}, headers=admin)
    bob_id, bob = _member(client, admin, "bob")
    carol_id, carol = _member(client, admin, "carol")
    client.put("/api/workspaces/alpha/members", json={"user_id": bob_id, "role": "viewer"},
               headers=admin)
    client.put("/api/workspaces/alpha/members", json={"user_id": carol_id, "role": "owner"},
               headers=admin)
    assert client.get("/api/workspaces/alpha/secrets", headers=bob).status_code == 403
    assert client.put("/api/workspaces/alpha/secrets/X", json={"value": "v"},
                      headers=bob).status_code == 403
    put = client.put("/api/workspaces/alpha/secrets/X",
                     json={"value": "owner-value-1", "user_id": carol_id}, headers=carol)
    assert put.status_code == 200, put.text
    listed = client.get("/api/workspaces/alpha/secrets", headers=carol)
    assert listed.status_code == 200
    assert listed.json()[0]["created_by"] == carol_id
    assert listed.json()[0]["user_id"] == carol_id


def test_agent_allowlist_route_validates_and_guards(single, client, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "capability_guard", "block", raising=False)
    _agent("worker")
    assert client.get("/api/agents/worker/secrets").json() == {"secrets": [],
                                                               "github_identity": "app"}
    assert client.put("/api/agents/worker/secrets", json={"secrets": ["bad name"]}).status_code == 400
    ok = client.put("/api/agents/worker/secrets", json={"secrets": ["TOKEN", "TOKEN"]})
    assert ok.status_code == 200 and ok.json()["secrets"] == ["TOKEN"]
    from agents.registry import get_agent
    assert get_agent("worker").secrets == ["TOKEN"]
    _agent("fetcher", tools=["fetch_url"])
    blocked = client.put("/api/agents/fetcher/secrets", json={"secrets": ["TOKEN"]})
    assert blocked.status_code == 400 and "secrets:TOKEN" in blocked.json()["detail"]
    assert client.get("/api/agents/nobody/secrets").status_code == 404


def test_multi_mode_agent_allowlist_needs_an_admin(multi, client):
    admin = _admin(client)
    _agent("worker")
    _, bob = _member(client, admin, "bob")
    assert client.put("/api/agents/worker/secrets", json={"secrets": ["T"]},
                      headers=bob).status_code == 403
    assert client.put("/api/agents/worker/secrets", json={"secrets": ["T"]},
                      headers=admin).status_code == 200


def test_a_flow_gets_its_agents_names_at_workspace_and_user_scope_only(key, monkeypatch):
    from common import secrets
    from flow import store as flow_store
    _agent("node_a", secrets=["SHARED"])
    _agent("node_b", secrets=["OWN"])
    secrets.set_secret("ws", "SHARED", "workspace-shared")
    secrets.set_secret("ws", "OWN", "b-only-value", agent_id="node_b")
    secrets.set_secret("ws", "UNDECLARED", "nobody-asked")
    flow = {"id": "f1", "nodes": [{"id": "n1", "data": {"agent_id": "node_a"}},
                                  {"id": "n2", "agent_id": "node_b"}]}
    monkeypatch.setattr(flow_store, "get_flow", lambda fid: flow if fid == "f1" else None)
    # OWN is agent-scoped to node_b, and a flow's one process cannot keep it
    # away from node_a, so it stays out.
    assert secrets.env_for_flow("ws", "f1", None) == {"SHARED": "workspace-shared"}
    assert secrets.env_for_flow("ws", "missing", None) == {}

