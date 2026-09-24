"""Task outcomes: a rubric, an independent grader, another attempt or a block.

Covers tasks/outcome.py (rubric parsing, the definition, grading, what a
finished run leads to), the rubric grader in evals/graders.py, the finalizer
hook in managers/runs/task_finalize.py, the instruction sections in
tasks/context.py and the routes in dashboard/backend/routes/outcomes.py.
The grader's model is always a fake: nothing here reaches a provider.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import graders
from evals.graders import COSTED_GRADERS, GRADERS, grade, grade_rubric
from evals.models import Case, GraderSpec
from managers import run_manager as rm
from tasks import outcome as oc
from tasks import service as ts
from tasks.models import TaskStatus

RUBRIC = """# Definition of done
- **Tests pass**: the whole suite runs green
- **Docs**: the README explains the new flag
"""


class FakeGrader:
    """A chat model that answers every grading with the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.built_with = []

    def build(self, **kw):
        self.built_with.append(kw)
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        reply = self.replies.pop(0) if self.replies else self.replies_default()
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(content=reply,
                               usage_metadata={"input_tokens": 1200, "output_tokens": 80})

    @staticmethod
    def replies_default():
        return "not json at all"

    def human_text(self, i=-1):
        return self.prompts[i][-1][1]


def _grade_json(tests=True, docs=True, **overall):
    return json.dumps({
        "criteria": [
            {"name": "Tests pass", "passed": tests, "score": 1.0 if tests else 0.2,
             "feedback": "" if tests else "The suite was never run."},
            {"name": "Docs", "passed": docs, "score": 1.0 if docs else 0.4,
             "feedback": "" if docs else "README does not mention --flag."},
        ],
        "passed": tests and docs, "score": 1.0, "feedback": overall.get("feedback", "summary"),
    })


@pytest.fixture
def fake_grader(monkeypatch):
    def _make(*replies):
        fake = FakeGrader(replies)
        import agents.agent_utils as au
        monkeypatch.setattr(au, "build_chat_model", fake.build)
        monkeypatch.setattr(graders, "model_call_cost", lambda *a, **k: 0.0042)
        return fake
    return _make


@pytest.fixture
def notifications(monkeypatch):
    sent = []
    import plans.service as ps
    monkeypatch.setattr(ps, "create_notification", lambda **kw: sent.append(kw))
    return sent


# ── Rubric parsing ────────────────────────────────────────────────────────────

def test_bullets_are_the_criteria_with_named_leads():
    crit = oc.parse_rubric(RUBRIC)
    assert crit == [
        {"name": "Tests pass", "description": "the whole suite runs green"},
        {"name": "Docs", "description": "the README explains the new flag"},
    ]


def test_nested_items_fold_into_their_parent_and_checkboxes_are_stripped():
    crit = oc.parse_rubric("- [ ] Docs: README updated\n  - with an example\n1. Lint clean\n")
    assert crit[0] == {"name": "Docs", "description": "README updated (with an example)"}
    assert crit[1]["name"] == "Lint clean"


def test_a_long_bullet_is_named_by_its_opening_words():
    text = "The change log has an entry for this release and it mentions the flag by name"
    crit = oc.parse_rubric(f"- {text}")
    assert crit[0]["name"].endswith("…") and len(crit[0]["name"]) <= 61
    assert crit[0]["description"] == text


def test_headings_are_the_criteria_when_there_are_no_bullets():
    crit = oc.parse_rubric("## Correctness\nRight for every input.\n\n## Style\nLint clean.\n")
    assert [c["name"] for c in crit] == ["Correctness", "Style"]
    assert crit[0]["description"] == "Right for every input."


def test_a_rubric_without_structure_is_one_overall_criterion():
    assert oc.parse_rubric("Be correct and concise.") == [
        {"name": "Overall", "description": "Be correct and concise."}]
    assert oc.parse_rubric("   ") == []


def test_duplicate_names_are_told_apart():
    assert [c["name"] for c in oc.parse_rubric("- Same\n- Same\n")] == ["Same", "Same (2)"]


# ── The definition ────────────────────────────────────────────────────────────

def test_normalize_fills_defaults_and_reads_the_grader_forms():
    out = oc.normalize_outcome({"rubric": " - a ", "grader": "anthropic/claude-x"})
    assert out == {"rubric": "- a", "max_iterations": oc.DEFAULT_MAX_ITERATIONS,
                   "grader": {"provider": "anthropic", "model": "claude-x"}, "threshold": None}
    out = oc.normalize_outcome({"rubric": "r", "grader": {"provider": "openai", "model": "m"},
                                "threshold": 0.8, "max_iterations": 5})
    assert out["grader"] == {"provider": "openai", "model": "m"} and out["threshold"] == 0.8


@pytest.mark.parametrize("bad", [
    {"rubric": ""},
    {"rubric": "r", "max_iterations": 0},
    {"rubric": "r", "max_iterations": 11},
    {"rubric": "r", "threshold": 1.5},
    {"rubric": "r", "grader": "no-slash"},
    {"rubric": "r", "grader": {"provider": "openai"}},
])
def test_normalize_refuses_what_it_cannot_honour(bad):
    with pytest.raises(oc.OutcomeError):
        oc.normalize_outcome(bad)


def test_the_grader_is_the_named_one_then_the_env_then_the_workspace(monkeypatch):
    assert oc.resolve_grader({"provider": "p", "model": "m"}, None) == {"provider": "p", "model": "m"}
    monkeypatch.setenv(oc.GRADER_MODEL_ENV, "openai/gpt-grader")
    assert oc.resolve_grader(None, None) == {"provider": "openai", "model": "gpt-grader"}
    monkeypatch.delenv(oc.GRADER_MODEL_ENV)
    monkeypatch.setattr(oc, "_workspace_model", lambda ws: {"provider": "ollama", "model": "qwen"})
    assert oc.resolve_grader(None, "acme") == {"provider": "ollama", "model": "qwen"}


# ── The rubric grader ─────────────────────────────────────────────────────────

def test_rubric_is_a_registered_costed_grader():
    assert GRADERS["rubric"] is grade_rubric
    assert "rubric" in COSTED_GRADERS


def test_every_criterion_passing_passes(fake_grader):
    fake = fake_grader(_grade_json())
    r = grade("the work", Case(input="add a flag", rubric=RUBRIC), GraderSpec("rubric"))
    assert r.passed and r.score == 1.0
    assert [c["name"] for c in r.extra["criteria"]] == ["Tests pass", "Docs"]
    assert r.extra["cost_usd"] == 0.0042
    assert r.extra["tokens"] == {"input": 1200, "output": 80}
    # A fresh call at temperature 0 that sees the request, the rubric and the result.
    assert fake.built_with[0]["temperature"] == 0.0
    prompt = fake.human_text()
    assert "add a flag" in prompt and "Tests pass" in prompt and "the work" in prompt


def test_one_unmet_criterion_fails_and_keeps_its_feedback(fake_grader):
    fake_grader(_grade_json(docs=False))
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric"))
    assert not r.passed
    assert r.score == pytest.approx(0.7)
    docs = r.extra["criteria"][1]
    assert docs["passed"] is False and "README" in docs["feedback"]


def test_a_threshold_lets_a_high_mean_pass(fake_grader):
    fake_grader(_grade_json(docs=False))
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric", {"threshold": 0.6}))
    assert r.passed


def test_scores_on_other_scales_and_renamed_criteria_are_read(fake_grader):
    fake_grader(json.dumps({"criteria": [
        {"name": "tests  PASS", "passed": "yes", "score": 9},
        {"name": "Documentation", "score": "40%"},
    ]}))
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric"))
    tests, docs = r.extra["criteria"]
    assert tests["passed"] and tests["score"] == pytest.approx(0.9)
    assert docs["name"] == "Docs" and not docs["passed"] and docs["score"] == pytest.approx(0.4)


def test_a_criterion_the_grader_skipped_is_a_fail(fake_grader):
    fake_grader(json.dumps({"criteria": [{"name": "Tests pass", "passed": True, "score": 1}]}))
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric"))
    assert not r.passed
    assert "did not assess" in r.extra["criteria"][1]["feedback"]


def test_an_unreadable_grade_is_an_error_never_a_pass(fake_grader):
    fake_grader("It looks fine to me.")
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric"))
    assert not r.passed and r.extra["error"]
    assert "grader failed" in r.detail


def test_a_model_that_raises_is_an_error(fake_grader):
    fake_grader(RuntimeError("provider down"))
    r = grade("x", Case(rubric=RUBRIC), GraderSpec("rubric"))
    assert not r.passed and "RuntimeError" in r.extra["error"]


def test_the_tool_trail_reaches_the_grader_but_not_the_conversation(fake_grader):
    fake = fake_grader(_grade_json())
    payload = {"tool_calls": [
        {"tool": "run_tests", "input": {"path": "tests"}, "output": "3 passed"},
        {"tool": "write_file", "input": {"path": "README.md"}, "output": "ERROR: denied"},
    ], "llm_invocations": [{"messages": "SECRET AGENT REASONING"}]}
    grade_rubric("x", Case(rubric=RUBRIC), {"rubric": RUBRIC}, payload)
    prompt = fake.human_text()
    assert "run_tests" in prompt and "write_file" in prompt and "-> error" in prompt
    assert "SECRET AGENT REASONING" not in prompt


def test_no_rubric_refuses_to_grade():
    r = grade("x", Case(), GraderSpec("rubric"))
    assert not r.passed and "no rubric" in r.detail


# ── A finished run on a task with an outcome ──────────────────────────────────

def _task_with_outcome(max_iterations=3, **kw):
    t = ts.create_task("add a flag", description="Add --flag to the CLI")
    ts.update_task(t.id, outcome=oc.normalize_outcome(
        {"rubric": RUBRIC, "max_iterations": max_iterations, **kw}))
    return t


def _finish_run(task_id, agent_id="swe_agent", output="I added the flag.", params=None):
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running", link_to_session=False)
    ts.assign_agent(task_id, agent_id, params, run_id=rid)
    ts.update_task(task_id, status=TaskStatus.in_progress)
    rm.close_run(rid, status="completed", exit_code=0, output=output)
    rm.finalize_task_from_run(rid, "completed", 0)
    return rid


def test_a_passing_outcome_resolves_the_task_as_usual(fake_grader, no_launch):
    fake_grader(_grade_json())
    t = _task_with_outcome()
    rid = _finish_run(t.id)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.resolved
    assert task.assigned_agent_type is None
    [ev] = task.outcome_evaluations
    assert ev["iteration"] == 1 and ev["run_id"] == rid and ev["passed"] is True
    assert ev["trigger"] == "run" and ev["cost_usd"] == 0.0042 and ev["graded_at"]
    assert no_launch == []
    log = [e["type"] for e in ts.get_task_activity_log(t.id)]
    assert "outcome_graded" in log


def test_an_unmet_outcome_relaunches_the_same_agent_with_feedback(fake_grader, no_launch):
    fake = fake_grader(_grade_json(docs=False))
    t = _task_with_outcome()
    _finish_run(t.id, params={"description": "Implement the flag"})

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.in_progress
    assert no_launch == [(str(t.id), "swe_agent")]
    assert task.assigned_agent_type == "swe_agent"
    assert task.assigned_agent_run_id == "fake-run-id"
    desc = task.assigned_agent_params["description"]
    assert desc.startswith("Implement the flag") and "[Outcome attempt 2/3]" in desc
    assert task.outcome_evaluations[-1]["passed"] is False
    assert "outcome_retry" in [e["type"] for e in ts.get_task_activity_log(t.id)]
    # The grader saw the run's output, not the agent's conversation.
    assert "I added the flag." in fake.human_text()


def test_the_attempt_note_is_replaced_not_stacked(fake_grader, no_launch):
    fake_grader(_grade_json(docs=False), _grade_json(docs=False))
    t = _task_with_outcome()
    _finish_run(t.id, params={"description": "Implement the flag"})
    params = ts.get_task(t.id).assigned_agent_params
    _finish_run(t.id, params=params)
    desc = ts.get_task(t.id).assigned_agent_params["description"]
    assert desc.count("[Outcome attempt") == 1 and "[Outcome attempt 3/3]" in desc


def test_the_limit_blocks_the_task_naming_the_unmet_criteria(fake_grader, no_launch, notifications):
    fake_grader(_grade_json(docs=False))
    t = _task_with_outcome(max_iterations=1)
    _finish_run(t.id)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked
    assert "Docs" in task.blocked_reason and "README" in task.blocked_reason
    assert "Tests pass" not in task.blocked_reason
    assert task.assigned_agent_type is None
    assert no_launch == []
    outcome_notes = [n for n in notifications if "outcome" in n["title"]]
    assert outcome_notes and outcome_notes[0]["severity"] == "warning"
    assert "outcome_limit" in [e["type"] for e in ts.get_task_activity_log(t.id)]


def test_two_grader_errors_in_a_row_block_instead_of_burning_attempts(fake_grader, no_launch, notifications):
    fake_grader("garbage", "more garbage")
    t = _task_with_outcome(max_iterations=5)
    _finish_run(t.id)
    first = ts.get_task(t.id)
    assert first.status == TaskStatus.in_progress, "one grader error still counts as an attempt"
    assert first.outcome_evaluations[-1]["error"]
    _finish_run(t.id)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked
    assert "grader failed 2 times in a row" in task.blocked_reason
    assert len(no_launch) == 1


def test_a_launch_that_fails_blocks_rather_than_resolving(fake_grader, monkeypatch, notifications):
    fake_grader(_grade_json(docs=False))

    def _boom(*a, **k):
        raise RuntimeError("budget exceeded")
    import agents.agent_launcher as launcher
    monkeypatch.setattr(launcher, "start_run", _boom)
    t = _task_with_outcome()
    _finish_run(t.id)
    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked and "budget exceeded" in task.blocked_reason


def test_a_relaunch_moves_the_run_bound_continuation_to_the_new_run(fake_grader, no_launch, monkeypatch):
    fake_grader(_grade_json(docs=False))
    import managers.runs.task_finalize as tf
    fired = []
    monkeypatch.setattr(tf, "_trigger_session_continuation", lambda cont, st: fired.append(cont))
    from common.session_service import pop_continuations_for_task, register_continuation

    t = _task_with_outcome()
    rid = rm.new_unique_run_id()
    register_continuation("sess-1", str(t.id), run_id=rid)
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    ts.assign_agent(t.id, "swe_agent", None, run_id=rid)
    ts.update_task(t.id, status=TaskStatus.in_progress)
    rm.close_run(rid, status="completed", exit_code=0, output="done")
    rm.finalize_task_from_run(rid, "completed", 0)

    assert fired == [], "the orchestrator follows up after the last attempt, not this one"
    assert pop_continuations_for_task(str(t.id), run_id="fake-run-id")


def test_routing_and_review_runs_are_not_graded(fake_grader, no_launch):
    fake = fake_grader(_grade_json(docs=False))
    t = _task_with_outcome()
    _finish_run(t.id, agent_id="orchestrator")
    assert fake.prompts == []
    assert ts.get_task(t.id).outcome_evaluations == []


def test_a_task_without_an_outcome_never_calls_a_grader(fake_grader, no_launch):
    fake = fake_grader(_grade_json())
    t = ts.create_task("plain")
    _finish_run(t.id)
    assert ts.get_task(t.id).status == TaskStatus.resolved
    assert fake.prompts == []


def test_a_broken_outcome_check_falls_back_to_the_normal_path(monkeypatch, no_launch):
    def _boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(oc, "on_run_completed", _boom)
    t = _task_with_outcome()
    _finish_run(t.id)
    assert ts.get_task(t.id).status == TaskStatus.resolved


def test_an_agent_that_blocked_itself_is_not_graded(fake_grader, no_launch):
    fake = fake_grader(_grade_json())
    t = _task_with_outcome()
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    ts.update_task(t.id, status=TaskStatus.blocked, blocked_reason="cannot")
    rm.close_run(rid, status="completed", exit_code=0, output="gave up")
    rm.finalize_task_from_run(rid, "completed", 0)
    assert fake.prompts == [] and ts.get_task(t.id).status == TaskStatus.blocked


# ── What the next attempt reads ───────────────────────────────────────────────

def test_the_instruction_carries_the_definition_of_done():
    from tasks.context import build_task_instruction
    t = _task_with_outcome()
    text = build_task_instruction(str(t.id), "Implement it")
    assert "DEFINITION OF DONE" in text and "the whole suite runs green" in text
    assert "OUTCOME REVIEW" not in text


def test_after_a_failed_grading_the_instruction_carries_the_feedback():
    from tasks.context import build_task_instruction
    t = _task_with_outcome()
    ts.update_task(t.id, outcome_evaluations=[{
        "iteration": 1, "trigger": "run", "passed": False, "score": 0.7,
        "criteria": [
            {"name": "Tests pass", "passed": True, "score": 1.0, "feedback": ""},
            {"name": "Docs", "passed": False, "score": 0.4, "feedback": "README does not mention --flag."},
        ],
        "feedback": "Close, docs missing.",
    }])
    text = build_task_instruction(str(t.id), "Implement it")
    assert "OUTCOME REVIEW OF YOUR PREVIOUS ATTEMPT (attempt 1 of at most 3" in text
    assert "- Docs: NOT MET (0.40). README does not mention --flag." in text
    assert "- Tests pass: met (1.00)" in text
    assert "Overall: Close, docs missing." in text


def test_after_a_passing_grading_there_is_no_review_block():
    from tasks.context import build_task_instruction
    t = _task_with_outcome()
    ts.update_task(t.id, outcome_evaluations=[{"iteration": 1, "passed": True, "score": 1.0,
                                               "criteria": []}])
    assert "OUTCOME REVIEW" not in build_task_instruction(str(t.id), "x")


def test_a_grader_error_tells_the_agent_to_state_how_each_criterion_is_met():
    from tasks.context import build_task_instruction
    t = _task_with_outcome()
    ts.update_task(t.id, outcome_evaluations=[{"iteration": 1, "passed": False, "score": 0.0,
                                               "criteria": [], "error": "bad json"}])
    assert "could not assess" in build_task_instruction(str(t.id), "x")


# ── Routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    backend = str(Path(__file__).resolve().parents[1] / "dashboard" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import outcomes as outcome_routes

    app = FastAPI()
    app.include_router(outcome_routes.router)
    return TestClient(app)


def _audit_actions():
    from common import db
    rows = db.get_conn().execute("SELECT action FROM audit_log ORDER BY id").fetchall()
    return [r["action"] for r in rows]


def test_routes_set_read_and_remove_an_outcome(client):
    t = ts.create_task("work")
    assert client.get(f"/api/tasks/{t.id}/outcome").json()["outcome"] is None

    resp = client.put(f"/api/tasks/{t.id}/outcome",
                      json={"rubric": RUBRIC, "max_iterations": 4, "grader": "openai/gpt-x",
                            "threshold": 0.9})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["outcome"]["grader"] == {"provider": "openai", "model": "gpt-x"}
    assert [c["name"] for c in body["criteria"]] == ["Tests pass", "Docs"]
    assert ts.get_task(t.id).outcome["max_iterations"] == 4

    ts.update_task(t.id, outcome_evaluations=[{"iteration": 1, "passed": True}])
    assert client.get(f"/api/tasks/{t.id}/outcome/evaluations").json()["evaluations"] == [
        {"iteration": 1, "passed": True}]

    resp = client.delete(f"/api/tasks/{t.id}/outcome")
    assert resp.status_code == 200 and resp.json()["outcome"] is None
    assert ts.get_task(t.id).outcome_evaluations == []
    assert _audit_actions() == ["task.outcome.set", "task.outcome.delete"]


@pytest.mark.parametrize("body", [
    {"rubric": "  "},
    {"rubric": "r", "max_iterations": 11},
    {"rubric": "r", "threshold": 2},
    {"rubric": "r", "grader": "just-a-model"},
])
def test_routes_refuse_an_invalid_outcome(client, body):
    t = ts.create_task("work")
    assert client.put(f"/api/tasks/{t.id}/outcome", json=body).status_code == 400
    assert ts.get_task(t.id).outcome is None


def test_routes_404_on_an_unknown_task(client):
    assert client.get("/api/tasks/00000000-0000-0000-0000-000000000000/outcome").status_code == 404


def test_grade_now_grades_the_latest_run_without_moving_the_task(client, fake_grader, no_launch):
    fake_grader(_grade_json(docs=False))
    t = _task_with_outcome()
    rid = rm.new_unique_run_id()
    rm.open_run(rid, "swe_agent", task_id=str(t.id), status="running", link_to_session=False)
    rm.close_run(rid, status="completed", exit_code=0, output="the result")
    ts.update_task(t.id, status=TaskStatus.resolved)

    resp = client.post(f"/api/tasks/{t.id}/outcome/grade")
    assert resp.status_code == 200, resp.text
    ev = resp.json()["evaluation"]
    assert ev["run_id"] == rid and ev["trigger"] == "manual" and ev["passed"] is False
    task = ts.get_task(t.id)
    assert task.status == TaskStatus.resolved and no_launch == []
    assert resp.json()["attempts"] == 0, "a manual grading is not an attempt"
    assert "task.outcome.grade" in _audit_actions()


def test_grade_now_without_a_run_is_a_400(client):
    t = _task_with_outcome()
    assert client.post(f"/api/tasks/{t.id}/outcome/grade").status_code == 400


def test_task_routes_validate_an_outcome_on_create_and_update():
    """An outcome set through the task routes gets the same validation and
    defaults as one set on its own route."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import tasks as tasks_routes

    app = FastAPI()
    app.include_router(tasks_routes.router)
    client = TestClient(app)

    bad = client.post("/api/tasks", json={"title": "t", "outcome": {"rubric": "  "}})
    assert bad.status_code == 400

    ok = client.post("/api/tasks", json={"title": "t", "outcome": {"rubric": "- **Done**: it works"}})
    assert ok.status_code == 200, ok.text
    tid = ok.json()["id"]
    from tasks import service as ts
    stored = ts.get_task(tid).outcome
    assert stored["rubric"].startswith("- **Done**")
    assert stored["max_iterations"] == 3

    assert client.patch(f"/api/tasks/{tid}", json={"outcome": {"rubric": "x", "max_iterations": 99}}).status_code == 400
    assert client.patch(f"/api/tasks/{tid}", json={"outcome": None}).status_code == 200
    assert ts.get_task(tid).outcome is None


# ── A flow, team, loop or scenario finishing a task with an outcome ───────────

def _finish_executor(task_id, kind="team", output="The team added the flag.", params=None):
    from tasks.context import persist_task_result
    from tasks.models import Executor
    ts.assign_executor(task_id, Executor(kind=kind, id=f"{kind}-1"), params or {}, run_id=f"{kind}-run-1")
    ts.update_task(task_id, status=TaskStatus.in_progress)
    persist_task_result(str(task_id), f"{kind}-run-1", output, agent_id=kind)
    rm.finalize_flow_task(str(task_id), "completed", 0)


@pytest.fixture
def executor_launches(monkeypatch):
    calls = []

    def _assign(tid, executor, params, *, task_to_dict):
        calls.append((str(tid), executor.kind, dict(params or {})))
        ts.update_task(tid, status=TaskStatus.in_progress)
        return {"run_id": "next-run"}

    import tasks.assign as ta
    monkeypatch.setattr(ta, "assign_executor_to_task", _assign)
    return calls


def test_an_executor_that_meets_the_outcome_resolves(fake_grader, executor_launches):
    fake = fake_grader(_grade_json())
    t = _task_with_outcome()
    _finish_executor(t.id)

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.resolved
    [ev] = task.outcome_evaluations
    assert ev["passed"] is True and ev["run_id"] == "team-run-1"
    assert executor_launches == []
    assert "The team added the flag." in fake.human_text()


def test_an_executor_that_misses_the_outcome_starts_again_with_the_review(fake_grader, executor_launches):
    fake_grader(_grade_json(docs=False))
    t = _task_with_outcome()
    _finish_executor(t.id, params={"goal": "Add --flag"})

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.in_progress
    [(tid, kind, params)] = executor_launches
    assert tid == str(t.id) and kind == "team"
    # A team starts from a goal: the attempt note and the review go into it.
    assert params["goal"].startswith("Add --flag") and "[Outcome attempt 2/3]" in params["goal"]
    assert "OUTCOME REVIEW" in params["goal"].upper()
    assert "outcome_retry" in [e["type"] for e in ts.get_task_activity_log(t.id)]


def test_an_executor_at_the_attempt_limit_blocks(fake_grader, executor_launches, notifications):
    fake_grader(_grade_json(docs=False))
    t = _task_with_outcome(max_iterations=1)
    _finish_executor(t.id, kind="flow")

    task = ts.get_task(t.id)
    assert task.status == TaskStatus.blocked
    assert "Docs" in (task.blocked_reason or "")
    assert executor_launches == []
    assert notifications


def test_an_executors_grading_is_charged_to_its_run(fake_grader, executor_launches):
    fake_grader(_grade_json())
    t = _task_with_outcome()
    # A team working a task opens a run record under its team run id.
    rm.open_run("team-run-1", "team:team-1", task_id=str(t.id), status="running",
                link_to_session=False)
    _finish_executor(t.id)

    [ev] = ts.get_task(t.id).outcome_evaluations
    assert ev["cost_run_id"] == "team-run-1"
    [call] = rm.get_run_by_id("team-run-1")["loop"]["aux_calls"]
    assert call["purpose"] == "outcome_grader" and call["input_tokens"] == 1200


def test_a_flow_grading_is_charged_to_the_tasks_last_run(fake_grader, executor_launches):
    fake_grader(_grade_json())
    t = _task_with_outcome()
    node = rm.new_unique_run_id()
    rm.open_run(node, "writer", task_id=str(t.id), status="running", link_to_session=False)
    rm.close_run(node, status="completed", exit_code=0)
    _finish_executor(t.id, kind="flow")

    [ev] = ts.get_task(t.id).outcome_evaluations
    assert ev["cost_run_id"] == node
    assert rm.get_run_by_id(node)["loop"]["aux_calls"][0]["purpose"] == "outcome_grader"
