"""
``consult_advisor`` (tools/advisor.py): an agent asks a second model for
advice mid-run. The advisor sees only what the agent writes into the call; the
call lands on the run's ``aux_calls`` at the advisor's own price, counts
against the run's money cap, and is limited per run and in answer length. The
tool is bound, with its prompt section, only for an agent that names an
advisor (agents/agent_factory.py), and the field is part of the agent's
version fingerprint (agents/versions.py).

No provider is called: the advisor model is a recording fake.
"""
from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace
from typing import Any, List

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agents import agent_loop
from agents.agent_loop import LoopState
from agents.callbacks import guards
from common.pricing import run_cost_usd
from tools import advisor


class _FakeAdvisor:
    def __init__(self, text: str = "Back up the table first, then migrate.",
                 inp: int = 1_000_000, out: int = 100_000):
        self.text, self.inp, self.out = text, inp, out
        self.model_name = "big-thinker"
        self.seen: List[Any] = []

    def invoke(self, messages, *_a, **_k):
        self.seen.append(list(messages))
        return AIMessage(content=self.text, usage_metadata={
            "input_tokens": self.inp, "output_tokens": self.out, "total_tokens": self.inp + self.out})


@pytest.fixture
def fake(monkeypatch):
    model = _FakeAdvisor()
    built = {}

    def _build(provider, model_name, max_chars):
        built.update(provider=provider, model=model_name, max_chars=max_chars)
        return model

    monkeypatch.setattr(advisor, "build_advisor_model", _build)
    from tools import delegation
    monkeypatch.setattr(delegation, "resolve_model",
                        lambda spec, models=None: tuple(spec.split("/", 1)))
    model.built = built
    return model


@pytest.fixture
def state():
    s = LoopState(run_id="run-advice")
    token = agent_loop.set_state(s)
    yield s
    agent_loop.reset_state(token)


def _tool(model_id="anthropic/big-thinker"):
    [tool] = advisor.create_advisor_tools(SimpleNamespace(id="a", advisor_model=model_id))
    return tool


def test_no_advisor_no_tool():
    assert advisor.create_advisor_tools(SimpleNamespace(id="a", advisor_model=None)) == []
    assert advisor.create_advisor_tools(None) == []


def test_the_advisor_sees_only_the_question_and_the_context(fake, state):
    tool = _tool()
    assert tool.name == "consult_advisor"
    out = json.loads(tool.invoke({"question": "Migrate in place or copy first?",
                                  "context": "Postgres 15, 40 GB table, no replica."}))
    assert out["ok"] and out["answer"] == fake.text and out["advisor"] == "anthropic/big-thinker"
    [messages] = fake.seen
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    assert "Migrate in place or copy first?" in messages[1].content
    assert "40 GB table" in messages[1].content
    assert fake.built == {"provider": "anthropic", "model": "big-thinker", "max_chars": 4000}


def test_the_call_is_counted_on_the_run_at_the_advisors_price(fake, state):
    _tool().invoke({"question": "Which index?"})
    [call] = state.summary()["aux_calls"]
    assert call == {"purpose": "advisor", "provider": "anthropic", "model": "big-thinker",
                    "input_tokens": 1_000_000, "output_tokens": 100_000, "cached_tokens": 0}
    prices = {("openai", "small"): (1.0, 2.0, 0.1), ("anthropic", "big-thinker"): (15.0, 75.0, 1.5)}
    run = {"provider": "openai", "model": "small",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
           "loop": state.summary()}
    assert run_cost_usd(run, prices) == pytest.approx(1.0 + 15.0 + 7.5)


def test_the_call_counts_against_the_run_budget(fake, state, monkeypatch):
    monkeypatch.setenv(guards.RUN_BUDGET_ENV_LIMIT, "50")
    monkeypatch.setenv(guards.RUN_BUDGET_ENV_PRICES, json.dumps([
        ["openai", "small", 1.0, 2.0, 0.1], ["anthropic", "big-thinker", 15.0, 75.0, 1.5]]))
    guards.reset_run_budget_spend()
    try:
        _tool().invoke({"question": "Is this safe?"})
        guard = guards.RunBudgetGuard.from_env(provider="openai", model="small")
        assert guard.spent_usd == pytest.approx(15.0 + 7.5)
    finally:
        guards.reset_run_budget_spend()


def test_calls_per_run_are_limited(fake, state, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_LOOP_ADVISOR_MAX_CALLS", "2")
    tool = _tool()
    first = json.loads(tool.invoke({"question": "one"}))
    assert first["calls_left"] == 1
    assert json.loads(tool.invoke({"question": "two"}))["ok"]
    third = json.loads(tool.invoke({"question": "three"}))
    assert third["ok"] is False and third["code"] == "limit_reached"
    assert len(fake.seen) == 2


def test_no_calls_allowed_binds_no_tool(monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_LOOP_ADVISOR_MAX_CALLS", "0")
    assert advisor.create_advisor_tools(SimpleNamespace(id="a", advisor_model="openai/x")) == []


def test_the_answer_is_cut_to_the_limit(fake, state, monkeypatch):
    monkeypatch.setenv("AGENTS_HUB_LOOP_ADVISOR_MAX_ANSWER_CHARS", "300")
    fake.text = "x" * 2000
    out = json.loads(_tool().invoke({"question": "long?"}))
    assert out["truncated"] is True and len(out["answer"]) == 301
    assert fake.built["max_chars"] == 300


def test_an_unknown_model_is_an_answer_not_a_crash(state, monkeypatch):
    from tools import delegation

    def _refuse(spec, models=None):
        raise ValueError("not enabled")

    monkeypatch.setattr(delegation, "resolve_model", _refuse)
    out = json.loads(_tool("openai/gone").invoke({"question": "?"}))
    assert out["ok"] is False and out["code"] == "model_unavailable"
    assert state.aux_calls == []


def test_the_advisor_is_part_of_the_agent_version():
    from agents.registry import AgentSpec, _validate_agent_dict
    from agents.versions import _spec_parts

    base = AgentSpec(id="adv", name="adv", type="standard", entrypoint="agents.standard_agent:StandardAgent")
    assert "advisor_model" not in _spec_parts(base)
    with_advisor = dataclasses.replace(base, advisor_model="anthropic/big-thinker")
    assert _spec_parts(with_advisor)["advisor_model"] == "anthropic/big-thinker"
    stored = with_advisor.to_dict()
    assert stored["advisor_model"] == "anthropic/big-thinker"
    assert _validate_agent_dict(stored).advisor_model == "anthropic/big-thinker"


@pytest.fixture
def live_registry():
    from common.bootstrap import seed_registry_from_bootstrap
    seed_registry_from_bootstrap()
    yield


def test_the_factory_binds_the_tool_only_with_an_advisor(live_registry):
    from agents.agent_factory import AgentFactory
    from agents.registry import add_agent, get_agent, remove_agent

    factory = AgentFactory()
    plain = factory._build_agent("main-agent")
    assert "consult_advisor" not in {t.name for t in plain._tools}
    assert "## Advisor" not in plain.system_prompt

    spec = get_agent("main-agent")
    add_agent(dataclasses.replace(spec, id="advised_probe", definition_id="main-agent", system=False,
                                  advisor_model="anthropic/big-thinker"))
    try:
        built = factory._build_agent("advised_probe")
        assert "consult_advisor" in {t.name for t in built._tools}
        assert "## Advisor" in built.system_prompt and "anthropic/big-thinker" in built.system_prompt
    finally:
        remove_agent("advised_probe")


def test_the_loop_settings_route_takes_a_catalog_advisor(live_registry, monkeypatch):
    import sys
    from pathlib import Path

    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import agent_loop_settings as routes
    from tools import delegation

    monkeypatch.setattr(delegation, "enabled_models",
                        lambda: [{"id": "anthropic/big-thinker", "provider": "anthropic",
                                  "model": "big-thinker", "context_window": 0}])
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    from agents.registry import add_agent, get_agent, remove_agent
    add_agent(dataclasses.replace(get_agent("main-agent"), id="advised_route", definition_id="main-agent",
                                  system=False))
    try:
        assert client.get("/api/agents/advised_route/loop-settings").json()["advisor_model"] is None
        resp = client.put("/api/agents/advised_route/loop-settings", json={"advisor_model": "anthropic/big-thinker"})
        assert resp.status_code == 200 and resp.json()["advisor_model"] == "anthropic/big-thinker"
        assert get_agent("advised_route").advisor_model == "anthropic/big-thinker"
        assert client.put("/api/agents/advised_route/loop-settings",
                          json={"advisor_model": "openai/not-there"}).status_code == 400
        resp = client.put("/api/agents/advised_route/loop-settings", json={"advisor_model": None})
        assert resp.json()["advisor_model"] is None
    finally:
        remove_agent("advised_route")
