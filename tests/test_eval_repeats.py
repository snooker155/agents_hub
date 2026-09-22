"""Repeats and variance: a config can rerun every case N times, and the
summary reports the spread across those attempts instead of a single number.
"""
import pytest

from evals import store
from evals.models import Case, EvalSet, GraderSpec, RunConfig


def make_set(**kw):
    defaults = dict(
        name="set",
        cases=[Case(case_id="c1", input="q1", expected="Paris")],
        graders=[GraderSpec("substring")],
    )
    defaults.update(kw)
    return store.save_eval_set(EvalSet(**defaults))


class _FakeResult:
    def __init__(self, ok=True, output=""):
        self.ok = ok
        self.agent_output = output
        self.error = None if ok else "agent exploded"


class _FakeInvocation:
    def __init__(self, result):
        self.result = result
        self.duration_ms = 12
        self.process = {"token_usage": {"inbound_tokens": 10, "outbound_tokens": 5}}


@pytest.fixture
def stub_agent(monkeypatch):
    """Same scripted responder as test_evals.py, plus a call counter and the
    ability to script outputs per-call (not just per-prompt) so repeats can be
    made to disagree with each other."""
    calls = {"n": 0}
    script = {}  # prompt -> list of outputs, consumed in order

    class FakeAgent:
        provider, model, system_prompt = "openai", "gpt-5", "sys"
        agent_id = "fake"

    def _invoke(agent, prompt, **kw):
        calls["n"] += 1
        queue = script.get(prompt)
        if queue:
            return _FakeInvocation(queue.pop(0))
        return _FakeInvocation(_FakeResult(True, "Paris"))

    import agents.agent_factory as factory
    import agents.agent_invoke as invoke
    monkeypatch.setattr(factory, "create_agent", lambda *a, **k: FakeAgent())
    monkeypatch.setattr(invoke, "invoke_agent", _invoke)
    return {"calls": calls, "script": script}


# ── repeats produce N results per case ──────────────────────────────────────

def test_repeats_run_each_case_that_many_times(stub_agent):
    s = make_set(cases=[
        Case(case_id="c1", input="q1", expected="Paris"),
        Case(case_id="c2", input="q2", expected="Paris"),
    ])
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [RunConfig(agent_id="a", label="base", repeats=3)])

    results = store.list_results(run.eval_run_id)
    assert len(results) == 6  # 2 cases x 3 repeats
    by_case = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r.attempt)
    assert sorted(by_case["c1"]) == [1, 2, 3]
    assert sorted(by_case["c2"]) == [1, 2, 3]
    assert stub_agent["calls"]["n"] == 6


def test_repeats_default_to_one_and_cap_at_ten():
    assert RunConfig(agent_id="a").resolved_repeats() == 1
    assert RunConfig(agent_id="a", repeats=0).resolved_repeats() == 1
    assert RunConfig(agent_id="a", repeats=99).resolved_repeats() == 10
    assert RunConfig(agent_id="a", repeats=4).resolved_repeats() == 4


def test_repeats_round_trip_through_to_dict_and_from_dict():
    cfg = RunConfig.from_dict({"agent_id": "a", "repeats": 5})
    assert cfg.repeats == 5
    assert cfg.to_dict()["repeats"] == 5
    # A blank/absent key defaults to 1, same as a fresh RunConfig.
    assert RunConfig.from_dict({"agent_id": "a"}).repeats == 1


# ── aggregate math: pass_rate, mean, std, min, max ──────────────────────────

def test_summary_reports_pass_rate_mean_std_min_max(stub_agent):
    """Three attempts of the same case, scripted to disagree, so the aggregate
    math has a real spread to report instead of a constant."""
    s = make_set(cases=[Case(case_id="c1", input="q1", expected="Paris")])
    stub_agent["script"]["q1"] = [
        _FakeResult(True, "Paris"),        # substring match -> score 1.0, passed
        _FakeResult(True, "London"),       # no match -> score 0.0, failed
        _FakeResult(True, "Paris, France"),  # match -> score 1.0, passed
    ]
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [RunConfig(agent_id="a", label="base", repeats=3)])

    # Aggregate fields are persisted rounded to 4 decimals, so comparisons
    # against an exact fraction need an absolute tolerance wider than that.
    cfg_summary = run.summary["base"]
    assert cfg_summary["total"] == 3
    assert cfg_summary["pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert cfg_summary["score"] == pytest.approx(2 / 3, abs=1e-4)  # mean of [1, 0, 1]
    assert cfg_summary["min"] == pytest.approx(0.0, abs=1e-4)
    assert cfg_summary["max"] == pytest.approx(1.0, abs=1e-4)
    # Population std of [1, 0, 1]: mean 2/3, variance = ((1/3)^2*2 + (2/3)^2)/3
    expected_std = ((1 / 3) ** 2 * 2 + (2 / 3) ** 2) / 3
    assert cfg_summary["std"] == pytest.approx(expected_std ** 0.5, abs=1e-4)

    case_summary = cfg_summary["cases"]["c1"]
    assert case_summary["attempts"] == 3
    assert case_summary["pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert case_summary["unstable"] is True  # 2 passed, 1 failed: attempts disagree


def test_a_case_with_no_repeat_variance_is_not_flagged_unstable(stub_agent):
    s = make_set(cases=[Case(case_id="c1", input="q1", expected="Paris")])
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [RunConfig(agent_id="a", label="base", repeats=3)])
    case_summary = run.summary["base"]["cases"]["c1"]
    assert case_summary["unstable"] is False
    assert case_summary["std"] == pytest.approx(0.0)
    assert run.summary["base"]["unstable_cases"] == 0


def test_default_repeats_of_one_matches_the_pre_repeats_summary_shape(stub_agent):
    """With repeats=1 every attempt-level number collapses to the single-run
    number: existing behaviour must not change for the common case."""
    s = make_set(cases=[
        Case(case_id="c1", input="q1", expected="Paris"),
        Case(case_id="c2", input="q2", expected="London"),
    ])
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [RunConfig(agent_id="a", label="base")])
    summary = run.summary["base"]
    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["score"] == pytest.approx(0.5)
    assert summary["pass_rate"] == pytest.approx(0.5)
    assert set(summary["cases"]) == {"c1", "c2"}
    assert all(v["attempts"] == 1 for v in summary["cases"].values())


# ── cost estimate scales with repeats ───────────────────────────────────────

def test_project_cost_scales_with_repeats(monkeypatch):
    import evals.runner as runner
    monkeypatch.setattr(runner, "_run_cost", lambda p, m, i, o: (i + o) / 1_000_000 * 2.0)

    s = EvalSet(name="x", cases=[Case(input="q")] * 4, graders=[GraderSpec("substring")])
    once = runner.project_cost(s, [RunConfig(agent_id="a", provider="openai", model="gpt-5")])
    thrice = runner.project_cost(
        s, [RunConfig(agent_id="a", provider="openai", model="gpt-5", repeats=3)]
    )

    assert thrice["per_config"][0]["repeats"] == 3
    assert thrice["estimated_agent_cost"] == pytest.approx(once["estimated_agent_cost"] * 3)
    assert thrice["llm_calls"] == once["llm_calls"] * 3


def test_project_cost_clamps_repeats_to_the_cap(monkeypatch):
    import evals.runner as runner
    monkeypatch.setattr(runner, "_run_cost", lambda p, m, i, o: 1.0)

    s = EvalSet(name="x", cases=[Case(input="q")], graders=[])
    out = runner.project_cost(s, [RunConfig(agent_id="a", repeats=999)])
    assert out["per_config"][0]["repeats"] == 10
