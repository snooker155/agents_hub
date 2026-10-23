"""A new agent joins the main agent's delegates and handoffs (agents/main_links.py),
unless the guard is strict and the link would close the lethal trifecta: then it
is not linked, the reason is kept, and the main agent says so when asked."""
from __future__ import annotations

import json

import pytest

from agents import capability_guard
from agents import main_links
from agents import prompt_assembly
from agents.registry import AgentSpec, add_agent, get_agent, get_agent_raw, replace_all_raw


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    from agents.agent_factory import get_factory
    monkeypatch.setattr(get_factory(), "definitions_dir", defs)
    replace_all_raw([])
    # A main agent shaped like the shipped one: run_shell behind an approval
    # and the override, private reads, a delegating tool, one safe delegate.
    add_agent(AgentSpec(id="helper", name="Helper", type="langchain", entrypoint="agents.agent_factory:build_agent_executor", tools=["calculator"]))
    add_agent(AgentSpec(
        id="main-agent", name="Main Agent", type="langchain", entrypoint="agents.agent_factory:build_agent_executor", system=True,
        tools=["read_file", "run_agent_tool", "run_shell"], tool_policy={"run_shell": "always_ask"},
        capability_override=True, delegates=["helper"],
    ), user_edit=False)
    yield
    replace_all_raw([])


def _new(agent_id, tools):
    add_agent(AgentSpec(id=agent_id, name=agent_id.title(), type="langchain", entrypoint="agents.agent_factory:build_agent_executor", tools=tools))


def _mode(monkeypatch, mode):
    monkeypatch.setattr(capability_guard, "guard_mode", lambda: mode)


def test_a_safe_agent_is_linked_both_ways(monkeypatch):
    _mode(monkeypatch, "block")
    _new("calc", ["calculator"])
    assert main_links.link_new_agent("calc") == {"linked": True, "reason": ""}
    main = get_agent_raw("main-agent")
    assert main.delegates == ["helper", "calc"]
    assert main.handoffs == ["calc"]
    assert not main.user_modified


def test_strict_mode_refuses_a_trifecta_link_and_keeps_the_reason(monkeypatch):
    _mode(monkeypatch, "block")
    _new("scout", ["fetch_url"])
    out = main_links.link_new_agent("scout")
    assert out["linked"] is False and "trifecta" in out["reason"].lower()
    main = get_agent_raw("main-agent")
    assert "scout" not in main.delegates and "scout" not in (main.handoffs or [])
    assert "scout" in main.link_refusals

    prompt = main_links.refusal_prompt(get_agent("main-agent"))
    assert "`scout` (Scout)" in prompt and "security policy" in prompt

    from tools.handoff import _perform
    from chat import handoff as handoff_mod
    monkeypatch.setattr(handoff_mod, "current", lambda: type("S", (), {"intent": None})())
    reply = json.loads(_perform(caller_id="main-agent", caller_name="Main Agent", targets=[],
                                default_filter="full", workspace=None, target="scout",
                                reason="r", note=None, history=None))
    assert reply.get("code") == "security_refused"
    assert "cannot pass the chat" in json.dumps(reply)

    from common.agent_context import current_agent_id
    from tools.langchain_tools import _delegation_blocked
    token = current_agent_id.set("main-agent")
    try:
        assert "security policy" in _delegation_blocked("scout")
    finally:
        current_agent_id.reset(token)


def test_warn_mode_links_anyway(monkeypatch):
    _mode(monkeypatch, "warn")
    _new("scout", ["fetch_url"])
    assert main_links.link_new_agent("scout")["linked"] is True
    assert "scout" in get_agent_raw("main-agent").handoffs


def test_a_later_manual_link_hides_the_refusal(monkeypatch):
    _mode(monkeypatch, "block")
    _new("scout", ["fetch_url"])
    main_links.link_new_agent("scout")
    import dataclasses
    main = get_agent_raw("main-agent")
    add_agent(dataclasses.replace(main, handoffs=["scout"]), user_edit=False)
    assert main_links.refusals_for(get_agent("main-agent")) == {}


def test_deleting_an_agent_unlinks_it(monkeypatch):
    _mode(monkeypatch, "block")
    _new("calc", ["calculator"])
    _new("scout", ["fetch_url"])
    main_links.link_new_agent("calc")
    main_links.link_new_agent("scout")
    main_links.unlink_agent("calc")
    main_links.unlink_agent("scout")
    main = get_agent_raw("main-agent")
    assert main.delegates == ["helper"] and not main.handoffs and not main.link_refusals


def test_system_agents_and_the_main_agent_itself_are_skipped(monkeypatch):
    _mode(monkeypatch, "block")
    assert main_links.link_new_agent("main-agent")["skipped"] is True
    assert main_links.link_new_agent("no_such")["skipped"] is True


def test_the_create_route_links_the_new_agent(monkeypatch):
    _mode(monkeypatch, "block")
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    r = TestClient(app).post("/api/agents/create", json={
        "name": "Summer", "system_prompt": "Add numbers.", "tools": ["calculator"]})
    assert r.status_code == 200, r.text
    assert r.json()["main_agent_link"] == {"linked": True, "reason": ""}
    assert "summer" in get_agent_raw("main-agent").handoffs
