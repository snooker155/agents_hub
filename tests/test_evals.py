"""Eval harness: graders, store round-trips, aggregation, cost projection.

The runner's LLM calls are stubbed — what is worth testing is that a score is
computed the way the suite claims, that a failed run scores zero instead of
vanishing from the average, and that a sweep stops on a cost ceiling.
"""
import pytest

from evals import store
from evals.graders import GRADERS, grade, grade_all
from evals.models import Case, EvalSet, GraderSpec, RunConfig


def make_set(**kw):
    defaults = dict(
        name="set",
        cases=[Case(case_id="c1", input="q1", expected="Paris")],
        graders=[GraderSpec("substring")],
    )
    defaults.update(kw)
    return store.save_eval_set(EvalSet(**defaults))


# -- Deterministic graders ----------------------------------------------------

def test_exact_grader():
    c = Case(expected="Paris")
    assert grade("  paris ", c, GraderSpec("exact")).passed          # case-insensitive
    assert not grade("Paris, France", c, GraderSpec("exact")).passed


def test_exact_grader_case_sensitive_option():
    c = Case(expected="Paris")
    assert not grade("paris", c, GraderSpec("exact", {"case_sensitive": True})).passed


def test_substring_gives_partial_credit():
    """A partial score localises a regression that pass/fail would hide."""
    c = Case()
    spec = GraderSpec("substring", {"all_of": ["alpha", "beta", "gamma"]})
    r = grade("alpha and beta only", c, spec)
    assert r.score == pytest.approx(2 / 3)
    assert not r.passed
    assert "gamma" in r.detail


def test_grader_without_a_reference_fails_loudly_not_silently():
    r = grade("anything", Case(), GraderSpec("exact"))
    assert not r.passed and "expected" in r.detail


def test_regex_grader():
    c = Case()
    assert grade("order #4821 shipped", c, GraderSpec("regex", {"pattern": r"#\d{4}"})).passed
    assert not grade("no id here", c, GraderSpec("regex", {"pattern": r"#\d{4}"})).passed


def test_regex_grader_reports_a_bad_pattern():
    r = grade("x", Case(), GraderSpec("regex", {"pattern": "([unclosed"}))
    assert not r.passed and "invalid pattern" in r.detail


def test_json_graders_handle_fenced_output():
    c = Case()
    fenced = 'Here you go:\n```json\n{"name": "x", "n": 3}\n```\nHope that helps.'
    assert grade(fenced, c, GraderSpec("json_valid")).passed
    schema = {"type": "object", "required": ["name", "n"],
              "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}}
    assert grade(fenced, c, GraderSpec("json_schema", {"schema": schema})).passed


def test_json_schema_reports_what_is_wrong():
    schema = {"type": "object", "required": ["name"],
              "properties": {"name": {"type": "string"}}}
    r = grade('{"name": 5}', Case(), GraderSpec("json_schema", {"schema": schema}))
    assert not r.passed
    assert "expected string" in r.detail


def test_json_schema_does_not_let_a_boolean_pass_as_a_number():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    r = grade('{"n": true}', Case(), GraderSpec("json_schema", {"schema": schema}))
    assert not r.passed


def test_assertions_grader_scores_the_fraction_that_hold():
    spec = GraderSpec("assertions", {"assertions": [
        {"type": "contains", "value": "yes"},
        {"type": "not_contains", "value": "error"},
        {"type": "min_length", "value": 1000},
    ]})
    r = grade("yes, all good", Case(), spec)
    assert r.score == pytest.approx(2 / 3)
    assert not r.passed


def test_unknown_grader_scores_zero_rather_than_crashing():
    r = grade("x", Case(), GraderSpec("no_such_grader"))
    assert not r.passed and "unknown grader" in r.detail


def test_a_crashing_grader_is_contained(monkeypatch):
    def boom(output, case, params):
        raise RuntimeError("kaboom")
    monkeypatch.setitem(GRADERS, "exploding", boom)
    r = grade("x", Case(), GraderSpec("exploding"))
    assert not r.passed and "kaboom" in r.detail


# -- Aggregation --------------------------------------------------------------

def test_grade_all_requires_every_grader_to_pass():
    """Averaging a failed rubric away would hide it — passing is a conjunction."""
    c = Case(expected="Paris")
    specs = [GraderSpec("substring"), GraderSpec("regex", {"pattern": r"\d{4}"})]
    per, combined, passed = grade_all("Paris", c, specs)
    assert combined == pytest.approx(0.5)
    assert not passed
    assert set(per) == {"substring", "regex"}


def test_grade_all_respects_weights():
    c = Case(expected="Paris")
    specs = [
        GraderSpec("substring", weight=3.0),                    # scores 1.0
        GraderSpec("regex", {"pattern": r"\d{4}"}, weight=1.0),  # scores 0.0
    ]
    _, combined, _ = grade_all("Paris", c, specs)
    assert combined == pytest.approx(0.75)


def test_grade_all_with_no_graders_scores_zero():
    assert grade_all("x", Case(), []) == ({}, 0.0, False)


# -- LLM judge ----------------------------------------------------------------

def test_llm_judge_parses_a_score_and_keeps_the_reasoning(monkeypatch):
    class FakeLLM:
        def invoke(self, prompt):
            assert "RUBRIC" in prompt
            return type("R", (), {"content": '{"score": 8, "reasoning": "mostly right"}'})()

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())
    r = grade("some answer", Case(rubric="Is it right?"), GraderSpec("llm_judge"))
    assert r.score == pytest.approx(0.8)
    assert r.passed
    # The number is never the only thing kept.
    assert r.extra["reasoning"] == "mostly right"


def test_llm_judge_without_a_rubric_refuses_to_guess():
    r = grade("answer", Case(), GraderSpec("llm_judge"))
    assert not r.passed and "no rubric" in r.detail


def test_llm_judge_survives_an_unparseable_reply(monkeypatch):
    class FakeLLM:
        def invoke(self, prompt):
            return type("R", (), {"content": "I think it was pretty good honestly"})()

    import agents.agent_utils as au
    monkeypatch.setattr(au, "build_chat_model", lambda **kw: FakeLLM())
    r = grade("x", Case(rubric="r"), GraderSpec("llm_judge"))
    assert not r.passed and "parseable" in r.detail


# -- Store --------------------------------------------------------------------

def test_eval_set_round_trip():
    s = make_set(description="d", agent_id="a1")
    got = store.get_eval_set(s.eval_set_id)
    assert got.name == "set" and got.agent_id == "a1"
    assert got.cases[0].expected == "Paris"
    assert got.graders[0].kind == "substring"


def test_list_eval_sets_includes_global_sets_for_a_workspace():
    make_set(name="global", workspace=None)
    make_set(name="scoped", workspace="ws1")
    make_set(name="other", workspace="ws2")
    names = {e.name for e in store.list_eval_sets("ws1")}
    assert names == {"global", "scoped"}


def test_add_and_remove_case():
    s = make_set()
    store.add_case(s.eval_set_id, Case(case_id="c2", input="q2"))
    assert len(store.get_eval_set(s.eval_set_id).cases) == 2
    store.remove_case(s.eval_set_id, "c1")
    remaining = store.get_eval_set(s.eval_set_id).cases
    assert [c.case_id for c in remaining] == ["c2"]


def test_deleting_a_set_deletes_its_runs_and_results():
    from evals.models import EvalResult, EvalRun
    s = make_set()
    run = store.save_eval_run(EvalRun(eval_set_id=s.eval_set_id))
    store.save_result(EvalResult(eval_run_id=run.eval_run_id, case_id="c1"))
    assert store.delete_eval_set(s.eval_set_id)
    assert store.get_eval_run(run.eval_run_id) is None
    assert store.list_results(run.eval_run_id) == []


# -- Runner -------------------------------------------------------------------

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
    """Replace agent construction and invocation with a scripted responder."""
    outputs = {}

    class FakeAgent:
        provider, model, system_prompt = "openai", "gpt-5", "sys"
        agent_id = "fake"

    import agents.agent_factory as factory
    import agents.agent_invoke as invoke
    monkeypatch.setattr(factory, "create_agent", lambda *a, **k: FakeAgent())
    monkeypatch.setattr(
        invoke, "invoke_agent",
        lambda agent, prompt, **kw: _FakeInvocation(
            outputs.get(prompt, _FakeResult(True, "Paris"))
        ),
    )
    return outputs


def test_run_eval_scores_every_cell(stub_agent):
    s = make_set(cases=[
        Case(case_id="c1", input="q1", expected="Paris"),
        Case(case_id="c2", input="q2", expected="London"),
    ])
    run = __import__("evals.runner", fromlist=["run_eval"]).run_eval(
        s.eval_set_id, [RunConfig(agent_id="a", label="base")]
    )
    assert run.status == "completed"
    summary = run.summary["base"]
    assert summary["total"] == 2
    assert summary["passed"] == 1          # "Paris" matches c1, not c2
    assert summary["score"] == pytest.approx(0.5)


def test_a_failed_run_scores_zero_instead_of_being_dropped(stub_agent):
    """Skipping an errored cell would quietly inflate the average."""
    stub_agent["q1"] = _FakeResult(ok=False)
    s = make_set(cases=[
        Case(case_id="c1", input="q1", expected="Paris"),
        Case(case_id="c2", input="q2", expected="Paris"),
    ])
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [RunConfig(agent_id="a", label="base")])
    summary = run.summary["base"]
    assert summary["total"] == 2 and summary["errors"] == 1
    assert summary["score"] == pytest.approx(0.5)


def test_matrix_is_case_by_config(stub_agent):
    s = make_set(cases=[Case(case_id="c1", input="q1", expected="Paris")])
    from evals.runner import run_eval
    run = run_eval(s.eval_set_id, [
        RunConfig(agent_id="a", label="A"),
        RunConfig(agent_id="a", model="other", label="B"),
    ])
    matrix = store.build_matrix(run.eval_run_id)
    assert set(matrix) == {"c1"}
    assert set(matrix["c1"]) == {"A", "B"}


def test_cost_ceiling_stops_the_sweep(stub_agent, monkeypatch):
    import evals.runner as runner
    # Force a non-zero per-cell cost so the ceiling is reachable.
    monkeypatch.setattr(runner, "_run_cost", lambda *a: 1.0)
    s = make_set(cases=[Case(case_id=f"c{i}", input=f"q{i}", expected="Paris")
                        for i in range(5)])
    run = runner.run_eval(
        s.eval_set_id, [RunConfig(agent_id="a", label="base")], cost_ceiling=2.0
    )
    assert run.status == "stopped"
    assert "cost ceiling" in (run.error or "")
    assert run.summary["base"]["total"] < 5


def test_eval_runs_are_tagged_so_cost_aggregation_skips_them():
    from common.pricing import EVALUATION_CHANNELS
    from evals.models import EVAL_CHANNEL
    assert EVAL_CHANNEL in EVALUATION_CHANNELS
    assert "replay" in EVALUATION_CHANNELS


def test_project_cost_reports_the_call_count_before_spending(monkeypatch):
    import evals.runner as runner
    # The test root has no model catalog, so price the estimate explicitly.
    monkeypatch.setattr(runner, "_run_cost",
                        lambda p, m, i, o: (i + o) / 1_000_000 * 2.0)
    s = EvalSet(name="x", cases=[Case(input="q")] * 10,
                graders=[GraderSpec("llm_judge"), GraderSpec("substring")])
    out = runner.project_cost(
        s, [RunConfig(agent_id="a", provider="openai", model="gpt-5"),
            RunConfig(agent_id="a", provider="openai", model="gpt-5", label="b")]
    )
    assert out["cases"] == 10 and out["configs"] == 2
    # 20 cells, plus one judge call per cell.
    assert out["llm_calls"] == 40
    assert out["uses_llm_judge"] is True
    # Judge spend is reported separately: it is real, and it is not the cost of
    # the thing being measured.
    assert out["estimated_agent_cost"] > 0
    assert out["estimated_grader_cost"] > 0
    assert out["estimated_total_cost"] == pytest.approx(
        out["estimated_agent_cost"] + out["estimated_grader_cost"], abs=1e-4
    )


def test_project_cost_never_raises_without_a_price_catalog():
    """A missing catalog means unknown pricing, not a broken estimate screen."""
    from evals.runner import project_cost
    out = project_cost(EvalSet(name="x", cases=[Case(input="q")]),
                       [RunConfig(agent_id="a")])
    assert out["estimated_total_cost"] >= 0.0
