"""
Loops: the exit criterion, the convergence rules, and the feedback carried back.

The evaluator and the flow are both stubbed here. That is deliberate — what is
worth testing about a loop is not that an LLM can be called, but that it stops
when it should, keeps going when it should, and hands the next attempt what the
reviewer actually said.
"""
import pytest

from loops import store
from loops.evaluator import build_evaluation_prompt, parse_verdict, resolve_final_agent
from loops.models import Iteration, Loop, LoopRun, Verdict
from loops.runner import (
    LoopStopped, _check_convergence, build_iteration_context, run_loop,
)


# ── Reading the judge's answer ────────────────────────────────────────────────

def test_a_clean_json_verdict_is_read_whole():
    v = parse_verdict('{"score": 72, "verdict": "continue", "reason": "thin", "feedback": "cite it"}')
    assert (v.score, v.verdict, v.reason, v.feedback) == (72.0, "continue", "thin", "cite it")
    assert not v.done


def test_a_fenced_verdict_wrapped_in_prose_is_still_read():
    v = parse_verdict('Sure!\n```json\n{"score": 90, "verdict": "stop"}\n```\nHope that helps.')
    assert v.score == 90.0 and v.done


def test_an_unparseable_answer_never_counts_as_approval():
    """A judge that could not make itself understood has not accepted anything."""
    v = parse_verdict("Looks good to me, honestly.")
    assert v.verdict == "continue"
    assert v.error
    # The prose is still worth something: it becomes the next attempt's feedback.
    assert v.feedback == "Looks good to me, honestly."


def test_an_empty_answer_continues_and_records_the_error():
    v = parse_verdict("")
    assert v.verdict == "continue" and v.error


def test_synonyms_for_stopping_are_accepted():
    for word in ("stop", "done", "accept", "PASS", "complete"):
        assert parse_verdict('{"verdict": "%s"}' % word).done


def test_a_score_out_of_range_is_clamped_not_trusted():
    assert parse_verdict('{"score": 420, "verdict": "stop"}').score == 100.0
    assert parse_verdict('{"score": -5, "verdict": "stop"}').score == 0.0


def test_a_score_written_as_text_is_still_a_number():
    assert parse_verdict('{"score": "72/100", "verdict": "continue"}').score == 72.0


# ── Who judges ────────────────────────────────────────────────────────────────

def _flow(*node_ids, tail_is_agent=True):
    nodes = [
        {"id": nid, "data": {"agent_id": f"agent_{nid}", "category": "agent"}}
        for nid in node_ids
    ]
    if not tail_is_agent:
        nodes[-1]["data"] = {"entity_id": "join", "category": "processor"}
    edges = [{"source": a, "target": b} for a, b in zip(node_ids, node_ids[1:])]
    return {"nodes": nodes, "edges": edges, "entry_point": node_ids[0]}


def test_the_judge_is_the_flows_last_agent():
    assert resolve_final_agent(_flow("n1", "n2", "n3")) == "agent_n3"


def test_a_non_agent_tail_is_skipped_backwards():
    """A transform node has no opinion; the last agent before it does."""
    assert resolve_final_agent(_flow("n1", "n2", tail_is_agent=False)) == "agent_n1"


def test_a_flow_with_no_agents_has_no_judge():
    assert resolve_final_agent({"nodes": [], "edges": []}) is None


# ── The prompt the judge is given ─────────────────────────────────────────────

def test_the_evaluation_prompt_carries_the_criterion_and_the_history():
    prompt = build_evaluation_prompt(
        criterion="every claim is sourced", goal="write the post",
        output="a draft", iteration=3, max_iterations=5,
        history=[{"iteration": 1, "score": 40, "reason": "thin"},
                 {"iteration": 2, "score": 55, "reason": "better"}],
    )
    assert "every claim is sourced" in prompt
    assert "attempt 1: score 40" in prompt and "attempt 2: score 55" in prompt
    assert "Attempt 3 of at most 5" in prompt


def test_a_very_long_output_is_clipped_from_the_middle():
    """The closing section of a draft is the most revealing part, so it stays."""
    output = "HEAD" + ("x" * 40000) + "TAIL"
    prompt = build_evaluation_prompt(
        criterion="c", goal="g", output=output, iteration=1, max_iterations=2)
    assert "HEAD" in prompt and "TAIL" in prompt
    assert "omitted from the middle" in prompt
    assert len(prompt) < len(output)


# ── What the next attempt is told ─────────────────────────────────────────────

def test_the_first_attempt_sees_only_the_request():
    assert build_iteration_context(
        goal="write the post", criterion="c", iteration=1,
        previous_output="", verdict=None) == "write the post"


def test_a_later_attempt_sees_the_rejection_and_the_feedback():
    ctx = build_iteration_context(
        goal="write the post", criterion="every claim is sourced", iteration=2,
        previous_output="the rejected draft",
        verdict=Verdict(score=55, verdict="continue", reason="unsourced",
                        feedback="add a citation to each claim"),
    )
    assert "ATTEMPT 2" in ctx
    assert "add a citation to each claim" in ctx
    assert "the rejected draft" in ctx
    assert "55/100" in ctx
    # An agent told to run again without being told why produces the same thing.
    assert "unsourced" in ctx


# ── Convergence ───────────────────────────────────────────────────────────────

def _loop(**kw):
    return Loop(name="L", flow_id="f", exit_criterion="c", **kw)


def _iteration(**kw):
    return Iteration(loop_run_id="r", iteration=kw.pop("iteration", 1), **kw)


def test_a_stop_verdict_ends_the_loop():
    with pytest.raises(LoopStopped) as e:
        _check_convergence(_loop(min_iterations=1, target_score=None),
                           _iteration(verdict="stop", score=90),
                           iteration=1, stale=0)
    assert e.value.reason == "criterion_met"


def test_min_iterations_overrules_an_early_stop():
    """Models praise their own first draft; a loop that never runs twice is a flow."""
    _check_convergence(_loop(min_iterations=2), _iteration(verdict="stop", score=99),
                       iteration=1, stale=0)


def test_the_target_score_ends_the_loop_even_on_a_continue_verdict():
    with pytest.raises(LoopStopped) as e:
        _check_convergence(_loop(target_score=80), _iteration(verdict="continue", score=85),
                           iteration=1, stale=0)
    assert e.value.reason == "target_score"


def test_a_loop_that_stops_improving_stops_running():
    with pytest.raises(LoopStopped) as e:
        _check_convergence(_loop(patience=2), _iteration(verdict="continue", score=50),
                           iteration=4, stale=2)
    assert e.value.reason == "no_improvement"


def test_patience_zero_disables_the_check():
    _check_convergence(_loop(patience=0, target_score=None),
                       _iteration(verdict="continue", score=10), iteration=9, stale=9)


def test_a_flow_that_produced_nothing_ends_the_loop():
    with pytest.raises(LoopStopped) as e:
        _check_convergence(_loop(), _iteration(status="failed", output=""),
                           iteration=1, stale=0)
    assert e.value.reason == "flow_failed"


# ── The whole loop, with the flow and the judge stubbed ───────────────────────

@pytest.fixture
def stub_run(monkeypatch):
    """Run the real loop over a scripted flow and a scripted judge."""
    import loops.runner as runner

    monkeypatch.setattr(runner, "_prepare_context",
                        lambda *a, **k: ("ws", "/tmp/ws", "task-1", "sess-1"))
    monkeypatch.setattr(runner, "_finalize_task", lambda run: None)
    monkeypatch.setattr(runner, "_publish", lambda *a, **k: None)

    contexts: list = []

    def _make(scores):
        remaining = list(scores)

        def _fake_flow(**kwargs):
            contexts.append(kwargs["shared_context"])
            return ({"combined_output": f"draft {len(contexts)}",
                     "node_outputs": {"n1": "draft"}, "state": {}}, {})

        def _fake_evaluate(loop, flow, **kwargs):
            score = remaining.pop(0) if remaining else 0
            return (Verdict(score=score,
                            verdict="stop" if score >= 90 else "continue",
                            reason=f"scored {score}", feedback=f"fix {score}"), 0.01)

        monkeypatch.setattr(runner, "_run_flow_once", _fake_flow)
        monkeypatch.setattr(runner, "evaluate", _fake_evaluate)
        return contexts

    return _make


def _seed_loop(**kw):
    from flow import store as flow_store
    import loops.runner as runner
    loop = store.save_loop(Loop(name="L", flow_id="f", exit_criterion="c", **kw))
    # The runner loads the flow itself; a minimal stand-in is enough because the
    # execution of it is stubbed.
    runner_flow = {"nodes": [{"id": "n1", "data": {"agent_id": "a", "category": "agent"}}],
                   "edges": []}
    return loop, runner_flow


def test_a_loop_stops_as_soon_as_the_judge_accepts(monkeypatch, stub_run):
    loop, flow = _seed_loop(max_iterations=5, min_iterations=1, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_run([50, 95, 99])

    run = run_loop(loop.loop_id, goal="write it")
    assert run.iterations_done == 2
    assert run.stop_reason == "criterion_met"
    assert run.status == "completed"


def test_every_iteration_is_kept_with_its_own_score(monkeypatch, stub_run):
    loop, flow = _seed_loop(max_iterations=4, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_run([30, 60, 95])

    run = run_loop(loop.loop_id, goal="write it")
    rows = store.list_iterations(run.loop_run_id)
    assert [r["iteration"] for r in rows] == [1, 2, 3]
    assert [r["score"] for r in rows] == [30, 60, 95]
    assert [r["verdict"] for r in rows] == ["continue", "continue", "stop"]
    assert run.best_score == 95


def test_the_reviewers_feedback_reaches_the_next_iteration(monkeypatch, stub_run):
    loop, flow = _seed_loop(max_iterations=3, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    contexts = stub_run([40, 95])

    run_loop(loop.loop_id, goal="write it")
    assert contexts[0] == "write it"
    assert "fix 40" in contexts[1]
    assert "draft 1" in contexts[1]


def test_the_iteration_cap_is_a_completed_run_not_a_failure(monkeypatch, stub_run):
    loop, flow = _seed_loop(max_iterations=2, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_run([10, 20, 30])

    run = run_loop(loop.loop_id, goal="write it")
    assert run.iterations_done == 2
    assert run.stop_reason == "max_iterations"
    # The two passes it did run are real work, so the run completed.
    assert run.status == "completed"


def test_a_stop_request_ends_the_loop_between_iterations(monkeypatch, stub_run):
    loop, flow = _seed_loop(max_iterations=5, target_score=None)
    monkeypatch.setattr("flow.store.get_flow", lambda fid: flow)
    stub_run([10, 20, 30, 40, 50])

    import loops.runner as runner
    original = runner._execute_iteration

    def _stop_after_first(**kwargs):
        it = original(**kwargs)
        store.request_stop(kwargs["run"].loop_run_id)
        return it

    monkeypatch.setattr(runner, "_execute_iteration", _stop_after_first)
    run = run_loop(loop.loop_id, goal="write it")
    assert run.status == "stopped" and run.stop_reason == "stopped"
    assert run.iterations_done == 1


def test_a_loop_pointing_at_a_missing_flow_refuses_to_start(monkeypatch):
    loop = store.save_loop(Loop(name="L", flow_id="gone"))
    monkeypatch.setattr("flow.store.get_flow", lambda fid: None)
    with pytest.raises(ValueError, match="missing flow"):
        run_loop(loop.loop_id, goal="x")


# ── Storage ───────────────────────────────────────────────────────────────────

def test_deleting_a_loop_takes_its_runs_and_iterations_with_it():
    loop = store.save_loop(Loop(name="L", flow_id="f"))
    run = store.save_run(LoopRun(loop_id=loop.loop_id))
    store.save_iteration(Iteration(loop_run_id=run.loop_run_id, iteration=1))

    assert store.delete_loop(loop.loop_id)
    assert store.get_run(run.loop_run_id) is None
    assert store.list_iterations(run.loop_run_id) == []
