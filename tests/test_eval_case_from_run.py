"""
"To eval case" on any run (evals/runner.py case_from_run): an agent run or an
entity run (flow, team, loop, scenario), a failed run defaulting `expected`
to empty instead of "what it did", and the `/api/evals/for-run/{run_id}` route
that lists which eval sets fit a run.

No model is called anywhere here.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

from common import entity_runs
from evals import runner, store, targets
from evals.models import EvalSet
from managers import run_manager as rm
from routes import evals as eval_routes


def run(coro):
    return asyncio.run(coro)


def _open_agent_run(run_id, agent_id="helper", *, status="completed",
                    user_message="hi there", response_text="hello world",
                    error=None, workspace="acme", model="gpt-5"):
    rm.open_run(run_id, agent_id, workspace=workspace, status="running", model=model)
    rm.update_run(run_id, {
        "status": status, "error": error, "model": model,
        "process": {
            "input_context": {"system_prompt": "", "history": [], "user_message": user_message},
            "response": {"text": response_text, "structured": None},
        },
    })


# ── Agent runs ────────────────────────────────────────────────────────────────

def test_case_from_run_agent_regression_default():
    _open_agent_run("run_a1")
    case = runner.case_from_run("run_a1")
    assert case.input == "hi there"
    assert case.expected == "hello world"
    assert case.source_run_id == "run_a1"
    assert case.metadata["target_kind"] == "agent"
    assert case.metadata["target_id"] == "helper"
    assert case.metadata["model"] == "gpt-5"


def test_case_from_run_failed_agent_defaults_expected_empty_and_keeps_error():
    _open_agent_run("run_a2", status="failed", error="boom: rate limited", response_text="")
    case = runner.case_from_run("run_a2")
    assert case.input == "hi there"
    assert case.expected is None
    assert case.metadata["error"] == "boom: rate limited"


def test_case_from_run_failed_agent_explicit_expected_still_wins():
    _open_agent_run("run_a3", status="error", error="agent crashed")
    case = runner.case_from_run("run_a3", expected="what it should have said", rubric="be polite")
    assert case.expected == "what it should have said"
    assert case.rubric == "be polite"


def test_case_from_run_unknown_id_raises():
    with pytest.raises(ValueError):
        runner.case_from_run("no-such-run")


def test_case_from_run_agent_with_no_input_raises():
    rm.open_run("run_a4", "helper", workspace="acme", status="completed")
    with pytest.raises(ValueError):
        runner.case_from_run("run_a4")


# ── Entity runs ───────────────────────────────────────────────────────────────

def test_case_from_run_team():
    entity_runs.upsert({
        "run_id": "tr1", "entity_id": "team1", "workspace": "acme", "status": "completed",
        "goal": "write a haiku", "result": "old pond / a frog jumps in / water's sound",
    }, kind="team")
    case = runner.case_from_run("tr1")
    assert case.input == "write a haiku"
    assert "frog" in case.expected
    assert case.metadata["target_kind"] == "team"
    assert case.metadata["target_id"] == "team1"


def test_case_from_run_loop_failed_defaults_expected_empty():
    entity_runs.upsert({
        "run_id": "lr1", "entity_id": "loop1", "workspace": "acme", "status": "failed",
        "goal": "converge on a summary", "result": "", "error": "ceiling reached",
    }, kind="loop")
    case = runner.case_from_run("lr1")
    assert case.input == "converge on a summary"
    assert case.expected is None
    assert case.metadata["error"] == "ceiling reached"


def test_case_from_run_scenario_renders_scores_and_state():
    entity_runs.upsert({
        "run_id": "sr1", "entity_id": "scn1", "workspace": "acme", "status": "completed",
        "title": "negotiation run", "scores": {"deal_reached": 1},
        "final_state": {"price": 42},
    }, kind="scenario")
    case = runner.case_from_run("sr1")
    assert case.input == "negotiation run"
    assert "deal_reached" in case.expected
    assert "42" in case.expected
    assert case.metadata["target_kind"] == "scenario"


def test_case_from_run_flow_derives_output_from_last_leaf_run(monkeypatch):
    entity_runs.upsert({
        "run_id": "fr1", "entity_id": "flow1", "workspace": "acme", "status": "completed",
        "title": "summarize the doc",
    }, kind="flow")
    rm.open_run("node1", "a1", workspace="acme", status="completed")
    rm.update_run("node1", {"status": "completed", "output": ""})
    rm.open_run("node2", "a2", workspace="acme", status="completed")
    rm.update_run("node2", {"status": "completed", "output": "final answer"})
    monkeypatch.setattr(targets, "leaf_run_ids", lambda kind, rid: ["node1", "node2"])
    case = runner.case_from_run("fr1")
    assert case.input == "summarize the doc"
    assert case.expected == "final answer"
    assert case.metadata["target_kind"] == "flow"
    assert case.metadata["target_id"] == "flow1"


def test_case_from_run_entity_run_with_no_input_raises():
    entity_runs.upsert({
        "run_id": "tr2", "entity_id": "team2", "workspace": "acme", "status": "completed",
        "goal": "", "result": "",
    }, kind="team")
    with pytest.raises(ValueError):
        runner.case_from_run("tr2")


# ── /api/evals/for-run/{run_id} ─────────────────────────────────────────────

def test_for_run_route_lists_only_fitting_sets_and_reports_kind():
    _open_agent_run("run_b1", agent_id="writer")
    agent_set = store.save_eval_set(EvalSet(name="agent set", workspace="acme",
                                            target={"kind": "agent", "id": "writer"}))
    store.save_eval_set(EvalSet(name="team set", workspace="acme",
                                target={"kind": "team", "id": "team1"}))

    out = run(eval_routes.eval_sets_for_run("run_b1"))
    assert out["run"]["target_kind"] == "agent"
    assert out["run"]["target_id"] == "writer"
    assert out["run"]["failed"] is False
    ids = {e["eval_set_id"] for e in out["eval_sets"]}
    assert agent_set.eval_set_id in ids
    assert len(out["eval_sets"]) == 1
    assert out["preview"]["input"] == "hi there"
    assert out["preview"]["expected"] == "hello world"
    assert out["preview_error"] is None


def test_for_run_route_reports_failed_and_entity_kind():
    entity_runs.upsert({
        "run_id": "sr2", "entity_id": "scn2", "workspace": "acme", "status": "failed",
        "error": "role crashed",
    }, kind="scenario")
    out = run(eval_routes.eval_sets_for_run("sr2"))
    assert out["run"]["target_kind"] == "scenario"
    assert out["run"]["failed"] is True
    assert out["run"]["finished"] is True
    # No title and no input recorded on this run: no usable preview.
    assert out["preview"] is None
    assert out["preview_error"]


def test_for_run_route_unknown_run_is_404():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        run(eval_routes.eval_sets_for_run("nope"))
    assert exc.value.status_code == 404
