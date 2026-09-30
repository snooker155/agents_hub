"""Online evals: the ``online_eval`` alert rule, sampling, the grading loop,
threshold firing and the per-agent summary (evals/online.py).

A run is opened and closed through ``managers.run_manager`` exactly as any
run is, so ``evaluate_run_finished`` is reached from the real finish path.
Firing is captured by replacing ``notify.rules._fire``, the one function the
grading loop calls to raise the rule's notification.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from evals import online
from managers import run_manager as rm
from notify import rules as notify_rules
from notify import store as notify_store
from workspace import create_workspace_folder

BACKEND = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


@pytest.fixture
def ws():
    name = f"online-evals-ws-{uuid4().hex[:8]}"
    create_workspace_folder(name)
    return name


@pytest.fixture
def fired(monkeypatch):
    calls = []

    def _capture(workspace, rule, *, title, body, severity):
        calls.append({"workspace": workspace, "rule": rule, "title": title,
                      "body": body, "severity": severity})

    monkeypatch.setattr(notify_rules, "_fire", _capture)
    return calls


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.main import app
    return TestClient(app)


def _rule(ws, **over):
    data = {
        "kind": "online_eval",
        "agent_id": "qa_agent",
        "sample_rate": 1.0,
        "graders": [{"kind": "exact", "params": {"expected": "hello"}}],
        "min_score": 0.5,
        "severity": "error",
        "channels": ["dashboard"],
    }
    data.update(over)
    return notify_store.create_rule(ws, data)


def _finished_run(ws, output, *, agent_id="qa_agent", status="completed", channel="local"):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, status="running", workspace=ws, channel=channel,
                link_to_session=False)
    rm.close_run(rid, status=status, exit_code=0 if status == "completed" else 1,
                 output=output, input="say hello")
    return rid


# ── Rule creation ───────────────────────────────────────────────────────────

def test_create_rule_keeps_online_eval_fields(ws):
    rule = _rule(ws, sample_rate=0.25, rubric="Is it polite?")
    assert rule["kind"] == "online_eval"
    assert rule["sample_rate"] == 0.25
    assert rule["min_score"] == 0.5
    assert rule["severity"] == "error"
    assert rule["graders"][0]["kind"] == "exact"
    assert rule["rubric"] == "Is it polite?"


@pytest.mark.parametrize("bad", [
    {"graders": []},
    {"graders": [{"kind": "no_such_grader"}]},
    {"sample_rate": 1.5},
    {"min_score": -0.1},
    {"severity": "loud"},
])
def test_create_rule_refuses_bad_fields(ws, bad):
    with pytest.raises(ValueError):
        _rule(ws, **bad)


def test_rule_api_round_trip(ws, client):
    resp = client.post(f"/api/notify/rules?workspace={ws}", json={
        "kind": "online_eval", "agent_id": "qa_agent", "sample_rate": 0.5,
        "graders": [{"kind": "regex", "params": {"pattern": "hel+o"}},
                    {"kind": "llm_judge", "params": {"rubric": "Friendly?"}, "weight": 2}],
        "min_score": 0.7, "severity": "warning",
    })
    assert resp.status_code == 201, resp.text
    rule = resp.json()["rule"]
    assert [g["kind"] for g in rule["graders"]] == ["regex", "llm_judge"]
    assert rule["graders"][1]["weight"] == 2

    resp = client.patch(f"/api/notify/rules/{rule['id']}?workspace={ws}",
                        json={"sample_rate": 0.1, "min_score": 0.9})
    assert resp.status_code == 200
    assert resp.json()["rule"]["sample_rate"] == 0.1
    assert resp.json()["rule"]["graders"] == rule["graders"]

    resp = client.patch(f"/api/notify/rules/{rule['id']}?workspace={ws}",
                        json={"sample_rate": 3})
    assert resp.status_code == 400

    bad = client.post(f"/api/notify/rules?workspace={ws}",
                      json={"kind": "online_eval", "graders": []})
    assert bad.status_code == 400


# ── Sampling ────────────────────────────────────────────────────────────────

def test_sampling_is_deterministic_and_follows_the_rate():
    rule = {"id": "rule-1", "sample_rate": 0.3}
    ids = [f"run-{i}" for i in range(2000)]
    first = [online.is_sampled(rule, r) for r in ids]
    again = [online.is_sampled(rule, r) for r in ids]
    assert first == again
    share = sum(first) / len(first)
    assert 0.25 < share < 0.35
    assert not any(online.is_sampled({"id": "r", "sample_rate": 0.0}, r) for r in ids[:50])
    assert all(online.is_sampled({"id": "r", "sample_rate": 1.0}, r) for r in ids[:50])
    # A different rule samples a different subset of the same runs.
    other = [online.is_sampled({"id": "rule-2", "sample_rate": 0.3}, r) for r in ids]
    assert other != first


def test_finish_path_only_queues_matching_completed_runs(ws, fired):
    _rule(ws)
    good = _finished_run(ws, "hello")
    _finished_run(ws, "hello", agent_id="someone_else")
    _finished_run(ws, "boom", status="failed")
    _finished_run(ws, "hello", channel="eval")

    jobs = online.list_jobs()
    assert [j["run_id"] for j in jobs] == [good]
    assert jobs[0]["status"] == "pending"
    # Nothing is graded on the finish path itself.
    assert fired == []


# ── The grading loop ────────────────────────────────────────────────────────

def test_loop_grades_a_passing_run_without_firing(ws, fired):
    _rule(ws)
    rid = _finished_run(ws, "Hello")

    assert online.process_pending() == 1
    results = online.recent_results("qa_agent")
    assert len(results) == 1
    res = results[0]
    assert res["run_id"] == rid
    assert res["score"] == 1.0 and res["passed"] is True
    assert res["details"][0]["kind"] == "exact"
    assert res["definition_hash"] is None or isinstance(res["definition_hash"], str)
    assert online.list_jobs()[0]["status"] == "done"
    assert fired == []
    # A second pass finds nothing left to do.
    assert online.process_pending() == 0


def test_loop_fires_below_the_threshold(ws, fired):
    rule = _rule(ws)
    rid = _finished_run(ws, "goodbye")

    online.process_pending()
    res = online.recent_results("qa_agent")[0]
    assert res["score"] == 0.0 and res["passed"] is False
    assert len(fired) == 1
    assert fired[0]["severity"] == "error"
    assert fired[0]["rule"]["id"] == rule["id"]
    assert rid in fired[0]["body"]


def test_rule_level_expected_and_weights(ws, fired):
    _rule(ws, expected="hello", min_score=0.4, graders=[
        {"kind": "exact", "weight": 1},
        {"kind": "regex", "params": {"pattern": "^nope$"}, "weight": 1},
    ])
    _finished_run(ws, "hello")
    online.process_pending()
    res = online.recent_results("qa_agent")[0]
    assert res["score"] == pytest.approx(0.5)
    assert res["passed"] is False           # every grader must pass
    assert fired == []                      # but 0.5 is not below 0.4


def test_failed_grading_is_recorded_not_retried(ws, fired):
    rule = _rule(ws)
    online.maybe_enqueue(ws, rule, {"run_id": "ghost-run", "status": "completed",
                                    "agent_id": "qa_agent"})
    assert online.process_pending() == 0
    job = online.list_jobs()[0]
    assert job["status"] == "failed"
    assert "not found" in (job["error"] or "")
    assert online.process_pending() == 0
    assert online.list_jobs()[0]["attempts"] == 1


def test_jobs_survive_a_restart_and_abandoned_ones_are_requeued(ws, fired, monkeypatch):
    _rule(ws)
    _finished_run(ws, "hello")
    # A process claimed the job and died before finishing it.
    claimed = online._claim(10)
    assert len(claimed) == 1
    assert online.list_jobs()[0]["status"] == "running"
    monkeypatch.setattr(online, "STALE_RUNNING_SECONDS", -1)
    assert online.process_pending() == 1
    job = online.list_jobs()[0]
    assert job["status"] == "done" and job["attempts"] == 2


# ── Summary endpoint ────────────────────────────────────────────────────────

def test_summary_endpoint_groups_by_rule_and_version(ws, fired, client):
    r1 = _rule(ws)
    r2 = _rule(ws, graders=[{"kind": "regex", "params": {"pattern": "o"}}])
    _finished_run(ws, "hello")
    _finished_run(ws, "nope")
    online.process_pending()

    resp = client.get("/api/agents/qa_agent/online-evals/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 4
    by_rule = {r["rule_id"]: r for r in body["by_rule"]}
    assert by_rule[r1["id"]]["count"] == 2
    assert by_rule[r1["id"]]["pass_rate"] == 0.5
    assert by_rule[r2["id"]]["pass_rate"] == 1.0
    assert sum(v["count"] for v in body["by_version"]) == 4
    assert body["pending_jobs"] == 0

    recent = client.get("/api/agents/qa_agent/online-evals?limit=3").json()["results"]
    assert len(recent) == 3
