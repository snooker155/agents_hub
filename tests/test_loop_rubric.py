"""A loop graded against a rubric: the same per-criterion grader a task's
outcome uses (evals.graders.grade_rubric), mapped onto the loop's Verdict.

Without a rubric the old evaluator path runs unchanged (tests/test_loops.py
covers it); these tests pin the rubric path, its persistence and its routes.
The grader's model is always a fake.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import graders
from loops import evaluator, store
from loops.models import Loop, Verdict
from loops.runner import run_loop

RUBRIC = "- **Sources**: every claim cites a source\n- **Length**: under 800 words\n"


def _reply(sources=True, length=True):
    return json.dumps({"criteria": [
        {"name": "Sources", "passed": sources, "score": 1.0 if sources else 0.3,
         "feedback": "" if sources else "Paragraph 2 cites nothing."},
        {"name": "Length", "passed": length, "score": 1.0 if length else 0.5,
         "feedback": "" if length else "Cut 200 words."},
    ], "feedback": "overall note"})


@pytest.fixture
def fake_model(monkeypatch):
    def _make(*replies):
        state = {"replies": list(replies), "prompts": [], "built": []}

        class _LLM:
            def invoke(self, messages):
                state["prompts"].append(messages)
                return SimpleNamespace(content=state["replies"].pop(0) if state["replies"] else "??",
                                       usage_metadata={"input_tokens": 10, "output_tokens": 5})

        def _build(**kw):
            state["built"].append(kw)
            return _LLM()

        import agents.agent_utils as au
        monkeypatch.setattr(au, "build_chat_model", _build)
        monkeypatch.setattr(graders, "model_call_cost", lambda *a, **k: 0.01)
        return state
    return _make


def test_rubric_and_grader_round_trip_through_the_store():
    loop = store.save_loop(Loop(name="L", flow_id="f", rubric=RUBRIC,
                                grader={"provider": "openai", "model": "gpt-grader"}))
    again = store.get_loop(loop.loop_id)
    assert again.rubric == RUBRIC
    assert again.grader == {"provider": "openai", "model": "gpt-grader"}
    assert Loop.from_dict({"grader": "anthropic/claude-x"}).grader == {
        "provider": "anthropic", "model": "claude-x"}
    assert Loop.from_dict({}).rubric == "" and Loop.from_dict({}).grader is None


def test_a_rubric_that_passes_stops_the_loop(fake_model, monkeypatch):
    state = fake_model(_reply())
    monkeypatch.setattr(evaluator, "_evaluate_with_model",
                        lambda *a, **k: pytest.fail("the old evaluator must not run"))
    loop = Loop(name="L", flow_id="f", rubric=RUBRIC, target_score=None,
                grader={"provider": "openai", "model": "gpt-grader"})
    verdict, cost = evaluator.evaluate(loop, {}, goal="write it", output="the article", iteration=1)
    assert verdict.done and verdict.score == 100.0
    assert verdict.feedback == "" and cost == 0.01
    assert verdict.agent == "rubric:gpt-grader"
    assert state["built"][0] == {"provider": "openai", "model": "gpt-grader", "temperature": 0.0}
    raw = json.loads(verdict.raw)
    assert raw["kind"] == "rubric" and [c["name"] for c in raw["criteria"]] == ["Sources", "Length"]


def test_unmet_criteria_become_the_next_passes_feedback(fake_model):
    fake_model(_reply(sources=False))
    loop = Loop(name="L", flow_id="f", rubric=RUBRIC, target_score=None)
    verdict, _ = evaluator.evaluate(loop, {}, goal="g", output="o", iteration=1)
    assert verdict.verdict == "continue"
    assert verdict.score == pytest.approx(65.0)
    assert "- Sources: Paragraph 2 cites nothing." in verdict.feedback
    assert "Length" not in verdict.feedback
    assert "Overall: overall note" in verdict.feedback


def test_the_target_score_is_the_rubrics_threshold(fake_model):
    fake_model(_reply(length=False))
    loop = Loop(name="L", flow_id="f", rubric=RUBRIC, target_score=70)
    verdict, _ = evaluator.evaluate(loop, {}, goal="g", output="o", iteration=1)
    assert verdict.done and verdict.score == pytest.approx(75.0)


def test_a_grader_failure_is_a_continue_never_an_acceptance(fake_model):
    fake_model("I like it")
    loop = Loop(name="L", flow_id="f", rubric=RUBRIC)
    verdict, _ = evaluator.evaluate(loop, {}, goal="g", output="o", iteration=1)
    assert verdict.verdict == "continue" and verdict.error and verdict.score is None
    assert "could not assess" in verdict.feedback


def test_without_a_rubric_the_old_evaluator_runs(monkeypatch):
    called = []
    monkeypatch.setattr(evaluator, "_evaluate_with_model",
                        lambda prompt, **k: (called.append(prompt) or Verdict(verdict="stop"), 0.0))
    loop = Loop(name="L", flow_id="f", exit_criterion="good", evaluator_mode="model")
    verdict, _ = evaluator.evaluate(loop, {}, goal="g", output="o", iteration=1)
    assert called and verdict.done


def test_the_loop_evaluator_model_grades_when_no_grader_is_named(fake_model):
    state = fake_model(_reply())
    loop = Loop(name="L", flow_id="f", rubric=RUBRIC, evaluator_mode="model",
                evaluator_provider="ollama", evaluator_model="qwen")
    evaluator.evaluate(loop, {}, goal="g", output="o", iteration=1)
    assert state["built"][0]["model"] == "qwen" and state["built"][0]["provider"] == "ollama"


def test_a_whole_loop_converges_on_the_rubric(fake_model, monkeypatch):
    """The real runner with the flow stubbed and the rubric grader faked:
    the first pass misses a criterion, the second is told which and passes."""
    import loops.runner as runner

    fake_model(_reply(sources=False), _reply())
    monkeypatch.setattr(runner, "_prepare_context",
                        lambda *a, **k: ("ws", "/tmp/ws", "task-1", "sess-1"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)
    contexts = []

    def _fake_flow(**kwargs):
        contexts.append(kwargs["shared_context"])
        return ({"combined_output": f"draft {len(contexts)}", "node_outputs": {}, "state": {}}, {})

    monkeypatch.setattr(runner, "_run_flow_once", _fake_flow)
    loop = store.save_loop(Loop(name="L", flow_id="f", rubric=RUBRIC, max_iterations=4,
                                target_score=None))
    monkeypatch.setattr("flow.store.get_flow", lambda fid: {"nodes": [], "edges": []})

    run = run_loop(loop.loop_id, goal="write it")
    assert run.iterations_done == 2 and run.stop_reason == "criterion_met"
    assert "Paragraph 2 cites nothing." in contexts[1]
    assert "Rubric (graded per criterion)" in contexts[1]
    rows = store.list_iterations(run.loop_run_id)
    assert [r["verdict"] for r in rows] == ["continue", "stop"]
    assert json.loads(rows[1]["evaluator_raw"])["passed"] is True


# ── Routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import loops as loop_routes

    monkeypatch.setattr("flow.store.get_flow", lambda fid: {"id": fid, "name": "F", "nodes": [], "edges": []})
    app = FastAPI()
    app.include_router(loop_routes.router)
    return TestClient(app)


def test_routes_accept_a_rubric_and_a_grader(client):
    resp = client.post("/api/loops", json={"name": "L", "flow_id": "f", "rubric": RUBRIC,
                                           "grader": "openai/gpt-grader"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rubric"] == RUBRIC
    assert body["grader"] == {"provider": "openai", "model": "gpt-grader"}
    assert body["resolved_evaluator"]["mode"] == "rubric"
    assert [c["name"] for c in body["resolved_evaluator"]["criteria"]] == ["Sources", "Length"]

    resp = client.put(f"/api/loops/{body['loop_id']}", json={"name": "L", "flow_id": "f", "rubric": ""})
    assert resp.status_code == 200
    assert resp.json()["rubric"] == "" and resp.json()["resolved_evaluator"]["mode"] != "rubric"


def test_routes_refuse_a_grader_without_a_model(client):
    resp = client.post("/api/loops", json={"name": "L", "flow_id": "f", "rubric": RUBRIC,
                                           "grader": "nomodel"})
    assert resp.status_code == 400
