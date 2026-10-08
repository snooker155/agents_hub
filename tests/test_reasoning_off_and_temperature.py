"""Thinking level off and the temperature a model is built with.

Off has to mean off. OpenAI's reasoning models keep reasoning when a request
names no effort: each family falls back to its own default, ``medium`` on most
of them, billed and invisible. So off is sent as the lowest effort the model
accepts, and at effort ``none`` the model takes a temperature again.

The temperature itself comes from the agent, else the model's own value on the
Models page, else the global default. An agent without one used to be built
with 0.0, which hid both other sources.
"""
import pytest

from agents.agent_utils import build_chat_model
from providers import catalog as catalog_mod
from providers.reasoning_profile import openai_off_effort, reasoning_profile


@pytest.mark.parametrize("model, default, off, temperature", [
    ("gpt-5", "medium", "minimal", "never"),
    ("gpt-5-mini", "medium", "minimal", "never"),
    ("gpt-5-nano-2025-08-07", "medium", "minimal", "never"),
    ("gpt-5.1", "none", "none", "when_off"),
    ("gpt-5.4-mini", "none", "none", "when_off"),
    ("gpt-5.5", "medium", "none", "when_off"),
    ("gpt-5.6-sol", "medium", "none", "when_off"),
    ("openai/gpt-5.6-terra", "medium", "none", "when_off"),
    ("o3", "medium", "low", "never"),
    ("o4-mini", "medium", "low", "never"),
    ("gpt-5-pro", "high", None, "never"),
])
def test_the_measured_table(model, default, off, temperature):
    assert reasoning_profile("openai", model) == {
        "default": default, "off": off, "temperature": temperature}


@pytest.mark.parametrize("provider, model", [
    ("openai", "gpt-4o"), ("openai", "gpt-5-chat-latest"),
    ("ollama", "gpt-oss:20b"), ("google", "gemini-2.5-pro"),
])
def test_models_without_a_known_profile(provider, model):
    assert reasoning_profile(provider, model) is None
    if provider == "openai":
        assert openai_off_effort(model) is None


def test_claude_does_not_think_unless_asked():
    assert reasoning_profile("anthropic", "claude-opus-5-5")["default"] == "off"


@pytest.fixture
def catalog(monkeypatch):
    """A Models page catalog the build reads its per-model values from."""
    doc = {"openai": {"default": "", "models": []}}
    monkeypatch.setattr(catalog_mod, "load_catalog_raw", lambda: doc)
    return doc


def _built(model, thinking_level, temperature=None):
    llm = build_chat_model(provider="openai", model=model, api_key="sk-test",
                           thinking_level=thinking_level, temperature=temperature)
    body = llm._get_request_payload([("human", "hi")])
    return ("responses" if llm._use_responses_api(body) else "chat"), body


@pytest.mark.parametrize("model, effort", [
    ("gpt-5", "minimal"), ("gpt-5-mini", "minimal"), ("o3", "low"), ("o4-mini", "low"),
])
def test_off_holds_a_reasoning_model_to_its_lowest_effort(catalog, model, effort):
    endpoint, body = _built(model, "off", temperature=0.4)
    assert endpoint == "chat"
    assert body["reasoning_effort"] == effort
    # Any effort above none rejects a temperature.
    assert "temperature" not in body


def test_off_on_a_newer_family_sends_none_with_the_temperature(catalog):
    endpoint, body = _built("gpt-5.4", "off", temperature=0.4)
    assert endpoint == "chat"
    assert body["reasoning_effort"] == "none"
    # langchain strips it from every gpt-5 request; the API takes it here.
    assert body["temperature"] == 0.4


def test_off_on_a_responses_only_model(catalog):
    endpoint, body = _built("gpt-5.6-sol", "off", temperature=0.4)
    assert endpoint == "responses"
    assert body["reasoning"] == {"effort": "none"}
    assert body["temperature"] == 0.4


def test_off_cannot_lower_a_pro_model(catalog):
    _, body = _built("gpt-5-pro", "off")
    assert "reasoning_effort" not in body and "reasoning" not in body


def test_no_level_keeps_the_provider_default(catalog):
    # Utility calls (titles, judges) never chose a level and keep reasoning.
    _, body = _built("gpt-5", None)
    assert "reasoning_effort" not in body and "reasoning" not in body


def test_off_on_a_chat_model_just_sends_the_temperature(catalog):
    _, body = _built("gpt-4o", "off", temperature=0.4)
    assert "reasoning_effort" not in body
    assert body["temperature"] == 0.4


def test_the_temperature_chain(catalog, monkeypatch):
    from common.config import settings
    monkeypatch.setattr(settings, "temperature", 0.1)
    catalog["openai"]["models"] = [{"id": "gpt-4o", "temperature": 0.8}]

    # The model's own value, then the agent's, then the global default.
    assert _built("gpt-4o", None)[1]["temperature"] == 0.8
    assert _built("gpt-4o", None, temperature=0.3)[1]["temperature"] == 0.3
    assert _built("gpt-4.1", None)[1]["temperature"] == 0.1


def test_the_model_value_reaches_a_newer_family_with_thinking_off(catalog):
    catalog["openai"]["models"] = [{"id": "gpt-5.4", "temperature": 0.6}]
    assert _built("gpt-5.4", "off")[1]["temperature"] == 0.6


def test_an_agent_without_a_temperature_leaves_it_unset(monkeypatch):
    from agents import registry
    from agents.agent_factory import get_factory
    from agents.registry import AgentSpec

    spec = AgentSpec(id="plain", name="Plain", type="standard", entrypoint="", tools=[])
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: spec)
    monkeypatch.setattr("agents.prompt_assembly.assemble_prompt", lambda *a, **k: "prompt")
    assert get_factory().load_definition("plain")["temperature"] is None


def test_the_catalog_stores_and_serves_a_temperature(monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import models as models_route

    assert models_route._norm_model({"id": "gpt-4o", "temperature": "0.7"})["temperature"] == 0.7
    assert models_route._norm_model({"id": "gpt-4o", "temperature": 5})["temperature"] == 2.0
    assert models_route._norm_model({"id": "gpt-4o"})["temperature"] is None
    shown = models_route._catalog_response(
        {"openai": {"default": "", "models": [{"id": "gpt-5", "temperature": None}]}})
    assert shown["providers"]["openai"]["models"][0]["reasoning"]["off"] == "minimal"
    assert "global_temperature" in shown


# ── The global temperature on the Settings page, and no workspace level ──────

@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
    from routes import settings as settings_routes
    monkeypatch.setattr(settings_routes, "_ENV_FILE", tmp_path / ".env")
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    app = FastAPI()
    app.include_router(settings_routes.router)
    return TestClient(app), settings_routes


def test_the_global_temperature_applies_live_and_is_written(settings_client, catalog, monkeypatch):
    from common.config import settings as live
    monkeypatch.setattr(live, "temperature", 0.0)
    client, routes = settings_client

    assert client.put("/api/settings", json={"temperature": 0.6}).status_code == 200
    assert live.temperature == 0.6
    assert routes._read_env().get("LLM_TEMPERATURE") == "0.6"
    # The next model without its own temperature runs at it, no restart.
    assert _built("gpt-4o", None)[1]["temperature"] == 0.6

    assert client.put("/api/settings", json={"temperature": 3}).status_code == 400
    assert live.temperature == 0.6


def test_a_workspace_temperature_no_longer_applies(monkeypatch):
    # It had no editor and the workspace Save erased it; the chain is now the
    # agent, the model on the Models page, then the global value.
    import workspace
    from agents.agent_factory import get_factory

    meta = {"settings": {"temperature": 0.9, "max_tokens": 1234}}
    monkeypatch.setattr(workspace, "get_workspace_metadata", lambda name: meta)
    monkeypatch.setattr(workspace, "get_workspace_default_model_config", lambda m: {})
    monkeypatch.setattr(workspace, "get_effective_settings",
                        lambda name: {"temperature": "0.9", "max_tokens": "1234"})
    monkeypatch.setattr("common.personal_workspace.model_source", lambda name: None)

    config = {"temperature": None, "max_tokens": None}
    get_factory()._resolve_model_config(config, "ws")
    assert config["temperature"] is None
    # The workspace's other LLM setting still applies.
    assert config["max_tokens"] == 1234
