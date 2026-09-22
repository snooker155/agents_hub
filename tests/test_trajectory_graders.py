"""Trajectory graders: score the tool calls a run actually made, not its text.

Each grader reads ``common.run_payloads``'s canonical ``tool_calls`` list for
the run behind a case (``EvalResult.run_id``), so the fixture here writes a
synthetic run through the real writer (``managers.run_manager.update_run`` ->
``managers.runs.store._write_payload_row`` -> ``common.run_payloads.canonicalize``)
rather than poking the eval_results/run_payloads tables directly.
"""
import pytest

from evals.graders import grade, grade_all
from evals.models import Case, GraderSpec
from managers import run_manager as rm


def _make_run(tool_calls, agent_id="test-agent"):
    """A run record plus a structured payload carrying these tool calls."""
    run_id = rm.new_unique_run_id()
    rm.open_run(run_id, agent_id)
    rm.update_run(run_id, {"process": {"tool_calls": tool_calls}})
    return run_id


CALLS = [
    {"step": 1, "tool": "search_docs", "input": '{"query": "pricing"}',
     "output": '{"ok": true, "hits": 3}'},
    {"step": 2, "tool": "read_doc", "input": '{"doc_id": "costs"}',
     "output": '{"ok": true, "text": "..."}'},
    {"step": 3, "tool": "search_docs", "input": '{"query": "budget"}',
     "output": '{"ok": true, "hits": 1}'},
]

CALLS_WITH_ERROR = CALLS + [
    {"step": 4, "tool": "fetch_url", "input": '{"url": "http://x"}',
     "output": '{"ok": false, "error": "timeout"}'},
]


# ── tool_called ──────────────────────────────────────────────────────────────

def test_tool_called_passes_when_called_at_least_once():
    run_id = _make_run(CALLS)
    r = grade("out", Case(), GraderSpec("tool_called", {"tool": "read_doc"}), run_id=run_id)
    assert r.passed
    assert "1 time" in r.detail


def test_tool_called_fails_when_never_called():
    run_id = _make_run(CALLS)
    r = grade("out", Case(), GraderSpec("tool_called", {"tool": "delete_file_tool"}), run_id=run_id)
    assert not r.passed
    assert "0 time" in r.detail


def test_tool_called_respects_min_and_max_times():
    run_id = _make_run(CALLS)  # search_docs called twice
    ok = grade("out", Case(), GraderSpec("tool_called", {"tool": "search_docs", "min_times": 2}),
               run_id=run_id)
    assert ok.passed

    too_strict = grade("out", Case(),
                       GraderSpec("tool_called", {"tool": "search_docs", "max_times": 1}),
                       run_id=run_id)
    assert not too_strict.passed


def test_tool_called_without_a_run_id_fails_loudly():
    r = grade("out", Case(), GraderSpec("tool_called", {"tool": "search_docs"}))
    assert not r.passed
    assert "no run payload" in r.detail


# ── tool_not_called ──────────────────────────────────────────────────────────

def test_tool_not_called_passes_when_absent():
    run_id = _make_run(CALLS)
    r = grade("out", Case(), GraderSpec("tool_not_called", {"tool": "delete_file_tool"}),
              run_id=run_id)
    assert r.passed


def test_tool_not_called_fails_when_present():
    run_id = _make_run(CALLS)
    r = grade("out", Case(), GraderSpec("tool_not_called", {"tool": "read_doc"}), run_id=run_id)
    assert not r.passed
    assert "read_doc" in r.detail


# ── tool_sequence ────────────────────────────────────────────────────────────

def test_tool_sequence_matches_in_order_non_contiguous():
    run_id = _make_run(CALLS)
    r = grade("out", Case(),
              GraderSpec("tool_sequence", {"tools": ["search_docs", "search_docs"]}),
              run_id=run_id)
    assert r.passed


def test_tool_sequence_fails_on_wrong_order():
    run_id = _make_run(CALLS)
    r = grade("out", Case(),
              GraderSpec("tool_sequence", {"tools": ["read_doc", "search_docs", "search_docs"]}),
              run_id=run_id)
    # read_doc happened between the two search_docs calls, not before both.
    assert not r.passed


def test_tool_sequence_contiguous_requires_back_to_back():
    run_id = _make_run(CALLS)
    not_contig = grade("out", Case(),
                       GraderSpec("tool_sequence",
                                  {"tools": ["search_docs", "search_docs"], "contiguous": True}),
                       run_id=run_id)
    assert not not_contig.passed  # read_doc sits between them

    contig = grade("out", Case(),
                   GraderSpec("tool_sequence",
                              {"tools": ["search_docs", "read_doc"], "contiguous": True}),
                   run_id=run_id)
    assert contig.passed


# ── max_tool_calls ───────────────────────────────────────────────────────────

def test_max_tool_calls_passes_under_the_limit():
    run_id = _make_run(CALLS)  # 3 calls
    r = grade("out", Case(), GraderSpec("max_tool_calls", {"limit": 5}), run_id=run_id)
    assert r.passed


def test_max_tool_calls_fails_over_the_limit():
    run_id = _make_run(CALLS)  # 3 calls
    r = grade("out", Case(), GraderSpec("max_tool_calls", {"limit": 2}), run_id=run_id)
    assert not r.passed
    assert "3 tool call" in r.detail


# ── tool_input_matches ───────────────────────────────────────────────────────

def test_tool_input_matches_finds_the_pattern_in_some_call():
    run_id = _make_run(CALLS)
    r = grade("out", Case(),
              GraderSpec("tool_input_matches", {"tool": "search_docs", "pattern": "budget"}),
              run_id=run_id)
    assert r.passed


def test_tool_input_matches_fails_when_no_call_matches():
    run_id = _make_run(CALLS)
    r = grade("out", Case(),
              GraderSpec("tool_input_matches", {"tool": "search_docs", "pattern": "refund"}),
              run_id=run_id)
    assert not r.passed


def test_tool_input_matches_fails_when_the_tool_was_never_called():
    run_id = _make_run(CALLS)
    r = grade("out", Case(),
              GraderSpec("tool_input_matches", {"tool": "fetch_url", "pattern": "x"}),
              run_id=run_id)
    assert not r.passed
    assert "was not called" in r.detail


# ── no_error_tool_results ────────────────────────────────────────────────────

def test_no_error_tool_results_passes_on_a_clean_run():
    run_id = _make_run(CALLS)
    r = grade("out", Case(), GraderSpec("no_error_tool_results"), run_id=run_id)
    assert r.passed


def test_no_error_tool_results_fails_when_a_tool_errored():
    run_id = _make_run(CALLS_WITH_ERROR)
    r = grade("out", Case(), GraderSpec("no_error_tool_results"), run_id=run_id)
    assert not r.passed
    assert "fetch_url" in r.detail


def test_no_error_tool_results_catches_the_on_tool_error_string_convention():
    calls = CALLS + [{"step": 4, "tool": "fetch_url", "input": "{}", "output": "ERROR: boom"}]
    run_id = _make_run(calls)
    r = grade("out", Case(), GraderSpec("no_error_tool_results"), run_id=run_id)
    assert not r.passed


# ── grade_all forwards run_id ────────────────────────────────────────────────

def test_grade_all_forwards_run_id_to_trajectory_graders_only():
    """An output grader must not choke on the extra context, and a trajectory
    grader in the same suite must still get the payload it needs."""
    run_id = _make_run(CALLS)
    case = Case(expected="hello")
    specs = [GraderSpec("substring"), GraderSpec("tool_called", {"tool": "read_doc"})]
    per_grader, combined, passed = grade_all("hello world", case, specs, run_id=run_id)
    assert per_grader["substring"]["passed"]
    assert per_grader["tool_called"]["passed"]
    assert passed
    assert combined == pytest.approx(1.0)


def test_grade_all_without_run_id_still_runs_output_graders():
    """Backward compatibility: existing callers that never pass run_id keep working."""
    case = Case(expected="hello")
    per_grader, combined, passed = grade_all("hello", case, [GraderSpec("exact")])
    assert passed and combined == pytest.approx(1.0)


def test_unknown_run_id_reads_as_zero_calls_not_a_crash():
    """A run id that names nothing recorded behaves like an empty trajectory
    (never raises); only the *absence* of a run id refuses outright."""
    r = grade("out", Case(), GraderSpec("tool_called", {"tool": "x"}), run_id="run_does_not_exist")
    assert not r.passed
    assert "0 time" in r.detail
