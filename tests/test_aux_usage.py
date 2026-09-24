"""
Model calls a run makes beside its own loop (common/aux_usage.py): the tool
policy classifier, guardrail judges, schema repairs and the outcome grader are
listed on the run, priced at their own models and counted against the run's
money cap.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agents import agent_loop
from agents.agent_loop import LoopState
from agents.callbacks import guards
from common import aux_usage
from common.pricing import run_cost_usd


def _reply(text="ok", inp=1_000_000, out=100_000):
    return AIMessage(content=text, usage_metadata={"input_tokens": inp, "output_tokens": out,
                                                   "total_tokens": inp + out})


@pytest.fixture
def state():
    s = LoopState(run_id="run-aux")
    token = agent_loop.set_state(s)
    yield s
    agent_loop.reset_state(token)


class _FakeModel:
    def __init__(self, reply):
        self.reply = reply
        self.model_name = "small"

    def invoke(self, *_a, **_k):
        return self.reply


def test_a_call_outside_a_run_is_not_counted():
    assert aux_usage.record("tool_policy", provider="openai", model="small", response=_reply()) is None


def test_a_call_inside_a_run_lands_on_its_loop(state):
    item = aux_usage.record("guardrail", provider="openai", model="small", response=_reply())
    assert item["input_tokens"] == 1_000_000 and item["output_tokens"] == 100_000
    assert state.summary()["aux_calls"] == [item]


def test_the_run_cost_adds_each_call_at_its_own_model():
    prices = {("openai", "big"): (10.0, 30.0, 1.0), ("openai", "small"): (1.0, 2.0, 0.1)}
    run = {"provider": "openai", "model": "big",
           "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}},
           "loop": {"aux_calls": [{"purpose": "tool_policy", "provider": "openai", "model": "small",
                                   "input_tokens": 1_000_000, "output_tokens": 500_000}]}}
    assert run_cost_usd(run, prices) == pytest.approx(10.0 + 1.0 + 1.0)


def test_a_call_counts_against_the_run_cap(state, monkeypatch):
    monkeypatch.setenv(guards.RUN_BUDGET_ENV_LIMIT, "5")
    monkeypatch.setenv(guards.RUN_BUDGET_ENV_PRICES, json.dumps([["openai", "small", 1.0, 2.0, 0.1]]))
    guards.reset_run_budget_spend()
    try:
        aux_usage.record("structured_repair", provider="openai", model="small", response=_reply())
        guard = guards.RunBudgetGuard.from_env(provider="openai", model="big")
        assert guard.spent_usd == pytest.approx(1.0 + 0.2)
    finally:
        guards.reset_run_budget_spend()


def test_the_policy_classifier_is_counted(state, monkeypatch):
    from agents import agent_utils
    from tools import permission_policy as pp

    monkeypatch.setattr(agent_utils, "build_chat_model",
                        lambda **kw: _FakeModel(_reply('{"decision": "run", "reason": "fine"}', 800, 20)))
    decision = pp.classify(tool_id="read_file", tool_input={"path": "a"}, agent_spec=SimpleNamespace(),
                           settings={"tool_policy_model": "openai/small"})
    assert decision[0] == "run"
    [call] = state.aux_calls
    assert call["purpose"] == "tool_policy" and call["model"] == "small" and call["input_tokens"] == 800


def test_a_guardrail_judge_is_counted(state, monkeypatch):
    from agents import agent_utils
    from guardrails.checks import check_judge

    monkeypatch.setattr(agent_utils, "build_chat_model",
                        lambda **kw: _FakeModel(_reply('{"violation": false, "reason": ""}', 300, 10)))
    assert check_judge({"instruction": "no dates"}, "hello", guardrail_model="openai/small") == (None, None)
    [call] = state.aux_calls
    assert call["purpose"] == "guardrail" and call["provider"] == "openai" and call["output_tokens"] == 10


def test_the_outcome_grader_is_added_to_the_graded_run():
    from managers import run_manager as rm
    from tasks.outcome import _count_on_run

    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", status="running", link_to_session=False)
    rm.close_run(rid, status="completed", exit_code=0)
    _count_on_run(rid, {"tokens": {"input": 1200, "output": 80},
                        "grader": {"provider": "openai", "model": "small"}})
    [call] = rm.get_run_by_id(rid)["loop"]["aux_calls"]
    assert call == {"purpose": "outcome_grader", "provider": "openai", "model": "small",
                    "input_tokens": 1200, "output_tokens": 80, "cached_tokens": 0}
