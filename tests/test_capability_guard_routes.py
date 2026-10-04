"""
The capability guard's operator switches (agents/capability_guard.py,
dashboard/backend/routes/agents.py, routes/settings.py):

* POST /agents/{id}/delegates answers a blocked delegation with a 409 that
  carries the structured violation, the way /tools already did, instead of a
  bare 500 the page could not show.
* POST /agents/{id}/capability-override lifts the block for one agent: the
  same delegation and the same tools are then saved and returned with a
  ``capability_warning``. Turning it back off never fails.
* ``guard_mode`` and ``override_requires_container`` are resolved live from
  .env, so the Settings page switch (PUT /settings capability_guard) applies
  without a restart; the PUT rejects an unknown mode.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from agents.registry import AgentSpec, add_agent, get_agent, replace_all_raw

PRIVATE_AND_OUT = ["read_file", "notify_user", "run_agent_tool"]
UNTRUSTED = ["web_search", "fetch_url"]


def _spec(agent_id: str, tools, **extra) -> AgentSpec:
    return AgentSpec(
        id=agent_id, name=agent_id, description="", type="langchain",
        entrypoint="agents.agent_launcher:run", tools=list(tools), **extra,
    )


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch):
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    from common.config import settings
    monkeypatch.setattr(settings, "capability_guard", "block")
    monkeypatch.setattr(settings, "capability_override_requires_container", True)
    monkeypatch.setattr(settings, "agent_mode", "local")
    replace_all_raw([])
    add_agent(_spec("helper", ["calculator"]), user_edit=False)
    add_agent(_spec("searcher", list(UNTRUSTED)), user_edit=False)
    # An explicit allowlist: an unrestricted delegator would already reach the
    # searcher, and the change under test would be grandfathered.
    add_agent(_spec("main", list(PRIVATE_AND_OUT), delegates=["helper"]), user_edit=False)
    yield
    replace_all_raw([])


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agents as agent_routes

    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def test_blocked_delegation_is_a_409_with_the_violation(client):
    r = client.post("/api/agents/main/delegates", json={"delegates": ["searcher"]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "capability_violation"
    assert detail["rule_id"] == "lethal_trifecta_via_delegation"
    assert detail["guard_mode"] == "block"
    assert any("searcher" in src for src in detail["sources"]["ingests_untrusted"])
    assert get_agent("main").delegates == ["helper"]


def test_override_lets_the_same_delegation_through_as_a_warning(client):
    r = client.post("/api/agents/main/capability-override", json={"capability_override": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability_override"] is True
    # Block mode, container requirement on, local execution: saved, but a
    # build would still refuse it, and the page must say so.
    assert body["honoured_at_build"] is False
    assert body["capability_warning"] is None  # nothing formed yet

    r = client.post("/api/agents/main/delegates", json={"delegates": ["searcher"]})
    assert r.status_code == 200, r.text
    warning = r.json()["capability_warning"]
    assert warning["rule_id"] == "lethal_trifecta_via_delegation"
    assert get_agent("main").delegates == ["searcher"]

    # The same tools save is allowed too, and keeps reporting the exposure.
    r = client.post("/api/agents/main/tools", json={"tools": PRIVATE_AND_OUT + ["fetch_url"]})
    assert r.status_code == 200, r.text
    assert r.json()["capability_warning"]["rule_id"] == "lethal_trifecta"


def test_override_off_again_keeps_the_record_and_refuses_new_reach(client):
    client.post("/api/agents/main/capability-override", json={"capability_override": True})
    assert client.post("/api/agents/main/delegates", json={"delegates": ["searcher"]}).status_code == 200
    r = client.post("/api/agents/main/capability-override", json={"capability_override": False})
    assert r.status_code == 200, r.text  # grandfathered, not refused
    assert get_agent("main").delegates == ["searcher"]
    assert r.json()["capability_warning"]["rule_id"] == "lethal_trifecta_via_delegation"
    # ...but widening the same rule is not what grandfathering covers when the
    # rule is different: adding the ingest tool directly forms the plain
    # trifecta, a different rule id, and is refused again.
    r = client.post("/api/agents/main/tools", json={"tools": PRIVATE_AND_OUT + ["fetch_url"]})
    assert r.status_code == 409


def test_system_workspace_agent_cannot_take_the_override(client, monkeypatch):
    from common.system_workspace import WORKSPACE
    add_agent(_spec("sys", ["read_file"], owner_workspace=WORKSPACE), user_edit=False)
    r = client.post("/api/agents/sys/capability-override", json={"capability_override": True})
    assert r.status_code == 400


def test_guard_mode_follows_the_env_file_live(monkeypatch):
    from agents.capability_guard import guard_mode, override_requires_container
    assert guard_mode() == "block"
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"CAPABILITY_GUARD": "warn"})
    assert guard_mode() == "warn"
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"CAPABILITY_GUARD": "nonsense"})
    assert guard_mode() == "block"
    assert override_requires_container() is True
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"CAPABILITY_OVERRIDE_REQUIRES_CONTAINER": "false"})
    assert override_requires_container() is False


def test_warn_mode_saves_the_delegation_and_returns_the_warning(client, monkeypatch):
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"CAPABILITY_GUARD": "warn"})
    r = client.post("/api/agents/main/delegates", json={"delegates": ["searcher"]})
    assert r.status_code == 200, r.text
    assert r.json()["capability_warning"]["rule_id"] == "lethal_trifecta_via_delegation"
    # And in warn mode the override is honoured at build time everywhere.
    assert client.get("/api/agents/main/capability-override").json()["honoured_at_build"] is True


def test_settings_put_validates_and_writes_the_mode(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import settings as settings_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "_ENV_FILE", env_file)
    app = FastAPI()
    app.include_router(settings_routes.router)
    c = TestClient(app)
    assert c.put("/api/settings", json={"capability_guard": "loud"}).status_code == 400
    r = c.put("/api/settings", json={"capability_guard": "WARN", "capability_override_requires_container": False})
    assert r.status_code == 200, r.text
    text = env_file.read_text()
    assert 'CAPABILITY_GUARD="warn"' in text
    assert 'CAPABILITY_OVERRIDE_REQUIRES_CONTAINER="false"' in text


def test_settings_put_validates_the_web_search_provider(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import settings as settings_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "_ENV_FILE", env_file)
    app = FastAPI()
    app.include_router(settings_routes.router)
    c = TestClient(app)
    assert c.put("/api/settings", json={"web_search_provider": "google"}).status_code == 400
    assert c.put("/api/settings", json={"web_search_max_results": 50}).status_code == 400
    r = c.put("/api/settings", json={"web_search_provider": "Tavily", "web_search_api_key": "k1", "web_search_max_results": 8})
    assert r.status_code == 200, r.text
    text = env_file.read_text()
    assert 'WEB_SEARCH_PROVIDER="tavily"' in text and 'WEB_SEARCH_API_KEY="k1"' in text


def test_web_search_config_follows_the_env_file_live(monkeypatch):
    from tools.web import _search_config, _search_max_results
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"WEB_SEARCH_PROVIDER": "Exa", "WEB_SEARCH_API_KEY": " k ", "WEB_SEARCH_MAX_RESULTS": "7"})
    assert _search_config() == ("exa", "k")
    assert _search_max_results() == 7


def test_settings_put_writes_fetch_limits_and_domain_lists(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import settings as settings_routes

    env_file = tmp_path / ".env"
    monkeypatch.setattr(settings_routes, "_ENV_FILE", env_file)
    app = FastAPI()
    app.include_router(settings_routes.router)
    c = TestClient(app)
    assert c.put("/api/settings", json={"web_fetch_max_chars": 10}).status_code == 400
    assert c.put("/api/settings", json={"web_allow_domains": ["http://x.com/a"]}).status_code == 400
    r = c.put("/api/settings", json={
        "web_fetch_max_chars": 5000, "web_fetch_timeout": 7.5, "web_fetch_max_redirects": 2,
        "web_domain_policy_enabled": True, "web_allow_domains": [" Wikipedia.org ", "arxiv.org", "wikipedia.org"],
        "web_deny_domains": [],
    })
    assert r.status_code == 200, r.text
    text = env_file.read_text()
    assert 'WEB_FETCH_MAX_CHARS="5000"' in text and 'WEB_FETCH_TIMEOUT="7.5"' in text
    assert 'WEB_DOMAIN_POLICY_ENABLED="true"' in text
    assert 'WEB_ALLOW_DOMAINS="[\\"wikipedia.org\\", \\"arxiv.org\\"]"' in text
    assert 'WEB_DENY_DOMAINS="[]"' in text
    # ...and the same file reads back through the live helpers.
    from common.dotenv import read_env
    monkeypatch.setattr("common.config.read_dot_env", lambda: read_env(env_file))
    from tools.web import _fetch_limits, _live_bool, _live_list
    assert _fetch_limits() == (5000, 7.5, 2)
    assert _live_bool("WEB_DOMAIN_POLICY_ENABLED", False) is True
    assert _live_list("WEB_ALLOW_DOMAINS", ()) == ("wikipedia.org", "arxiv.org")


def test_live_list_accepts_a_comma_separated_line(monkeypatch):
    from tools.web import _live_list
    monkeypatch.setattr("common.config.read_dot_env", lambda: {"WEB_DENY_DOMAINS": "evil.test, bad.example"})
    assert _live_list("WEB_DENY_DOMAINS", ()) == ("evil.test", "bad.example")


def test_workspace_web_policy_route_reads_and_writes_the_lists(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import workspaces as ws_routes

    store: dict = {"settings": {"require_tool_approval": True}}
    monkeypatch.setattr(ws_routes, "get_workspace_metadata", lambda name: store)
    monkeypatch.setattr(ws_routes, "update_workspace_metadata", lambda name, patch: store.update(patch))
    monkeypatch.setattr(ws_routes, "_ensure_writable_workspace", lambda name: None)
    monkeypatch.setattr(ws_routes.audit, "record", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(ws_routes.router)
    c = TestClient(app)

    r = c.get("/api/workspaces/dev/web-policy")
    assert r.status_code == 200, r.text
    assert r.json()["policy"] == {"enabled": False, "allow_domains": [], "deny_domains": []}
    assert set(r.json()["global"]) == {"enabled", "allow_domains", "deny_domains"}

    assert c.put("/api/workspaces/dev/web-policy", json={"allow_domains": ["https://x.com"]}).status_code == 400
    r = c.put("/api/workspaces/dev/web-policy", json={"enabled": True, "allow_domains": [" Wikipedia.org ", "wikipedia.org"], "deny_domains": ["evil.test"]})
    assert r.status_code == 200, r.text
    assert r.json()["policy"] == {"enabled": True, "allow_domains": ["wikipedia.org"], "deny_domains": ["evil.test"]}
    # The other keys of the settings block are untouched.
    assert store["settings"]["require_tool_approval"] is True
    # ...and tools/web.py honours the workspace list over the global one.
    from tools import web
    monkeypatch.setattr(web, "_workspace_web_settings", lambda workspace=None: store["settings"])
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    assert web.check_domain_policy("https://en.wikipedia.org/x")[0] is True
    assert web.check_domain_policy("https://evil.test/")[0] is False
    assert web.check_domain_policy("https://example.org/")[0] is False
