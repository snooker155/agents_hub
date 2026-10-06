"""The model a chat delegation runs on (``run_agent_tool``'s ``model``).

What is promised: an empty ``model`` builds the delegate on its own model; a
catalog id builds it on that provider and model for this call only; a model
outside the enabled catalog is refused before any run is opened, with the
list of models that would work.

Run: ``python -m pytest tests/test_run_agent_model.py -q``
"""
from __future__ import annotations

import json

import pytest

from agents.registry import AgentSpec

CATALOG = {
    "openai": {"default": "gpt-4o-mini", "models": [
        {"id": "gpt-4o-mini", "enabled": True},
        {"id": "gpt-4o", "enabled": False},
    ]},
    "anthropic": {"default": "", "models": [{"id": "claude-haiku-4-5", "enabled": True}]},
}


class _Built(Exception):
    """Stops the tool right after the build, which is all these tests read."""


@pytest.fixture
def chat(monkeypatch):
    spec = AgentSpec(id="worker", name="Worker", type="langchain", entrypoint="x")
    monkeypatch.setattr("providers.catalog.load_catalog_raw", lambda: CATALOG)
    monkeypatch.setattr("tools.langchain_tools.reg_get_agent", lambda aid: spec if aid == "worker" else None)
    monkeypatch.setattr("tools.langchain_tools._active_workspace", lambda: "default")
    monkeypatch.setattr("tools.langchain_tools._agent_available", lambda spec, ws: True)
    monkeypatch.setattr("tools.langchain_tools._delegation_blocked", lambda aid: None)
    monkeypatch.setattr("agents.roles.resolve", lambda aid, ws: aid)
    builds: list = []

    def fake_create_agent(agent_id, workspace=None, **params):
        builds.append({"agent_id": agent_id, **params})
        raise _Built("stop here")

    monkeypatch.setattr("agents.agent_factory.create_agent", fake_create_agent)
    return builds


def _call(**kwargs):
    from tools.langchain_tools import run_agent_tool
    return json.loads(run_agent_tool.invoke({"agent_id": "worker", "input": "sum this up", **kwargs}))


def test_without_a_model_the_delegate_keeps_its_own(chat):
    _call()
    assert chat == [{"agent_id": "worker"}]


def test_a_catalog_id_builds_the_delegate_on_that_model(chat):
    _call(model="anthropic/claude-haiku-4-5")
    assert chat == [{"agent_id": "worker", "provider": "anthropic", "model": "claude-haiku-4-5"}]


def test_a_bare_id_resolves_when_unambiguous(chat):
    _call(model="gpt-4o-mini")
    assert chat[0]["provider"] == "openai" and chat[0]["model"] == "gpt-4o-mini"


def test_a_model_outside_the_catalog_is_refused_before_the_run(chat):
    out = _call(model="openai/gpt-4o")
    assert out["ok"] is False and out["code"] == "bad_model"
    assert "anthropic/claude-haiku-4-5" in out["models"]
    assert chat == []


def test_the_delegating_system_agents_can_pick_a_model():
    """The seed hands the model list to the agents that delegate in chat, and
    the assistant inherits both the tools and the rule from main-agent."""
    from agents.agent_factory import get_factory
    from agents.registry import get_agent
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    for agent_id in ("main-agent", "orchestrator", "researcher", "assistant"):
        assert "list_models_tool" in get_agent(agent_id).tools, agent_id
    for agent_id in ("main-agent", "orchestrator", "assistant"):
        assert "delegate_task_tool" in get_agent(agent_id).tools, agent_id
    prompt = get_factory().load_definition("assistant")["system_prompt"]
    assert "Choosing a model for a delegate" in prompt
