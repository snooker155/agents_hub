"""A/B experiments between stored versions of an agent's definition
(evals/experiments.py, the pin in agents/agent_factory.py, the routing call in
managers/runs/lifecycle.open_run and the /api/agents/{id}/experiment routes).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agents import agent_cache, prompt_assembly
from agents import versions as av
from agents.registry import AgentSpec, add_agent, replace_all_raw
from common import db
from evals import experiments
from managers import run_manager as rm

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture(autouse=True)
def isolated_definitions(tmp_path, monkeypatch):
    defs = tmp_path / "definitions"
    defs.mkdir()
    monkeypatch.setattr(prompt_assembly, "DEFINITIONS_DIR", defs)
    return defs


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    replace_all_raw([])
    experiments.clear_pins()
    agent_cache.invalidate()
    from common.config import settings
    monkeypatch.setattr(settings, "agent_cache_enabled", True)
    monkeypatch.setattr(settings, "agent_cache_ttl", 0)
    yield
    experiments.clear_pins()
    agent_cache.invalidate()
    replace_all_raw([])


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _spec(agent_id: str, *, tools=None, temperature=None) -> AgentSpec:
    return AgentSpec(id=agent_id, name=agent_id, type="langchain",
                     entrypoint="agents.definitions.demo:build",
                     tools=list(tools or []), temperature=temperature)


@pytest.fixture
def two_versions():
    """``ab_agent`` with v1 (prompt A, temperature 0.1) and v2 (prompt B,
    temperature 0.9), and the live definition equal to v2."""
    add_agent(_spec("ab_agent", temperature=0.1))
    prompt_assembly.write_instructions("ab_agent", "Prompt A")
    v1 = av.ensure_current_version("ab_agent")
    add_agent(_spec("ab_agent", temperature=0.9))
    prompt_assembly.write_instructions("ab_agent", "Prompt B")
    v2 = av.ensure_current_version("ab_agent")
    assert (v1, v2) == (1, 2)
    return v1, v2


# ── Validation ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("arms, message", [
    ([{"version": 1, "share": 1.0}], "at least two"),
    ([{"version": 1, "share": 0.5}, {"version": 2, "share": 0.4}], "sum to 1"),
    ([{"version": 1, "share": 0.5}, {"version": 1, "share": 0.5}], "two arms"),
    ([{"version": 1, "share": 0.5}, {"version": 7, "share": 0.5}], "no version 7"),
    ([{"version": 1, "share": 0.0}, {"version": 2, "share": 1.0}], "above 0"),
])
def test_arms_are_validated(two_versions, arms, message):
    with pytest.raises(ValueError, match=message):
        experiments.put_experiment("ab_agent", enabled=True, arms=arms)


def test_one_open_experiment_per_agent(two_versions):
    arms = [{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}]
    first = experiments.put_experiment("ab_agent", enabled=True, arms=arms, note="n1")
    paused = experiments.put_experiment("ab_agent", enabled=False, arms=arms, note="n2")
    assert paused["experiment_id"] == first["experiment_id"]    # same arms: same experiment
    assert paused["enabled"] is False and paused["note"] == "n2"

    changed = experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.2}, {"version": 2, "share": 0.8}])
    assert changed["experiment_id"] != first["experiment_id"]
    assert experiments.get_experiment(first["experiment_id"])["ended_at"]
    assert experiments.get_active("ab_agent")["experiment_id"] == changed["experiment_id"]

    ended = experiments.end_experiment("ab_agent")
    assert ended["ended_at"] and experiments.get_active("ab_agent") is None
    assert experiments.get_latest("ab_agent")["experiment_id"] == changed["experiment_id"]


# ── Arm choice ──────────────────────────────────────────────────────────────

def test_arm_choice_is_deterministic_and_weighted():
    exp = {"experiment_id": "exp_x", "arms": [{"version": 1, "share": 0.2},
                                              {"version": 2, "share": 0.8}]}
    picks = [experiments.choose_arm(exp, f"run:{i}")["version"] for i in range(2000)]
    assert picks == [experiments.choose_arm(exp, f"run:{i}")["version"] for i in range(2000)]
    share_v1 = picks.count(1) / len(picks)
    assert 0.16 < share_v1 < 0.24


def test_chat_turns_of_one_conversation_share_an_arm():
    key_a = experiments.routing_key_for("run-1", task_id="conv-9", session_type="chat")
    key_b = experiments.routing_key_for("run-2", task_id="conv-9", session_type="chat")
    assert key_a == key_b == "conv:conv-9"
    assert experiments.routing_key_for("run-1", task_id="T-1", session_type="task") == "run:run-1"


# ── The factory builds from the snapshot ────────────────────────────────────

class _FakeStandardAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.system_prompt = kwargs.get("system_prompt")
        self.provider = kwargs.get("provider")
        self.model = kwargs.get("model")


def test_factory_builds_a_stored_version(two_versions, isolated_definitions, monkeypatch):
    from agents import agent_factory

    monkeypatch.setattr(agent_factory, "StandardAgent", _FakeStandardAgent)
    factory = agent_factory.AgentFactory(definitions_dir=str(isolated_definitions))

    live = factory._build_agent("ab_agent")
    old = factory._build_agent("ab_agent", definition_version=1)
    assert "Prompt B" in live.system_prompt and "Prompt A" not in live.system_prompt
    assert "Prompt A" in old.system_prompt and "Prompt B" not in old.system_prompt
    assert live.kwargs["temperature"] == 0.9
    assert old.kwargs["temperature"] == 0.1

    # A missing version falls back to the live definition.
    missing = factory._build_agent("ab_agent", definition_version=42)
    assert "Prompt B" in missing.system_prompt


def test_cache_keeps_arms_apart(two_versions, isolated_definitions, monkeypatch):
    from agents.agent_factory import AgentFactory

    factory = AgentFactory(definitions_dir=str(isolated_definitions))
    built = []

    def _fake_build(agent_id, workspace=None, **overrides):
        built.append(overrides.get("definition_version"))
        return object()

    monkeypatch.setattr(factory, "_build_agent", _fake_build)
    a1 = factory.create_agent("ab_agent", workspace="ws", definition_version=1)
    a2 = factory.create_agent("ab_agent", workspace="ws", definition_version=2)
    again = factory.create_agent("ab_agent", workspace="ws", definition_version=1)
    live = factory.create_agent("ab_agent", workspace="ws")
    assert a1 is not a2 and again is a1 and live not in (a1, a2)
    assert built == [1, 2, None]


def test_open_run_routes_and_the_build_records_the_assignment(two_versions, isolated_definitions,
                                                              monkeypatch):
    from agents.agent_factory import AgentFactory

    exp = experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    factory = AgentFactory(definitions_dir=str(isolated_definitions))
    seen = []
    monkeypatch.setattr(factory, "_build_agent",
                        lambda agent_id, workspace=None, **o: seen.append(o.get("definition_version")) or object())

    rid = rm.new_unique_run_id()
    rm.open_run(rid, "ab_agent", status="running", link_to_session=False)
    expected = experiments.choose_arm(exp, f"run:{rid}")["version"]
    factory.create_agent("ab_agent")

    assert seen == [expected]
    row = experiments.assignment_for_run(rid)
    assert row["experiment_id"] == exp["experiment_id"]
    assert int(row["version"]) == expected
    # The run says which definition it actually ran.
    assert rm.get_run_by_id(rid)["definition_hash"] == av.get_version_row("ab_agent", expected)["hash"]


def test_eval_runs_and_paused_experiments_are_not_routed(two_versions):
    experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "ab_agent", status="running", channel="eval", link_to_session=False)
    assert experiments.peek_pin("ab_agent") is None

    experiments.put_experiment(
        "ab_agent", enabled=False, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "ab_agent", status="running", link_to_session=False)
    assert experiments.peek_pin("ab_agent") is None


def test_pin_to_a_deleted_version_falls_back(two_versions):
    experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "ab_agent", status="running", link_to_session=False)
    pin = experiments.peek_pin("ab_agent")
    assert pin is not None
    with db.transaction() as conn:
        conn.execute("DELETE FROM agent_versions WHERE agent_id = ? AND version = ?",
                     ("ab_agent", pin["version"]))
    assert experiments.take_pin("ab_agent") is None
    assert experiments.peek_pin("ab_agent") is None


# ── Report ──────────────────────────────────────────────────────────────────

def _run_in_arm(exp, version, *, status, tokens, score=None):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "ab_agent", status="running", link_to_session=False,
                provider="openai", model="gpt-4o-mini")
    rm.close_run(rid, status=status, exit_code=0 if status == "completed" else 1,
                 process={"token_usage": {"inbound_tokens": tokens, "outbound_tokens": tokens},
                          "duration_ms": 1000 * tokens})
    experiments.record_assignment({"run_id": rid, "agent_id": "ab_agent",
                                   "experiment_id": exp["experiment_id"], "version": version,
                                   "routing_key": f"run:{rid}"})
    if score is not None:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO online_eval_results (run_id, rule_id, agent_id, score, passed, graded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rid, "rule-1", "ab_agent", score, 1 if score >= 0.5 else 0, "2026-09-23T00:00:00+00:00"))
    return rid


def test_report_aggregates_per_arm(two_versions, client):
    exp = experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    experiments.clear_pins()
    _run_in_arm(exp, 1, status="completed", tokens=10, score=1.0)
    _run_in_arm(exp, 1, status="failed", tokens=30, score=0.0)
    _run_in_arm(exp, 2, status="completed", tokens=20, score=1.0)

    resp = client.get("/api/agents/ab_agent/experiment/report")
    assert resp.status_code == 200, resp.text
    arms = {a["version"]: a for a in resp.json()["arms"]}
    assert arms[1]["runs"] == 2 and arms[1]["completed"] == 1 and arms[1]["failed"] == 1
    assert arms[1]["mean_tokens"] == pytest.approx(40.0)
    assert arms[1]["mean_duration_ms"] == pytest.approx(20000.0)
    assert arms[1]["mean_score"] == pytest.approx(0.5)
    assert arms[1]["pass_rate"] == pytest.approx(0.5)
    assert arms[2]["runs"] == 1 and arms[2]["mean_score"] == pytest.approx(1.0)
    assert arms[2]["mean_tokens"] == pytest.approx(40.0)


# ── Routes ──────────────────────────────────────────────────────────────────

def test_experiment_routes(two_versions, client):
    assert client.get("/api/agents/ab_agent/experiment").json()["experiment"] is None

    resp = client.put("/api/agents/ab_agent/experiment", json={
        "enabled": True, "note": "new prompt",
        "arms": [{"version": 1, "share": 0.3}, {"version": "current", "share": 0.7}]})
    assert resp.status_code == 200, resp.text
    exp = resp.json()["experiment"]
    # The live definition is v2 already, so "current" resolves to it.
    assert sorted(a["version"] for a in exp["arms"]) == [1, 2]

    bad = client.put("/api/agents/ab_agent/experiment", json={
        "arms": [{"version": 1, "share": 0.3}, {"version": 2, "share": 0.3}]})
    assert bad.status_code == 400

    got = client.get("/api/agents/ab_agent/experiment").json()
    assert got["active"] is True and got["experiment"]["note"] == "new prompt"

    assert client.delete("/api/agents/ab_agent/experiment").status_code == 200
    assert client.get("/api/agents/ab_agent/experiment").json()["active"] is False
    assert client.delete("/api/agents/ab_agent/experiment").status_code == 404


def test_current_arm_snapshots_an_unsaved_live_definition(two_versions):
    prompt_assembly.write_instructions("ab_agent", "Prompt C")
    v3 = av.ensure_current_version("ab_agent")
    assert v3 == 3
    assert av.get_version_row("ab_agent", 3)["definition"]["instructions"] == "Prompt C"
    assert av.ensure_current_version("ab_agent") == 3


def test_new_snapshot_does_not_change_the_arms(two_versions):
    exp = experiments.put_experiment(
        "ab_agent", enabled=True, arms=[{"version": 1, "share": 0.5}, {"version": 2, "share": 0.5}])
    add_agent(_spec("ab_agent", temperature=0.5))     # snapshots v2's state again or v3
    prompt_assembly.write_instructions("ab_agent", "Prompt D")
    av.ensure_current_version("ab_agent")
    still = experiments.get_active("ab_agent")
    assert still["experiment_id"] == exp["experiment_id"]
    assert [a["version"] for a in still["arms"]] == [1, 2]
    assert av.get_version_row("ab_agent", 1)["definition"]["instructions"] == "Prompt A"
