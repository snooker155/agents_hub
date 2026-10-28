"""
Who reaches what in a multi-user hub, and what a workspace's model policy allows.

- Records named by id (common/record_access.py): an agent, flow, team or loop
  of another workspace is refused whatever workspace the request names; a
  system agent is readable by everyone and changed only by an administrator.
- The administrator's writes (common/auth.py ``admin_only``): the model
  catalog, the settings and a workspace's model policy.
- The model policy (workspace/model_policy.py): the default, the header's
  choice, an agent's model, a run's model switch, and the build itself.
- The sidebar rows hidden from members (common/member_menu.py).

Follows the fixture shape of tests/test_record_access.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

PASSWORD = "hunter2-but-longer"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


@pytest.fixture
def multi(monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "auth_mode", "multi", raising=False)
    monkeypatch.setattr(settings, "api_token", "", raising=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin(client) -> dict:
    response = client.post("/api/auth/bootstrap", json={"username": "root", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["token"])


def _member(client, admin, username: str):
    created = client.post("/api/auth/users", json={"username": username, "password": PASSWORD},
                          headers=admin)
    assert created.status_code == 200, created.text
    session = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert session.status_code == 200, session.text
    return created.json()["id"], _bearer(session.json()["token"])


def _two_workspaces(client, admin):
    """w1 and w2, alice an editor of w1 only, bob an editor of w2 only."""
    for name in ("w1", "w2"):
        assert client.post("/api/workspaces", json={"name": name}, headers=admin).status_code == 200
    alice_id, alice = _member(client, admin, "alice")
    bob_id, bob = _member(client, admin, "bob")
    for ws, uid in (("w1", alice_id), ("w2", bob_id)):
        assert client.put(f"/api/workspaces/{ws}/members", json={"user_id": uid, "role": "editor"},
                          headers=admin).status_code == 200
    return alice, bob


def _agent(agent_id: str, **fields):
    from agents.registry import AgentSpec, add_agent
    add_agent(AgentSpec(id=agent_id, name=agent_id, type="langchain",
                        entrypoint="agents.standard_agent:StandardAgent", tools=[], **fields))


# ── the decision itself ──────────────────────────────────────────────────────

def test_target_names_the_record_and_skips_route_words():
    from common.record_access import target
    assert target("/api/agents/x/memory") == ("agent", "x", ["memory"])
    assert target("/api/agents/create") is None
    assert target("/api/agents") is None
    assert target("/api/flows/import") is None
    assert target("/api/flows/runs/r1/resume") == ("flow_run", "r1", ["resume"])
    assert target("/api/teams/runs/r2") == ("team_run", "r2", [])
    assert target("/api/loops/l1/run") == ("loop", "l1", ["run"])
    assert target("/api/workspaces/w1") is None


def test_decide_rules():
    from common.record_access import Record, decide
    roles = {"w1": "editor", "w2": "viewer"}
    role_in = roles.get
    own = Record(workspace="w1")
    other = Record(workspace="w3")
    system = Record(workspace="default", everywhere=True, system=True)
    copy = Record(workspace="w1", system_definition=True)

    def run(method, record, rest=(), kind="agent", named=None):
        return decide(method=method, kind=kind, rest=list(rest), record=record,
                      named_workspace=named, role_in=role_in)

    assert run("GET", own) is None and run("PUT", own) is None
    assert run("GET", other) and run("PUT", other)
    assert run("PUT", Record(workspace="w2"))                 # a viewer does not write
    assert run("GET", system) is None
    assert "administrator" in run("PUT", system)
    # Per-workspace sub-paths are checked against the named workspace.
    assert run("POST", system, ["set-default-chat"], named="w1") is None
    assert run("POST", system, ["set-default-chat"], named="w3")
    # A copy sharing a system agent's prompt: its prompt is the administrator's.
    assert run("PUT", copy, ["definition"])
    assert run("PUT", copy, ["description"]) is None


# ── records by id ────────────────────────────────────────────────────────────

def test_another_workspaces_agent_is_refused_whatever_workspace_is_named(multi, client):
    admin = _admin(client)
    alice, bob = _two_workspaces(client, admin)
    _agent("bob_agent", owner_workspace="w2")

    assert client.get("/api/agents/bob_agent", headers=bob, params={"workspace": "w2"}).status_code == 200
    for params in ({}, {"workspace": "w1"}):
        resp = client.get("/api/agents/bob_agent", headers=alice, params=params)
        assert resp.status_code == 403, resp.text
    resp = client.put("/api/agents/bob_agent/description", json={"description": "x"},
                      headers=alice, params={"workspace": "w1"})
    assert resp.status_code == 403
    assert client.get("/api/agents/bob_agent", headers=admin).status_code == 200


def test_a_system_agent_is_read_by_members_and_changed_by_the_administrator(multi, client):
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    _agent("sys_helper", system=True)

    assert client.get("/api/agents/sys_helper", headers=alice,
                      params={"workspace": "w1"}).status_code == 200
    refused = client.put("/api/agents/sys_helper/description", json={"description": "mine now"},
                         headers=alice, params={"workspace": "w1"})
    assert refused.status_code == 403
    assert "administrator" in refused.json()["detail"]
    assert client.put("/api/agents/sys_helper/description", json={"description": "ok"},
                      headers=admin).status_code == 200


def test_flows_of_another_workspace_are_not_listed_read_or_created(multi, client):
    admin = _admin(client)
    alice, bob = _two_workspaces(client, admin)
    created = client.post("/api/flows", json={"name": "bobs", "workspace": "w2"}, headers=bob)
    assert created.status_code == 200, created.text
    flow_id = created.json()["id"]

    assert client.get(f"/api/flows/{flow_id}", headers=alice).status_code == 403
    assert client.get(f"/api/flows/{flow_id}", headers=alice,
                      params={"workspace": "w1"}).status_code == 403
    listed = client.get("/api/flows", headers=alice).json()
    assert flow_id not in {f["id"] for f in listed}
    assert client.post("/api/flows", json={"name": "sneaky", "workspace": "w2"},
                       headers=alice).status_code == 403
    assert client.get(f"/api/flows/{flow_id}", headers=bob).status_code == 200


def test_a_chat_turn_is_refused_against_another_workspaces_agent(multi, client):
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    _agent("bob_agent2", owner_workspace="w2")
    resp = client.post("/api/chat/message", headers=alice,
                       json={"message": "hi", "agent_id": "bob_agent2", "workspace": "w1"})
    assert resp.status_code == 403
    resp = client.post("/api/chat/message", headers=alice,
                       json={"message": "hi", "agent_id": "bob_agent2", "workspace": "w2"})
    assert resp.status_code == 403


# ── the administrator's writes ───────────────────────────────────────────────

def test_models_and_settings_are_written_by_the_administrator_only(multi, client):
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    for method, path, body in (("put", "/api/models", {}),
                               ("post", "/api/models/discover/openai", None),
                               ("put", "/api/settings", {}),
                               ("put", "/api/workspaces/w1/model-policy", {"models": []})):
        resp = getattr(client, method)(path, headers=alice, **({"json": body} if body is not None else {}))
        assert resp.status_code == 403, (path, resp.text)
        assert resp.json()["detail"] == "Administrator access required"
    # Reads stay open, and so does the browser's own workspace selection.
    assert client.get("/api/models", headers=alice).status_code == 200
    assert client.put("/api/settings/workspace", json={"workspace": "w1"},
                      headers=alice).status_code == 200


def test_auth_admin_only_matrix():
    from common.auth import admin_only
    assert admin_only("PUT", "/api/models")
    assert admin_only("POST", "/api/models/local/runtime/load")
    assert not admin_only("POST", "/api/models/local/runtime/voices")
    assert not admin_only("GET", "/api/models")
    assert not admin_only("PUT", "/api/settings/workspace")
    assert admin_only("PUT", "/api/workspaces/w1/model-policy")
    assert not admin_only("GET", "/api/workspaces/w1/model-policy")
    assert not admin_only("PUT", "/api/workspaces/w1/model")


# ── the model policy ─────────────────────────────────────────────────────────

def test_policy_normalizes_and_one_model_is_the_default():
    from workspace.model_policy import from_metadata, normalize
    stored = normalize({"models": ["openai/gpt-a"], "temperature": "0.3", "effort": "low"})
    assert stored["default"] == {"provider": "openai", "model": "gpt-a"}
    policy = from_metadata({"model_policy": stored})
    assert policy.allows("openai", "gpt-a") and not policy.allows("openai", "gpt-b")
    assert policy.clamp("anthropic", "claude-x") == ("openai", "gpt-a")
    assert policy.temperature == 0.3 and policy.effort == "low"
    assert normalize({"models": []}) is None
    with pytest.raises(ValueError):
        normalize({"models": ["openai/gpt-a"], "default": "openai/gpt-b"})
    with pytest.raises(ValueError):
        normalize({"models": ["openai/gpt-a"], "temperature": 5})


def test_policy_is_the_workspace_default_and_fixes_the_effort():
    from workspace import get_workspace_default_model_config, get_workspace_model_effort
    meta = {"model_default": {"provider": "anthropic", "model": "claude-x"}, "model_effort": "high",
            "model_policy": {"models": [{"provider": "openai", "model": "gpt-a"}], "effort": "low"}}
    assert get_workspace_default_model_config(meta) == {"provider": "openai", "model": "gpt-a"}
    assert get_workspace_model_effort(meta) == "low"


def test_the_policy_limits_the_header_the_default_and_an_agents_model(multi, client, monkeypatch):
    from routes import workspaces as ws_routes
    monkeypatch.setattr(ws_routes, "_enabled_model_ids", lambda: None)
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    resp = client.put("/api/workspaces/w1/model-policy", headers=admin,
                      json={"models": ["openai/gpt-a", "openai/gpt-b"], "default": "openai/gpt-b",
                            "temperature": 0.1})
    assert resp.status_code == 200, resp.text

    state = client.get("/api/workspaces/w1/model", headers=alice).json()
    assert state["workspace_default"]["model"] == "gpt-b"
    assert [m["model"] for m in state["policy"]["models"]] == ["gpt-a", "gpt-b"]

    assert client.put("/api/workspaces/w1/model", headers=alice,
                      json={"provider": "anthropic", "model": "claude-x"}).status_code == 400
    assert client.put("/api/workspaces/w1/model", headers=alice,
                      json={"provider": "openai", "model": "gpt-a"}).status_code == 200
    assert client.put("/api/workspaces/w1/default-model", headers=admin,
                      json={"provider": "openai", "model": "gpt-a"}).status_code == 400

    _agent("alice_agent", owner_workspace="w1")
    assert client.post("/api/agents/alice_agent/model", headers=alice, params={"workspace": "w1"},
                       json={"provider": "anthropic", "model": "claude-x"}).status_code == 400
    assert client.post("/api/agents/alice_agent/model", headers=alice, params={"workspace": "w1"},
                       json={"provider": "openai", "model": "gpt-b"}).status_code == 200

    # Clearing the policy hands the choice back.
    assert client.put("/api/workspaces/w1/model-policy", headers=admin,
                      json={"models": []}).json() == {"policy": None}


def test_catalog_picks_follow_the_policy(monkeypatch):
    from tools import delegation
    catalog = [{"id": "openai/gpt-a", "provider": "openai", "model": "gpt-a", "context_window": 0},
               {"id": "openai/gpt-b", "provider": "openai", "model": "gpt-b", "context_window": 0}]
    monkeypatch.setattr(delegation, "_enabled_catalog_models", lambda: list(catalog))
    from workspace import model_policy
    policy = model_policy.from_metadata({"model_policy": {"models": ["openai/gpt-a"]}})
    monkeypatch.setattr(model_policy, "for_workspace", lambda ws: policy if ws == "w1" else None)

    assert [m["id"] for m in delegation.enabled_models("w1")] == ["openai/gpt-a"]
    assert len(delegation.enabled_models()) == 2
    assert delegation.resolve_model("openai/gpt-a", workspace="w1") == ("openai", "gpt-a")
    with pytest.raises(ValueError):
        delegation.resolve_model("openai/gpt-b", workspace="w1")
    assert delegation.resolve_model("openai/gpt-b", workspace=None) == ("openai", "gpt-b")


class _FakeStandardAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")
        self.tools = kwargs.get("tools")
        self.spec = kwargs.get("spec")


def test_a_build_outside_the_policy_runs_on_its_default(tmp_path, monkeypatch):
    from agents import agent_cache, agent_factory, prompt_assembly
    from agents.registry import replace_all_raw
    from workspace import model_policy
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    monkeypatch.setattr(agent_factory, "StandardAgent", _FakeStandardAgent)
    replace_all_raw([])
    agent_cache.invalidate()
    try:
        _agent("pinned", provider="anthropic", model="claude-x", temperature=0.9, api_key="sk-own")
        prompt_assembly.write_instructions("pinned", "Base prompt")
        policy = model_policy.from_metadata({"model_policy": {
            "models": ["openai/gpt-a"], "temperature": 0.1, "effort": "low", "max_tokens": 99}})
        monkeypatch.setattr(model_policy, "for_workspace", lambda ws: policy if ws == "w1" else None)
        factory = agent_factory.AgentFactory(definitions_dir=str(defs))

        built = factory._build_agent("pinned", workspace="w1")
        assert (built.provider, built.model) == ("openai", "gpt-a")
        assert built.kwargs["temperature"] == 0.1 and built.kwargs["max_tokens"] == 99
        assert built.kwargs["thinking_level"] == "low"
        assert built.kwargs["api_key"] != "sk-own"

        free = factory._build_agent("pinned", workspace="w2")
        assert (free.provider, free.model) == ("anthropic", "claude-x")
        assert free.kwargs["temperature"] == 0.9
    finally:
        agent_cache.invalidate()
        replace_all_raw([])


# ── the sidebar ──────────────────────────────────────────────────────────────

def test_members_get_the_hidden_rows_and_the_administrator_sets_them(multi, client):
    from common import member_menu
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    try:
        assert client.get("/api/auth/me", headers=admin).json()["hidden_menu"] == []
        assert client.get("/api/auth/me", headers=alice).json()["hidden_menu"] == list(
            member_menu.DEFAULT_HIDDEN)
        assert client.put("/api/settings/member-menu", json={"hidden": ["/costs"]},
                          headers=alice).status_code == 403
        saved = client.put("/api/settings/member-menu", json={"hidden": ["/costs", "/chat", "bad"]},
                           headers=admin)
        assert saved.json()["hidden"] == ["/costs"]
        assert client.get("/api/auth/me", headers=alice).json()["hidden_menu"] == ["/costs"]
    finally:
        member_menu.set_hidden(None)


def test_a_personal_workspace_follows_defaults_policy_and_special_models_lock(multi, client, monkeypatch):
    from routes import workspaces as ws_routes
    monkeypatch.setattr(ws_routes, "_enabled_model_ids", lambda: None)
    admin = _admin(client)
    alice, _bob = _two_workspaces(client, admin)
    personal = client.get("/api/auth/me", headers=alice).json()["personal_workspace"]
    assert personal

    # No policy yet: the member sets the special models of their own workspace.
    assert client.put(f"/api/workspaces/{personal}/special-models", headers=alice,
                      json={"special_models": {}}).status_code == 200

    assert client.put("/api/workspaces/default/model-policy", headers=admin,
                      json={"models": ["openai/gpt-a"]}).status_code == 200
    state = client.get(f"/api/workspaces/{personal}/model", headers=alice).json()
    assert state["policy"]["inherited_from"] == "default"
    assert state["workspace_default"]["model"] == "gpt-a"
    assert client.put(f"/api/workspaces/{personal}/model", headers=alice,
                      json={"provider": "anthropic", "model": "claude-x"}).status_code == 400
    assert client.put(f"/api/workspaces/{personal}/default-model", headers=alice,
                      json={"provider": "anthropic", "model": "claude-x"}).status_code == 400

    # The policy locks special models by default: members read, the administrator writes.
    payload = client.get(f"/api/workspaces/{personal}/special-models", headers=alice).json()
    assert payload["locked"] is True
    refused = client.put(f"/api/workspaces/{personal}/special-models", headers=alice,
                         json={"special_models": {}})
    assert refused.status_code == 403
    assert client.put(f"/api/workspaces/{personal}/special-models", headers=admin,
                      json={"special_models": {}}).status_code == 200

    # Switched off, members set them again.
    assert client.put("/api/workspaces/default/model-policy", headers=admin,
                      json={"models": ["openai/gpt-a"], "lock_special_models": False}).status_code == 200
    assert client.put(f"/api/workspaces/{personal}/special-models", headers=alice,
                      json={"special_models": {}}).status_code == 200
    # A policy of its own beats the inherited one.
    assert client.put(f"/api/workspaces/{personal}/model-policy", headers=admin,
                      json={"models": ["openai/gpt-b"]}).status_code == 200
    own = client.get(f"/api/workspaces/{personal}/model", headers=alice).json()["policy"]
    assert own["inherited_from"] is None and own["default"]["model"] == "gpt-b"
