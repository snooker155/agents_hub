"""Reasoning effort as a model setting.

The effort moved off the Behavior tab (``reasoning.thinking_level``) onto the
agent's Model tab (``AgentSpec.effort``). It is inherited like the model: the
agent's own, then the header's choice for agents that inherit the model too,
then the model's default on the Models page, then the provider's. What a value
may be depends on the model, so each one is clamped to what the model accepts.
"""
import sys
from pathlib import Path

import pytest

from agents.agent_utils import build_chat_model
from agents.registry import AgentSpec, _validate_agent_dict
from providers import catalog as catalog_mod
from providers.reasoning_profile import clamp_effort, effective_effort, reasoning_profile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))


@pytest.fixture
def catalog(monkeypatch):
    doc = {"openai": {"default": "", "models": []}, "anthropic": {"default": "", "models": []}}
    monkeypatch.setattr(catalog_mod, "load_catalog_raw", lambda: doc)
    return doc


def _openai_body(model, level):
    llm = build_chat_model(provider="openai", model=model, api_key="sk-test", thinking_level=level)
    body = llm._get_request_payload([("human", "hi")])
    return ("responses" if llm._use_responses_api(body) else "chat"), body


# ── Clamping ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model, asked, sent", [
    ("gpt-5", "xhigh", "high"),       # above the top: the top
    ("gpt-5", "none", "minimal"),     # below the bottom: the bottom
    ("gpt-5.6-sol", "minimal", "none"),  # between: the nearest below
    ("gpt-5.6-sol", "max", "xhigh"),
    ("o3", "minimal", "low"),
    ("gpt-5-pro", "low", "high"),
])
def test_an_effort_the_model_lacks_is_clamped(model, asked, sent):
    assert clamp_effort(reasoning_profile("openai", model), asked) == sent


def test_off_is_what_the_profile_sends_for_off():
    assert clamp_effort(reasoning_profile("openai", "gpt-5.6-sol"), "off") == "none"
    assert clamp_effort(reasoning_profile("anthropic", "claude-opus-4-8"), "off") is None
    # Opus 5.5 cannot stop thinking: off is its lowest.
    assert clamp_effort(reasoning_profile("anthropic", "claude-opus-5-5"), "off") == "low"


@pytest.mark.parametrize("model, efforts", [
    ("claude-opus-5-5", ["low", "medium", "high", "xhigh", "max"]),
    ("claude-opus-4-8", ["off", "low", "medium", "high", "xhigh", "max"]),
    ("claude-sonnet-4-6", ["off", "low", "medium", "high", "max"]),
    ("claude-haiku-4-5", ["off", "low", "medium", "high"]),
])
def test_claude_efforts_follow_the_family(model, efforts):
    assert reasoning_profile("anthropic", model)["efforts"] == efforts


# ── What reaches the provider ─────────────────────────────────────────────────

def test_xhigh_reaches_a_gpt_model_that_takes_it(catalog):
    endpoint, body = _openai_body("gpt-6-luna", "xhigh")
    assert endpoint == "responses"
    assert body["reasoning"]["effort"] == "xhigh"


def test_inherit_takes_the_model_default_from_the_catalog(catalog):
    catalog["openai"]["models"] = [{"id": "gpt-5.6-sol", "effort": "high"}]
    assert _openai_body("gpt-5.6-sol", "inherit")[1]["reasoning"]["effort"] == "high"


def test_inherit_without_a_catalog_value_sends_the_provider_default(catalog):
    # Sent explicitly, so the reasoning summary is asked for as well.
    _, body = _openai_body("gpt-5.6-sol", "inherit")
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}


def test_claude_gets_output_config_effort_with_adaptive_thinking(catalog):
    llm = build_chat_model(provider="anthropic", model="claude-opus-5-5", api_key="sk-ant",
                           thinking_level="xhigh")
    body = llm._get_request_payload([("human", "hi")])
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "xhigh"}


def test_claude_off_where_thinking_can_stop_sends_no_thinking(catalog):
    llm = build_chat_model(provider="anthropic", model="claude-opus-4-8", api_key="sk-ant",
                           thinking_level="off")
    body = llm._get_request_payload([("human", "hi")])
    assert "thinking" not in body and "output_config" not in body


def test_older_claude_thinks_on_a_budget_without_output_config(catalog):
    llm = build_chat_model(provider="anthropic", model="claude-haiku-4-5", api_key="sk-ant",
                           thinking_level="high")
    body = llm._get_request_payload([("human", "hi")])
    assert body["thinking"]["type"] == "enabled"
    assert "output_config" not in body


# ── The agent record ──────────────────────────────────────────────────────────

def _record(**extra):
    return {"id": "a1", "name": "A1", "type": "langchain",
            "entrypoint": "agents.agent_factory:build_agent_executor", **extra}


def test_a_positive_legacy_level_becomes_the_agent_effort():
    spec = _validate_agent_dict(_record(reasoning={"think_enabled": True, "thinking_level": "high"}))
    assert spec.effort == "high"
    assert spec.reasoning == {"think_enabled": True}
    assert spec.to_dict()["effort"] == "high"


def test_a_legacy_off_becomes_inherit():
    spec = _validate_agent_dict(_record(reasoning={"thinking_level": "off"}))
    assert spec.effort is None
    assert "effort" not in spec.to_dict()


def test_an_effort_of_its_own_wins_over_a_legacy_level():
    spec = _validate_agent_dict(_record(effort="low", reasoning={"thinking_level": "high"}))
    assert spec.effort == "low"


# ── The chain in the factory ──────────────────────────────────────────────────

@pytest.fixture
def workspace_effort(monkeypatch):
    meta = {"model_effort": None}
    import workspace
    monkeypatch.setattr(workspace, "get_workspace_metadata", lambda name: meta)
    return meta


def test_the_agent_effort_wins(workspace_effort):
    from agents.agent_factory import AgentFactory
    workspace_effort["model_effort"] = "high"
    assert AgentFactory._resolve_effort({"effort": "low"}, "ws") == "low"


def test_the_header_applies_when_the_model_is_inherited(workspace_effort):
    from agents.agent_factory import AgentFactory
    workspace_effort["model_effort"] = "high"
    assert AgentFactory._resolve_effort({"provider": None}, "ws") == "high"


def test_the_header_skips_an_agent_with_a_model_of_its_own(workspace_effort):
    from agents.agent_factory import AgentFactory
    workspace_effort["model_effort"] = "high"
    assert AgentFactory._resolve_effort({"provider": "openai", "model": "gpt-5"}, "ws") == "inherit"


def test_effective_effort_names_the_source(catalog):
    catalog["openai"]["models"] = [{"id": "gpt-5", "effort": "low"}]
    assert effective_effort("openai", "gpt-5", agent="xhigh")["value"] == "high"
    assert effective_effort("openai", "gpt-5", agent="xhigh")["source"] == "agent"
    assert effective_effort("openai", "gpt-5", workspace="high")["source"] == "workspace"
    assert effective_effort("openai", "gpt-5", workspace="high", model_inherited=False)["source"] == "model"
    assert effective_effort("openai", "gpt-5.6-sol")["source"] == "provider"
    assert effective_effort("google", "gemini-2.5-pro")["value"] is None


# ── Routes ────────────────────────────────────────────────────────────────────

def test_the_model_route_saves_and_clears_an_effort(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agents import registry
    from routes.agents import capabilities

    store = {"a1": AgentSpec(id="a1", name="A1", type="langchain", entrypoint="")}
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: store.get(agent_id))
    monkeypatch.setattr(registry, "add_agent", lambda spec: store.__setitem__(spec.id, spec))
    app = FastAPI()
    app.include_router(capabilities.router)
    client = TestClient(app)

    saved = client.post("/api/agents/a1/model", json={"effort": "xhigh"}).json()
    assert saved["effort"] == "xhigh" and store["a1"].effort == "xhigh"
    assert "effective_effort" in saved
    assert client.post("/api/agents/a1/model", json={"effort": "loud"}).status_code == 400
    assert client.post("/api/agents/a1/model", json={"effort": ""}).json()["effort"] is None
    # The old Behavior tab field still lands on the effort.
    client.post("/api/agents/a1/reasoning", json={"thinking_level": "medium"})
    assert store["a1"].effort == "medium"


def test_the_catalog_keeps_a_model_effort():
    from routes import models as models_route
    assert models_route._norm_model({"id": "gpt-5", "effort": "High"})["effort"] == "high"
    assert models_route._norm_model({"id": "gpt-5", "effort": "loud"})["effort"] is None
    shown = models_route._catalog_response({"openai": {"default": "", "models": [{"id": "gpt-5"}]}})
    assert shown["providers"]["openai"]["models"][0]["reasoning"]["efforts"] == [
        "minimal", "low", "medium", "high"]


def test_the_header_effort_is_stored_apart_from_the_model(monkeypatch):
    import asyncio
    from routes import workspaces as ws_routes

    meta = {"model_override": {"provider": "openai", "model": "gpt-5"}}
    monkeypatch.setattr(ws_routes, "_ensure_writable_workspace", lambda name: None)
    monkeypatch.setattr(ws_routes, "update_workspace_metadata", lambda name, upd: meta.update(upd))

    assert asyncio.run(ws_routes.set_workspace_model("ws", {"effort": "high"})) == {"effort": "high"}
    # The model override is untouched by an effort-only change.
    assert meta == {"model_override": {"provider": "openai", "model": "gpt-5"}, "model_effort": "high"}
    asyncio.run(ws_routes.set_workspace_model("ws", {"effort": "inherit"}))
    assert meta["model_effort"] is None
    with pytest.raises(Exception):
        asyncio.run(ws_routes.set_workspace_model("ws", {"effort": "loud"}))


def test_a_personal_workspace_takes_defaults_header_effort(monkeypatch):
    from workspace import storage
    monkeypatch.setattr(storage, "get_workspace_metadata",
                        lambda name: {"model_effort": "low"} if name == "default" else {})
    assert storage.get_workspace_model_effort({"personal_of": "u1"}) == "low"
    assert storage.get_workspace_model_effort({"personal_of": "u1", "model_effort": "high"}) == "high"
    assert storage.get_workspace_model_effort({}) is None
