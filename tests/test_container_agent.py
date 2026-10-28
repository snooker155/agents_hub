"""A worker assigned to a parent task works through its subtasks.

Covers the container agent (tasks.assign, tasks.service.promote_runnable_subtasks,
finish_container_subtask, the finalizer hook), the outcome graded on the
subtasks' results (tasks.outcome.on_container_completed, the grading run's
input) and the merged run list (tasks.service.get_task_runs).
"""
import json
from types import SimpleNamespace

import pytest

from managers import run_manager as rm
from tasks import outcome as oc
from evals import graders
from tasks import service as ts
from tasks.assign import assign_agent_to_task
from tasks.models import TaskStatus
from tasks.serialize import task_to_dict

RUBRIC = "- **Page**: index.html lists the three drinks"


class FakeGrader:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def build(self, **kw):
        return self

    def invoke(self, messages):
        self.prompts.append(messages)
        return SimpleNamespace(content=self.reply,
                               usage_metadata={"input_tokens": 300, "output_tokens": 40})


def _verdict(passed):
    return json.dumps({
        "criteria": [{"name": "Page", "passed": passed, "score": 1.0 if passed else 0.2,
                      "feedback": "" if passed else "Latte is missing."}],
        "passed": passed, "score": 1.0 if passed else 0.2, "feedback": "summary",
    })


@pytest.fixture(autouse=True)
def known_agents(monkeypatch):
    """swe_agent and the orchestrator exist; nothing else does (no reviewer)."""
    from agents import registry
    monkeypatch.setattr(registry, "get_agent", lambda agent_id: SimpleNamespace(id=agent_id)
                        if agent_id in ("swe_agent", "orchestrator") else None)


@pytest.fixture
def fake_grader(monkeypatch):
    def _make(reply):
        fake = FakeGrader(reply)
        import agents.agent_utils as au
        monkeypatch.setattr(au, "build_chat_model", fake.build)
        monkeypatch.setattr(graders, "model_call_cost", lambda *a, **k: 0.001)
        return fake
    return _make


def _parent(n=2, outcome=False):
    parent = ts.create_task("menu page", description="index.html with prices")
    subs = [ts.add_subtask(parent.id, f"part {i}") for i in range(n)]
    if outcome:
        ts.update_task(parent.id, outcome=oc.normalize_outcome({"rubric": RUBRIC, "max_iterations": 2}))
    return parent, subs


def _finish(task_id, output="done", agent_id="swe_agent"):
    """Close the run the container started on a subtask, as the run subprocess would."""
    rid = rm.new_unique_run_id()
    rm.open_run(rid, agent_id, task_id=str(task_id), status="running", link_to_session=False)
    ts.assign_agent(task_id, agent_id, None, run_id=rid)
    ts.set_task_result(task_id, output, run_id=rid, agent_id=agent_id)
    rm.close_run(rid, status="completed", exit_code=0, output=output)
    rm.finalize_task_from_run(rid, "completed", 0)
    return rid


def test_a_worker_on_a_parent_starts_its_first_subtask_not_the_parent(no_launch):
    parent, (a, b) = _parent()
    out = assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)

    assert out["container"] is True and out["run_id"] is None
    assert ts.get_task(parent.id).container_agent == "swe_agent"
    assert ts.get_task(parent.id).status == TaskStatus.in_progress
    assert no_launch == [(str(a.id), "swe_agent")]
    assert ts.get_task(a.id).assigned_agent_type == "swe_agent"
    assert ts.get_task(b.id).status == TaskStatus.todo


def test_each_finished_subtask_is_done_and_the_next_one_starts(no_launch):
    parent, (a, b) = _parent()
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)

    _finish(a.id)
    assert ts.get_task(a.id).status == TaskStatus.done
    assert no_launch[-1] == (str(b.id), "swe_agent")

    _finish(b.id)
    assert ts.get_task(b.id).status == TaskStatus.done
    assert ts.get_task(parent.id).status == TaskStatus.done


def test_the_orchestrator_takes_the_container_back(no_launch):
    parent, (a, _b) = _parent()
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    assign_agent_to_task(parent.id, "orchestrator", None, task_to_dict=task_to_dict)
    assert ts.get_task(parent.id).container_agent is None


def test_with_every_subtask_done_the_agent_runs_on_the_parent(no_launch):
    parent, subs = _parent()
    for s in subs:
        ts.update_task(s.id, status=TaskStatus.done)
    out = assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    assert not out.get("container")
    assert no_launch == [(str(parent.id), "swe_agent")]


def test_the_parent_outcome_is_graded_on_the_subtasks_results(no_launch, fake_grader):
    fake = fake_grader(_verdict(True))
    parent, (a, b) = _parent(outcome=True)
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id, output="Wrote the markup for espresso")
    _finish(b.id, output="Added latte and cappuccino prices")

    task = ts.get_task(parent.id)
    assert task.status == TaskStatus.done
    [ev] = task.outcome_evaluations
    assert ev["passed"] is True and "input_text" not in ev
    prompt = str(fake.prompts[-1])
    assert "Wrote the markup for espresso" in prompt and "Added latte and cappuccino prices" in prompt

    grading = rm.get_run_by_id(ev["grading_run_id"])
    assert "RUBRIC" in grading["input"] and "Added latte and cappuccino prices" in grading["input"]
    assert "Page: met" in grading["output"]
    # In the activity the grader acted; the container agent's work was graded.
    [entry] = [e for e in ts.get_task_activity_log(parent.id) if e["type"] == "outcome_graded"]
    assert (entry["agent"], entry["graded_agent"]) == ("outcome_grader", "swe_agent")


def test_an_unmet_parent_outcome_starts_the_agent_on_the_parent(no_launch, fake_grader):
    fake_grader(_verdict(False))
    parent, (a,) = _parent(n=1, outcome=True)
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id)

    task = ts.get_task(parent.id)
    assert task.status == TaskStatus.in_progress
    assert no_launch[-1] == (str(parent.id), "swe_agent")
    assert task.outcome_evaluations[-1]["passed"] is False


def test_a_met_retry_on_the_parent_completes_it(no_launch, fake_grader):
    grader = fake_grader(_verdict(False))
    parent, (a,) = _parent(n=1, outcome=True)
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id)
    assert ts.get_task(parent.id).status == TaskStatus.in_progress

    # The retry on the parent passes the grader: the work is finished and
    # checked, so the parent is done rather than resolved.
    grader.reply = _verdict(True)
    _finish(parent.id, output="index.html now lists latte too")

    task = ts.get_task(parent.id)
    assert task.outcome_evaluations[-1]["passed"] is True
    assert task.status == TaskStatus.done
    assert task.assigned_agent_type is None


def test_task_runs_include_side_runs_and_subtask_runs():
    parent, (a,) = _parent(n=1)
    side = rm.new_unique_run_id()
    rm.open_run(side, "decomposer", task_id=str(parent.id), status="running", link_to_session=False)
    rm.close_run(side, status="completed", exit_code=0, output="split")
    sub_run = rm.new_unique_run_id()
    rm.open_run(sub_run, "swe_agent", task_id=str(a.id), status="running", link_to_session=False)
    rm.close_run(sub_run, status="completed", exit_code=0, output="done")

    assert [r["run_id"] for r in ts.get_task_runs(parent.id)] == [side]
    runs = ts.get_task_runs(parent.id, include_subtasks=True)
    assert [r["run_id"] for r in runs] == [side, sub_run]
    assert runs[1]["subtask"]["id"] == str(a.id)


def test_results_list_every_run_with_its_saved_result_or_its_answer():
    import asyncio
    from dashboard.backend.routes.tasks import get_task_result

    parent, (a,) = _parent(n=1)
    split = rm.new_unique_run_id()
    rm.open_run(split, "decomposer", task_id=str(parent.id), status="running", link_to_session=False)
    rm.close_run(split, status="completed", exit_code=0, output="Split into one subtask")
    work = rm.new_unique_run_id()
    rm.open_run(work, "swe_agent", task_id=str(a.id), status="running", link_to_session=False)
    ts.set_task_result(a.id, "Wrote index.html", files=["index.html"], run_id=work, agent_id="swe_agent")
    rm.close_run(work, status="completed", exit_code=0, output="raw answer")

    out = asyncio.run(get_task_result(parent.id))
    assert [(e["agent_id"], e["result"]) for e in out["results"]] == [
        ("decomposer", "Split into one subtask"), ("swe_agent", "Wrote index.html")]
    assert out["results"][1]["subtask"]["id"] == str(a.id)
    assert out["files"] == ["index.html"]


def test_activity_names_the_agent_and_plain_statuses(no_launch):
    task = ts.create_task("solo")
    assign_agent_to_task(task.id, "swe_agent", None, task_to_dict=task_to_dict)
    log = ts.get_task_activity_log(task.id)
    assigned = [e for e in log if e["type"] == "agent_assigned"]
    assert [e["agent"] for e in assigned] == ["swe_agent"]
    [change] = [e for e in log if e["type"] == "status_change"]
    assert (change["from"], change["to"], change["agent"]) == ("todo", "in_progress", "swe_agent")
    assert "TaskStatus" not in change["message"]


def test_a_subtask_whose_agent_reports_blocked_stops_the_container(no_launch):
    parent, (a, b) = _parent()
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id, output="Checked the page: it needs a browser to verify.\n"
                          "STATUS: blocked: no browser to open the page")
    sub = ts.get_task(a.id)
    assert sub.status == TaskStatus.blocked
    assert "no browser to open the page" in sub.blocked_reason
    # Nothing else starts: the person decides what happens next.
    assert ts.get_task(b.id).status == TaskStatus.todo
    assert ts.get_task(b.id).assigned_agent_type is None


def test_a_subtask_reporting_done_goes_on(no_launch):
    parent, (a, b) = _parent()
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id, output="Created index.html.\nSTATUS: done")
    assert ts.get_task(a.id).status == TaskStatus.done
    assert no_launch[-1] == (str(b.id), "swe_agent")


def test_deleting_the_last_open_subtask_completes_the_parent(no_launch):
    parent, (a, b) = _parent()
    assign_agent_to_task(parent.id, "swe_agent", None, task_to_dict=task_to_dict)
    _finish(a.id)
    ts.stop_task(b.id)            # a check nobody here can make
    assert ts.get_task(parent.id).status == TaskStatus.in_progress
    ts.delete_task(b.id)
    assert ts.get_task(parent.id).status == TaskStatus.done


def test_files_of_every_run_are_listed_not_the_latest_alone():
    import asyncio
    from dashboard.backend.routes.tasks import get_task_result

    parent, (a,) = _parent(n=1)
    first, retry = rm.new_unique_run_id(), rm.new_unique_run_id()
    ts.set_task_result(a.id, "Styled the page", files=["index.html", "styles.css"],
                       run_id=first, agent_id="swe_agent")
    ts.set_task_result(a.id, "Checked again, nothing to change", files=[],
                       run_id=retry, agent_id="swe_agent")

    assert asyncio.run(get_task_result(a.id))["files"] == ["index.html", "styles.css"]
    assert asyncio.run(get_task_result(parent.id))["files"] == ["index.html", "styles.css"]
