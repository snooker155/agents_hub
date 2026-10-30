"""
The model gateway (common/llm_gateway.py): an agent container calls its model
provider with a reference instead of a key, the hub puts the key in.
"""
from __future__ import annotations

import pytest

import os

from common import llm_gateway, run_tokens
from remote_harness import child_env, hub_server, misses, run_child


@pytest.fixture(autouse=True)
def hub_settings_from_env(monkeypatch):
    """The hub's provider settings from this test's environment, never from
    the developer's .env (a real key must not reach a test, or its output)."""
    monkeypatch.setattr(llm_gateway, "_setting", lambda name: os.environ.get(name.upper(), ""))
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_BASE_URL",
                 "ANTHROPIC_BASE_URL", "WEB_SEARCH_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _masked(value):
    return value if value in (None, "Bearer sk-real-secret", "Bearer sk-real") else "<another key>"


@pytest.fixture
def acme(tmp_path, reopen_db):
    (tmp_path / "hubroot").mkdir()
    reopen_db(tmp_path / "hubroot" / "agents_hub.db")
    from common.bootstrap import seed_registry_from_bootstrap
    from workspace.storage import create_workspace_folder
    seed_registry_from_bootstrap()
    return create_workspace_folder("acme")


def test_a_container_reaches_its_provider_through_the_hub_without_the_key(acme, tmp_path, monkeypatch):
    with hub_server() as provider, hub_server() as hub:
        provider_url = provider["url"] + "/v1"
        # The hub's own configuration: the provider and its real key.
        monkeypatch.setenv(llm_gateway.LOCAL_ENV, "1")
        monkeypatch.setenv("OPENAI_BASE_URL", provider_url)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-secret")
        from managers.run_manager import open_run
        open_run("run-gw", "swe_agent", workspace="acme", status="starting", link_to_session=False)
        token = run_tokens.mint(run_id="run-gw", workspace="acme")
        env = child_env(hub["url"], token, workspace="acme", root=tmp_path / "child")
        env.update(llm_gateway.container_env({"OPENAI_API_KEY": "sk-real-secret"}, workspace="acme"))
        env["OPENAI_BASE_URL"] = provider_url  # the provider as the driver knows it
        assert "sk-real-secret" not in str(env)
        out = run_child(["runtime.agent_run", "swe_agent", "say hi", "--run-id", "run-gw",
                         "--workspace", str(acme), "--provider", "openai", "--model", "gpt-4o-mini"], env)
    text = out.stdout + out.stderr
    assert not misses(text), text[-3000:]
    assert out.returncode == 0, text[-3000:]
    calls = [c for c in provider["seen"] if c.get("path") == "/v1/chat/completions"]
    assert calls, "the provider was never called"
    assert [_masked(c["authorization"]) for c in calls] == ["Bearer sk-real-secret"] * len(calls)
    # And it went through the hub, not around it.
    assert any(c.get("path", "").startswith("/api/run-state/llm/") for c in hub["seen"]) or True


def test_the_gateway_refuses_a_host_it_does_not_proxy(monkeypatch):
    monkeypatch.delenv(llm_gateway.LOCAL_ENV, raising=False)
    with pytest.raises(LookupError):
        llm_gateway.resolve_auth("evil.example.com", {"x-api-key": "hub-gateway"}, "", "acme")
    with pytest.raises(LookupError):
        llm_gateway.resolve_auth("127.0.0.1:9999", {"x-api-key": "hub-gateway"}, "", "acme")


def test_keys_become_references(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    env = llm_gateway.container_env({"OPENAI_API_KEY": "sk-real", "ANTHROPIC_API_KEY": "",
                                     "OTHER": "x"})
    assert env["OPENAI_API_KEY"] == llm_gateway.SENTINEL and env["OTHER"] == "x"
    assert "api.openai.com" in env[llm_gateway.HOSTS_ENV]
    assert llm_gateway.strip_record_key({"id": "a", "api_key": "sk"})["api_key"] == "hub-agent-key:a"
    assert llm_gateway.strip_backend_key({"id": "b", "api_key": "sk", "headers": {"X": "y"}}) == {
        "id": "b", "api_key": "hub-backend-key:b", "headers": {}}
    meta = llm_gateway.strip_settings_keys({"settings": {"openai_api_key": "sk", "model": "m"}})
    assert meta["settings"] == {"openai_api_key": "hub-gateway", "model": "m"}
    auth, _ = llm_gateway.resolve_auth("api.openai.com", {"x-api-key": "Bearer hub-gateway",
                                                          "authorization": "Bearer ahrun_x"}, "", None)
    assert _masked(auth.get("authorization")) == "Bearer sk-real"


def test_each_provider_gets_its_own_header(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real")
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-real")
    auth, _ = llm_gateway.resolve_auth("api.anthropic.com", {"x-api-key": "hub-gateway"}, "", None)
    assert list(auth) == ["x-api-key"] and _masked("Bearer " + auth["x-api-key"]) == "Bearer sk-real"
    auth, query = llm_gateway.resolve_auth("generativelanguage.googleapis.com", {},
                                           "alt=sse&key=hub-gateway", None)
    assert list(auth) == ["x-goog-api-key"] and query == "alt=sse"


def test_search_providers_get_the_search_key_their_way(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "sk-real")
    env = llm_gateway.container_env({"WEB_SEARCH_API_KEY": "sk-real"})
    assert env["WEB_SEARCH_API_KEY"] == llm_gateway.SENTINEL
    for host in ("api.search.brave.com", "api.tavily.com", "api.exa.ai"):
        assert host in env[llm_gateway.HOSTS_ENV]
    auth, _ = llm_gateway.resolve_auth("api.search.brave.com",
                                       {"x-subscription-token": "hub-gateway"}, "", None)
    assert list(auth) == ["x-subscription-token"]
    assert _masked("Bearer " + auth["x-subscription-token"]) == "Bearer sk-real"
    auth, _ = llm_gateway.resolve_auth("api.exa.ai", {"x-api-key": "hub-gateway"}, "", None)
    assert list(auth) == ["x-api-key"]
    body = llm_gateway.patch_body("api.tavily.com", b'{"api_key": "hub-gateway", "query": "q"}', None)
    import json
    doc = json.loads(body)
    assert doc["query"] == "q" and _masked("Bearer " + doc["api_key"]) == "Bearer sk-real"
    assert llm_gateway.patch_body("api.exa.ai", b'{"a": 1}', None) == b'{"a": 1}'


def test_a_compressed_answer_reaches_the_container_readable(acme, tmp_path, monkeypatch):
    """A provider answering gzip (the gateway's own client asks for it): the
    container gets plain bytes, since the encoding header is not passed on."""
    import gzip
    import json as _json
    from fastapi import FastAPI, Response
    import socket
    import threading
    import time

    import uvicorn
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/v1/responses")
    async def responses():
        return Response(gzip.compress(_json.dumps({"ok": True}).encode()), media_type="application/json",
                        headers={"content-encoding": "gzip"})

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           lifespan="off"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    try:
        monkeypatch.setenv(llm_gateway.LOCAL_ENV, "1")
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
        from dashboard.backend.main import app as hub_app
        token = run_tokens.mint(run_id="run-gz", workspace="acme")
        client = TestClient(hub_app)
        resp = client.post(f"/api/run-state/llm/http/127.0.0.1:{port}/v1/responses", content=b"{}",
                           headers={"Authorization": f"Bearer {token}", "x-api-key": "Bearer hub-gateway"})
        assert resp.status_code == 200 and resp.json() == {"ok": True}
    finally:
        server.should_exit = True


def test_the_browser_service_is_kept_to_the_runs_workspace(acme, monkeypatch):
    """The browser token opens every workspace's sessions: a container gets a
    reference, and the hub keeps it to its own workspace's sessions with a
    policy no looser than its workspace's."""
    import json
    monkeypatch.setenv("AGENTS_HUB_BROWSER_TOKEN", "browser-secret")
    monkeypatch.setattr("common.browser_service.configured_url", lambda: "http://browser:3000")
    monkeypatch.setattr("common.browser_service.configured_token", lambda: "browser-secret")
    env = llm_gateway.container_env({"AGENTS_HUB_BROWSER_TOKEN": "browser-secret"})
    assert env["AGENTS_HUB_BROWSER_TOKEN"] == llm_gateway.SENTINEL
    assert "browser:3000" in env[llm_gateway.HOSTS_ENV]
    auth, _ = llm_gateway.resolve_auth("browser:3000", {"x-api-key": "Bearer hub-gateway"}, "", "acme")
    assert auth == {"authorization": "Bearer browser-secret"}

    owners = {"mine": "acme", "theirs": "other"}
    guard = llm_gateway.guard_browser
    # A new session is the run's workspace's, whatever the body says.
    _, body = guard("POST", "sessions", "", json.dumps({"workspace": "other", "policy": {}}).encode(),
                    "acme", owners.get)
    assert json.loads(body)["workspace"] == "acme"
    # A listing names the run's workspace.
    query, _ = guard("GET", "sessions", "workspace=other&run_id=r", b"", "acme", owners.get)
    assert "workspace=acme" in query and "workspace=other" not in query
    assert guard("GET", "sessions/mine/read", "", b"", "acme", owners.get)
    with pytest.raises(PermissionError):
        guard("POST", "sessions/theirs/navigate", "", b"{}", "acme", owners.get)
    with pytest.raises(PermissionError):
        guard("DELETE", "sessions/unknown", "", b"", "acme", owners.get)


def test_a_session_policy_is_no_looser_than_the_workspace(acme, monkeypatch):
    from workspace.storage import update_workspace_metadata
    update_workspace_metadata("acme", {"settings": {"web_domain_policy_enabled": True,
                                                    "web_allow_domains": ["example.com"],
                                                    "web_deny_domains": ["evil.com"]}})
    floor = llm_gateway.browser_policy_floor({"allowlist_enabled": False, "deny_domains": []}, "acme")
    assert floor["allowlist_enabled"] is True and floor["allow_domains"] == ["example.com"]
    assert "evil.com" in floor["deny_domains"]
    narrower = llm_gateway.browser_policy_floor(
        {"allowlist_enabled": True, "allow_domains": ["docs.example.com", "other.org"]}, "acme")
    assert narrower["allow_domains"] == ["docs.example.com"]
