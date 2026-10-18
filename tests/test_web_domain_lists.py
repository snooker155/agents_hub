"""An agent's own ``allowed_domains`` / ``blocked_domains`` for the web tool
(agents/registry.py, tools/web.py), merged with the
workspace's and the global lists: workspace blocked always applies, the
agent's allowed narrows, a blocked host wins, subdomains match.

No network: fetch is refused before any request.
"""
from __future__ import annotations

import json

import pytest

from agents.registry import AgentSpec
from tools import web


@pytest.fixture
def lists(monkeypatch):
    """Global lists off, a workspace with settings the test fills, and the
    running agent's record the test sets."""
    from common.config import settings
    monkeypatch.setattr("common.config.read_dot_env", lambda: {})
    monkeypatch.setattr("common.config.live_setting", lambda key, default="": default)
    monkeypatch.setattr(settings, "web_deny_domains", ())
    monkeypatch.setattr(settings, "web_allow_domains", ())
    monkeypatch.setattr(settings, "web_domain_policy_enabled", False)
    monkeypatch.delenv("AGENTS_HUB_NETWORK", raising=False)
    monkeypatch.delenv("AGENTS_HUB_ALLOWED_HOSTS", raising=False)
    ws: dict = {}
    agent = {"spec": None}
    monkeypatch.setattr(web, "_workspace_web_settings", lambda workspace=None: ws)
    monkeypatch.setattr(web, "_current_agent_spec", lambda: agent["spec"])

    class _Lists:
        workspace = ws

        @staticmethod
        def agent(allowed=(), blocked=()):
            agent["spec"] = AgentSpec(id="webby", name="Webby", type="local", entrypoint="m:f",
                                      allowed_domains=list(allowed), blocked_domains=list(blocked))

    return _Lists


def test_the_spec_round_trips_and_cleans_the_lists():
    spec = AgentSpec(id="a", name="a", type="local", entrypoint="m:f",
                     allowed_domains=["docs.python.org"], blocked_domains=["evil.example"])
    d = spec.to_dict()
    assert d["allowed_domains"] == ["docs.python.org"]
    assert d["blocked_domains"] == ["evil.example"]
    assert "allowed_domains" not in AgentSpec(id="b", name="b", type="local", entrypoint="m:f").to_dict()


def test_spec_loader_normalises():
    from agents.registry import _validate_agent_dict
    spec = _validate_agent_dict({"id": "c", "name": "c", "type": "local", "entrypoint": "m:f",
                   "allowed_domains": ["HTTPS://Docs.Python.org/3/", ""],
                   "blocked_domains": [".Evil.Example"]})
    assert spec.allowed_domains == ["docs.python.org"]
    assert spec.blocked_domains == ["evil.example"]


def test_agent_blocked_adds_to_the_workspace_deny_list(lists):
    lists.workspace["web_deny_domains"] = ["ads.example"]
    lists.agent(blocked=["tracker.example"])
    ok, reason = web.check_domain_policy("https://cdn.tracker.example/x")
    assert not ok and "this agent's blocked_domains" in reason
    ok, reason = web.check_domain_policy("https://ads.example/")
    assert not ok and "deny list" in reason
    assert web.check_domain_policy("https://example.org/")[0]


def test_agent_allowed_narrows(lists):
    lists.agent(allowed=["python.org"])
    assert web.check_domain_policy("https://docs.python.org/3/")[0]
    ok, reason = web.check_domain_policy("https://example.org/")
    assert not ok and "allowed_domains" in reason


def test_agent_allowed_cannot_widen_the_workspace_allowlist(lists):
    lists.workspace.update({"web_domain_policy_enabled": True, "web_allow_domains": ["docs.python.org"]})
    lists.agent(allowed=["python.org", "example.org"])
    assert web.check_domain_policy("https://docs.python.org/3/")[0]
    ok, reason = web.check_domain_policy("https://example.org/")
    assert not ok and "domain allowlist" in reason
    ok, _ = web.check_domain_policy("https://www.python.org/")
    assert not ok


def test_blocked_wins_over_allowed(lists):
    lists.workspace["web_deny_domains"] = ["internal.python.org"]
    lists.agent(allowed=["python.org"], blocked=["wiki.python.org"])
    assert web.check_domain_policy("https://docs.python.org/")[0]
    assert not web.check_domain_policy("https://wiki.python.org/")[0]
    assert not web.check_domain_policy("https://x.internal.python.org/")[0]


def test_subdomain_matching_is_not_a_suffix_match(lists):
    lists.agent(blocked=["example.com"])
    assert not web.check_domain_policy("https://a.example.com/")[0]
    assert web.check_domain_policy("https://notexample.com/")[0]


def test_fetch_url_refuses_with_a_clear_tool_result(lists):
    lists.agent(blocked=["evil.example"])
    out = web.fetch_url.invoke({"url": "https://evil.example/page"})
    assert "fetch_url refused" in out and "blocked_domains" in out


# ── the route ────────────────────────────────────────────────────────────────

def test_route_reads_and_writes_the_lists(monkeypatch):
    from fastapi.testclient import TestClient

    from agents.registry import add_agent, get_agent
    from dashboard.backend.main import app
    add_agent(AgentSpec(id="web_route_agent", name="W", type="local", entrypoint="m:f"),
              user_edit=False)
    client = TestClient(app)
    r = client.put("/api/agents/web_route_agent/web-domains",
                   json={"allowed_domains": ["Docs.Python.org"], "blocked_domains": ["evil.example"]})
    assert r.status_code == 200, r.text
    assert r.json() == {"allowed_domains": ["docs.python.org"], "blocked_domains": ["evil.example"]}
    assert get_agent("web_route_agent").blocked_domains == ["evil.example"]
    got = client.get("/api/agents/web_route_agent/web-domains", params={"workspace": "default"})
    assert got.status_code == 200
    assert "evil.example" in got.json()["effective"]["blocked"]
    bad = client.put("/api/agents/web_route_agent/web-domains",
                     json={"blocked_domains": ["https://x.org/path"]})
    assert bad.status_code == 400
    assert client.get("/api/agents/nope/web-domains").status_code == 404
    assert json.dumps(r.json())
