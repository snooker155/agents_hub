"""Per-task money cap (common/run_budget.py, RunBudgetGuard, the approve route).

The workspace budget protects a period; this protects one task from a runaway
run. The cap reaches the run process as three environment variables, the guard
in the child prices each LLM call from its reported usage and parks the run as
``awaiting_approval`` with a ``kind: "budget"`` record, and the operator either
raises the cap (the task resumes) or stops it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

import common.budget as budget
import common.run_budget as run_budget
from agents.callbacks import RunBudgetExceeded, RunBudgetGuard
from agents.callbacks.guards import reset_run_budget_spend
from tasks import service as ts
from tasks.models import TaskStatus

PRICES = {("openai", "gpt-x"): (2.0, 8.0, 0.2)}


@pytest.fixture(autouse=True)
def _fresh_spend(monkeypatch):
    reset_run_budget_spend()
    for key in (run_budget.ENV_LIMIT, run_budget.ENV_SPENT, run_budget.ENV_PRICES):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_run_budget_spend()


def _response(input_tokens: int, output_tokens: int, cached: int = 0, model: str = "gpt-x") -> LLMResult:
    msg = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cached},
        },
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


# -------------------- workspace budget block --------------------

def test_normalize_budget_carries_the_run_limit():
    assert budget.normalize_budget({})["run_limit_usd"] == 0.0
    assert budget.normalize_budget({"run_limit_usd": "2.5"})["run_limit_usd"] == 2.5
    assert budget.normalize_budget({"run_limit_usd": -3})["run_limit_usd"] == 0.0
    assert budget.normalize_budget({"run_limit_usd": "x"})["run_limit_usd"] == 0.0


def test_set_and_get_budget_round_trip_the_run_limit():
    budget.set_budget("acme", {"hard_limit_usd": 50, "run_limit_usd": 3})
    got = budget.get_budget("acme")
    assert got["run_limit_usd"] == 3.0
    assert got["hard_limit_usd"] == 50.0
    assert budget.budget_status("acme")["run_limit_usd"] == 3.0


def test_the_costs_route_reads_and_writes_the_run_limit():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import costs

    app = FastAPI()
    app.include_router(costs.router)
    api = TestClient(app)
    prefix = costs.router.prefix or ""
    resp = api.post(f"{prefix}/budget?workspace=acme",
                    json={"hard_limit_usd": 10, "soft_limit_usd": 5, "period": "daily",
                          "run_limit_usd": 1.5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_limit_usd"] == 1.5
    assert api.get(f"{prefix}/budget?workspace=acme").json()["run_limit_usd"] == 1.5


# -------------------- effective cap and launch env --------------------

def test_the_task_cap_wins_over_the_workspace_default(monkeypatch):
    monkeypatch.setattr(budget, "get_budget", lambda ws: {"run_limit_usd": 4.0})
    assert run_budget.effective_cap(SimpleNamespace(budget_usd=None), "acme") == 4.0
    assert run_budget.effective_cap(SimpleNamespace(budget_usd=1.25), "acme") == 1.25
    # An explicit 0 is "uncapped", even where the workspace has a default.
    assert run_budget.effective_cap(SimpleNamespace(budget_usd=0), "acme") == 0.0
    assert run_budget.effective_cap(SimpleNamespace(budget_usd=None), None) == 0.0


def test_launch_env_is_empty_without_a_cap(monkeypatch):
    monkeypatch.setattr(budget, "get_budget", lambda ws: {"run_limit_usd": 0.0})
    task = SimpleNamespace(id="t-1", budget_usd=None)
    assert run_budget.launch_env(task, "acme") == {}


def test_launch_env_carries_cap_spend_and_prices(monkeypatch):
    monkeypatch.setattr("common.pricing.load_price_map", lambda: dict(PRICES))
    monkeypatch.setattr(run_budget, "task_spend_usd", lambda task_id: 0.75)
    env = run_budget.launch_env(SimpleNamespace(id="t-1", budget_usd=2.0), "acme")
    assert float(env[run_budget.ENV_LIMIT]) == 2.0
    assert float(env[run_budget.ENV_SPENT]) == 0.75
    assert json.loads(env[run_budget.ENV_PRICES]) == [["openai", "gpt-x", 2.0, 8.0, 0.2]]


def test_launch_env_fails_open(monkeypatch):
    def _boom(task_id):
        raise RuntimeError("db down")
    monkeypatch.setattr(run_budget, "price_rows", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(run_budget, "task_spend_usd", _boom)
    assert run_budget.launch_env(SimpleNamespace(id="t-1", budget_usd=2.0), "acme") == {}


def test_task_spend_sums_the_task_s_runs_without_eval_runs(monkeypatch):
    runs = [
        {"task_id": "t-1", "provider": "openai", "model": "gpt-x", "channel": "local",
         "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}}},
        {"task_id": "t-1", "provider": "openai", "model": "gpt-x", "channel": "eval",
         "process": {"token_usage": {"inbound_tokens": 1_000_000, "outbound_tokens": 0}}},
        {"task_id": "t-1", "provider": "openai", "model": "gpt-x", "channel": "local",
         "process": {"token_usage": {"inbound_tokens": 0, "outbound_tokens": 500_000}}},
    ]
    seen = {}

    def _query(**kwargs):
        seen.update(kwargs)
        return {"items": runs, "total": len(runs)}

    monkeypatch.setattr("managers.run_manager.query_runs", _query)
    monkeypatch.setattr("common.pricing.load_price_map", lambda: dict(PRICES))
    assert run_budget.task_spend_usd("t-1") == pytest.approx(2.0 + 4.0)
    assert seen["task_id"] == "t-1"


# -------------------- the guard --------------------

def test_from_env_is_none_without_a_cap(monkeypatch):
    assert RunBudgetGuard.from_env() is None
    monkeypatch.setenv(run_budget.ENV_LIMIT, "0")
    assert RunBudgetGuard.from_env() is None


def test_the_guard_prices_calls_like_run_cost_usd(monkeypatch):
    monkeypatch.setenv(run_budget.ENV_LIMIT, "100")
    monkeypatch.setenv(run_budget.ENV_PRICES, json.dumps([["openai", "gpt-x", 2.0, 8.0, 0.2]]))
    guard = RunBudgetGuard.from_env(provider="openai", model="gpt-x")
    # 600k fresh input at $2, 400k cached at $0.2, 100k output at $8.
    assert guard.cost_of(_response(1_000_000, 100_000, cached=400_000)) == pytest.approx(
        1.2 + 0.08 + 0.8)


def test_the_guard_raises_when_a_call_crosses_the_cap(monkeypatch):
    monkeypatch.setenv(run_budget.ENV_LIMIT, "1.0")
    monkeypatch.setenv(run_budget.ENV_SPENT, "0.5")
    monkeypatch.setenv(run_budget.ENV_PRICES, json.dumps([["openai", "gpt-x", 2.0, 8.0, 0.2]]))
    guard = RunBudgetGuard.from_env(provider="openai", model="gpt-x")

    guard.on_chat_model_start({}, [[]])
    guard.on_llm_end(_response(100_000, 0), run_id="c1")  # $0.20, total $0.70
    guard.on_llm_start({}, ["p"])
    with pytest.raises(RunBudgetExceeded) as exc:
        guard.on_llm_end(_response(200_000, 0), run_id="c2")  # $0.40, total $1.10
    assert exc.value.spent_usd == pytest.approx(1.1)
    assert exc.value.limit_usd == 1.0
    # The next call is refused before it is made.
    with pytest.raises(RunBudgetExceeded):
        guard.on_chat_model_start({}, [[]])


def test_a_run_launched_over_its_cap_parks_before_its_first_call(monkeypatch):
    monkeypatch.setenv(run_budget.ENV_LIMIT, "1.0")
    monkeypatch.setenv(run_budget.ENV_SPENT, "1.0")
    guard = RunBudgetGuard.from_env()
    with pytest.raises(RunBudgetExceeded):
        guard.on_llm_start({}, ["p"])


def test_a_call_seen_by_two_guards_is_charged_once():
    a = RunBudgetGuard(100.0, prices=dict(PRICES), provider="openai", model="gpt-x")
    b = RunBudgetGuard(100.0, prices=dict(PRICES), provider="openai", model="gpt-x")
    a.on_llm_end(_response(1_000_000, 0), run_id="same-call")
    b.on_llm_end(_response(1_000_000, 0), run_id="same-call")
    assert a.spent_usd == pytest.approx(2.0)
    assert b.spent_usd == pytest.approx(2.0)


def test_unknown_models_count_as_free():
    guard = RunBudgetGuard(1.0, prices=dict(PRICES), provider="other", model="mystery")
    guard.on_llm_end(_response(10_000_000, 10_000_000, model="mystery"), run_id="z")
    assert guard.spent_usd == 0.0


def test_a_dated_model_name_falls_back_to_the_agent_s_model():
    guard = RunBudgetGuard(100.0, prices=dict(PRICES), provider="openai", model="gpt-x")
    guard.on_llm_end(_response(1_000_000, 0, model="gpt-x-2026-01-01"), run_id="d")
    assert guard.spent_usd == pytest.approx(2.0)


def test_the_guard_stops_a_real_chat_model_call(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    msg = AIMessage(content="hi", usage_metadata={
        "input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000})
    model = FakeMessagesListChatModel(responses=[msg, msg])
    guard = RunBudgetGuard(1.0, prices=dict(PRICES), provider="openai", model="gpt-x")
    with pytest.raises(RunBudgetExceeded):
        model.invoke("hello", config={"callbacks": [guard]})


def test_standard_agent_turns_the_cap_into_a_budget_park(monkeypatch):
    from agents.standard_agent import StandardAgent

    agent = StandardAgent.__new__(StandardAgent)
    agent.agent_id = "swe_agent"
    agent.provider = "openai"
    agent.model = "gpt-x"
    agent.max_tool_repeats = 3
    agent.workspace = None

    seen = {}

    class _Exec:
        def invoke(self, payload, config=None):
            seen["callbacks"] = list((config or {}).get("callbacks") or [])
            raise RunBudgetExceeded(1.2, 1.0)

    agent._executor = _Exec()
    monkeypatch.setattr(StandardAgent, "_context_window_guard", lambda self: None)
    monkeypatch.setenv(run_budget.ENV_LIMIT, "1.0")
    result = agent.run("go", run_id="r-9")
    assert result.status == "awaiting_approval"
    pending = result.pending_approval
    assert pending["kind"] == "budget"
    assert pending["spent_usd"] == 1.2 and pending["limit_usd"] == 1.0
    assert pending["agent_id"] == "swe_agent" and pending["run_id"] == "r-9"
    assert "money cap" in pending["reason"]
    # The guard rides the run, last so the stats callback sees the call first.
    assert isinstance(seen["callbacks"][-1], RunBudgetGuard)


# -------------------- parking and the approve route --------------------

def _budget_parked_task(agent_id="swe_agent"):
    task = ts.create_task("costly work", workspace="acme", budget_usd=1.0)
    ts.update_task(task.id, assigned_agent_type=agent_id)
    ts.park_task_awaiting_approval(
        task.id,
        RunBudgetExceeded(1.2, 1.0).pending(agent_id=agent_id),
        run_id="r-1",
    )
    return task


def test_a_budget_park_keeps_the_numbers_and_notifies(monkeypatch):
    notes = []
    monkeypatch.setattr("plans.service.create_notification", lambda **kw: notes.append(kw))
    task = _budget_parked_task()
    parked = ts.get_task(task.id)
    assert parked.status == TaskStatus.awaiting_approval
    assert parked.pending_approval["kind"] == "budget"
    assert parked.pending_approval["spent_usd"] == 1.2
    assert parked.pending_approval["limit_usd"] == 1.0
    assert len(notes) == 1
    assert notes[0]["title"] == "Run paused at its money cap"
    assert notes[0]["source"]["task_id"] == str(task.id)
    assert notes[0]["source"]["run_id"] == "r-1"
    assert "costly work" in notes[0]["body"]


@pytest.fixture
def client(monkeypatch):
    launched = {}

    def _capture(task_id, agent_id, params=None, run_id=None):
        launched["task_id"] = str(task_id)
        launched["agent_id"] = agent_id
        launched["params"] = params or {}
        launched["budget_usd"] = ts.get_task(task_id).budget_usd
        return ("new-run", "session-1")

    from agents.registry import AgentSpec
    monkeypatch.setattr(
        "agents.registry.get_agent",
        lambda agent_id: AgentSpec(id=agent_id, name="SWE", type="local",
                                   entrypoint="agents.standard_agent:StandardAgent"),
    )
    audits = []
    monkeypatch.setattr("common.audit.record", lambda action, **kw: audits.append(action))
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tasks as task_routes

    monkeypatch.setattr(task_routes.agent_launcher, "start_run", _capture)
    app = FastAPI()
    app.include_router(task_routes.router)
    return TestClient(app), launched, audits


def test_raising_the_cap_resumes_the_task(client):
    api, launched, audits = client
    task = _budget_parked_task()

    resp = api.post(f"/api/tasks/{task.id}/approve", json={"approved": True, "budget_usd": 3})

    assert resp.status_code == 200, resp.text
    resumed = ts.get_task(task.id)
    assert resumed.status == TaskStatus.in_progress
    assert resumed.pending_approval is None
    assert resumed.budget_usd == 3.0
    # The new cap is on the task before the run is launched, so the launcher
    # hands it to the resumed run; and no tool call was approved.
    assert launched["budget_usd"] == 3.0
    assert resumed.approved_calls == []
    assert "money cap" in launched["params"]["description"]
    assert "$3.00" in launched["params"]["description"]
    assert "budget.raise" in audits


def test_a_new_cap_must_exceed_the_spend(client):
    api, launched, _ = client
    task = _budget_parked_task()
    for body in ({"approved": True}, {"approved": True, "budget_usd": 1.2},
                 {"approved": True, "budget_usd": 0.5}):
        resp = api.post(f"/api/tasks/{task.id}/approve", json=body)
        assert resp.status_code == 400, body
    assert launched == {}
    assert ts.get_task(task.id).status == TaskStatus.awaiting_approval


def test_refusing_stops_the_task_at_the_cap(client):
    api, launched, audits = client
    task = _budget_parked_task()

    resp = api.post(f"/api/tasks/{task.id}/approve", json={"approved": False})

    assert resp.status_code == 200, resp.text
    stopped = ts.get_task(task.id)
    assert stopped.status == TaskStatus.blocked
    assert "budget cap" in (stopped.blocked_reason or "").lower()
    assert stopped.pending_approval is None
    assert stopped.assigned_agent_type is None
    assert launched == {}
    assert "budget.stop" in audits
