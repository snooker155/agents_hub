"""
A loop that is interrupted resumes from the iteration it had reached.

Each iteration is a whole flow, so the thing worth testing is that a resumed
run does not pay for the iterations it already did: it keeps their rows, their
scores and their trajectory, and starts at the next one. The flow and the judge
are stubbed, as in tests/test_loops.py — what is under test is the bookkeeping.
"""
from __future__ import annotations

import pytest

import loops.runner as runner
from loops import store
from loops.models import Loop, LoopRun, Verdict
from loops.runner import LoopResumeError, resume_loop_run, run_loop

#: The real iteration function, captured before any test patches it. A test
#: that kills a run mid-flight puts this back before resuming, which is what
#: the restarted process would be running.
_REAL_EXECUTE = runner._execute_iteration


class _ProcessDied(BaseException):
    """Stand-in for the backend going away mid-run.

    Deliberately a BaseException: ``run_loop`` catches ``Exception`` and closes
    the run as failed, which is precisely what a killed process does *not* get
    to do. This leaves the row exactly as a crash leaves it — status running,
    position written up to the last finished iteration.
    """


@pytest.fixture
def stub_flow(monkeypatch):
    """Run the real loop over a scripted flow and a scripted judge."""
    monkeypatch.setattr(runner, "_prepare_context",
                        lambda *a, **k: ("ws", "/tmp/ws", "task-1", "sess-1"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)

    contexts: list = []

    def _make(scores, die_after=None):
        remaining = list(scores)

        def _fake_flow(**kwargs):
            contexts.append(kwargs["shared_context"])
            return ({"combined_output": f"draft {len(contexts)}",
                     "node_outputs": {"n1": "draft"}, "state": {}}, {})

        def _fake_evaluate(loop, flow, **kwargs):
            score = remaining.pop(0) if remaining else 0
            return (Verdict(score=score, verdict="stop" if score >= 90 else "continue",
                            reason=f"scored {score}", feedback=f"fix {score}"), 0.01)

        monkeypatch.setattr(runner, "_run_flow_once", _fake_flow)
        monkeypatch.setattr(runner, "evaluate", _fake_evaluate)

        if die_after is not None:
            def _die(**kwargs):
                it = _REAL_EXECUTE(**kwargs)
                if kwargs["iteration"] >= die_after:
                    # The position for this iteration has not been written yet;
                    # let run_loop record it, then die on the next pass.
                    kwargs["run"].__dict__["_die_next"] = True
                return it

            def _guard(**kwargs):
                if kwargs["run"].__dict__.get("_die_next"):
                    raise _ProcessDied()
                return _die(**kwargs)

            monkeypatch.setattr(runner, "_execute_iteration", _guard)
        return contexts

    return _make


def _seed_loop(**kw):
    loop = store.save_loop(Loop(name="L", flow_id="f", exit_criterion="c", **kw))
    flow = {"nodes": [{"id": "n1", "data": {"agent_id": "a", "category": "agent"}}], "edges": []}
    return loop, flow


# ── the position ────────────────────────────────────────────────────────────

def test_the_position_is_written_after_every_iteration(monkeypatch, stub_flow):
    loop, flow = _seed_loop(max_iterations=3, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_flow([30, 60, 95])

    run = run_loop(loop.loop_id, goal="write it")
    position = store.get_position(run.loop_run_id)
    assert position["iterations_done"] == 3
    assert position["best_score"] == 95
    assert position["previous_output"] == "draft 3"
    assert [h["score"] for h in position["history"]] == [30, 60, 95]
    assert position["verdict"]["feedback"] == "fix 95"
    assert position["heartbeat_at"]


# ── the resume ──────────────────────────────────────────────────────────────

def test_a_loop_killed_after_two_iterations_resumes_and_finishes_at_four(
    monkeypatch, stub_flow
):
    loop, flow = _seed_loop(max_iterations=4, target_score=None, patience=0)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_flow([10, 20, 30, 40], die_after=2)

    with pytest.raises(_ProcessDied):
        run_loop(loop.loop_id, goal="write it")

    # The row is left as a crash leaves it: running, with two iterations done.
    killed = store.get_run(store.list_runs(loop.loop_id, limit=1)[0].loop_run_id)
    assert killed.status == "running"
    assert killed.position["iterations_done"] == 2
    assert [r["iteration"] for r in store.list_iterations(killed.loop_run_id)] == [1, 2]

    # Resuming continues at iteration 3 and runs to the cap.
    monkeypatch.setattr(runner, "_execute_iteration", _REAL_EXECUTE)
    resumed = resume_loop_run(killed.loop_run_id)

    assert resumed.loop_run_id == killed.loop_run_id       # the same run, continued
    assert resumed.iterations_done == 4
    assert resumed.stop_reason == "max_iterations"
    assert resumed.status == "completed"
    rows = store.list_iterations(killed.loop_run_id)
    assert [r["iteration"] for r in rows] == [1, 2, 3, 4]
    assert [r["score"] for r in rows] == [10, 20, 30, 40]


def test_the_resumed_iteration_still_carries_the_last_reviewers_feedback(
    monkeypatch, stub_flow
):
    loop, flow = _seed_loop(max_iterations=3, target_score=None, patience=0)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    contexts = stub_flow([40, 50, 60], die_after=1)

    with pytest.raises(_ProcessDied):
        run_loop(loop.loop_id, goal="write it")
    run_id = store.list_runs(loop.loop_id, limit=1)[0].loop_run_id

    monkeypatch.setattr(runner, "_execute_iteration", _REAL_EXECUTE)
    resume_loop_run(run_id)

    # The pass after the resume was told what the reviewer said before it.
    assert "fix 40" in contexts[1]
    assert "draft 1" in contexts[1]


def test_the_spend_of_the_iterations_already_paid_for_is_carried_over(
    monkeypatch, stub_flow
):
    loop, flow = _seed_loop(max_iterations=3, target_score=None, patience=0)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_flow([10, 20, 30], die_after=2)

    with pytest.raises(_ProcessDied):
        run_loop(loop.loop_id, goal="write it")
    run_id = store.list_runs(loop.loop_id, limit=1)[0].loop_run_id
    spent_before = store.get_position(run_id)["spend"]

    monkeypatch.setattr(runner, "_execute_iteration", _REAL_EXECUTE)
    resumed = resume_loop_run(run_id)
    assert resumed.total_cost > spent_before


def test_a_run_with_nothing_behind_it_cannot_be_resumed():
    run = store.save_run(LoopRun(loop_id="l", status="running"))
    with pytest.raises(LoopResumeError, match="no position"):
        resume_loop_run(run.loop_run_id)


def test_a_finished_run_is_not_resumable():
    run = store.save_run(LoopRun(loop_id="l", status="completed",
                                 position={"iterations_done": 2}))
    with pytest.raises(LoopResumeError, match="already finished"):
        resume_loop_run(run.loop_run_id)


def test_an_automatic_resume_counts_itself(monkeypatch, stub_flow):
    """The watchdog's cap needs a counter that survives the process that set it."""
    loop, flow = _seed_loop(max_iterations=3, target_score=None, patience=0)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_flow([10, 20, 30], die_after=1)

    with pytest.raises(_ProcessDied):
        run_loop(loop.loop_id, goal="write it")
    run_id = store.list_runs(loop.loop_id, limit=1)[0].loop_run_id

    monkeypatch.setattr(runner, "_execute_iteration", _REAL_EXECUTE)
    resume_loop_run(run_id, auto=True)
    assert store.get_run(run_id).resume_attempts == 1


def test_a_heartbeat_is_refreshed_without_disturbing_the_position():
    run = store.save_run(LoopRun(loop_id="l", status="running"))
    store.save_position(run.loop_run_id, {"iterations_done": 2, "best_score": 70})
    store.touch_heartbeat(run.loop_run_id)
    position = store.get_position(run.loop_run_id)
    assert position["iterations_done"] == 2 and position["best_score"] == 70
    assert position["heartbeat_at"]
