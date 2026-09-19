"""
Regression-replay tests: replay_run rebuilds + re-invokes a recorded run,
records a clearly-tagged replay run, and returns an original-vs-replay diff.
The agent build and invocation are stubbed so no real LLM call happens.
"""
import json

import pytest

from agents import agent_replay
from agents.agent_invoke import AgentInvocation
from managers import run_manager as rm
from common import budget, pricing


class _FakeAgent:
    provider = "anthropic"
    model = "claude-sonnet-5"
    system_prompt = "sys prompt"
    agent_id = "swe_agent"


class _FakeResult:
    ok = True
    agent_output = "replayed answer"
    error = None
    response = None


def _fake_invocation(agent, prompt, **kw):
    proc = {
        "token_usage": {"inbound_tokens": 100, "outbound_tokens": 50, "total_tokens": 150},
        "input_context": {"system_prompt": "sys prompt", "history": [], "user_message": prompt},
        "response": {"text": "replayed answer", "structured": None},
    }
    return AgentInvocation(result=_FakeResult(), duration_ms=42, process=proc, stats=None)


def _seed_original(run_id="orig", *, agent="swe_agent", ws="ws1", output="original answer"):
    rm.open_run(run_id, agent, workspace=ws, provider="openai", model="gpt-4o",
                channel="local", link_to_session=False)
    rm.seed_run_input_context(run_id, "sys prompt", "hello world")
    rm.update_run(run_id, {"status": "completed", "output": output})


def _stub_agent(monkeypatch, create=None, invoke=None):
    monkeypatch.setattr("agents.agent_factory.create_agent",
                        create or (lambda *a, **k: _FakeAgent()))
    monkeypatch.setattr("agents.agent_invoke.invoke_agent", invoke or _fake_invocation)


def test_replay_creates_tagged_run_and_diffs(monkeypatch):
    _stub_agent(monkeypatch)
    _seed_original()

    result = agent_replay.replay_run("orig")

    assert result["replay_run_id"] != "orig"
    replay_rec = rm.get_run_by_id(result["replay_run_id"])
    assert replay_rec["channel"] == "replay"
    assert replay_rec["replay_of"] == "orig"
    assert result["replay"]["model"] == "claude-sonnet-5"
    assert result["replay"]["ok"] is True
    assert "replayed answer" in result["replay"]["text"]
    assert result["original"]["text"] == "original answer"
    assert result["identical"] is False
    assert result["diff"]  # non-empty unified diff


def test_replay_identical_output(monkeypatch):
    _stub_agent(monkeypatch)
    _seed_original(output="replayed answer")  # same as the fake replay output
    result = agent_replay.replay_run("orig")
    assert result["identical"] is True


def test_replay_model_override_forwarded(monkeypatch):
    captured = {}

    def _fake_create(agent_id, workspace=None, **ov):
        captured.update(ov)
        return _FakeAgent()

    _stub_agent(monkeypatch, create=_fake_create)
    _seed_original()

    agent_replay.replay_run("orig", model="gpt-4o", provider="openai")
    assert captured == {"model": "gpt-4o", "provider": "openai"}


def test_replay_missing_run_raises(monkeypatch):
    _stub_agent(monkeypatch)
    with pytest.raises(agent_replay.ReplayError):
        agent_replay.replay_run("does-not-exist")


def test_replay_no_input_raises(monkeypatch):
    _stub_agent(monkeypatch)
    rm.open_run("empty", "swe_agent", workspace="ws1", channel="local", link_to_session=False)
    rm.update_run("empty", {"status": "completed"})
    with pytest.raises(agent_replay.ReplayError):
        agent_replay.replay_run("empty")


def test_replay_run_excluded_from_budget_spend(monkeypatch, tmp_path):
    # Price catalog so both runs would otherwise cost money.
    models_file = tmp_path / "models.json"
    models_file.write_text(json.dumps({
        "anthropic": {"default": "claude-sonnet-5", "models": [
            {"id": "claude-sonnet-5", "enabled": True, "input_price": 3.0, "output_price": 15.0},
        ]},
        "openai": {"default": "gpt-4o", "models": [
            {"id": "gpt-4o", "enabled": True, "input_price": 2.5, "output_price": 10.0},
        ]},
    }), encoding="utf-8")
    monkeypatch.setattr(pricing, "MODELS_FILE", models_file)

    _stub_agent(monkeypatch)
    _seed_original(ws="ws1")
    # Give the original run some tokens so it has real spend.
    rm.update_run("orig", {"process": {"token_usage": {
        "inbound_tokens": 1_000_000, "outbound_tokens": 0, "total_tokens": 1_000_000}}})
    baseline = budget.workspace_period_spend("ws1", "total")
    assert baseline == pytest.approx(2.5)

    agent_replay.replay_run("orig")  # creates a replay run with 100/50 tokens in ws1

    # Spend is unchanged: the replay run is excluded from budget aggregation.
    assert budget.workspace_period_spend("ws1", "total") == pytest.approx(2.5)
