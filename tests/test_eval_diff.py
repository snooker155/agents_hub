"""Diff of two eval runs: fixed, regressed, same, only_a, only_b.

Builds the two runs directly out of ``evals.store`` (no agent stub needed) so
each test controls exactly which (case, config) pairs exist in which run and
what they scored, which is all ``diff_runs`` looks at.
"""
import pytest

from evals import store
from evals.models import EvalResult, EvalRun
from evals.runner import diff_runs


def _run(eval_set_id="evs_1", **kw):
    run = EvalRun(eval_set_id=eval_set_id, status="completed", **kw)
    return store.save_eval_run(run)


def _result(eval_run_id, case_id, config_label, *, passed, score, attempt=1):
    r = EvalResult(
        eval_run_id=eval_run_id, case_id=case_id, config_label=config_label,
        attempt=attempt, ok=True, passed=passed, score=score,
    )
    return store.save_result(r)


def test_a_case_that_started_failing_and_now_passes_is_fixed():
    run_a = _run()
    run_b = _run()
    _result(run_a.eval_run_id, "c1", "base", passed=False, score=0.0)
    _result(run_b.eval_run_id, "c1", "base", passed=True, score=1.0)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    row = diff["cases"][0]
    assert row["case_id"] == "c1" and row["config_a"] == "base" and row["config_b"] == "base"
    assert row["a"] == {"passed": False, "score": 0.0}
    assert row["b"] == {"passed": True, "score": 1.0}
    assert row["change"] == "fixed"
    assert diff["summary"] == {
        "fixed": 1, "regressed": 0, "same": 0,
        "pass_rate_a": 0.0, "pass_rate_b": 1.0,
    }


def test_a_case_that_used_to_pass_and_now_fails_is_regressed():
    run_a = _run()
    run_b = _run()
    _result(run_a.eval_run_id, "c1", "base", passed=True, score=1.0)
    _result(run_b.eval_run_id, "c1", "base", passed=False, score=0.2)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    assert diff["cases"][0]["change"] == "regressed"
    assert diff["summary"]["regressed"] == 1
    assert diff["summary"]["fixed"] == 0


def test_a_case_that_passes_in_both_or_fails_in_both_is_the_same():
    run_a = _run()
    run_b = _run()
    _result(run_a.eval_run_id, "pass_case", "base", passed=True, score=1.0)
    _result(run_b.eval_run_id, "pass_case", "base", passed=True, score=0.9)
    _result(run_a.eval_run_id, "fail_case", "base", passed=False, score=0.1)
    _result(run_b.eval_run_id, "fail_case", "base", passed=False, score=0.0)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    changes = {row["case_id"]: row["change"] for row in diff["cases"]}
    assert changes == {"pass_case": "same", "fail_case": "same"}
    assert diff["summary"]["same"] == 2
    assert diff["summary"]["fixed"] == 0 and diff["summary"]["regressed"] == 0


def test_a_case_only_in_one_run_is_only_a_or_only_b():
    run_a = _run()
    run_b = _run()
    _result(run_a.eval_run_id, "removed_case", "base", passed=True, score=1.0)
    _result(run_b.eval_run_id, "new_case", "base", passed=True, score=1.0)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    changes = {row["case_id"]: row["change"] for row in diff["cases"]}
    assert changes == {"removed_case": "only_a", "new_case": "only_b"}

    only_a_row = next(r for r in diff["cases"] if r["case_id"] == "removed_case")
    assert only_a_row["config_a"] == "base" and only_a_row["config_b"] is None
    assert only_a_row["b"] is None and only_a_row["a"] == {"passed": True, "score": 1.0}

    only_b_row = next(r for r in diff["cases"] if r["case_id"] == "new_case")
    assert only_b_row["config_b"] == "base" and only_b_row["config_a"] is None
    assert only_b_row["a"] is None

    # only_a/only_b do not count toward fixed/regressed/same.
    assert diff["summary"]["fixed"] == 0
    assert diff["summary"]["regressed"] == 0
    assert diff["summary"]["same"] == 0


def test_diff_matches_by_case_and_config_label_not_just_case():
    """The same case under two differently-labelled configs is two separate
    pairs, not one — a diff must not conflate columns that measure different
    agents/models just because they share a case id."""
    run_a = _run()
    run_b = _run()
    _result(run_a.eval_run_id, "c1", "gpt", passed=True, score=1.0)
    _result(run_a.eval_run_id, "c1", "claude", passed=False, score=0.0)
    _result(run_b.eval_run_id, "c1", "gpt", passed=True, score=1.0)
    _result(run_b.eval_run_id, "c1", "claude", passed=True, score=1.0)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    by_config = {row["config_a"] or row["config_b"]: row["change"] for row in diff["cases"]}
    assert by_config == {"gpt": "same", "claude": "fixed"}


def test_diff_aggregates_repeats_before_comparing():
    """A case run with repeats compares on its pass_rate (majority of attempts),
    not a single attempt — consistent with summarize()'s per-case aggregate."""
    run_a = _run()
    run_b = _run()
    # Run A: 2 of 3 attempts failed -> pass_rate 1/3 -> counts as failed.
    _result(run_a.eval_run_id, "c1", "base", passed=False, score=0.0, attempt=1)
    _result(run_a.eval_run_id, "c1", "base", passed=False, score=0.0, attempt=2)
    _result(run_a.eval_run_id, "c1", "base", passed=True, score=1.0, attempt=3)
    # Run B: 2 of 3 attempts passed -> pass_rate 2/3 -> counts as passed.
    _result(run_b.eval_run_id, "c1", "base", passed=True, score=1.0, attempt=1)
    _result(run_b.eval_run_id, "c1", "base", passed=True, score=1.0, attempt=2)
    _result(run_b.eval_run_id, "c1", "base", passed=False, score=0.0, attempt=3)

    diff = diff_runs(run_a.eval_run_id, run_b.eval_run_id)
    assert diff["cases"][0]["change"] == "fixed"
    assert diff["summary"]["pass_rate_a"] == pytest.approx(0.0)
    assert diff["summary"]["pass_rate_b"] == pytest.approx(1.0)


def test_diff_refuses_an_unknown_run_id():
    run_a = _run()
    with pytest.raises(ValueError):
        diff_runs(run_a.eval_run_id, "nope")
    with pytest.raises(ValueError):
        diff_runs("nope", run_a.eval_run_id)
