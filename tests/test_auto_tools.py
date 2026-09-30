"""agents/auto_tools.py mirrors the factory's build-time injections for the
Tools tab: which tools an agent gets on top of its record, and why."""
from __future__ import annotations

import pytest

from agents.registry import AgentSpec
from agents.auto_tools import auto_injected_tools


def _spec(**extra) -> AgentSpec:
    return AgentSpec(id="a", name="a", description="", type="langchain",
                     entrypoint="agents.agent_launcher:run", tools=["read_file"], **extra)


@pytest.fixture(autouse=True)
def _no_pools(monkeypatch):
    monkeypatch.setattr("memory.binding.effective_memory_pools", lambda *a, **k: [])


def _ids(spec):
    return {t["id"]: t["reason"] for t in auto_injected_tools(spec, None)}


def test_plain_agent_only_lists_the_project_graph_reader():
    assert _ids(_spec()) == {"get_project_graph": "project"}


def test_handoff_targets_bring_the_handoff_tool_with_a_description():
    out = auto_injected_tools(_spec(handoffs=["b"]), None)
    h = next(t for t in out if t["id"] == "handoff_to_agent")
    assert h["reason"] == "handoffs"
    assert "conversation" in h["description"]


def test_reasoning_skills_and_clarify_gate():
    ids = _ids(_spec(reasoning={"think_enabled": True, "plan_enabled": True},
                     skills_enabled=True, clarify_gate=True))
    assert ids["think"] == "think"
    assert ids["plan"] == "plan" and ids["save_plan"] == "plan" and ids["assess_complexity"] == "plan"
    assert ids["get_skill"] == "skills" and ids["create_skill"] == "skills"
    assert ids["ask_user"] == "clarify_gate"


def test_a_tool_already_on_the_record_is_not_listed_twice():
    spec = _spec(clarify_gate=True)
    spec = AgentSpec(**{**spec.__dict__, "tools": ["read_file", "ask_user"]})
    assert "ask_user" not in _ids(spec)


def test_memory_pool_brings_the_pool_tools(monkeypatch):
    monkeypatch.setattr("memory.binding.effective_memory_pools", lambda *a, **k: ["pool1"])
    ids = _ids(_spec(episodic_write_enabled=False))
    assert ids["recall"] == "memory_pool" and ids["remember"] == "memory_pool"
    assert "record_episode" not in ids
    ids = _ids(_spec(episodic_write_enabled=True))
    assert ids["record_episode"] == "memory_pool"
