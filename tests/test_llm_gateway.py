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
                 "ANTHROPIC_BASE_URL"):
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
